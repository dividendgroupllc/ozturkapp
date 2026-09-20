# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Smena, kassa harakati va hisobot uchun maydonlar va hisoblar.

`after_migrate.run()` shu modulning `setup()` funksiyasini chaqiradi.
Har bir funksiya IDEMPOTENT (qayta-qayta migrate xavfsiz).

KASSA HARAKATI HISOBI
=====================
Kassadan chiqarilgan/kiritilgan pul (`Ozturk Cash Movement`) Journal Entry
bilan yoziladi, uning bir tomoni — naqd usul hisobi, ikkinchi tomoni —
«qarshi hisob». Qarshi hisobni kod tanlay olmaydi: pulning nima uchun
chiqqani (xarajat, inkassatsiya, ...) buxgalter qarori. Shuning uchun:

  1. `Company.custom_cash_movement_account` — sozlanadigan maydon;
  2. u BO'SH bo'lsa, bu yerda «Kassa harakati» oraliq hisobi (Asset,
     «Current Assets» ostida) yaratiladi va maydonga yoziladi. Buxgalter
     uni oyda bir marta haqiqiy xarajat/seyf hisobiga o'tkazadi yoki
     maydonni boshqa hisobga almashtiradi.

Maydon ALLAQACHON to'ldirilgan bo'lsa unga TEGILMAYDI. Hisoblar rejasiga
faqat shu yerda (migratsiyada, ko'rinadigan tarzda) hisob qo'shiladi —
kassirning so'rovi ichida hech qachon.

    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.cashier_shift_setup.setup
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from ozturkapp.ozturkapp.doctype.ozturk_cash_movement.ozturk_cash_movement import (
    COMPANY_ACCOUNT_FIELD,
)

#: Oraliq hisob nomi (Frappe unga « - <abbr>» qo'shadi).
CLEARING_ACCOUNT_NAME = "Kassa harakati"

#: Yangi hisob qo'yiladigan guruh (ERPNext standart hisoblar rejasida bor).
CLEARING_PARENT_NAME = "Current Assets"

COMPANY_FIELDS = {
    "Company": [
        {
            "fieldname": COMPANY_ACCOUNT_FIELD,
            "label": "Kassa harakati hisobi",
            "fieldtype": "Link",
            "options": "Account",
            "insert_after": "default_cash_account",
            "description": (
                "Kassadan chiqarilgan/kiritilgan pul (xarajat, inkassatsiya, kassaga "
                "qo'shish) Journal Entry'ning shu hisobiga yoziladi. Guruh bo'lmagan "
                "hisob tanlang."
            ),
        }
    ]
}


def setup():
    create_fields()
    create_clearing_accounts()
    frappe.db.commit()


def create_fields():
    create_custom_fields(COMPANY_FIELDS, ignore_validate=True)
    print("✅ Kompaniya maydoni tayyor (Company.custom_cash_movement_account)")


def create_clearing_accounts():
    """POS Profile'i bor har bir kompaniya uchun oraliq hisobni ta'minlaydi."""
    for company in sorted(set(frappe.get_all("POS Profile", pluck="company"))):
        account = ensure_clearing_account(company)
        if account and not frappe.db.get_value("Company", company, COMPANY_ACCOUNT_FIELD):
            frappe.db.set_value("Company", company, COMPANY_ACCOUNT_FIELD, account)
            print(f"✅ '{company}': kassa harakati hisobi -> {account}")


def ensure_clearing_account(company: str):
    """«Kassa harakati - <abbr>» hisobini topadi yoki yaratadi. Nomini qaytaradi.

    Guruh topilmasa (hisoblar rejasi nostandart) hisob YARATILMAYDI —
    o'ylab topilgan joyga qo'yish rejani buzadi. Bunda `None` qaytadi va
    buxgalter maydonni qo'lda tanlaydi.
    """
    abbr = frappe.get_cached_value("Company", company, "abbr")
    name = f"{CLEARING_ACCOUNT_NAME} - {abbr}"
    if frappe.db.exists("Account", name):
        return name

    parent = frappe.db.get_value(
        "Account",
        {"company": company, "account_name": CLEARING_PARENT_NAME, "is_group": 1},
        "name",
    )
    if not parent:
        print(
            f"⚠️  '{company}': «{CLEARING_PARENT_NAME}» guruhi topilmadi — "
            f"«{CLEARING_ACCOUNT_NAME}» hisobi yaratilmadi. "
            "Company → «Kassa harakati hisobi» maydonini qo'lda tanlang."
        )
        return None

    account = frappe.get_doc(
        {
            "doctype": "Account",
            "account_name": CLEARING_ACCOUNT_NAME,
            "company": company,
            "parent_account": parent,
            "is_group": 0,
        }
    )
    account.insert(ignore_permissions=True)
    print(f"✅ '{company}': hisob yaratildi -> {account.name}")
    return account.name
