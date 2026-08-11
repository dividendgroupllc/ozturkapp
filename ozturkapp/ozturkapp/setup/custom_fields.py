# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Ozturkapp ishlashi uchun zarur Custom Field va Property Setter'lar.

Jazira'da bular fixture JSON orqali tashilardi. Bu yerda dasturiy yaratamiz —
yangi saytda ishonchliroq va idempotent (qayta-qayta ishga tushirsa bo'ladi).

    bench --site ozturk.local execute ozturkapp.ozturkapp.setup.custom_fields.run
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

CUSTOM_FIELDS = {
    # Soatbay ish haqi — Employee Daily/Period Hours hisobotlari shunga tayanadi
    "Employee": [
        {
            "fieldname": "hourly_rate",
            "label": "Hourly Rate (Soatlik stavka)",
            "fieldtype": "Currency",
            "options": "salary_currency",
            "insert_after": "designation",
            "translatable": 0,
        }
    ],
    # Kirim/chiqim sababi — ish vaqti hisoblashda TEMP_OUT/RETURN ajratiladi
    "Employee Checkin": [
        {
            "fieldname": "checkin_source",
            "label": "Checkin Source",
            "fieldtype": "Select",
            "options": "\nManual\nImport",
            "default": "Manual",
            "insert_after": "log_type",
            "translatable": 0,
        },
        {
            "fieldname": "checkin_reason",
            "label": "Checkin Reason",
            "fieldtype": "Select",
            "options": "\nIN\nOUT\nTEMP_OUT\nRETURN",
            "insert_after": "checkin_source",
            "translatable": 0,
        },
    ],
    # Production Entry -> Stock Entry bog'lanishi (dashboard link uchun ham)
    "Stock Entry": [
        {
            "fieldname": "custom_production_entry",
            "label": "Production Entry",
            "fieldtype": "Link",
            "options": "Production Entry",
            "insert_after": "stock_entry_type",
            "read_only": 1,
            "no_copy": 1,
            "print_hide": 1,
            "translatable": 0,
        }
    ],
}

# (doctype, fieldname, property, value, property_type)
# fieldname None bo'lsa — DocType darajasidagi property
PROPERTY_SETTERS = [
    # Barcode skanerlash maydonlari restoran oqimida ishlatilmaydi
    ("Sales Invoice", "scan_barcode", "hidden", "1", "Check"),
    ("POS Invoice", "scan_barcode", "hidden", "1", "Check"),
    ("Stock Entry", "scan_barcode", "hidden", "1", "Check"),
    # STIR (tax_id) faktura va chop etishda ko'rinsin
    ("Sales Invoice", "tax_id", "hidden", "0", "Check"),
    ("Sales Invoice", "tax_id", "print_hide", "0", "Check"),
    # Yaxlitlangan jami — ko'rsatiladi
    ("Sales Invoice", "disable_rounded_total", "default", "0", "Text"),
    ("Sales Invoice", "rounded_total", "hidden", "0", "Check"),
    ("Sales Invoice", "rounded_total", "print_hide", "0", "Check"),
    ("Sales Invoice", "base_rounded_total", "hidden", "0", "Check"),
    ("Sales Invoice", "base_rounded_total", "print_hide", "1", "Check"),
    # Summa so'z bilan
    ("Sales Invoice", "in_words", "hidden", "0", "Check"),
    ("Sales Invoice", "in_words", "print_hide", "0", "Check"),
    # Qo'shimcha chegirma hisobi yashiriladi
    ("Sales Invoice", "additional_discount_account", "hidden", "1", "Check"),
    ("Sales Invoice", "additional_discount_account", "mandatory_depends_on", "", "Code"),
]


def create_fields():
    """Custom Field'larni yaratadi (idempotent)."""
    create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
    print(f"✅ Custom Field'lar tayyor ({sum(len(v) for v in CUSTOM_FIELDS.values())} ta)")


def create_property_setters():
    """Property Setter'larni o'rnatadi (idempotent)."""
    for doctype, fieldname, prop, value, prop_type in PROPERTY_SETTERS:
        if not frappe.db.exists("DocType", doctype):
            print(f"⏭️  DocType yo'q, o'tkazildi: {doctype}")
            continue
        make_property_setter(
            doctype, fieldname, prop, value, prop_type, for_doctype=not fieldname
        )
    print(f"✅ Property Setter'lar tayyor ({len(PROPERTY_SETTERS)} ta)")


def run():
    create_fields()
    create_property_setters()
