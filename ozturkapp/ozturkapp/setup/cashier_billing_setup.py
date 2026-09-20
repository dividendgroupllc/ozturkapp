# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""To'lov, chegirma, qaytarish va choychaqa uchun maydonlar va hisob.

Bu modul yaratadi:

  * POS Invoice maydonlari — chegirma sababi/tasdiqlagan menejer, hisobni
    qayta chop etish belgisi, qaytarish sababi;
  * «Tips Payable» majburiyat hisobi — choychaqa soliq qatori shu hisobga
    tushadi (xizmat haqi va QQS'dan ALOHIDA, foydasi kassirniki emas —
    xodimlarga to'lanadigan qarz).

`after_migrate.run()` shu modulning `setup()` funksiyasini chaqiradi.
Har bir funksiya IDEMPOTENT (qayta-qayta migrate xavfsiz).

    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.cashier_billing_setup.setup
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from ozturkapp.ozturkapp.utils.cashier_billing import TIPS_ACCOUNT_NAME

#: Choychaqa hisobi qaysi guruh ostiga tushadi (kompaniyada birinchi topilgani).
PARENT_ACCOUNT_CANDIDATES = ("Current Liabilities",)

POS_INVOICE_FIELDS = {
    "POS Invoice": [
        {
            "fieldname": "custom_discount_reason",
            "label": "Discount Reason",
            "fieldtype": "Small Text",
            "insert_after": "discount_amount",
            "allow_on_submit": 1,
            "description": "Kassir kiritgan chegirma sababi (chegirma tarixi uchun).",
        },
        {
            "fieldname": "custom_discount_approved_by",
            "label": "Discount Approved By",
            "fieldtype": "Link",
            "options": "User",
            "read_only": 1,
            "allow_on_submit": 1,
            "insert_after": "custom_discount_reason",
            "description": (
                "Chegirma kassir chegarasidan oshgan bo'lsa — uni tasdiqlagan menejer."
            ),
        },
        {
            "fieldname": "custom_reprint_needed",
            "label": "Reprint Needed",
            "fieldtype": "Check",
            "default": "0",
            "no_copy": 1,
            "read_only": 1,
            "allow_on_submit": 1,
            "insert_after": "invoice_printed",
            "description": (
                "Hisob chop etilgandan keyin chegirma o'zgardi — qog'ozdagi chek "
                "eskirgan, mijozga yangisini chiqarish kerak."
            ),
        },
        {
            "fieldname": "custom_return_reason",
            "label": "Return Reason",
            "fieldtype": "Small Text",
            "no_copy": 1,
            "read_only": 1,
            "allow_on_submit": 1,
            "insert_after": "return_against",
            "depends_on": "eval:doc.is_return",
            "description": "Chekni qaytarish sababi (menejer tasdig'i bilan).",
        },
    ]
}


def setup():
    create_fields()
    frappe.db.commit()


def create_fields():
    """Custom Field'lar va choychaqa hisobi."""
    create_custom_fields(POS_INVOICE_FIELDS, ignore_validate=True)
    print("✅ Chegirma/qaytarish maydonlari tayyor (POS Invoice)")

    for company in _restaurant_companies():
        _ensure_tips_account(company)


def _restaurant_companies() -> list:
    """URY restoranlari ishlatadigan kompaniyalar."""
    return sorted(
        {
            company
            for company in frappe.get_all("URY Restaurant", pluck="company")
            if company
        }
    )


def _ensure_tips_account(company: str) -> str | None:
    """«Tips Payable - <abbr>» (majburiyat). Mavjud bo'lsa qayta yaratilmaydi."""
    abbr = frappe.db.get_value("Company", company, "abbr")
    name = f"{TIPS_ACCOUNT_NAME} - {abbr}"

    if frappe.db.exists("Account", name):
        return name

    parent = _find_parent_account(company, abbr)
    if not parent:
        print(f"⚠️  '{company}' kompaniyasida joriy majburiyatlar guruhi topilmadi — choychaqa hisobi yaratilmadi")
        return None

    account = frappe.new_doc("Account")
    account.account_name = TIPS_ACCOUNT_NAME
    account.parent_account = parent
    account.company = company
    account.root_type = "Liability"
    account.report_type = "Balance Sheet"
    account.is_group = 0
    account.insert(ignore_permissions=True)

    print(f"✅ Hisob yaratildi: {account.name}")
    return account.name


def _find_parent_account(company: str, abbr: str):
    for candidate in PARENT_ACCOUNT_CANDIDATES:
        name = f"{candidate} - {abbr}"
        if frappe.db.exists("Account", name):
            return name

    return frappe.db.get_value(
        "Account",
        {"company": company, "root_type": "Liability", "is_group": 1},
        "name",
    )
