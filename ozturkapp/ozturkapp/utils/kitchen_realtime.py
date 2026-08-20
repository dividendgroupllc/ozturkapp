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

    from ozturkapp.ozturkapp.utils import kitchen_status, notifications

    # ── Bekor qilish KOT'i — OVQAT BUYURTMASI EMAS ────────────────────
    # URY taom zakazdan olib tashlanganda shunday hujjat yaratadi.
    # Ilgari u shu yerdan o'tib, oshxonaga «Yangi buyurtma» xabarini
    # yuborardi — ya'ni olib tashlangan taom yangi buyurtma bo'lib
    # ko'rinardi.
    if doc.get("type") in kitchen_status.CANCELLATION_KOT_TYPES:
        _on_cancellation_kot(doc)
        return

    emit_kot_change(
        doc.get("branch"), doc.name, "KOT_CREATED",
        doc.get("production"), doc.get("invoice"),
    )

    # YANGI BUYURTMA -> OSHXONAGA xabar.
    #
    # Bildirishnoma AYNAN shu yerda, `waiter.submit_order()` da EMAS:
    # KOT'ni Desktop POS ham, kassir ham, ofitsant ilovasi ham yaratadi.
    # Manbaning har biriga alohida xabar yozsak, bittasi unutilardi va
    # o'sha yo'l bilan kelgan buyurtma jimgina o'tib ketardi.
    notifications.order_placed(
        doc.get("branch"),
        doc.get("invoice"),
        doc.get("restaurant_table"),
        len(doc.get("kot_items") or []),
        doc.get("production"),
    )


def _on_cancellation_kot(doc):
    """Taom zakazdan olib tashlandi — asl chiptani ham yopamiz.

    NEGA SHU YERDA
    ==============
    Taomni olib tashlash yo'llari ko'p: ofitsant ilovasi, Desktop POS,
    kassa oynasi, URY POS. Hammasi oxir-oqibat `sync_order()` ga boradi
    va u bekor-KOT yaratadi. Ya'ni `URY KOT.on_submit` — YAGONA choke
    point. Har bir mijozga alohida tozalash yozsak, bittasi unutilardi.

    IKKI XIL NATIJA
    ===============
        oshxona hali boshlamagan  -> asl qator ham, «to'xtat» kartasi
                                     ham yo'qoladi; oshpaz bezovta
                                     qilinmaydi (yo'q ishni to'xtatib
                                     bo'lmaydi)
        oshxona boshlab yuborgan  -> karta EKRANDA QOLADI va oshpazga
                                     «TO'XTATING» xabari boradi
    """
    from ozturkapp.ozturkapp.utils import notifications, order_cancel

    branch = doc.get("branch")
    nothing_to_stop = order_cancel.apply_item_cancellation(doc)

    if nothing_to_stop:
        # Chiptani darhol "ko'rilgan" deb yopamiz. `verified` +
        # `order_status` — URY'ning O'Z mexanizmi
        # (`ury_kot_display.confirm_cancel_kot`), shuning uchun Mosaic
        # KDS ham bu kartani ko'rsatmaydi.
        frappe.db.set_value(
            "URY KOT",
            doc.name,
            {
                "verified": 1,
                "verified_by": frappe.session.user,
                "order_status": "Cancelled",
            },
            update_modified=False,
        )

    emit_kot_change(
        branch,
        doc.name,
        "KOT_CANCELLED" if nothing_to_stop else "KOT_STOP_REQUESTED",
        doc.get("production"),
        doc.get("invoice"),
    )

    if nothing_to_stop:
        return

    names = ", ".join(
        f"{row.get('item_name') or row.get('item')} ×{row.get('cancelled_qty') or row.get('quantity')}"
        for row in (doc.get("kot_items") or [])
    )
    notifications.order_cancelled(
        branch,
        doc.get("invoice"),
        names,
        doc.get("restaurant_table"),
        doc.get("production"),
    )


def on_kot_cancel(doc, method=None):
    """KOT bekor qilinganda ekrandan olib tashlash uchun (TZ §31)."""
    if doc.doctype != "URY KOT":
        return

    emit_kot_change(
        doc.get("branch"), doc.name, "KOT_CANCELLED",
        doc.get("production"), doc.get("invoice"),
    )
