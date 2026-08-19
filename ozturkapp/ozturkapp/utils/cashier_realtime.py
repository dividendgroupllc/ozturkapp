# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa oynasi uchun realtime xabarlar (TZ §13).

XAVFSIZLIK — NEGA XABAR "YUPQA"
===============================
`frappe/realtime.py:70`: `room`, `user`, `doctype` berilmasa xabar SAYT
XONASIGA, ya'ni BARCHA Desk foydalanuvchilariga ketadi. URY'ning mavjud
`reload_ro`, `table_freed`, `kot_update_*` hodisalari ham shunday ishlaydi.

Shuning uchun bu yerdagi xabarlar faqat IDENTIFIKATORLARNI tashiydi:
stol nomi, chek nomi, o'zgarish sababi. Summa, mijoz ismi, ofitsant —
HECH QACHON. Mijoz xabarni olgach, ruxsat tekshiruvidan o'tadigan API
orqali ma'lumotni QAYTA SO'RAYDI (TZ §17, §24 — backend yagona haqiqat manbai).

`after_commit=True`
===================
Xabar tranzaksiya commit bo'lgandan KEYIN yuboriladi. Aks holda kassir
ekrani hali yozilmagan ma'lumotni o'qib, eski holatni ko'rsatib qo'yishi
mumkin edi.

MAVJUD HODISALARNI QAYTA ISHLATAMIZ
===================================
Kassa sahifasi quyidagilarga ham obuna bo'ladi (yangisini yozmaymiz):

    reload_ro               URY  — chek submit bo'ldi
    pos_invoice_updated     URY  — birlashtirilgan chek yangilandi
    table_freed             ozturkapp — stol bo'shatildi
    pending_order_cancelled ozturkapp — buyurtma bekor qilindi
"""

import frappe

#: Kassa zal rejasi uchun yagona hodisa nomi.
EVENT_FLOOR = "ozturk_cashier_floor"

#: Bitta chek o'zgargani haqida (ochiq buyurtma paneli uchun).
EVENT_ORDER = "ozturk_cashier_order"


def _tables_touched(doc) -> list:
    """Chek qaysi stollarga tegishli — birlashtirilganlari bilan birga."""
    tables = []
    primary = doc.get("restaurant_table")
    if primary:
        tables.append(primary)

    merged = doc.get("custom_merged_tables")
    if merged:
        tables.extend(part.strip() for part in str(merged).split(",") if part.strip())

    return list(dict.fromkeys(tables))


def emit_floor_change(branch: str, tables=None, reason: str = "", invoice: str = None):
    """Zal holati o'zgargani haqida xabar (faqat identifikatorlar)."""
    if not branch:
        return

    frappe.publish_realtime(
        EVENT_FLOOR,
        {
            "branch": branch,
            "tables": list(tables or []),
            "reason": reason,
            "invoice": invoice,
        },
        after_commit=True,
    )


def emit_order_change(branch: str, invoice: str, reason: str = "", table: str = None):
    """Bitta buyurtma o'zgargani haqida xabar."""
    if not branch or not invoice:
        return

    frappe.publish_realtime(
        EVENT_ORDER,
        {"branch": branch, "invoice": invoice, "table": table, "reason": reason},
        after_commit=True,
    )


# ═══════════════════════════════════════════════════════════════════
#  Hujjat hodisalari (hooks.py dan chaqiriladi)
# ═══════════════════════════════════════════════════════════════════

#: `method` -> kassa ekraniga tushunarli sabab.
_REASONS = {
    "after_insert": "ORDER_CREATED",
    "on_update": "ORDER_UPDATED",
    "on_submit": "PAYMENT_COMPLETED",
    "on_cancel": "ORDER_CANCELLED",
    "on_trash": "ORDER_CANCELLED",
}


def on_pos_invoice_change(doc, method=None):
    """`POS Invoice` har qanday o'zgarganda zal holatini yangilash signali.

    Bu KASSA UCHUN ASOSIY SIGNAL. Sababi: ofitsant buyurtma yaratganda
    URY `sync_order()` -> `invoice.save()` ni chaqiradi, ya'ni shu hodisa
    ishga tushadi. URY'ning o'zi stol bandligini `frappe.db.set_value` bilan
    yozadi — u esa HECH QANDAY hujjat hodisasini ishga tushirmaydi, shuning
    uchun `URY Table` ga osilgan hook ishonchsiz bo'lardi.
    """
    if doc.doctype != "POS Invoice":
        return

    reason = _REASONS.get(method or "", "ORDER_UPDATED")
    tables = _tables_touched(doc)

    emit_floor_change(doc.get("branch"), tables, reason, doc.name)
    emit_order_change(doc.get("branch"), doc.name, reason, doc.get("restaurant_table"))


def on_table_change(doc, method=None):
    """`URY Table` Desk orqali qo'lda tahrirlanganda (layout, seats, ...).

    URY kodi bandlikni `frappe.db.set_value` bilan yozgani uchun bu hook
    faqat qo'lda tahrirlashni qamrab oladi — asosiy signal yuqoridagi
    `on_pos_invoice_change`.
    """
    if doc.doctype != "URY Table":
        return

    emit_floor_change(doc.get("branch"), [doc.name], "TABLE_UPDATED")


# ═══════════════════════════════════════════════════════════════════
#  Kassa smenasi (ofitsant ilovasidagi bloklovchi oyna uchun)
# ═══════════════════════════════════════════════════════════════════

#: Smena ochildi/yopildi. Ofitsant ilovasi shu xabar bilan bloklovchi
#: oynani ko'rsatadi yoki olib tashlaydi — ilovani qayta ochish shart emas.
EVENT_SHIFT = "ozturk_shift"


def emit_shift_change(branch: str, is_open: bool, reason: str = ""):
    """Smena holati o'zgargani haqida xabar.

    Payload ATAYLAB yupqa: filial va bayroq. Summa ham, kassir ismi ham
    yuborilmaydi — xabar sayt xonasiga ketadi (modul boshidagi izohga
    qarang). Ilova xabarni olgach kerak bo'lsa `waiter.get_context()` ni
    qayta so'raydi.
    """
    if not branch:
        return

    frappe.publish_realtime(
        EVENT_SHIFT,
        {"branch": branch, "open": bool(is_open), "reason": reason},
        after_commit=True,
    )


def on_pos_opening_change(doc, method=None):
    """`POS Opening Entry` tasdiqlandi yoki bekor qilindi.

    Hujjat hodisasiga bog'lanadi, chunki smenani UCH xil mijoz ochadi:
    kassa sahifasi, Desktop POS va ERPNext Desk. Ularning har biriga
    alohida `emit` yozish o'rniga manbaning O'ZIGA osiladi — qaysi yo'l
    bilan ochilishidan qat'i nazar xabar ketadi.
    """
    if doc.doctype != "POS Opening Entry":
        return

    opened = method == "on_submit"
    emit_shift_change(
        doc.get("branch"),
        opened,
        "SHIFT_OPENED" if opened else "SHIFT_CANCELLED",
    )


def on_pos_closing_change(doc, method=None):
    """`POS Closing Entry` tasdiqlandi yoki bekor qilindi.

    `POS Closing Entry` da `branch` maydoni YO'Q — u bog'langan
    `POS Opening Entry` dan o'qiladi.
    """
    if doc.doctype != "POS Closing Entry":
        return

    branch = frappe.db.get_value(
        "POS Opening Entry", doc.get("pos_opening_entry"), "branch"
    )
    closed = method == "on_submit"
    emit_shift_change(
        branch,
        not closed,
        "SHIFT_CLOSED" if closed else "SHIFT_REOPENED",
    )
