# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Ofitsant mobil ilovasi uchun sozlash (TZ §7.8, §12).

ROL
===
URY'da ofitsant roli ALLAQACHON bor — `URY Captain` (URY hujjatlarida
"Captain Transfer" aynan ofitsantni almashtirishni bildiradi). Yangi rol
YARATILMAYDI.

HISOB SO'RASH (TZ §7.8)
=======================
"Request Bill" tugmasi bosilganda kassir buni ko'rishi kerak. Buning uchun
`POS Invoice` ga ikkita maydon qo'shiladi — yangi DocType emas.

    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.waiter_setup.setup
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.permissions import add_permission, update_permission_property

#: URY'ning mavjud ofitsant roli.
WAITER_ROLE = "URY Captain"

#: Ofitsant ilovasiga kira oladigan rollar.
WAITER_ROLES = (WAITER_ROLE, "URY Manager", "System Manager")


WAITER_FIELDS = {
    "POS Invoice": [
        {
            "fieldname": "custom_bill_requested",
            "label": "Bill Requested",
            "fieldtype": "Check",
            "default": "0",
            "allow_on_submit": 1,
            "insert_after": "invoice_printed",
            "description": "Ofitsant mijoz nomidan hisob so'radi (mobil ilova).",
        },
        {
            "fieldname": "custom_bill_requested_at",
            "label": "Bill Requested At",
            "fieldtype": "Datetime",
            "read_only": 1,
            "allow_on_submit": 1,
            "insert_after": "custom_bill_requested",
        },
        {
            "fieldname": "custom_bill_requested_by",
            "label": "Bill Requested By",
            "fieldtype": "Link",
            "options": "User",
            "read_only": 1,
            "allow_on_submit": 1,
            "insert_after": "custom_bill_requested_at",
        },
    ]
}


#: Ofitsant buyurtma oladi, lekin TO'LOV QILMAYDI va narx o'zgartirmaydi.
#: `submit`/`cancel` BERILMAYDI — chekni faqat kassir yopadi.
#:
#: DIQQAT — "O'QISH" RUXSATLARI NEGA SHUNCHA KO'P
#: ==============================================
#: `POS Invoice.validate()` zanjiri ERPNext ichida bir nechta DocType'ni
#: `frappe.get_list()` bilan o'qiydi (masalan `validate_pos_opening_entry()`
#: -> `POS Opening Entry`). Ular o'qilmasa `frappe.PermissionError` BO'SH
#: XABAR bilan ko'tariladi va URY uni "Error while updating order: " deb
#: yutib yuboradi — nosozlikni topish juda qiyin bo'ladi.
#:
#: Shuning uchun quyidagilar FAQAT `read` — yozish huquqi berilmaydi.
WAITER_PERMISSIONS = {
    # ── Buyurtma ──────────────────────────────────────────────────────
    "POS Invoice": {WAITER_ROLE: ["read", "write", "create"]},
    "URY KOT": {WAITER_ROLE: ["read", "write", "create", "submit"]},
    "Customer": {WAITER_ROLE: ["read", "write", "create"]},
    # ── Restoran ma'lumotnomalari (faqat o'qish) ──────────────────────
    "URY Table": {WAITER_ROLE: ["read"]},
    "URY Room": {WAITER_ROLE: ["read"]},
    "URY Restaurant": {WAITER_ROLE: ["read"]},
    "URY Menu": {WAITER_ROLE: ["read"]},
    "URY Menu Course": {WAITER_ROLE: ["read"]},
    "URY Production Unit": {WAITER_ROLE: ["read"]},
    "POS Profile": {WAITER_ROLE: ["read"]},
    # ── ERPNext validatsiya zanjiri talab qiladigan o'qishlar ─────────
    "POS Opening Entry": {WAITER_ROLE: ["read"]},
    "Item": {WAITER_ROLE: ["read"]},
    "Item Group": {WAITER_ROLE: ["read"]},
    "Item Price": {WAITER_ROLE: ["read"]},
    "Price List": {WAITER_ROLE: ["read"]},
    "Mode of Payment": {WAITER_ROLE: ["read"]},
    "Sales Taxes and Charges Template": {WAITER_ROLE: ["read"]},
    "Warehouse": {WAITER_ROLE: ["read"]},
    "Company": {WAITER_ROLE: ["read"]},
    "Branch": {WAITER_ROLE: ["read"]},
    "UOM": {WAITER_ROLE: ["read"]},
    "Account": {WAITER_ROLE: ["read"]},
    "Cost Center": {WAITER_ROLE: ["read"]},
}


def setup():
    create_fields()
    create_permissions()
    frappe.db.commit()


def create_fields():
    create_custom_fields(WAITER_FIELDS, ignore_validate=True)
    print("✅ Ofitsant custom fieldlari tayyor (POS Invoice)")


def create_permissions():
    granted = 0

    for doctype, roles in WAITER_PERMISSIONS.items():
        if not frappe.db.exists("DocType", doctype):
            continue

        for role, ptypes in roles.items():
            if not frappe.db.exists("Role", role):
                print(f"⏭️  Rol yo'q: {role}")
                continue

            existing = frappe.db.get_value(
                "Custom DocPerm",
                {"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0},
                "name",
            )
            if not existing:
                add_permission(doctype, role, 0)

            for ptype in ptypes:
                if frappe.db.get_value(
                    "Custom DocPerm",
                    {"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0},
                    ptype,
                ):
                    continue
                update_permission_property(doctype, role, 0, ptype, 1, validate=False)
                granted += 1

    frappe.clear_cache()
    print(f"✅ Ofitsant ruxsatlari tayyor ({granted} ta yangi)")
    return granted
