# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassada hisobni taqsimlash (bill split) — POS Profile darajasida yoqish/o'chirish.

URY'DA TAYYOR MEXANIZM BOR — QAYTA YOZILMAYDI
==============================================
`ury.ury.doctype.ury_order.ury_order.split_bill()` allaqachon mavjud va
ishlaydi: tanlangan mahsulotlarni (to'liq yoki qisman miqdorda) chop
etilgan qoralama chekdan yangi, unga BOG'LIQ chekka ko'chiradi va soliq/
xizmat haqini ikkalasida ham qayta hisoblaydi. Kerakli maydonlar
(`POS Invoice.custom_split_from`, `custom_split_group`) ham allaqachon bor.

Biz bu mexanizmga TEGMAYMIZ (TZ §32) — faqat:
  1. kassa ekraniga chiqaramiz (`api/billing.py:split_bill`),
  2. POS Profile darajasida YOQISH/O'CHIRISH imkonini qo'shamiz.

`POS Profile.remove_items` BILAN ARALASHTIRMANG
================================================
`remove_items` ("Allow Cashier To Edit And Remove Table Order Items") —
umuman chop etilgan chekdan mahsulot o'chirish/tahrirlash ruxsati
(kengroq, boshqa maqsad — URY'ning o'zi beradi). Bu yerdagi maydon FAQAT
kassa ekranidagi «Hisobni bo'lish» tugmasi ko'rinsin-ko'rinmasligini
boshqaradi.

    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.bill_split_setup.setup
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint

BILL_SPLIT_FIELD = {
    "POS Profile": [
        {
            "fieldname": "custom_enable_bill_split",
            "label": "Hisobni taqsimlashga ruxsat berish",
            "fieldtype": "Check",
            "default": "0",
            "insert_after": "remove_items",
            "description": (
                "Yoqilsa: kassa oynasida «Hisobni bo'lish» tugmasi ko'rinadi — "
                "kassir mahsulotlarni belgilab, ular uchun alohida chek chiqarib, "
                "umumiy hisobdan chiqarib olishi mumkin bo'ladi."
            ),
        }
    ]
}


def setup():
    """Custom field (idempotent)."""
    create_fields()
    frappe.db.commit()


def create_fields():
    create_custom_fields(BILL_SPLIT_FIELD, ignore_validate=True)
    print("✅ Hisobni taqsimlash maydoni tayyor (POS Profile.custom_enable_bill_split)")


def is_enabled(pos_profile: str) -> bool:
    """POS Profile'da hisobni taqsimlash yoqilganmi.

    Maydon shu modul o'rnatilmagan saytda bo'lmasligi mumkin — shuning
    uchun avval tekshiriladi, aks holda SQL xato berardi.
    """
    if not pos_profile:
        return False
    if not frappe.db.has_column("POS Profile", "custom_enable_bill_split"):
        return False

    return bool(cint(frappe.db.get_value("POS Profile", pos_profile, "custom_enable_bill_split")))
