# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Valyuta belgisini yashirish.

Kompaniya faqat bitta valyutada (UZS) ishlaydi, `Currency UZS` belgisi esa
"лв" — u barcha summalar oldida chiqib turardi. ERPNext `Global Defaults`
dagi "Hide Currency Symbol" sozlamasi belgini hamma joyda (formalar,
reportlar, chop etish) yashiradi — summa faqat raqam bo'lib ko'rinadi.
"""

import frappe


def setup():
    doc = frappe.get_single("Global Defaults")
    if doc.hide_currency_symbol == "Yes":
        return
    doc.hide_currency_symbol = "Yes"
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    print("✅ Valyuta belgisi yashirildi (Global Defaults → Hide Currency Symbol)")
