# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""URY rollari uchun Custom DocPerm'lar.

MUAMMO
======
`URY Cashier` roli URY app bilan birga keladi, lekin unga hech qanday hujjat
ruxsati BERILMAYDI — standart `POS Invoice` ruxsatlari faqat `Accounts User` /
`Accounts Manager` da. Natijada faqat `URY Cashier` roli bor kassir:

  * smena ocholmaydi   (POS Opening Entry — create yo'q)
  * chek yarata olmaydi (POS Invoice — create/submit yo'q)
  * menyu narxini ko'rmaydi (Item Price — read yo'q)
  * smena yopa olmaydi (POS Closing Entry — create/submit yo'q)

jazira.local'da bu ruxsatlar qo'lda (UI orqali) berilgan edi — ya'ni kodda
hech qayerda yo'q va yangi saytda takrorlanmaydi.

YECHIM
======
Ta'riflar shu yerda kodga yozilgan va after_migrate'da avtomatik qo'llanadi.

    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.ury_permissions.create_permissions

MUHIM: bu funksiya faqat KERAKLI bayroqlarni YOQADI. Hech qachon o'chirmaydi —
UI'dan qo'shilgan qo'shimcha ruxsatlar saqlanib qoladi.
"""

import frappe
from frappe.permissions import add_permission, update_permission_property

#: {doctype: {role: [yoqiladigan bayroqlar]}}
URY_PERMISSIONS = {
    # ── Kassa smenasi ────────────────────────────────────────────────────
    "POS Opening Entry": {
        "URY Cashier": ["read", "write", "create", "submit", "cancel", "delete"],
    },
    "POS Closing Entry": {
        "URY Cashier": ["read", "write", "create", "submit", "cancel", "delete"],
    },
    # ── Cheklar ──────────────────────────────────────────────────────────
    "POS Invoice": {
        "URY Cashier": ["read", "write", "create", "submit", "cancel", "amend", "delete"],
    },
    "Sales Invoice": {
        "URY Cashier": ["read", "write", "create", "submit", "cancel", "amend", "delete"],
    },
    # ── Ma'lumotnomalar ──────────────────────────────────────────────────
    # Item Price — menyu narxi shu jadvaldan o'qiladi (sync_order ham).
    # read bo'lmasa kassa ekranida narxlar 0 bo'lib ko'rinadi.
    "Item Price": {
        "URY Cashier": ["read"],
    },
    # Customer — POS'da mijoz tanlash va yangi mijoz qo'shish uchun
    "Customer": {
        "URY Cashier": ["read", "write"],
    },
    # ── Menejer ──────────────────────────────────────────────────────────
    "Employee": {
        "URY Manager": ["read", "write", "create"],
    },
}


def create_permissions():
    """URY rollariga kerakli hujjat ruxsatlarini beradi (idempotent)."""
    granted = 0
    skipped = 0

    for doctype, roles in URY_PERMISSIONS.items():
        if not frappe.db.exists("DocType", doctype):
            print(f"⏭️  DocType yo'q, o'tkazildi: {doctype}")
            continue

        for role, ptypes in roles.items():
            if not frappe.db.exists("Role", role):
                print(f"⏭️  Rol yo'q, o'tkazildi: {role}")
                continue

            existing = frappe.db.get_value(
                "Custom DocPerm",
                {"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0},
                "name",
            )
            if not existing:
                # add_permission avval standart DocPerm'larni Custom DocPerm'ga
                # ko'chiradi (setup_custom_perms), keyin yangi qator qo'shadi
                add_permission(doctype, role, 0)

            for ptype in ptypes:
                current = frappe.db.get_value(
                    "Custom DocPerm",
                    {"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0},
                    ptype,
                )
                if current:
                    skipped += 1
                    continue
                update_permission_property(doctype, role, 0, ptype, 1, validate=False)
                granted += 1

    frappe.clear_cache()
    print(f"✅ URY ruxsatlari tayyor ({granted} ta yangi, {skipped} ta allaqachon bor)")
    return granted
