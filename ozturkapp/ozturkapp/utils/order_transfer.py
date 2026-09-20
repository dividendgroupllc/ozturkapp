# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Buyurtmani boshqa stolga ko'chirish va stollarni birlashtirish/ajratish.

NEGA URY'NING `table_transfer` / `merge_tables_batch` I CHAQIRILMAYDI
=====================================================================
Ikkalasi ham so'rov o'rtasida `frappe.db.commit()` qiladi (birlashtirish) yoki
xatoni jimgina yutadi (`change_table_in_kot` — `except: pass`). Kassa amali esa
"yo'q yoki hammasi" bo'lishi shart: ko'chirish yarim bajarilib, chek yangi
stolda, oshxona chiptasi esa eskisida qolsa oshpaz taomni noto'g'ri stolga
olib chiqadi. Shuning uchun bu yerda URY'ning MODELI (stol bandligi,
`merged_with`, `custom_merged_tables`) saqlanadi, lekin yozish bitta
savepoint ichida bajariladi.

URY MODELI (o'zgartirilmaydi)
=============================
    URY Table.occupied / latest_invoice_time   stol band
    URY Table.merged_with                      klaster a'zolari (CSV, simmetrik)
    POS Invoice.restaurant_table               asosiy stol
    POS Invoice.custom_merged_tables           asosiydan boshqa a'zolar (CSV)
    POS Invoice.custom_restaurant_room         asosiy stolning zali
    URY KOT.restaurant_table                   oshxona ekrani va chiptalar

QOIDALAR
========
    * Faqat qoralama (`docstatus = 0`), bekor qilinmagan chek. Hisob
      chiqarilgan (`invoice_printed = 1`) chek ham ko'chiriladi, lekin
      javobda `billed = True` qaytadi — kassir chekni qayta chiqarishi
      kerakligini bilsin.
    * Nishon stol: shu filialda, BO'SH (ochiq cheki yo'q) va birlashtirilmagan.
      Holat `URY Table.occupied` bayrog'idan EMAS, ochiq cheklardan
      keltiriladi (`table_status`) — kassa ekrani ham shunday ko'rsatadi.
    * BRON: nishon stolda bugungi faol bron bo'lsa, u faqat SHU MEHMONGA
      tegishli bo'lgandagina ruxsat etiladi (bron mijozi chekdagi mijoz bilan
      bir xil, yoki mijoz nomi bir xil). Ruxsat etilsa bron `Seated` bo'ladi
      va chekka bog'lanadi. Boshqa mehmonning broni ustiga o'tirg'izib
      bo'lmaydi — avval bronni bekor qiling.
    * Birlashtirilgan stolni ko'chirib bo'lmaydi (URY qoidasi) — avval ajrating.
    * Birlashtirish faqat BIR ZALDAGI stollar orasida (URY qoidasi).

ATOMIYLIK
=========
Chek va stol qatorlari `FOR UPDATE` bilan qulflanadi, barcha tekshiruv
qulfdan KEYIN qayta o'qilgan holatga qarab bajariladi, yozuvlar esa bitta
savepoint ichida — istalgan xatoda hammasi orqaga qaytadi. Realtime xabarlar
`after_commit` bilan ketadi, ya'ni orqaga qaytgan amal haqida xabar chiqmaydi.
"""

import json
from contextlib import contextmanager

import frappe
from frappe import _
from frappe.utils import cint

from ozturkapp.ozturkapp.utils import cashier_permissions, order_cancel, table_status
from ozturkapp.ozturkapp.utils.cashier_realtime import emit_floor_change, emit_order_change
from ozturkapp.ozturkapp.utils.kitchen_realtime import emit_kot_change

#: Oshxona ekranida hali kerak bo'lmagan (yakunlangan) chiptalar — ular
#: eski stol nomi bilan qolaveradi.
FINISHED_KOT_STATUSES = ("Served", "Cancelled")

#: Bir chaqiruvda qo'shiladigan eng ko'p stol (qulflanadigan qatorlar chegarasi).
MAX_MERGE_TABLES = 20

REASON_TRANSFERRED = "TABLE_TRANSFERRED"
REASON_MERGED = "TABLES_MERGED"
REASON_UNMERGED = "TABLES_UNMERGED"


# ═══════════════════════════════════════════════════════════════════
#  Ko'chirish
# ═══════════════════════════════════════════════════════════════════

def transfer(invoice: str, to_table: str, scope) -> dict:
    """Buyurtmani BO'SH stolga ko'chiradi.

    Returns:
        dict: invoice, from_table, to_table, room, billed, freed_tables,
              kots_updated, reservation
    """
    to_table = _table_name(to_table)

    with _atomic("order_transfer", invoice):
        order = _lock_order(invoice)
        primary = order.restaurant_table

        if not primary:
            frappe.throw(
                _("Bu buyurtmada stol yo'q (olib ketish / yetkazib berish) — ko'chirib bo'lmaydi."),
                title=_("Stol yo'q"),
            )
        if to_table == primary:
            frappe.throw(
                _("Buyurtma allaqachon {0} stolida.").format(to_table),
                title=_("Ko'chirish kerak emas"),
            )

        clusters = _clusters(order.branch)
        if len(clusters.get(primary, [primary])) > 1 or order.custom_merged_tables:
            frappe.throw(
                _(
                    "Birlashtirilgan stollarni ko'chirib bo'lmaydi — avval "
                    "stollarni ajrating."
                ),
                title=_("Stollar birlashtirilgan"),
            )

        _lock_tables([primary, to_table])
        target = cashier_permissions.assert_table_in_scope(to_table, scope)
        if order.branch and target.branch != order.branch:
            raise cashier_permissions.CashierPermissionError(
                _("Stollar orasida ko'chirish faqat bitta filial ichida mumkin")
            )

        busy = _busy_tables(order.branch, [to_table])
        _release_if_stale(to_table, clusters, busy)
        reservation = _assert_available([to_table], order, busy)

        # ── Yozish ────────────────────────────────────────────────────
        frappe.db.set_value(
            "POS Invoice",
            order.name,
            {"restaurant_table": to_table, "custom_restaurant_room": target.restaurant_room},
        )
        kots = _move_open_kots(order.name, to_table)
        _occupy([to_table], order.creation)
        freed = order_cancel.free_empty_tables(order.branch, [primary])
        _mark_seated(reservation, order.name)

    frappe.logger("ozturk_cashier").info(
        "Stol ko'chirildi: %s | %s -> %s | kassir=%s | hisob_chiqarilgan=%s",
        order.name, primary, to_table, frappe.session.user, bool(order.invoice_printed),
    )

    emit_floor_change(order.branch, [primary, to_table], REASON_TRANSFERRED, order.name)
    emit_order_change(order.branch, order.name, REASON_TRANSFERRED, to_table)
    _emit_kots(order.branch, kots, order.name)

    return {
        "invoice": order.name,
        "from_table": primary,
        "to_table": to_table,
        "room": target.restaurant_room,
        "billed": bool(order.invoice_printed),
        "freed_tables": freed,
        "kots_updated": len(kots),
        "reservation": reservation.name if reservation else None,
    }


# ═══════════════════════════════════════════════════════════════════
#  Birlashtirish
# ═══════════════════════════════════════════════════════════════════

def merge(invoice: str, tables, scope) -> dict:
    """Buyurtma stoliga yana bir necha BO'SH stolni qo'shadi (bitta hisob).

    Returns:
        dict: invoice, table, merged_tables, cluster, billed, reservations
    """
    targets = _parse_tables(tables)

    with _atomic("order_merge", invoice):
        order = _lock_order(invoice)
        primary = order.restaurant_table

        if not primary:
            frappe.throw(
                _("Bu buyurtmada stol yo'q (olib ketish / yetkazib berish) — stol qo'shib bo'lmaydi."),
                title=_("Stol yo'q"),
            )

        targets = [table for table in targets if table != primary]
        if not targets:
            frappe.throw(_("Birlashtirish uchun kamida bitta boshqa stolni tanlang."))

        _lock_tables([primary, *targets])
        clusters = _clusters(order.branch)
        cluster = _cluster_of(order, clusters)
        room = frappe.db.get_value("URY Table", primary, "restaurant_room")

        for table in targets:
            row = cashier_permissions.assert_table_in_scope(table, scope)
            if row.restaurant_room != room:
                frappe.throw(
                    _("{0} stoli boshqa zalda — faqat bir zaldagi stollarni birlashtirish mumkin.").format(
                        table
                    ),
                    title=_("Zal boshqa"),
                )

        busy = _busy_tables(order.branch, targets)
        for table in targets:
            _release_if_stale(table, clusters, busy)

        reservations = _assert_available(targets, order, busy, many=True)

        new_cluster = sorted({*cluster, *targets})
        _write_cluster(new_cluster, primary, order.creation)
        for reservation in reservations:
            _mark_seated(reservation, order.name)

    frappe.logger("ozturk_cashier").info(
        "Stollar birlashtirildi: %s | %s | kassir=%s",
        order.name, ", ".join(new_cluster), frappe.session.user,
    )

    emit_floor_change(order.branch, new_cluster, REASON_MERGED, order.name)
    emit_order_change(order.branch, order.name, REASON_MERGED, primary)

    return {
        "invoice": order.name,
        "table": primary,
        "merged_tables": [table for table in new_cluster if table != primary],
        "cluster": new_cluster,
        "billed": bool(order.invoice_printed),
        "reservations": [reservation.name for reservation in reservations],
    }


# ═══════════════════════════════════════════════════════════════════
#  Ajratish
# ═══════════════════════════════════════════════════════════════════

def unmerge(invoice: str, table: str, scope) -> dict:
    """Birlashtirilgan stollardan BIRINI ajratib, bo'shatadi.

    Asosiy stolni (chek turgan stolni) ajratib bo'lmaydi — chek uni band
    qilib turibdi; buni ko'chirish bilan hal qiladi.

    Returns:
        dict: invoice, table, released_table, merged_tables, cluster, billed
    """
    table = _table_name(table)

    with _atomic("order_unmerge", invoice):
        order = _lock_order(invoice)
        primary = order.restaurant_table

        if not primary:
            frappe.throw(
                _("Bu buyurtmada stol yo'q — ajratadigan stol ham yo'q."),
                title=_("Stol yo'q"),
            )

        cashier_permissions.assert_table_in_scope(table, scope)
        if table == primary:
            frappe.throw(
                _(
                    "{0} — buyurtmaning asosiy stoli, uni ajratib bo'lmaydi. "
                    "Buyurtmani boshqa stolga ko'chiring."
                ).format(table),
                title=_("Asosiy stol"),
            )

        cluster = _cluster_of(order, _clusters(order.branch))

        if table not in cluster:
            frappe.throw(
                _("{0} stoli bu buyurtmaga birlashtirilmagan.").format(table),
                title=_("Stol birlashtirilmagan"),
            )

        _lock_tables(cluster)

        # Partner stolning O'Z buyurtmasi bo'lsa (URY buni birlashtirishda
        # taqiqlaydi, lekin ma'lumot buzilgan bo'lishi mumkin) — ajratish uni
        # bo'shatib yuborardi.
        for other in table_status.get_open_orders(order.branch, [table]):
            if other.name != order.name and other.restaurant_table == table:
                frappe.throw(
                    _("{0} stolida boshqa ochiq buyurtma ({1}) bor — uni ajratib bo'lmaydi.").format(
                        table, other.name
                    ),
                    title=_("Stol band"),
                )

        remaining = sorted(member for member in cluster if member != table)
        frappe.db.set_value(
            "URY Table",
            table,
            {"merged_with": None, "occupied": 0, "latest_invoice_time": None},
            update_modified=False,
        )
        _write_cluster(remaining, primary, order.creation)

    frappe.logger("ozturk_cashier").info(
        "Stol ajratildi: %s | %s | kassir=%s", order.name, table, frappe.session.user
    )

    emit_floor_change(order.branch, cluster, REASON_UNMERGED, order.name)
    emit_order_change(order.branch, order.name, REASON_UNMERGED, primary)

    return {
        "invoice": order.name,
        "table": primary,
        "released_table": table,
        "merged_tables": [member for member in remaining if member != primary],
        "cluster": remaining,
        "billed": bool(order.invoice_printed),
    }


# ═══════════════════════════════════════════════════════════════════
#  Yangi buyurtma oldidan eski birlashtirish izini tozalash
# ═══════════════════════════════════════════════════════════════════

def release_stale_cluster(table: str, branch: str):
    """Ochiq cheki YO'Q, lekin `merged_with` qolgan stolni tarqatadi.

    NEGA KERAK
    ==========
    URY yangi buyurtmada stolning `merged_with` ini o'qib sherik stollarni
    chekning `custom_merged_tables` iga qo'shadi (`_reconcile_invoice_merged_tables`).
    Eski birlashtirishdan iz qolgan bo'lsa (masalan menejer stolni qo'lda
    bo'shatgan, yoki URY POS orqali to'langan) yangi mehmon o'tirgan zahoti
    QO'SHNI stol ham "band" bo'lib qolardi va u yerga o'tirgan haqiqiy
    mehmonga stol berilmasdi.

    Klasterning birorta a'zosida ochiq chek bo'lsa — hech narsa
    o'zgartirilmaydi (jonli birlashtirishga tegilmaydi).
    """
    clusters = _clusters(branch)
    if len(clusters.get(table, [table])) < 2:
        return
    _lock_tables(clusters[table])
    busy = _busy_tables(branch)
    if any(member in busy for member in clusters[table]):
        return
    _release_if_stale(table, clusters, busy)


# ═══════════════════════════════════════════════════════════════════
#  Yordamchilar
# ═══════════════════════════════════════════════════════════════════

@contextmanager
def _atomic(name: str, invoice: str):
    """Barcha yozuvlar yoki hech biri: xatoda savepoint'ga qaytadi."""
    save_point = f"{name}_{frappe.generate_hash(length=6)}"
    frappe.db.savepoint(save_point)
    try:
        yield
    except Exception:
        frappe.db.rollback(save_point=save_point)
        frappe.logger("ozturk_cashier").warning(
            "%s bekor qilindi (orqaga qaytarildi): %s", name, invoice
        )
        raise
    else:
        frappe.db.release_savepoint(save_point)


def _lock_order(invoice: str):
    """Chekni qulflaydi va holatini QULFDAN KEYIN qayta o'qiydi.

    Mijozdan yoki oldingi o'qishdan kelgan holatga ishonmaymiz: shu
    daqiqada boshqa kassir «To'lov» yoki «Bekor qilish» bosgan bo'lishi
    mumkin (TZ §24).

    `for_update=True` — QULFLOVCHI o'qish. MariaDB'ning standart REPEATABLE
    READ darajasida `SELECT ... FOR UPDATE` dan keyingi ODDIY `SELECT` so'rov
    boshidagi eski suratni qaytaradi: boshqa kassir to'lovni commit qilib
    ulgurgan bo'lsa ham chek hali "to'lanmagan" ko'rinardi va to'langan
    chek boshqa stolga ko'chib ketardi. Qulflovchi o'qish esa eng so'nggi
    commit qilingan qiymatni beradi.
    """
    order = frappe.db.get_value(
        "POS Invoice",
        invoice,
        [
            "name",
            "docstatus",
            "custom_cancelled",
            "branch",
            "restaurant_table",
            "custom_merged_tables",
            "invoice_printed",
            "customer",
            "customer_name",
            "creation",
        ],
        as_dict=True,
        for_update=True,
    )
    if not order:
        frappe.throw(_("'{0}' cheki topilmadi").format(invoice), frappe.DoesNotExistError)

    if order.docstatus != 0:
        frappe.throw(
            _("Faqat to'lanmagan buyurtma bilan ishlash mumkin."),
            title=_("Chek holati mos emas"),
        )
    if order.custom_cancelled:
        frappe.throw(_("Bekor qilingan buyurtma bilan ishlab bo'lmaydi."))

    return order


def _lock_tables(tables: list):
    """Stol qatorlarini TARTIB bilan qulflaydi (ikki kassir ustma-ust tushsa
    deadlock bo'lmasligi uchun)."""
    names = tuple(sorted(set(tables)))
    frappe.db.sql(
        "select name from `tabURY Table` where name in %s order by name for update",
        (names,),
    )


def _clusters(branch: str) -> dict:
    rows = frappe.get_all(
        "URY Table", filters={"branch": branch}, fields=["name", "merged_with"]
    )
    return table_status.build_clusters([dict(row) for row in rows])


def _cluster_of(order, clusters: dict) -> list:
    """Chek stolining klasteri: `merged_with` bo'yicha, chekdagi CSV bilan to'ldirilgan.

    Ikkalasi ham URY'da bir xil ma'lumotni saqlaydi va odatda mos keladi;
    ular ajralib qolgan bo'lsa (masalan `merged_with` qo'lda tozalangan)
    chekda yozilgan stollar yo'qolib ketmasin.
    """
    cluster = list(clusters.get(order.restaurant_table, [order.restaurant_table]))
    for extra in table_status.parse_merged_with(order.custom_merged_tables):
        if extra not in cluster:
            cluster.append(extra)
    return cluster


def _table_name(table) -> str:
    """Stol nomi: bo'sh yoki matn bo'lmasa tushunarli xato (aks holda `None` qulflashda
    saralashda `TypeError` bilan yiqilardi)."""
    name = table.strip() if isinstance(table, str) else ""
    if not name:
        frappe.throw(_("Stolni tanlang"), title=_("Stol ko'rsatilmagan"))
    return name


def _parse_tables(tables) -> list:
    if isinstance(tables, str):
        try:
            tables = json.loads(tables)
        except ValueError:
            tables = tables.split(",")

    if not isinstance(tables, (list, tuple)):
        frappe.throw(_("Stollar ro'yxati noto'g'ri formatda"))

    names = list(dict.fromkeys(str(table).strip() for table in tables if str(table or "").strip()))
    if len(names) > MAX_MERGE_TABLES:
        frappe.throw(
            _("Bir vaqtda {0} tadan ortiq stolni birlashtirib bo'lmaydi").format(MAX_MERGE_TABLES),
            title=_("Juda ko'p stol"),
        )
    return names


def _busy_tables(branch: str, watch=()) -> dict:
    """{stol: unda turgan ochiq chek} — filialdagi barcha ochiq cheklar bo'yicha.

    `watch` — hozir qulflangan va tekshirilayotgan stollar. Ochiq cheklar
    ODDIY o'qish bilan olinadi, u esa REPEATABLE READ da so'rov boshidagi
    eski suratni ko'radi: boshqa kassir shu stolni band qilib commit
    qilgan bo'lsa ham u bo'sh ko'rinardi va ikkita chek bir stolga tushardi.
    Qulflangan stol qatorining `occupied` bayrog'i esa (qulflovchi o'qish)
    ENG SO'NGGI qiymatni beradi: bayroq band desa-yu, ro'yxatda chek
    ko'rinmasa — chek qulflovchi o'qish bilan qayta izlanadi.
    """
    busy = {}
    for order in table_status.get_open_orders(branch):
        for table in table_status.tables_of(order):
            busy.setdefault(table, order.name)

    unseen = [
        table
        for table in dict.fromkeys(watch)
        if table not in busy
        and cint(frappe.db.get_value("URY Table", table, "occupied", for_update=True))
    ]
    if unseen:
        for table, invoice in _owners_now(unseen).items():
            busy.setdefault(table, invoice)
    return busy


def _owners_now(tables: list) -> dict:
    """Stollarni hozir band qilib turgan ochiq cheklar — QULFLOVCHI o'qish bilan.

    Faqat yuqoridagi shubhali holatda chaqiriladi (bayroq band, chek ko'rinmaydi):
    odatda bu eskirgan bayroq, kamdan-kam hollarda esa boshqa kassirning
    hozirgina commit qilgan buyurtmasi.
    """
    cancelled = (
        " and IFNULL(custom_cancelled, 0) = 0"
        if frappe.db.has_column("POS Invoice", "custom_cancelled")
        else ""
    )
    likes = " or ".join(["custom_merged_tables like %s"] * len(tables))
    rows = frappe.db.sql(
        f"""
        select name, restaurant_table, custom_merged_tables from `tabPOS Invoice`
        where docstatus = 0{cancelled}
          and (restaurant_table in %s or {likes})
        order by creation asc
        for update
        """,
        (tuple(tables), *[f"%{table}%" for table in tables]),
        as_dict=True,
    )

    owners = {}
    for row in rows:
        for table in table_status.tables_of(row):
            if table in tables:
                owners.setdefault(table, row.name)
    return owners


def claim_table(table: str, branch: str):
    """Yangi buyurtma oldidan stolni qulflaydi va HAQIQATAN bo'shligini tasdiqlaydi.

    `create_order` va ofitsant `submit_order` ishlatadi: ikki kassir (yoki
    kassir va ofitsant) bir bo'sh stolga bir vaqtda buyurtma ochsa, ikkinchisi
    birinchisining commit qilingan chekini ko'radi.
    """
    _lock_tables([table])
    owner = _busy_tables(branch, [table]).get(table)
    if owner:
        frappe.throw(
            _("{0} stolida ochiq buyurtma bor ({1}) — unga taom qo'shing.").format(table, owner),
            title=_("Stol band"),
        )


def _release_if_stale(table: str, clusters: dict, busy: dict):
    """Nishon stol birlashtirilgan bo'lsa: band bo'lsa rad etadi, EGASIZ bo'lsa tarqatadi.

    Klasterning birorta a'zosida ochiq chek bo'lmasa, `merged_with` — eski
    to'lovdan qolgan iz (URY POS bilan birlashtirilib, kassadan to'langan
    buyurtmalarda shunday bo'lgan). Uni tarqatmasak stol abadiy "birlashtirilgan"
    qolib, unga ko'chirib ham, birlashtirib ham bo'lmasdi.
    """
    cluster = clusters.get(table, [table])
    if len(cluster) == 1:
        return

    if any(member in busy for member in cluster):
        frappe.throw(
            _("{0} stoli boshqa stollar bilan birlashtirilgan va band.").format(table),
            title=_("Stol band"),
        )

    for member in cluster:
        frappe.db.set_value("URY Table", member, "merged_with", None, update_modified=False)
        clusters[member] = [member]


def _assert_available(tables: list, order, busy: dict, many: bool = False):
    """Nishon stol(lar) bo'shligini va broni yo'qligini (yoki shu mehmonniki
    ekanini) tasdiqlaydi. Mos bronlarni qaytaradi."""
    taken = {
        table: invoice
        for table, invoice in busy.items()
        if table in tables and invoice != order.name
    }
    if taken:
        frappe.throw(
            _("{0} stoli band — unda ochiq buyurtma bor ({1}).").format(
                ", ".join(sorted(taken)), ", ".join(sorted(set(taken.values())))
            ),
            title=_("Stol band"),
        )

    reservations = table_status.get_reservation_provider()(order.branch, tables)
    matched = []
    for table in tables:
        reservation = reservations.get(table)
        if not reservation:
            continue
        if not _reservation_is_for(reservation, order):
            frappe.throw(
                _(
                    "{0} stoli {1} uchun bron qilingan. Boshqa mehmonni o'tirg'izish "
                    "uchun avval bronni bekor qiling."
                ).format(table, reservation.customer_name or reservation.customer or ""),
                title=_("Stol bron qilingan"),
            )
        matched.append(reservation)

    return matched if many else (matched[0] if matched else None)


def _reservation_is_for(reservation, order) -> bool:
    """Bron shu chekning mehmoniga tegishlimi.

    Bron mijozi chekdagi mijoz bilan bir xil bo'lsa, yoki (bron faqat ism bilan
    olingan bo'lsa) ism harf registrisiz mos kelsa.
    """
    if reservation.customer and reservation.customer == order.customer:
        return True

    booked = (reservation.customer_name or "").strip().casefold()
    seated = (order.customer_name or "").strip().casefold()
    return bool(booked) and booked == seated


def _mark_seated(reservation, invoice: str):
    """Mehmon o'tirdi — bron yopiladi va chekka bog'lanadi."""
    if not reservation:
        return
    frappe.db.set_value(
        "URY Table Reservation",
        reservation.name,
        {"status": "Seated", "seated_invoice": invoice},
        update_modified=False,
    )


def _occupy(tables: list, creation):
    for table in tables:
        frappe.db.set_value(
            "URY Table",
            table,
            {"occupied": 1, "latest_invoice_time": creation},
            update_modified=False,
        )


def _write_cluster(cluster: list, primary: str, creation):
    """Klasterni URY modelida yozadi: `merged_with` (simmetrik), bandlik,
    va asosiy stoldagi barcha ochiq cheklarning `custom_merged_tables`."""
    cluster = sorted(cluster)
    partners = [member for member in cluster if member != primary]

    for member in cluster:
        others = [name for name in cluster if name != member]
        frappe.db.set_value(
            "URY Table",
            member,
            {
                "merged_with": ",".join(others) or None,
                "occupied": 1,
                "latest_invoice_time": creation,
            },
            update_modified=False,
        )

    # Hisob bo'lingan bo'lsa bitta stolda bir nechta ochiq chek bo'ladi —
    # ularning hammasi bir xil klasterni ko'rsatishi kerak.
    filters = {"docstatus": 0, "restaurant_table": primary}
    if frappe.db.has_column("POS Invoice", "custom_cancelled"):
        filters["custom_cancelled"] = 0

    for name in frappe.get_all("POS Invoice", filters=filters, pluck="name"):
        frappe.db.set_value(
            "POS Invoice",
            name,
            "custom_merged_tables",
            ",".join(partners) or None,
            update_modified=False,
        )


def _move_open_kots(invoice: str, table: str) -> list:
    """Chekning tugallanmagan KOT'larini yangi stolga o'tkazadi.

    Oshxona ekrani stol nomini `URY KOT.restaurant_table` dan oladi, chiptalar
    ham shundan (`utils/print_queue.py`). URY'ning o'z `change_table_in_kot()`
    faqat `verified = 0` va `Ready For Prepare` holatidagilarni yangilaydi va
    xatoni yutib yuboradi; biz esa tugallanmagan HAR QANDAY chiptani
    (bekor qilish/«to'xtat» kartalari ham) yangilaymiz — aks holda oshpaz
    to'xtatish haqidagi xabarni eski stol nomi bilan ko'rardi.

    Returns:
        list: `[{"kot", "production"}]` — realtime uchun.
    """
    if not frappe.db.exists("DocType", "URY KOT"):
        return []

    rows = frappe.get_all(
        "URY KOT",
        filters={
            "invoice": invoice,
            "docstatus": 1,
            "order_status": ["not in", list(FINISHED_KOT_STATUSES)],
        },
        fields=["name", "production"],
    )
    for row in rows:
        frappe.db.set_value(
            "URY KOT", row.name, "restaurant_table", table, update_modified=False
        )

    return [{"kot": row.name, "production": row.production} for row in rows]


def _emit_kots(branch: str, kots: list, invoice: str):
    """Oshxona ekrani (ozturkapp + Mosaic) yangilansin — eski stol nomi qolmasin."""
    for row in kots:
        emit_kot_change(branch, row["kot"], "KOT_TABLE_CHANGED", row["production"], invoice)
