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
