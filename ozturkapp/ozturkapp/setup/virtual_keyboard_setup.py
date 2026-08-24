# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa oynasidagi ekran klaviaturasi — POS Profile darajasida yoqish/o'chirish.

NEGA KERAK
==========
Kassa apparatida jismoniy klaviatura bo'lmasligi mumkin (planshet/sensorli
terminal). Summani kiritish maydonlari `inputmode="numeric"` bilan
belgilangan — bu brauzerning O'Z klaviaturasini ochadi, lekin kiosk
rejimidagi qurilmalarda bu doim ishlay bermaydi. Shu sababli sahifaning
o'zida ekran klaviaturasi chiziladi (kassani yopishdagi naqd pul sanog'i va
to'lov summasi maydonlari uchun).

Har bir kassada ham kerak emas (jismoniy klaviatura/sichqoncha bo'lgan
kassalarda ortiqcha) — shuning uchun POS Profile darajasida yoqish/o'chirish.

    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.virtual_keyboard_setup.setup
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint

VIRTUAL_KEYBOARD_FIELD = {
    "POS Profile": [
        {
            "fieldname": "custom_enable_virtual_keyboard",
            "label": "Virtual Keyboard",
            "fieldtype": "Check",
            "default": "0",
            "insert_after": "custom_enable_bill_split",
            "description": (
                "Yoqilsa: kassani yopish (naqd pul sanog'i) va to'lov summasini "
                "kiritish maydonlarida ekranga sonli klaviatura chiqadi — "
                "jismoniy klaviaturasiz sensorli kassalar uchun."
            ),
        }
    ]
}


def setup():
    """Custom field (idempotent)."""
    create_fields()
    frappe.db.commit()


def create_fields():
    create_custom_fields(VIRTUAL_KEYBOARD_FIELD, ignore_validate=True)
    print("✅ Virtual klaviatura maydoni tayyor (POS Profile.custom_enable_virtual_keyboard)")


def is_enabled(pos_profile: str) -> bool:
    """POS Profile'da ekran klaviaturasi yoqilganmi.

    Maydon shu modul o'rnatilmagan saytda bo'lmasligi mumkin — shuning
    uchun avval tekshiriladi, aks holda SQL xato berardi.
    """
    if not pos_profile:
        return False
    if not frappe.db.has_column("POS Profile", "custom_enable_virtual_keyboard"):
        return False

    return bool(
        cint(frappe.db.get_value("POS Profile", pos_profile, "custom_enable_virtual_keyboard"))
    )
