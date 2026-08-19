# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Oshxona realtime hodisalari (TZ §12, §13).

MAVJUD KANALNI QAYTA ISHLATAMIZ
===============================
URY allaqachon `kot_update_{branch}_{production}` kanaliga signal yuboradi
(`URYKOT.kotDisplayRealtime()`, `on_submit`). Mosaic KDS shu kanalni
tinglaydi. Biz YANGI INFRATUZILMA qurmaymiz (TZ §12) — o'sha kanalga
payload'siz signal yuboramiz, xuddi URY'ning o'zi `change_table_in_kot()`
da qilgani kabi. Shu tufayli Mosaic ham yangilanadi.

O'z sahifamiz uchun esa ikkita yupqa hodisa qo'shamiz. Ular FAQAT
identifikator tashiydi: xabar butun saytga (barcha Desk foydalanuvchilariga)
ketadi, shuning uchun mahsulot nomi, mijoz, summa YUBORILMAYDI —
mijoz ma'lumotni ruxsat tekshiriladigan API orqali qayta so'raydi (TZ §13).
"""

import frappe

#: Yangi/o'zgargan KOT — ro'yxat yangilanadi.
EVENT_KOT = "ozturk_kitchen_kot"

#: Bitta mahsulot holati o'zgardi — faqat o'sha karta yangilanadi (TZ §32).
EVENT_ITEM = "ozturk_kitchen_item"


def _ury_channel(branch: str, production: str) -> str:
    """URY'ning mavjud KDS kanali."""
    return "{}_{}_{}".format("kot_update", branch, production)


def emit_kot_change(branch, kot, reason, production=None, invoice=None):
    """KOT o'zgargani haqida (TZ §12: KOT_CREATED / KOT_UPDATED / KOT_CANCELLED)."""
    if not branch:
        return

    frappe.publish_realtime(
        EVENT_KOT,
        {
            "branch": branch,
            "kot": kot,
            "reason": reason,
            "station": production,
            "invoice": invoice,
        },
        after_commit=True,
    )

    # Mavjud Mosaic KDS ham xabardor bo'lsin — URY'ning o'z uslubi.
    if production:
        frappe.publish_realtime(_ury_channel(branch, production), after_commit=True)


def emit_item_change(branch, kot, kot_item, status, production=None, invoice=None):
    """Mahsulot holati o'zgardi (TZ §13: KOT_ITEM_STATUS_CHANGED).

    Iste'molchilar: oshxona ekranlari, Kassa, Ofitsant ilovasi.

    `invoice` NEGA KERAK
    ====================
    Ofitsant ilovasi ochiq buyurtma ekranini AYNAN chek nomi bo'yicha
    yangilaydi (`orderChanges` -> `invoice == _order.invoice`). Bu maydon
    yuborilmasa xabar yetib boradi, lekin ilova uni qaysi chekka tegishli
    ekanini bilmaydi va ekran YANGILANMAYDI — oshpaz holatni o'zgartirsa
    ham ofitsant qo'lda yangilamaguncha eski holatni ko'rib turadi.

    Aynan shu xato bo'lgan edi.
    """
    if not branch:
        return

    frappe.publish_realtime(
        EVENT_ITEM,
        {
            "branch": branch,
            "kot": kot,
            "kot_item": kot_item,
            "status": status,
            "station": production,
            "invoice": invoice,
        },
        after_commit=True,
    )


# ═══════════════════════════════════════════════════════════════════
#  Hujjat hodisalari (hooks.py)
# ═══════════════════════════════════════════════════════════════════

def on_kot_submit(doc, method=None):
    """Yangi KOT tasdiqlanganda oshxona ekraniga darhol chiqsin (TZ §12/#16).

    URY'ning o'z `kotDisplayRealtime()` metodi ham shu paytda ishlaydi, lekin
    u FAQAT `kot_update_{branch}_{production}` kanaliga yuboradi. Bizning
    sahifamiz o'z hodisamizni tinglaydi.
    """
    if doc.doctype != "URY KOT":
        return

    emit_kot_change(
        doc.get("branch"), doc.name, "KOT_CREATED",
        doc.get("production"), doc.get("invoice"),
    )


def on_kot_cancel(doc, method=None):
    """KOT bekor qilinganda ekrandan olib tashlash uchun (TZ §31)."""
    if doc.doctype != "URY KOT":
        return

    emit_kot_change(
        doc.get("branch"), doc.name, "KOT_CANCELLED",
        doc.get("production"), doc.get("invoice"),
    )
