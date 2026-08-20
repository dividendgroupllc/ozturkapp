# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Buyurtmani bekor qilish — YAGONA qoida va YAGONA yozuv nuqtasi.

QOIDA
=====
Ofitsant xato zakaz olib qo'ysa, uni kassir bekor qiladi. Lekin faqat
oshxona ishga kirishmagan bo'lsa::

    Oshxona hali BOSHLAMAGAN   ->  har qanday kassir bekor qiladi
    Oshxona BOSHLAB YUBORGAN   ->  faqat menejer bekor qila oladi

"Boshlangan" degan fakt shu yerda HISOBLANMAYDI — u
`kitchen_status.get_order_progress()` dan olinadi. Ya'ni oshxona holati
bo'yicha tizimda bitta haqiqat manbai bor.

HUJJAT O'CHIRILMAYDI
====================
`docstatus` 0 bo'lib qolaveradi, `custom_cancelled = 1` qo'yiladi, sabab
`cancel_reason` ga yoziladi. Chek KPI va tekshiruv uchun bazada qoladi.

DIQQAT — DRAFT SANOQLARI
========================
`custom_cancelled = 1` bo'lgan chek "to'lanmagan buyurtma" sanoqlaridan
CHIQARILISHI shart. Aks holda kassa smenasi yopilmay qoladi: sanoq
"2 ta to'lanmagan buyurtma bor" deydi, kassirning ro'yxati esa bo'sh
bo'ladi va u nima qilishni bilmaydi. Filtr `custom_cancelled = 0`
`table_status.get_open_orders()`, `desktop_pos._pending_filters()` va
`api/cashier.py` dagi smena sanoqlarida bir xil qo'llanadi.
"""

import frappe
from frappe import _
from frappe.utils import cint

from ozturkapp.ozturkapp.utils import cashier_permissions, kitchen_status, table_status
from ozturkapp.ozturkapp.utils.cashier_realtime import emit_floor_change, emit_order_change
from ozturkapp.ozturkapp.utils.kitchen_realtime import emit_kot_change

#: Sabab shundan qisqa bo'lsa qabul qilinmaydi — "." yoki "a" audit uchun
#: hech narsa bermaydi.
MIN_REASON_LENGTH = 3

#: Realtime va log'da ko'rinadigan sabab kodi.
REASON_CODE = "ORDER_CANCELLED"


# ═══════════════════════════════════════════════════════════════════
#  Qoida — o'qish (UI shu javobga qarab tugmani chizadi)
# ═══════════════════════════════════════════════════════════════════

def describe(doc, progress=None) -> dict:
    """Bu chekni HOZIR kim bekor qila oladi?

    Kassa oynasi tugmani shu javobga qarab chizadi, server esa
    `assert_can_cancel()` da AYNAN shu javobni qayta qo'llaydi — ya'ni
    frontend'ni chetlab o'tish hech narsa bermaydi (TZ §17).

    Args:
        doc: `POS Invoice` hujjati yoki `name`, `docstatus`,
             `custom_cancelled` maydonlari bo'lgan qator.
        progress: `kitchen_status.get_order_progress()` natijasi. Berilmasa
             o'zi so'raydi. Chek paneli uni allaqachon hisoblagan bo'ladi —
             ikkinchi marta so'ramaslik uchun uzatiladi.

    Returns:
        dict: allowed, requires_supervisor, kitchen_started,
              blocked_reason, warning
    """
    docstatus = cint(doc.get("docstatus"))

    if docstatus == 1:
        return _blocked(_("To'langan buyurtmani bekor qilib bo'lmaydi"))
    if docstatus == 2 or cint(doc.get("custom_cancelled")):
        return _blocked(_("Buyurtma allaqachon bekor qilingan"))

    if progress is None:
        progress = kitchen_status.get_order_progress(doc.get("name"))

    if not progress.get("started"):
        # Oshxona hali qo'l urmagan — bu ofitsantning xatosini tuzatish,
        # menejerni kutib o'tirishning hojati yo'q.
        return {
            "allowed": True,
            "requires_supervisor": False,
            "kitchen_started": False,
            "blocked_reason": "",
            "warning": "",
        }

    items = _started_labels(progress)

    if cashier_permissions.has_supervisor_role():
        return {
            "allowed": True,
            "requires_supervisor": True,
            "kitchen_started": True,
            "blocked_reason": "",
            "warning": _(
                "Oshxona ishni boshlab yuborgan ({0}). Bekor qilinsa taom "
                "chiqindi bo'ladi — bu amal menejer nomiga yoziladi."
            ).format(items),
        }

    return {
        "allowed": False,
        "requires_supervisor": True,
        "kitchen_started": True,
        "blocked_reason": _(
            "Oshxona ishni boshlab yuborgan ({0}) — bu buyurtmani faqat "
            "menejer bekor qila oladi."
        ).format(items),
        "warning": "",
    }


def assert_can_cancel(doc, progress=None) -> dict:
    """Qoidani majburlaydi. Ruxsat bo'lmasa tushunarli xato beradi."""
    state = describe(doc, progress)

    if not state["allowed"]:
        frappe.throw(
            state["blocked_reason"],
            title=_("Menejer huquqi kerak")
            if state["requires_supervisor"]
            else _("Bekor qilib bo'lmaydi"),
        )

    return state


def _blocked(reason: str) -> dict:
    return {
        "allowed": False,
        "requires_supervisor": False,
        "kitchen_started": False,
        "blocked_reason": reason,
        "warning": "",
    }


def _started_labels(progress) -> str:
    """«KUŞBAŞI PIDE — Tayyorlanmoqda, LAHMACUN — Tayyor» ko'rinishida."""
    rows = progress.get("started_items") or []
    shown = ", ".join(f"{row['item']} — {row['label']}" for row in rows[:3])

    if len(rows) > 3:
        shown += _(" va yana {0} ta").format(len(rows) - 3)

    return shown


# ═══════════════════════════════════════════════════════════════════
#  Yozish
# ═══════════════════════════════════════════════════════════════════

def cancel_invoice(row, reason, scope=None) -> dict:
    """Chekni bekor qiladi: bayroq + oshxona chiptasi + stol + realtime.

    Args:
        row: `cashier_permissions.assert_invoice_in_scope()` qaytargan qator.
        reason: kassir yozgan sabab (majburiy).
        scope: `resolve_scope()` natijasi — filialni aniqlash uchun.

    Returns:
        dict: invoice, cancelled_items, freed_tables, kitchen_started
    """
    reason = (reason or "").strip()
    if len(reason) < MIN_REASON_LENGTH:
        frappe.throw(
            _("Bekor qilish sababi yozilishi shart — u hisobotga tushadi."),
            title=_("Sabab ko'rsatilmagan"),
        )

    invoice = row.name

    # ── Atomiylik ─────────────────────────────────────────────────────
    # Shu daqiqada boshqa kassir «To'lov» tugmasini bosgan bo'lishi mumkin.
    # Qatorni qulflaymiz va holatni QULFDAN KEYIN qayta o'qiymiz — mijozdan
    # kelgan holatga ham, yuqorida o'qilgan `row` ga ham ishonmaymiz.
    frappe.db.sql(
        "select name from `tabPOS Invoice` where name = %s for update", invoice
    )

    fresh = frappe.db.get_value(
        "POS Invoice",
        invoice,
        [
            "docstatus",
            "custom_cancelled",
            "branch",
            "restaurant_table",
            "custom_merged_tables",
        ],
        as_dict=True,
    ) or frappe._dict()
    fresh.name = invoice

    progress = kitchen_status.get_order_progress(invoice)
    state = assert_can_cancel(fresh, progress)

    branch = fresh.branch or (scope or {}).get("branch")
    tables = _tables_of(fresh)

    values = {
        "custom_cancelled": 1,
        "cancel_reason": reason,
        # Maydon Data — unga o'qiladigan ism yoziladi (mavjud ma'lumot
        # ham shunday). Aniq foydalanuvchi `modified_by` da qoladi:
        # URY'ning "Cancelled Invoices" hisoboti AYNAN undan o'qiydi.
        "custom_cancel_by": _user_label(frappe.session.user),
    }

    # ── STOL BOG'LAMI UZILADI ─────────────────────────────────────────
    # Bekor qilingan chek `docstatus = 0` bo'lib qoladi, URY esa stoldagi
    # "faol buyurtma"ni AYNAN shu bog'lam bo'yicha qidiradi va bizning
    # `custom_cancelled` bayrog'imizni bilmaydi:
    #
    #     ury_order.get_order_invoice():
    #         filters    {docstatus: 0, invoice_printed: 0}
    #         or_filters {restaurant_table: <stol>,
    #                     custom_merged_tables: like %<stol>%}
    #
    # Natijada o'sha stolga keyingi zakaz olinganda URY bekor qilingan
    # chekni topib «Table-1 is already occupied» deb rad etardi
    # (`ury_order.py:840`) — stol `URY Table.occupied = 0` bo'lsa ham.
    #
    # Qaysi stol bo'lgani audit uchun alohida maydonda saqlanadi.
    if tables and frappe.db.has_column("POS Invoice", "custom_cancelled_table"):
        values["custom_cancelled_table"] = ", ".join(tables)
        values["restaurant_table"] = None
        values["custom_merged_tables"] = None

    frappe.db.set_value("POS Invoice", invoice, values, update_modified=True)

    cancelled_items = _close_kitchen_tickets(invoice, branch)
    freed = _free_empty_tables(branch, tables)

    frappe.logger("ozturk_cashier").info(
        "Buyurtma bekor qilindi: %s | kassir=%s | menejer_huquqi=%s | "
        "oshxona_boshlagan=%s | taom=%s | stol=%s | sabab=%s",
        invoice,
        frappe.session.user,
        state["requires_supervisor"],
        state["kitchen_started"],
        cancelled_items,
        ",".join(freed) or "-",
        reason,
    )

    emit_order_change(branch, invoice, REASON_CODE, fresh.restaurant_table)
    if freed:
        emit_floor_change(branch, freed, REASON_CODE, invoice)

    return {
        "invoice": invoice,
        "cancelled": True,
        "kitchen_started": state["kitchen_started"],
        "cancelled_items": cancelled_items,
        "freed_tables": freed,
        "reason": reason,
    }


# ═══════════════════════════════════════════════════════════════════
#  Oshxona chiptasi
# ═══════════════════════════════════════════════════════════════════

def _close_kitchen_tickets(invoice: str, branch: str) -> int:
    """Bekor qilingan chekning KOT chiptalarini yopadi.

    Busiz oshpaz allaqachon bekor qilingan buyurtmani tayyorlab yuboradi:
    chipta ekranda «Kutilmoqda» bo'lib turaveradi, chunki oshxona ekrani
    POS Invoice'ni umuman o'qimaydi (`api/kitchen.get_active_kots`).

    BERILGAN (`Served`) taomga TEGILMAYDI — u jismonan chiqib bo'lgan,
    uni "bekor qilingan" deb yozish oshxona hisobotini soxtalashtiradi.
    """
    if not frappe.db.exists("DocType", "URY KOT"):
        return 0

    rows = frappe.db.sql(
        """
        SELECT ki.name AS kot_item, ki.parent AS kot,
               ki.custom_kitchen_status AS status, k.production
        FROM `tabURY KOT Items` ki
        INNER JOIN `tabURY KOT` k ON k.name = ki.parent
        WHERE k.invoice = %(invoice)s AND k.docstatus = 1
          AND k.type IN %(types)s
        """,
        {"invoice": invoice, "types": kitchen_status.COOKING_KOT_TYPES},
        as_dict=True,
    )

    touched, count = {}, 0

    for row in rows:
        status = kitchen_status.normalize(row.status)
        if status in (kitchen_status.SERVED, kitchen_status.CANCELLED):
            continue

        # DIQQAT: `kitchen_status.assert_transition()` bu yerda ATAYLAB
        # chetlab o'tiladi. U OSHPAZ uchun yozilgan qoida
        # (Tayyorlanmoqda -> Bekor MUMKIN EMAS), bu yerda esa menejer
        # butun BUYURTMANI bekor qilyapti — ruxsat yuqorida,
        # `assert_can_cancel()` da tekshirilgan.
        frappe.db.set_value(
            "URY KOT Items",
            row.kot_item,
            {
                "custom_kitchen_status": kitchen_status.CANCELLED,
                "custom_status_changed_by": frappe.session.user,
            },
            update_modified=False,
        )
        count += 1
        touched[row.kot] = row.production

    for kot, production in touched.items():
        _sync_cancelled_kot(kot)
        emit_kot_change(branch, kot, REASON_CODE, production, invoice)

    return count


def apply_item_cancellation(cancel_kot, repair: bool = False) -> bool:
    """URY bekor-KOT yaratganda ASL chiptadagi qatorni ham yopadi.

    NEGA BU KERAK
    =============
    Ofitsant zakazdan bitta taomni olib tashlaganda URY faqat YANGI
    hujjat yaratadi (`ury_kot_generate.create_cancel_kot_doc`) — asl
    KOT qatoriga UMUMAN TEGMAYDI. Natijada oshpaz bitta taomni IKKI
    marta ko'radi: asl chiptada hamon «Kutilmoqda» va «Tayyorlashni
    boshlash» tugmasi bilan, ustiga qizil «Qisman bekor qilindi»
    kartasi. Ya'ni u bekor qilingan taomni bemalol pishirib yuboradi.

    Shu funksiya asl qatorni «iste'mol qiladi»:

        to'liq olib tashlangan  ->  qator `Cancelled` bo'ladi
        qisman kamaytirilgan    ->  `quantity` kamayadi

    QATORLAR QAYSI TARTIBDA
    =======================
    Faqat hali `Kutilmoqda` dagilari va ENG YANGISIDAN boshlab. Bitta
    taom bir necha raundda buyurtma qilingan bo'lishi mumkin: birinchi
    porsiya allaqachon pishmoqda, ikkinchisi navbatda — bekor bo'lishi
    kerak bo'lgani AYNAN oxirgisi.

    `cancel_kot.original_kot` ga TAYANILMAYDI: URY uni birinchi mos
    KOT'da to'xtatib yozadi, ya'ni ro'yxat to'liq bo'lmaydi.

    Args:
        repair: TUZATISHDAN OLDIN yaratilgan yozuvlarni tozalash rejimi.
            Odatda faqat `Kutilmoqda` dagi qator yopiladi. Tuzatishdan
            oldin esa bekor qilingan taom oshxona ekranida ko'rinib
            turaverdi va oshpaz uni bilmasdan boshlab yuborgan bo'lishi
            mumkin — o'sha porsiyalar ham yopiladi. `Berildi` ga
            TEGILMAYDI: u jismonan chiqib bo'lgan.

    Returns:
        bool: `True` — hammasi navbatda edi, ya'ni oshxona ishni
        boshlamagan. Bunda «to'xtat» kartasi ham keraksiz.
    """
    rows = cancel_kot.get("kot_items") or []
    if not rows:
        return True

    invoice = cancel_kot.get("invoice")
    branch = cancel_kot.get("branch")

    touched, unmatched = {}, 0

    for row in rows:
        # `cancelled_qty` — nechtasi bekor qilingani. Ba'zi oqimlarda u
        # to'lmaydi, unda butun qator bekor qilingan deb qaraladi.
        need = cint(row.get("cancelled_qty")) or cint(row.get("quantity"))
        if need <= 0:
            continue

        unmatched += _consume_pending_rows(
            invoice, row.get("item"), need, touched, repair=repair
        )

    for kot, production in touched.items():
        _sync_cancelled_kot(kot)
        emit_kot_change(branch, kot, "KOT_ITEM_CANCELLED", production, invoice)

    return unmatched <= 0


def _consume_pending_rows(
    invoice: str, item: str, need: int, touched: dict, repair: bool = False
) -> int:
    """Navbatdagi qatorlardan `need` donani yopadi. Qolganini qaytaradi.

    Qaytgan qiymat > 0 bo'lsa — o'shancha dona ALLAQACHON oshxonada
    (`Preparing`/`Ready`/`Served`). Bu faqat menejer majburan bekor
    qilganda bo'ladi: ofitsantga `waiter._assert_removals_allowed()`
    bunday qilishga ruxsat bermaydi.
    """
    if not invoice or not item or need <= 0:
        return need

    # Odatda faqat navbatdagi porsiya yopiladi. Tuzatish rejimida esa
    # boshlangan porsiyalar ham — ular oshxonaga XATO tufayli tushgan.
    # `Berildi` va allaqachon `Bekor qilingan` hech qachon tegilmaydi.
    allowed = (
        (kitchen_status.PENDING, kitchen_status.PREPARING, kitchen_status.READY)
        if repair
        else (kitchen_status.PENDING,)
    )

    rows = frappe.db.sql(
        """
        SELECT ki.name, ki.parent, ki.quantity, ki.cancelled_qty, k.production
        FROM `tabURY KOT Items` ki
        INNER JOIN `tabURY KOT` k ON k.name = ki.parent
        WHERE k.invoice = %(invoice)s AND k.docstatus = 1
          AND k.type IN %(types)s
          AND ki.item = %(item)s
          AND IFNULL(ki.custom_kitchen_status, %(pending)s) IN %(allowed)s
        ORDER BY
          CASE WHEN IFNULL(ki.custom_kitchen_status, %(pending)s) = %(pending)s
               THEN 0 ELSE 1 END,
          k.creation DESC, ki.idx DESC
        FOR UPDATE
        """,
        {
            "invoice": invoice,
            "item": item,
            "types": kitchen_status.COOKING_KOT_TYPES,
            "pending": kitchen_status.PENDING,
            "allowed": allowed,
        },
        as_dict=True,
    )

    for row in rows:
        if need <= 0:
            break

        qty = cint(row.quantity)
        if qty <= 0:
            continue

        if need >= qty:
            # Butun qator ketadi — oshxona ekranidan yo'qoladi.
            frappe.db.set_value(
                "URY KOT Items",
                row.name,
                {
                    "custom_kitchen_status": kitchen_status.CANCELLED,
                    "custom_status_changed_by": frappe.session.user,
                },
                update_modified=False,
            )
            need -= qty
        else:
            # Qisman: `quantity` HAQIQATAN kamayadi. URY Mosaic KDS
            # per-item holatni bilmaydi va aynan shu maydonni ko'rsatadi,
            # shuning uchun uni yangilamasak Mosaic eski sonni chizardi.
            frappe.db.set_value(
                "URY KOT Items",
                row.name,
                {
                    "quantity": qty - need,
                    "cancelled_qty": cint(row.cancelled_qty) + need,
                },
                update_modified=False,
            )
            need = 0

        touched[row.parent] = row.production

    return need


def _sync_cancelled_kot(kot: str):
    """KOT darajasidagi `order_status` ni chiptaga mos keltiradi.

    NEGA `kitchen._sync_kot_order_status()` CHAQIRILMAYDI
    ====================================================
    U ikki holatni biladi: hammasi berilgan -> "Served", aks holda
    -> "Ready For Prepare". To'liq bekor qilingan KOT unga tushsa
    «Ready For Prepare» ga QAYTARILADI va URY'ning Mosaic KDS'ida
    yana paydo bo'ladi (`ury/api/ury_kot_display.py:50` shu qiymat
    bo'yicha filtrlaydi).

    Shuning uchun bekor qilingan chipta uchun boshqa qiymat yoziladi —
    Mosaic uni ko'rsatmaydi, ozturkapp ekrani esa holatni baribir
    mahsulotlardan keltirib chiqaradi.
    """
    statuses = frappe.get_all(
        "URY KOT Items",
        filters={"parent": kot, "parenttype": "URY KOT"},
        pluck="custom_kitchen_status",
    )
    derived = kitchen_status.derive_kot_status(statuses)

    if derived == kitchen_status.CANCELLED:
        frappe.db.set_value(
            "URY KOT", kot, "order_status", "Cancelled", update_modified=False
        )
    elif derived == kitchen_status.SERVED:
        frappe.db.set_value(
            "URY KOT", kot, "order_status", "Served", update_modified=False
        )


# ═══════════════════════════════════════════════════════════════════
#  Stol
# ═══════════════════════════════════════════════════════════════════

def _free_empty_tables(branch: str, tables: list) -> list:
    """Boshqa ochiq cheki qolmagan stollarni bo'shatadi.

    Hisob bo'lingan bo'lsa bitta stolda bir nechta chek bo'ladi — bittasi
    bekor qilinganda stol BAND bo'lib qolishi shart (TZ §23).
    """
    if not tables or not branch:
        return []

    # `get_open_orders()` `custom_cancelled = 0` bo'yicha filtrlaydi, ya'ni
    # yuqorida qo'yilgan bayroq tufayli joriy chek bu ro'yxatga TUSHMAYDI.
    still_busy = set()
    for order in table_status.get_open_orders(branch, tables):
        if order.restaurant_table:
            still_busy.add(order.restaurant_table)
        still_busy.update(table_status.parse_merged_with(order.custom_merged_tables))

    freed = []
    for table in tables:
        if table in still_busy or not frappe.db.exists("URY Table", table):
            continue

        frappe.db.set_value(
            "URY Table",
            table,
            {"occupied": 0, "latest_invoice_time": None},
            update_modified=False,
        )
        freed.append(table)

    return freed


def _tables_of(row) -> list:
    """Chek band qilgan barcha stollar (birlashtirilganlari bilan)."""
    tables = [row.get("restaurant_table")]
    tables.extend(table_status.parse_merged_with(row.get("custom_merged_tables")))
    return list(dict.fromkeys(table for table in tables if table))


def _user_label(user: str) -> str:
    return frappe.db.get_value("User", user, "full_name") or user
