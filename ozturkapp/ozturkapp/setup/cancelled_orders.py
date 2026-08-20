# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Bekor qilingan eski cheklarni stoldan uzish (idempotent tozalash).

MUAMMO
======
Bekor qilingan chek O'CHIRILMAYDI — u `docstatus = 0` bo'lib qoladi va
faqat `custom_cancelled = 1` bilan belgilanadi. URY esa stoldagi "faol
buyurtma"ni AYNAN stol bog'lami bo'yicha qidiradi va bizning
bayrog'imizni bilmaydi::

    ury_order.get_order_invoice():
        filters    {docstatus: 0, invoice_printed: 0}
        or_filters {restaurant_table: <stol>,
                    custom_merged_tables: like %<stol>%}

Natijada o'sha stolga keyingi zakaz olinganda URY bekor qilingan chekni
topib «Table-1 is already occupied» deb rad etadi (`ury_order.py:840`) —
`URY Table.occupied = 0` bo'lsa ham. Stol AMALDA abadiy bloklanadi.

Yangi bekor qilishlar `utils/order_cancel.py` da darhol uziladi; bu
funksiya esa TUZATISHDAN OLDIN bekor qilingan cheklarni tozalaydi.

NEGA `patches.txt` EMAS
=======================
`custom_cancelled_table` maydoni `after_migrate` da yaratiladi, patch'lar
esa undan OLDIN ishlaydi — ya'ni patch paytida maydon hali yo'q. Shuning
uchun tozalash `after_migrate` ro'yxatida, maydon yaratilgandan KEYIN
turadi. Funksiya idempotent: tuzatiladigan chek qolmasa hech narsa
qilmaydi.
"""

import frappe


def detach_tables():
    """Stolga bog'liq qolgan bekor qilingan draft cheklarni uzadi."""
    for column in ("custom_cancelled", "custom_cancelled_table"):
        if not frappe.db.has_column("POS Invoice", column):
            return

    rows = frappe.db.sql(
        """
        SELECT name, restaurant_table, custom_merged_tables
        FROM `tabPOS Invoice`
        WHERE docstatus = 0 AND custom_cancelled = 1
          AND (ifnull(restaurant_table, '') != '' OR ifnull(custom_merged_tables, '') != '')
        """,
        as_dict=True,
    )
    if not rows:
        return

    for row in rows:
        remembered = ", ".join(
            part
            for part in [row.restaurant_table, row.custom_merged_tables]
            if part
        )
        frappe.db.set_value(
            "POS Invoice",
            row.name,
            {
                "custom_cancelled_table": remembered,
                "restaurant_table": None,
                "custom_merged_tables": None,
            },
            update_modified=False,
        )

    frappe.logger("ozturkapp").info(
        "Bekor qilingan %d ta chek stoldan uzildi: %s",
        len(rows),
        ", ".join(row.name for row in rows),
    )


def reconcile_cancel_kots():
    """Tuzatishdan OLDIN yaratilgan bekor-KOT'larni yopadi.

    MUAMMO
    ======
    URY taom zakazdan olib tashlanganda yangi «Partially cancelled» KOT
    yaratadi, ASL chiptaga esa tegmaydi. Tuzatishdan oldin bu ikki
    oqibatga olib kelardi:

        1. Bekor qilingan taom asl kartada «Kutilmoqda» bo'lib turardi —
           oshpaz uni bemalol pishirib yuborardi (hattoki holatini ham
           o'zgartira olardi);
        2. Ustiga qizil «Qisman bekor qilindi» kartasi qo'shilardi —
           bitta taom ekranda IKKI marta ko'rinardi.

    Yangi bekor qilishlar `utils/kitchen_realtime._on_cancellation_kot()`
    da darhol yopiladi. Bu funksiya esa ALLAQACHON yaratilgan yozuvlarni
    tuzatadi — usiz eski chiptalar oshxona ekranida abadiy osilib
    qolardi.

    TUZATISH REJIMI
    ===============
    Odatdagi oqimda faqat `Kutilmoqda` dagi porsiya yopiladi. Bu yerda
    esa `Tayyorlanmoqda` / `Tayyor` ham yopiladi: o'sha porsiyalar
    oshxonaga aynan SHU XATO tufayli tushgan — ofitsant ularni
    bekor qilgan paytda hammasi navbatda edi (buni server
    `waiter._assert_removals_allowed()` bilan kafolatlaydi).
    `Berildi` ga TEGILMAYDI — u jismonan chiqib bo'lgan.

    Idempotent: yopilgan chipta `verified = 1` bilan belgilanadi va
    ikkinchi yurgizishda umuman tanlanmaydi.
    """
    if not frappe.db.has_column("URY KOT Items", "custom_kitchen_status"):
        return

    from ozturkapp.ozturkapp.utils import kitchen_status, order_cancel

    pending_kots = frappe.get_all(
        "URY KOT",
        filters={
            "docstatus": 1,
            "type": ["in", list(kitchen_status.CANCELLATION_KOT_TYPES)],
            "verified": 0,
        },
        pluck="name",
        order_by="creation asc",
    )
    if not pending_kots:
        return

    for name in pending_kots:
        doc = frappe.get_doc("URY KOT", name)
        order_cancel.apply_item_cancellation(doc, repair=True)

        frappe.db.set_value(
            "URY KOT",
            name,
            {
                "verified": 1,
                "verified_by": frappe.session.user,
                "order_status": "Cancelled",
            },
            update_modified=False,
        )

    frappe.logger("ozturkapp").info(
        "Bekor qilish chiptalari yopildi (%d ta): %s",
        len(pending_kots),
        ", ".join(pending_kots),
    )
