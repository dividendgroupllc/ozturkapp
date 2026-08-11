# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Divident (ta'sischilarga to'lov) sozlamasi.

Jazira'da ta'sischilar nomi va hisob raqami (3200/3201) kodga qattiq yozilgan
edi. Bu yerda umumiy: har bir ta'sischi uchun

  1. «Divident <Ism>» nomli Party Type,
  2. Hisoblar rejasida AYNAN shu nomdagi Equity (kapital) hisobi

yaratiladi. Kassa hujjati Party Type nomiga qarab shu hisobni topadi —
hisob raqami ixtiyoriy bo'lishi mumkin.

Ishlatish:
    bench --site ozturk.local execute \\
        ozturkapp.ozturkapp.setup.dividend_setup.setup_dividend \\
        --kwargs "{'owner_name': 'Aziz', 'parent_account': 'Капитал - OZT'}"
"""

import frappe

DIVIDEND_PREFIX = "Divident"


def _dividend_name(owner_name: str) -> str:
    owner_name = (owner_name or "").strip()
    if not owner_name:
        frappe.throw("owner_name bo'sh bo'lmasligi kerak")
    if owner_name.startswith(DIVIDEND_PREFIX):
        return owner_name
    return f"{DIVIDEND_PREFIX} {owner_name}"


def create_dividend_party_type(owner_name: str) -> str:
    """«Divident <Ism>» Party Type yaratadi (idempotent). Nomini qaytaradi."""
    name = _dividend_name(owner_name)
    if frappe.db.exists("Party Type", name):
        print(f"⏭️  Party Type allaqachon bor: {name}")
        return name

    doc = frappe.new_doc("Party Type")
    doc.party_type = name
    doc.account_type = "Payable"
    doc.flags.ignore_links = True
    doc.insert(ignore_permissions=True)
    print(f"✅ Party Type yaratildi: {name}")
    return name


def create_dividend_account(owner_name: str, parent_account: str = None,
                            company: str = None, account_number: str = None) -> str:
    """«Divident <Ism>» nomli Equity hisobini yaratadi (idempotent)."""
    name = _dividend_name(owner_name)
    company = company or frappe.db.get_value("Company", {}, "name")
    if not company:
        frappe.throw("Company topilmadi — avval kompaniya yarating")

    existing = frappe.db.get_value(
        "Account",
        {"company": company, "account_name": name, "is_group": 0, "root_type": "Equity"},
        "name",
    )
    if existing:
        print(f"⏭️  Hisob allaqachon bor: {existing}")
        return existing

    if not parent_account:
        parent_account = frappe.db.get_value(
            "Account",
            {"company": company, "root_type": "Equity", "is_group": 1},
            "name",
            order_by="lft",
        )
    if not parent_account:
        frappe.throw(f"'{company}' uchun Equity guruh hisobi topilmadi — parent_account bering")

    acc = frappe.new_doc("Account")
    acc.account_name = name
    acc.parent_account = parent_account
    acc.company = company
    acc.root_type = "Equity"
    acc.report_type = "Balance Sheet"
    acc.is_group = 0
    if account_number:
        acc.account_number = account_number
    acc.insert(ignore_permissions=True)
    print(f"✅ Hisob yaratildi: {acc.name}")
    return acc.name


def setup_dividend(owner_name: str, parent_account: str = None,
                   company: str = None, account_number: str = None):
    """Bitta ta'sischi uchun to'liq sozlama: Party Type + Equity hisobi."""
    create_dividend_party_type(owner_name)
    create_dividend_account(owner_name, parent_account, company, account_number)
    frappe.db.commit()
    print(f"\n✅ '{_dividend_name(owner_name)}' divident sozlamasi tayyor")
