# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""
POS Invoice override hooks
===========================

Counter-service model uchun URY Table occupied flagini to'g'ri boshqarish.

Muammo: URY app occupied=0 ni faqat cancel/delete paytida tozalaydi,
payment (submit) vaqtida emas. Shuning uchun stiker raqamlari qayta
ishlatib bo'lmay qoladi.

Yechim: POS Invoice submit bo'lganda occupied=0 ga o'rnatiladi.
Shu orqali har bir to'lovdan keyin stiker raqami avtomatik bo'shaladi
va keyingi mijoz uchun qayta ishlatilishi mumkin.
"""

import frappe


def on_submit(doc, method):
    """POS Invoice submit bo'lganda (to'lov qilinganda) URY Table ni bo'shatish."""
    if doc.restaurant_table:
        frappe.db.set_value(
            "URY Table",
            doc.restaurant_table,
            {"occupied": 0, "latest_invoice_time": None},
        )
