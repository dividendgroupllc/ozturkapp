# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""E2E "rollback shim" himoyasi: bitta tranzaksiya, hech qachon COMMIT emas.

MAQSAD
======
Haqiqiy interfeys haqiqiy backend mantig'iga qarshi sinaladi, lekin `ozturk.local`
ishlab chiqarish nusxasiga o'xshaydi: HAR bir veb-so'rov COMMIT qiladi. Shu sababli
butun E2E bitta ochiq DB tranzaksiyasi ichida yuradi va oxirida ALBATTA `ROLLBACK`
qilinadi. Bu modul shu kafolatni beradi:

  1. `commit()` / `begin()` / `rollback()` "virtual" qilinadi: `commit` — hozirgi
     so'rov SAVEPOINT'ini yangilaydi (kod "saqlandi" deb hisoblaydi, xato bo'lsa esa
     faqat oxirgi "commit"dan keyingi yozuvlar bekor bo'ladi — xuddi haqiqiy
     so'rovdagidek). `rollback` — SAVEPOINT'ga qaytadi, butun tranzaksiyani emas.
  2. `frappe.db.sql` va kursor DDL / COMMIT / START TRANSACTION / autocommit'ni
     RAD ETADI (MariaDB'da DDL tranzaksiyani jimgina commit qiladi).
  3. `enqueue` / `sendmail` yozib olinadi va bajarilmaydi: haqiqiy ishchi (worker)
     rolled-back cheklarga tegmasin.
  4. Chiqishda (`finally`, SIGINT/SIGTERM/SIGHUP, `atexit`) ROLLBACK. SIGKILL bo'lsa
     ulanish uziladi va MariaDB tranzaksiyani o'zi qaytaradi.
  5. Mustaqil ikkinchi ulanish bilan BUTUN bazaning (890 jadval) `CHECKSUM TABLE`
     barmoq izi oldin/keyin solishtiriladi.
"""

import atexit
import re
import signal
import threading

import frappe
import pymysql

#: Tranzaksiyani jimgina yopadigan yoki umuman rad etiladigan buyruqlar.
_FORBIDDEN = re.compile(
    r"""^\s*(?:/\*.*?\*/\s*)*(?:
        commit | begin | start\s+transaction | create | alter | drop | truncate | rename
      | lock\s+tables | unlock\s+tables | flush | analyze | optimize | repair
      | grant | revoke | load\s+data | xa\s | install | uninstall
      | set\s+(?:session\s+|global\s+|@@(?:session\.|global\.)?)?(?:autocommit|@@autocommit)
      | rollback(?!\s+to\b)
    )\b""",
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

_SAVEPOINT = "e2e_request"


_REAL_FRAPPE_ENQUEUE = frappe.enqueue


class ForbiddenStatement(Exception):
    """E2E tranzaksiyasini buzadigan SQL buyrug'i."""


def check_statement(query) -> None:
    text = str(query)
    if _FORBIDDEN.match(text):
        raise ForbiddenStatement(f"E2E himoyasi rad etdi: {text.strip()[:120]}")


class Guard:
    """Bitta ochiq tranzaksiya va uning virtual commit/rollback semantikasi."""

    def __init__(self):
        self.db = None
        self.installed = False
        self.rolled_back = False
        self.enqueued = []
        self.pending_jobs = []
        self.job_errors = []
        self.skipped_jobs = []
        self.realtime = []
        self.mails = []
        self.commits = 0
        self.rollbacks = 0
        self._orig = {}
        self._conn = None

    # ── O'rnatish ──────────────────────────────────────────────────────

    def install(self):
        db = self.db = frappe.local.db

        # Ulanish `frappe.db` da birinchi so'rovda LAZY ochiladi.
        row = db.sql("select @@autocommit")
        if row[0][0] != 0:
            raise RuntimeError("autocommit YOQIQ — E2E xavfsiz emas")
        self._conn = db._conn

        orig_sql = db.sql
        orig_sql("start transaction")
        orig_sql(f"savepoint {_SAVEPOINT}")

        def guarded_sql(query, *args, **kwargs):
            check_statement(query)
            return orig_sql(query, *args, **kwargs)

        self._orig["sql"] = orig_sql
        db.sql = guarded_sql

        # Kursor va ulanish darajasi: `frappe.db.sql` ni chetlab o'tadigan kod ham to'xtasin.
        cursor = db._cursor
        orig_execute = cursor.execute

        def guarded_execute(query, args=None):
            check_statement(query)
            return orig_execute(query, args)

        cursor.execute = guarded_execute

        def refuse(*_a, **_k):
            raise ForbiddenStatement("E2E himoyasi: to'g'ridan-to'g'ri connection.commit/autocommit")

        self._conn.commit = refuse
        self._conn.autocommit = refuse

        db.commit = self._virtual_commit
        db.rollback = self._virtual_rollback
        db.begin = lambda *a, **k: None
        db.sql_ddl = lambda *a, **k: (_ for _ in ()).throw(ForbiddenStatement("DDL rad etildi"))

        self._patch_side_effects()
        self.installed = True
        atexit.register(self.rollback_all)

    def _patch_side_effects(self):
        import frappe.utils.background_jobs as background_jobs

        recorder = self

        def fake_enqueue(method, queue="default", timeout=None, event=None, is_async=True,
                         job_name=None, now=False, enqueue_after_commit=False, **kwargs):
            name = method if isinstance(method, str) else f"{method.__module__}.{method.__qualname__}"
            if now:
                # `now=True` — chaqiruvchi o'sha zahoti ichkarida bajarilishini kutadi.
                return self._orig_enqueue(method, queue=queue, timeout=timeout, event=event,
                                          is_async=False, job_name=job_name, now=True, **kwargs)
            job = {"method": name, "queue": queue, "kwargs": kwargs, "user": frappe.session.user,
                   "after_commit": bool(enqueue_after_commit)}

            def queue_job():
                recorder.pending_jobs.append(job)
                recorder.enqueued.append(
                    {"method": name, "queue": queue, "after_commit": job["after_commit"],
                     "kwargs": {k: (v if _jsonable(v) else str(v)) for k, v in kwargs.items()}}
                )

            # Haqiqiy Frappe kabi: `enqueue_after_commit` — faqat COMMIT bo'lsa; rollback uni tashlab yuboradi.
            if enqueue_after_commit:
                frappe.db.after_commit.add(queue_job)
            else:
                queue_job()
            return None

        def fake_sendmail(*args, **kwargs):
            recorder.mails.append({"recipients": kwargs.get("recipients"), "subject": kwargs.get("subject")})

        import frappe.realtime as realtime

        real_emit = realtime.emit_via_redis
        self._orig["emit"] = real_emit

        def recording_emit(event, message, room, *args, **kwargs):
            recorder.realtime.append({"event": event, "message": message, "room": room})
            return real_emit(event, message, room, *args, **kwargs)

        realtime.emit_via_redis = recording_emit

        self._orig_enqueue = background_jobs.enqueue
        background_jobs.enqueue = fake_enqueue
        frappe.enqueue = fake_enqueue
        self._orig["sendmail"] = frappe.sendmail
        frappe.sendmail = fake_sendmail

    # ── Virtual commit / rollback ──────────────────────────────────────

    def _virtual_commit(self):
        """`Database.commit` nusxasi: SQL `COMMIT` o'rniga SAVEPOINT yangilanadi."""
        db = self.db
        db.before_rollback.reset()
        db.after_rollback.reset()
        db.before_commit.run()

        self._orig["sql"](f"release savepoint {_SAVEPOINT}")
        self._orig["sql"](f"savepoint {_SAVEPOINT}")
        self.commits += 1

        db.value_cache.clear()
        db.after_commit.run()

    def _virtual_rollback(self, *, save_point=None):
        """`Database.rollback` nusxasi: butun tranzaksiya emas, so'rov SAVEPOINT'iga."""
        db = self.db
        if save_point:
            self._orig["sql"](f"rollback to savepoint {save_point}")
            db.value_cache.clear()
            return

        db.before_commit.reset()
        db.after_commit.reset()
        db.before_rollback.run()

        self._orig["sql"](f"rollback to savepoint {_SAVEPOINT}")
        self.rollbacks += 1

        db.value_cache.clear()
        db.after_rollback.run()

    def check_connection(self):
        """Ulanish almashtirilgan bo'lsa (uzilish/qayta ulanish) tranzaksiya yo'qolgan."""
        if frappe.local.db is not self.db or self.db._conn is not self._conn:
            raise RuntimeError("DB ulanishi almashtirildi — E2E tranzaksiyasi yo'qolgan")

    # ── Yakun ──────────────────────────────────────────────────────────

    def rollback_all(self):
        """Butun tranzaksiyani QAYTARADI. Bir necha marta chaqirish xavfsiz."""
        if self.rolled_back or not self.installed:
            return
        self.rolled_back = True
        self._conn.rollback()

    def uninstall(self):
        """Yamalarni olib tashlaydi (`bench execute` oxirida haqiqiy `commit()` bo'sh tranzaksiyaga tushsin)."""
        if not self.installed:
            return
        import frappe.utils.background_jobs as background_jobs

        for target, names in (
            (self.db, ("sql", "commit", "rollback", "begin", "sql_ddl")),
            (self.db._cursor, ("execute",)),
            (self._conn, ("commit", "autocommit")),
        ):
            for name in names:
                target.__dict__.pop(name, None)
        import frappe.realtime as realtime

        realtime.emit_via_redis = self._orig["emit"]
        background_jobs.enqueue = self._orig_enqueue
        frappe.enqueue = _REAL_FRAPPE_ENQUEUE
        frappe.sendmail = self._orig["sendmail"]
        self.installed = False

    def install_signal_handlers(self):
        def _raise(signum, _frame):
            raise SystemExit(128 + signum)

        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                signal.signal(sig, _raise)


def _jsonable(value) -> bool:
    return isinstance(value, (str, int, float, bool, type(None), list, dict))


# ═══════════════════════════════════════════════════════════════════
#  Barmoq izi: mustaqil ulanish, butun baza
# ═══════════════════════════════════════════════════════════════════

#: Aniq (qat'iy) solishtiriladigan jadvallar: son + mazmun.
CRITICAL_TABLES = (
    "POS Invoice", "POS Invoice Item", "POS Opening Entry", "POS Closing Entry", "GL Entry",
    "Series", "Ozturk Print Job", "Customer", "Comment", "URY KOT", "URY KOT Items",
    "URY Table", "Ozturk Cash Movement", "Payment Ledger Entry", "Stock Ledger Entry",
    "Sales Invoice", "Journal Entry", "Item Price", "POS Profile", "POS Payment Method",
    "Mode of Payment", "User", "Has Role", "URY User", "POS Profile User", "Error Log",
    "Version", "Deleted Document", "Contact", "Address", "Sessions", "Activity Log",
)


def fingerprint_connection():
    """Saytning DB'siga MUSTAQIL (tranzaksiyadan tashqari) ulanish."""
    conf = frappe.local.conf
    return pymysql.connect(
        host=conf.db_host or "127.0.0.1",
        port=int(conf.db_port or 3306),
        user=conf.db_name,
        password=conf.db_password,
        database=conf.db_name,
        autocommit=True,
        charset="utf8mb4",
    )


def fingerprint():
    """`{jadval: (checksum, qatorlar)}` — barcha oddiy jadvallar."""
    conn = fingerprint_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "select table_name from information_schema.tables "
            "where table_schema = database() and table_type = 'BASE TABLE' order by table_name"
        )
        tables = [row[0] for row in cur.fetchall()]

        checksums = {}
        for start in range(0, len(tables), 40):
            chunk = tables[start : start + 40]
            cur.execute("checksum table " + ",".join(f"`{t}`" for t in chunk))
            for name, value in cur.fetchall():
                checksums[name.split(".", 1)[-1]] = value

        counts = {}
        for doctype in CRITICAL_TABLES:
            table = f"tab{doctype}"
            if table in checksums:
                cur.execute(f"select count(*) from `{table}`")
                counts[doctype] = cur.fetchone()[0]

        cur.execute("select name, current from `tabSeries` order by name")
        series = [list(row) for row in cur.fetchall()]

        # MariaDB SEQUENCE'lari tranzaksiyaga bo'ysunmaydi: `NEXTVAL` rollbackda qaytmaydi.
        cur.execute("select table_name from information_schema.tables "
                    "where table_schema = database() and table_type = 'SEQUENCE'")
        sequences = {}
        for (name,) in cur.fetchall():
            cur.execute(f"select next_not_cached_value from `{name}`")
            sequences[name] = cur.fetchone()[0]

        rows = {}
        for table, key in ROW_LEVEL.items():
            cur.execute(f"select * from `{table}`")
            columns = [c[0] for c in cur.description]
            rows[table] = {str(r[columns.index(key)]): dict(zip(columns, map(str, r))) for r in cur.fetchall()}
        return {"checksums": checksums, "counts": counts, "series": series, "rows": rows, "sequences": sequences}
    finally:
        conn.close()


#: Haqiqiy serverdagi BOSHQA sessiyalar (kimdir dev saytni ochib tursa) shu jadvallarni doim
#: "tegib" turadi: qator darajasida solishtiramiz va faqat quyidagi ustunlar farqini kechiramiz.
ROW_LEVEL = {"tabUser": "name", "tabSessions": "sid"}
VOLATILE_COLUMNS = {"last_active", "last_login", "last_ip", "lastupdate", "last_known_versions", "sessiondata"}


def diff_fingerprints(before, after, ignore=()):
    """O'zgargan jadvallar ro'yxati (`ignore` — ma'lum fon shovqini)."""
    changed = []
    for table, value in after["checksums"].items():
        if table in ignore or before["checksums"].get(table) == value:
            continue
        if table in ROW_LEVEL and not _row_level_changes(before["rows"][table], after["rows"][table]):
            continue
        changed.append(table)
    for table in before["checksums"]:
        if table not in after["checksums"]:
            changed.append(table)
    return sorted(set(changed))


def _row_level_changes(old, new):
    """Qator qo'shilgan/o'chirilgan yoki o'zgaruvchan bo'lmagan ustun o'zgargan bo'lsa — farqlar."""
    problems = []
    for key in set(old) | set(new):
        if key not in old or key not in new:
            problems.append(key)
            continue
        diff = {c for c in old[key] if old[key][c] != new[key].get(c)} - VOLATILE_COLUMNS
        if diff:
            problems.append((key, sorted(diff)))
    return problems
