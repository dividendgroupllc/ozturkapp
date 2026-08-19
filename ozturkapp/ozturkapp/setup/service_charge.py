# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Xizmat haqi (12%) sozlamasi — ERPNext mexanizmi bilan (TZ §8, §9).

    Daromad hisobi  "Service Charge"
            |
            v
    Sales Taxes and Charges Template  "Restaurant Service Charge"
            |  (On Net Total, 12%)
            v
    URY Restaurant.default_tax_template
            |
            v
    POS Invoice.taxes[]   <- ERPNext hisoblaydi
            |
            v
    Kassa oynasi          <- faqat ko'rsatadi

Foiz FAQAT SHU YERDA (shablon qatorida) turadi. Kodda ham, frontend'da ham
takrorlanmaydi — TZ §8 talabi shu.

Ishga tushirish::

    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.service_charge.setup

Foizni keyinchalik o'zgartirish uchun kodga tegish SHART EMAS — Desk'da
"Restaurant Service Charge" shablonidagi `rate` maydonini tahrirlash yetarli.

IDEMPOTENT: qayta-qayta ishga tushirish xavfsiz, mavjud sozlama buzilmaydi.
"""

import frappe
from frappe.utils import flt

from ozturkapp.ozturkapp.utils.cashier_billing import (
    DEFAULT_SERVICE_CHARGE_RATE,
    SERVICE_CHARGE_ACCOUNT_NAME,
    SERVICE_CHARGE_TEMPLATE_TITLE,
)

#: Xizmat haqi daromadi qaysi guruh ostiga tushadi.
PARENT_ACCOUNT_CANDIDATES = ("Direct Income", "Income")


def setup(rate=None):
    """Barcha URY restoranlari uchun xizmat haqini sozlaydi."""
    rate = flt(rate) or DEFAULT_SERVICE_CHARGE_RATE
    restaurants = frappe.get_all(
        "URY Restaurant", fields=["name", "company", "default_tax_template"]
    )
    if not restaurants:
        print("⏭️  URY Restaurant topilmadi — o'tkazildi")
        return

    _ensure_custom_field()

    for restaurant in restaurants:
        _setup_for_restaurant(restaurant, rate)

    frappe.db.commit()


def _setup_for_restaurant(restaurant, rate):
    company = restaurant.company
    if not company:
        print(f"⏭️  '{restaurant.name}' restoranida kompaniya ko'rsatilmagan")
        return

    account = _ensure_account(company)
    template = _ensure_template(company, account, rate)

    updates = {"custom_service_charge_account": account}

    # Restoranda boshqa shablon allaqachon tanlangan bo'lsa — TEGMAYMIZ,
    # chunki unda soliq qatorlari ham bo'lishi mumkin. Faqat bo'sh bo'lsa
    # o'zimiznikini qo'yamiz.
    if not restaurant.default_tax_template:
        updates["default_tax_template"] = template
        print(f"✅ '{restaurant.name}' -> {template}")
    else:
        print(
            f"⏭️  '{restaurant.name}' da shablon allaqachon bor: "
            f"{restaurant.default_tax_template} — tegilmadi"
        )
        if not _template_has_account(restaurant.default_tax_template, account):
            print(
                f"   ⚠️  Diqqat: '{restaurant.default_tax_template}' ichida "
                f"'{account}' qatori yo'q. Xizmat haqini qo'lda qo'shing."
            )

    frappe.db.set_value("URY Restaurant", restaurant.name, updates)


# ═══════════════════════════════════════════════════════════════════
#  Tarkibiy qismlar
# ═══════════════════════════════════════════════════════════════════

def _ensure_account(company: str) -> str:
    """Xizmat haqi daromad hisobi (mavjud bo'lsa qayta yaratilmaydi)."""
    abbr = frappe.db.get_value("Company", company, "abbr")
    name = f"{SERVICE_CHARGE_ACCOUNT_NAME} - {abbr}"

    if frappe.db.exists("Account", name):
        return name

    parent = _find_parent_account(company, abbr)
    if not parent:
        frappe.throw(
            f"'{company}' kompaniyasida daromad hisoblari guruhi topilmadi"
        )

    account = frappe.new_doc("Account")
    account.account_name = SERVICE_CHARGE_ACCOUNT_NAME
    account.parent_account = parent
    account.company = company
    account.root_type = "Income"
    account.report_type = "Profit and Loss"
    account.account_type = "Income Account"
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
        {"company": company, "root_type": "Income", "is_group": 1},
        "name",
    )


def _ensure_template(company: str, account: str, rate: float) -> str:
    """Soliq/yig'im shabloni. Mavjud bo'lsa — foizga TEGILMAYDI.

    Sabab: menejer Desk'dan foizni o'zgartirgan bo'lishi mumkin, migratsiya
    uni qaytarib qo'ymasligi kerak.
    """
    existing = frappe.db.get_value(
        "Sales Taxes and Charges Template",
        {"title": SERVICE_CHARGE_TEMPLATE_TITLE, "company": company},
        "name",
    )
    if existing:
        print(f"⏭️  Shablon bor: {existing}")
        return existing

    template = frappe.new_doc("Sales Taxes and Charges Template")
    template.title = SERVICE_CHARGE_TEMPLATE_TITLE
    template.company = company
    template.append(
        "taxes",
        {
            "charge_type": "On Net Total",
            "account_head": account,
            "description": f"Xizmat haqi {flt(rate):g}%",
            "rate": flt(rate),
            "cost_center": frappe.db.get_value(
                "Company", company, "cost_center"
            ),
        },
    )
    template.insert(ignore_permissions=True)

    print(f"✅ Shablon yaratildi: {template.name} ({flt(rate):g}%)")
    return template.name


def _template_has_account(template: str, account: str) -> bool:
    return bool(
        frappe.db.exists(
            "Sales Taxes and Charges",
            {"parent": template, "account_head": account},
        )
    )


def _ensure_custom_field():
    """`URY Restaurant.custom_service_charge_account` — qaysi qator xizmat haqi.

    Shablonda bir nechta qator bo'lishi mumkin (QQS, xizmat haqi, ...).
    Kassa oynasi qaysi birini "Xizmat haqi" deb ko'rsatishini shu maydon
    aniqlaydi — nom bo'yicha taxmin qilish ishonchsiz.
    """
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    create_custom_fields(
        {
            "URY Restaurant": [
                {
                    "fieldname": "custom_service_charge_account",
                    "label": "Service Charge Account",
                    "fieldtype": "Link",
                    "options": "Account",
                    "insert_after": "default_tax_template",
                    "description": (
                        "Qaysi soliq qatori 'Xizmat haqi' sifatida ko'rsatilishi. "
                        "Foizning o'zi soliq shablonida turadi."
                    ),
                }
            ]
        },
        ignore_validate=True,
    )
