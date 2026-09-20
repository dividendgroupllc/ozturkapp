# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassadan buyurtma: yetkazib berish ma'lumoti uchun maydonlar.

Yetkazib berish buyurtmasida stol yo'q — kuryer uchun telefon va manzil
kerak. Yangi DocType YARATILMAYDI: ikkita maydon `POS Invoice` ga qo'shiladi
va URY'ning mavjud `sync_order` oqimi o'zgarmaydi.

`after_migrate.run()` shu modulning `setup()` funksiyasini chaqiradi.
Har bir funksiya IDEMPOTENT (qayta-qayta migrate xavfsiz).

    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.cashier_orders_setup.setup
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

#: Maydon nomlari — `api/cashier_orders.py`, `utils/table_status.py` va
#: `utils/cashier_billing.py` shularga tayanadi.
DELIVERY_PHONE_FIELD = "custom_delivery_phone"
DELIVERY_ADDRESS_FIELD = "custom_delivery_address"

ORDER_FIELDS = {
    "POS Invoice": [
        {
            "fieldname": DELIVERY_PHONE_FIELD,
            "label": "Delivery Phone",
            "fieldtype": "Data",
            "allow_on_submit": 1,
            "insert_after": "custom_comments",
            "description": "Yetkazib berish buyurtmasi: mijoz telefoni (kassadan kiritiladi).",
        },
        {
            "fieldname": DELIVERY_ADDRESS_FIELD,
            "label": "Delivery Address",
            "fieldtype": "Small Text",
            "allow_on_submit": 1,
            "insert_after": DELIVERY_PHONE_FIELD,
            "description": "Yetkazib berish buyurtmasi: manzil (kassadan kiritiladi).",
        },
    ]
}


def setup():
    create_fields()
    frappe.db.commit()


def create_fields():
    """Yetkazib berish maydonlari (idempotent)."""
    create_custom_fields(ORDER_FIELDS, ignore_validate=True)
    print("✅ Kassadan buyurtma maydonlari tayyor (POS Invoice: yetkazib berish)")
