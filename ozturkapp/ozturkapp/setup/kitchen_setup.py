# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Oshxona (KDS) uchun sozlash — custom fieldlar, rol va ruxsatlar (TZ §21, §22, §15).

NEGA CUSTOM FIELD
=================
`URY KOT Items` da mahsulot darajasidagi tayyorlash holati UMUMAN YO'Q.
URY faqat KOT darajasida `order_status` ni ("Ready For Prepare" -> "Served")
saqlaydi. TZ §4 esa HAR BIR MAHSULOT uchun alohida holat talab qiladi.

TZ §21/§22 ga ko'ra yangi DocType YARATILMAYDI — mavjud `URY KOT Items`
ga minimal custom field qo'shiladi.

`allow_on_submit = 1` MAJBURIY
==============================
`URY KOT` yaratilishi bilan submit qilinadi (`kot_doc.insert(); kot_doc.submit()`).
Submit qilingan hujjatning bola jadvali odatdagi `save()` bilan o'zgarmaydi —
shuning uchun bu maydonlar `allow_on_submit` bo'lishi shart.

`order_status` GA TEGILMAYDI
============================
URY'ning Mosaic KDS'i (`/URYMosaic`) `kot_list()` orqali
`order_status == "Ready For Prepare"` bo'yicha filtrlaydi. Uning
semantikasini o'zgartirsak Mosaic buziladi (TZ §18/#18, #19).
Biz `order_status` ni faqat URY'ning o'zi kabi — hamma mahsulot berilganda
"Served" ga o'tkazamiz.

Ishga tushirish::

    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.kitchen_setup.setup
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.permissions import add_permission, update_permission_property

#: Oshxona xodimi roli. URY'da oshxonaga oid rol YO'Q (faqat Cashier,
#: Captain, Manager), shuning uchun bu dublikat emas (TZ §15).
KITCHEN_ROLE = "URY Kitchen"

#: Oshxona ekraniga kira oladigan rollar.
KITCHEN_ROLES = (KITCHEN_ROLE, "URY Manager", "System Manager")

#: Mahsulot holatlari — `utils/kitchen_status.py` bilan bir xil bo'lishi shart.
STATUS_OPTIONS = "Pending\nPreparing\nReady\nServed\nCancelled"


KITCHEN_FIELDS = {
    "URY KOT Items": [
        {
            "fieldname": "custom_kitchen_status",
            "label": "Kitchen Status",
            "fieldtype": "Select",
            "options": STATUS_OPTIONS,
            "default": "Pending",
            "in_list_view": 1,
            "allow_on_submit": 1,
            "insert_after": "comments",
            "description": (
                "Mahsulotning tayyorlanish holati. Faqat oshxona ekrani orqali "
                "o'zgartiriladi — o'tishlar serverda tekshiriladi."
            ),
        },
        {
            "fieldname": "custom_started_at",
            "label": "Started At",
            "fieldtype": "Datetime",
            "read_only": 1,
            "allow_on_submit": 1,
            "insert_after": "custom_kitchen_status",
        },
        {
            "fieldname": "custom_ready_at",
            "label": "Ready At",
            "fieldtype": "Datetime",
            "read_only": 1,
            "allow_on_submit": 1,
            "insert_after": "custom_started_at",
        },
        {
            "fieldname": "custom_served_at",
            "label": "Served At",
            "fieldtype": "Datetime",
            "read_only": 1,
            "allow_on_submit": 1,
            "insert_after": "custom_ready_at",
        },
        {
            # Audit (TZ §33) — Frappe'ning Version mexanizmi bola jadval
            # `db_set` o'zgarishlarini yozmaydi, shuning uchun kim
            # o'zgartirganini o'zimiz saqlaymiz.
            "fieldname": "custom_status_changed_by",
            "label": "Status Changed By",
            "fieldtype": "Link",
            "options": "User",
            "read_only": 1,
            "allow_on_submit": 1,
            "insert_after": "custom_served_at",
        },
    ]
}


#: Oshxona xodimi NIMANI ko'ra oladi. Narx, mijoz, to'lov, buxgalteriya —
#: YO'Q (TZ §15, §27). `URY KOT` ga `write` beriladi, chunki mahsulot
#: holati bola jadvalda turadi; `submit`/`cancel`/`create` BERILMAYDI.
KITCHEN_PERMISSIONS = {
    "URY KOT": {KITCHEN_ROLE: ["read", "write", "print", "report"]},
    "URY Production Unit": {KITCHEN_ROLE: ["read"]},
    "URY Table": {KITCHEN_ROLE: ["read"]},
    "URY Menu Course": {KITCHEN_ROLE: ["read"]},
    "Item": {KITCHEN_ROLE: ["read"]},
}


def setup():
    """Custom fieldlar + rol + ruxsatlar (idempotent)."""
    create_fields()
    create_role()
    create_permissions()
    frappe.db.commit()


def create_fields():
    create_custom_fields(KITCHEN_FIELDS, ignore_validate=True)
    print("✅ Oshxona custom fieldlari tayyor (URY KOT Items)")


def create_role():
    if frappe.db.exists("Role", KITCHEN_ROLE):
        print(f"⏭️  Rol allaqachon bor: {KITCHEN_ROLE}")
        return

    frappe.get_doc(
        {
            "doctype": "Role",
            "role_name": KITCHEN_ROLE,
            "desk_access": 1,
            "is_custom": 1,
        }
    ).insert(ignore_permissions=True)
    print(f"✅ Rol yaratildi: {KITCHEN_ROLE}")


def create_permissions():
    """Faqat KERAKLI bayroqlarni yoqadi, hech qachon o'chirmaydi."""
    granted = 0

    for doctype, roles in KITCHEN_PERMISSIONS.items():
        if not frappe.db.exists("DocType", doctype):
            print(f"⏭️  DocType yo'q: {doctype}")
            continue

        for role, ptypes in roles.items():
            if not frappe.db.exists("Role", role):
                continue

            existing = frappe.db.get_value(
                "Custom DocPerm",
                {"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0},
                "name",
            )
            if not existing:
                add_permission(doctype, role, 0)

            for ptype in ptypes:
                current = frappe.db.get_value(
                    "Custom DocPerm",
                    {"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0},
                    ptype,
                )
                if current:
                    continue
                update_permission_property(doctype, role, 0, ptype, 1, validate=False)
                granted += 1

    frappe.clear_cache()
    print(f"✅ Oshxona ruxsatlari tayyor ({granted} ta yangi)")
    return granted
