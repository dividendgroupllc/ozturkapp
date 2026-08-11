# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa moduli uchun boshlang'ich sozlama.

    bench --site ozturk.local execute ozturkapp.ozturkapp.setup.kassa_setup.run_full_setup
"""

import frappe

# Kassa «Kontragent turi» ro'yxatida chiqadigan qo'shimcha Party Type'lar
PARTY_TYPES = [
    {"party_type": "Расходы", "account_type": "Payable"},
]


def create_party_types():
    """Kassa uchun Party Type'larni yaratadi (idempotent)."""
    for pt in PARTY_TYPES:
        name = pt["party_type"]
        if frappe.db.exists("Party Type", name):
            print(f"⏭️  Party Type allaqachon bor: {name}")
            continue
        doc = frappe.new_doc("Party Type")
        doc.party_type = name
        doc.account_type = pt["account_type"]
        doc.flags.ignore_links = True
        doc.insert(ignore_permissions=True)
        print(f"✅ Party Type yaratildi: {name}")


def create_sample_filials(names=None):
    """Namuna Kassa Filial'lar yaratadi (ixtiyoriy).

    Bitta kompaniyali saytda «filial» = xarajatlarni guruhlash birligi
    (masalan zal, oshxona, ma'muriyat).
    """
    names = names or ["Restoran", "Oshxona", "Administrativ"]
    company = frappe.db.get_value("Company", {}, "name")

    for name in names:
        if frappe.db.exists("Kassa Filial", name):
            print(f"⏭️  Kassa Filial allaqachon bor: {name}")
            continue
        doc = frappe.new_doc("Kassa Filial")
        doc.filial_name = name
        doc.is_active = 1
        doc.company = company
        doc.insert(ignore_permissions=True)
        print(f"✅ Kassa Filial yaratildi: {name}")


def run_full_setup():
    print("=" * 50)
    print("KASSA MODULE SETUP")
    print("=" * 50)

    print("\n1. Party Type'lar...")
    create_party_types()

    print("\n2. Namuna filiallar...")
    create_sample_filials()

    frappe.db.commit()
    print("\n✅ SETUP TAYYOR")
