# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""E2E ishga tushiruvchi ("rollback shim"). To'liq izoh: `README.md`.

    cd /home/sherzod/frappe-bench/sites && flock /tmp/ozturk_tests.lock \\
        ../env/bin/python -m ozturkapp.ozturkapp.tests.e2e.run

Tartib:
    1. Oldindan tekshiruv (disk, :8000 server, portlar) va BAZA BARMOQ IZI (oldin).
    2. Brauzer uchun vaqtinchalik foydalanuvchi (yagona commit qilinadigan yozuv).
    3. `Guard.install()`: bitta tranzaksiya, commit yo'q. Fikstura shu ichida.
    4. Shim serveri + Node/CDP haydovchisi (Chrome). Senariylar haqiqiy interfeysni
       haqiqiy backendga qarshi yuritadi.
    5. ROLLBACK, keshni tozalash, brauzer foydalanuvchisini o'chirish.
    6. BAZA BARMOQ IZI (keyin) == oldin — aks holda jarayon xato bilan tugaydi.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

import frappe

from ozturkapp.ozturkapp.tests.e2e import browser_user, fixtures
from ozturkapp.ozturkapp.tests.e2e.guard import Guard, _row_level_changes, diff_fingerprints, fingerprint
from ozturkapp.ozturkapp.tests.e2e.shim import Shim, make_server

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = "ozturk.local"
SITES_PATH = "/home/sherzod/frappe-bench/sites"
WORK_DIR = "/tmp/e2e_run"
SHIM_PORT = 8765
CHROME_PORT = 9333
MIN_FREE_MB = 500

#: Jadvallar: har bir ishga tushirishda o'zgarib turadigan fon shovqini (scheduler, RQ, keshlar).
#: Ular ham o'lchanadi (`background_noise`) va shundan tashqari HECH NARSA o'zgarmasligi kerak.


def _preflight():
    free_mb = shutil.disk_usage("/tmp").free // (1024 * 1024)
    if free_mb < MIN_FREE_MB:
        raise SystemExit(f"Diskda joy kam: {free_mb} MB (kerak: {MIN_FREE_MB} MB)")
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/method/ping", timeout=5) as response:
            if response.status != 200:
                raise OSError(response.status)
    except OSError as exc:
        raise SystemExit(f"bench serveri :8000 da javob bermayapti: {exc}")


def _print_table(title, rows, columns):
    print(f"\n=== {title} ===")
    if not rows:
        print("(yo'q)")
        return
    widths = [max(len(str(row.get(col, ""))) for row in rows + [{c: c for c in columns}]) for col in columns]
    widths = [min(w, 60) for w in widths]
    print("  ".join(c.ljust(w) for c, w in zip(columns, widths)))
    for row in rows:
        print("  ".join(str(row.get(c, ""))[:w].ljust(w) for c, w in zip(columns, widths)))


def main(scenarios: str = "", selftest: bool = False, headed: bool = False, keep_shots: bool = False,
         hold: int = 0):
    """Kirish nuqtasi (`bench execute` ham, `python -m` ham)."""
    own_init = not getattr(frappe.local, "site", None)
    if own_init:
        os.chdir(SITES_PATH)
        frappe.init(site=SITE, sites_path=SITES_PATH)
        frappe.connect()
    frappe.set_user("Administrator")
    site, sites_path = frappe.local.site, frappe.local.sites_path

    _preflight()
    shutil.rmtree(WORK_DIR, ignore_errors=True)  # oldingi (uzilgan) yugurishdan qolgan profil/skrinshotlar
    os.makedirs(WORK_DIR, exist_ok=True)

    before = fingerprint()
    time.sleep(1.5)
    noise = set(diff_fingerprints(before, fingerprint()))
    print(f"[e2e] baza barmoq izi olindi: {len(before['checksums'])} jadval, fon shovqini: {sorted(noise)}")

    creds = None if selftest else browser_user.new_credentials()
    guard = Guard()
    guard.install_signal_handlers()
    exit_code = 1
    shim = None
    result = {}
    try:
        if creds:
            browser_user.create(creds)
            print(f"[e2e] brauzer foydalanuvchisi: {creds['email']}")
        guard.install()
        shim = Shim(guard, site, sites_path)
        shim.ops.update(fixtures.OPS)
        print("[e2e] fikstura:", json.dumps(shim.control("setup_base", {})))
        if selftest:
            from ozturkapp.ozturkapp.tests.e2e import selftest as selftest_module

            result = selftest_module.run(shim, hold=hold)
            exit_code = 0 if result.get("ok") else 1
        else:
            result = _run_browser(shim, creds, scenarios, headed)
            exit_code = 0 if result.get("ok") else 1
    finally:
        problems = _finish(guard, shim, creds, before, noise, keep_shots)
        if problems:
            exit_code = 1
    _summarize(shim, guard, result, problems)
    if own_init:
        frappe.destroy()
        sys.exit(exit_code)
    return exit_code


def _run_browser(shim, creds, scenarios, headed):
    report_path = os.path.join(WORK_DIR, "report.json")
    if os.path.exists(report_path):
        os.remove(report_path)
    env = dict(
        os.environ,
        E2E_SHIM_PORT=str(SHIM_PORT),
        E2E_CHROME_PORT=str(CHROME_PORT),
        E2E_USER=creds["email"],
        E2E_PASSWORD=creds["password"],
        E2E_WORK=WORK_DIR,
        E2E_SCENARIOS=scenarios or "",
        E2E_HEADED="1" if headed else "",
        E2E_REPORT=report_path,
    )
    server = make_server(shim, SHIM_PORT)
    server.timeout = 0.25
    child = subprocess.Popen(["node", os.path.join(HERE, "driver", "run.js")], env=env, cwd=HERE)
    try:
        while child.poll() is None:
            server.handle_request()
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(10)
            except subprocess.TimeoutExpired:
                child.kill()
        server.server_close()

    report = {}
    if os.path.exists(report_path):
        with open(report_path) as handle:
            report = json.load(handle)
    report["ok"] = child.returncode == 0 and not shim.violations
    return report


def _finish(guard, shim, creds, before, noise, keep_shots):
    """ROLLBACK, kesh, foydalanuvchini o'chirish va barmoq iz taqqoslash."""
    problems = []
    try:
        guard.rollback_all()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"ROLLBACK xatosi: {exc}")
    try:
        frappe.clear_cache()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"kesh tozalanmadi: {exc}")
    guard.uninstall()

    if creds:
        removed = browser_user.cleanup(creds["email"])
        print(f"[e2e] brauzer foydalanuvchisi o'chirildi: {removed}")
        left = browser_user.leftovers(creds["email"])
        if left:
            problems.append(f"foydalanuvchi qoldig'i: {left}")

    # Haydovchi yiqilgan bo'lsa Chrome yetim qolmasin.
    subprocess.run(["pkill", "-f", "--", f"--remote-debugging-port={CHROME_PORT}"], check=False)
    time.sleep(0.5)
    # Chrome profili HAR DOIM o'chiriladi; xato skrinshotlari faqat `--keep-shots` bilan qoladi.
    shutil.rmtree(os.path.join(WORK_DIR, "profile"), ignore_errors=True)
    if not keep_shots:
        shutil.rmtree(WORK_DIR, ignore_errors=True)

    after = fingerprint()
    changed = diff_fingerprints(before, after, ignore=noise)
    if changed:
        detail = {t: _row_level_changes(before["rows"][t], after["rows"][t])
                  for t in changed if t in before.get("rows", {})}
        problems.append(f"BAZA O'ZGARGAN jadvallar: {changed} {detail or ''}")
    if before["counts"] != after["counts"]:
        problems.append(f"sanoqlar farq qiladi: {before['counts']} != {after['counts']}")
    if before["series"] != after["series"]:
        problems.append("tabSeries o'zgargan")
    if before["sequences"] != after["sequences"]:
        problems.append(f"SEQUENCE qiymatlari o'zgargan: {before['sequences']} -> {after['sequences']}")
    guard.final = {"before": before["counts"], "after": after["counts"], "changed": changed,
                   "tables": len(after["checksums"]), "sequences": len(after["sequences"])}
    return problems


def _summarize(shim, guard, result, problems):
    if shim is not None:
        print(f"\n[e2e] so'rovlar: {len(shim.log)}, virtual commit: {guard.commits}, "
              f"savepoint rollback: {guard.rollbacks}")
        print(f"[e2e] fon ishlari: {len(guard.enqueued)} ta navbatga qo'yildi; ozturkapp.* bajarildi, "
              f"BAJARILMADI: {sorted(set(guard.skipped_jobs))}")
        if guard.job_errors:
            _print_table("FON ISHI XATOLARI", guard.job_errors, ["method", "error"])
        bad = [r for r in shim.log if r["status"] >= 400]
        _print_table("Non-2xx javoblar", bad, ["n", "step", "actor", "http", "method", "status", "exc_type", "msg"])
        if shim.violations:
            _print_table("HIMOYA BUZILISHLARI", shim.violations, ["step", "method", "error"])
    final = getattr(guard, "final", None)
    if final:
        print("\n=== Qoldiq yozuvlar (oldin -> keyin) ===")
        for table in final["before"]:
            mark = "" if final["before"][table] == final["after"].get(table) else "  <== FARQ"
            print(f"  {table:24s} {final['before'][table]:>8} -> {final['after'].get(table)}{mark}")
        print(f"  o'zgargan jadvallar (fon shovqinsiz): {final['changed'] or 'YO`Q'}")
        print(f"  butun baza: {final['tables']} jadval CHECKSUM, tabSeries mazmuni, {final['sequences']} ta SEQUENCE solishtirildi")
    if problems:
        print("\n[e2e] MUAMMOLAR:")
        for problem in problems:
            print("  -", problem)
    print("\n[e2e] NATIJA:", "OK" if result.get("ok") and not problems else "XATO")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", default="", help="vergul bilan: a,b,c ... (bo'sh = hammasi)")
    parser.add_argument("--selftest", action="store_true", help="brauzersiz: faqat shim va backend")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--hold", type=int, default=0, help="--selftest: ochiq tranzaksiyani shuncha soniya ushlab turish")
    parser.add_argument("--cleanup-stale", action="store_true",
                        help="SIGKILL dan qolgan e2e-browser-tmp-* foydalanuvchilarini o'chirish")
    parser.add_argument("--keep-shots", action="store_true", help="xato skrinshotlarini /tmp/e2e_run/shots da qoldirish")
    options = parser.parse_args()
    if options.cleanup_stale:
        os.chdir(SITES_PATH)
        frappe.init(site=SITE, sites_path=SITES_PATH)
        frappe.connect()
        for (email,) in frappe.db.sql("select name from `tabUser` where name like %s", (browser_user.PREFIX + "-%",)):
            print(email, browser_user.cleanup(email), browser_user.leftovers(email))
        frappe.destroy()
        sys.exit(0)
    main(scenarios=options.scenarios, selftest=options.selftest, headed=options.headed, keep_shots=options.keep_shots, hold=options.hold)
