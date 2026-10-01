# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kompaniyalararo (inter-company) yordamchilar: Sklad, kassa hisobi, ichki kontragent.

Tuzilma:  «O'zturk» (guruh, o'zi savdo qilmaydi)
            ├─ «O'zturk Sklad»          — markaziy ombor, supplierlardan xarid qiladi
            └─ «O'zturk Maksim Gorkiy»  — restoran filiali (POS)
          Keyinchalik yangi filiallar qo'shiladi.

NEGA ALOHIDA MODUL. Kassa, Branch Stock Transfer va hisobotlar bir xil savollarga
javob izlaydi: «Sklad qaysi kompaniya?», «bu kompaniyaning naqd kassasi qaysi
hisob?», «X kompaniyani Y kitobida qaysi kontragent ifodalaydi?». Javoblar
nomlarga qattiq yozilmaydi — faqat sozlamadan (`Ozturk Settings`) va
ma'lumotlardan (`represents_company`, `Mode of Payment Account`) olinadi.
"""

import frappe
from frappe import _

SETTINGS = "Ozturk Settings"


# =============================================================================
# SKLAD
# =============================================================================

def get_sklad_company():
    """Markaziy Sklad kompaniyasi (`Ozturk Settings`).

    `sklad_company` bo'sh bo'lsa — `sklad_warehouse` omborining kompaniyasi.
    Ikkalasi ham bo'sh bo'lsa None: Sklad orqali oqimlar o'chadi, oddiy
    (bir kompaniyali) kassa ishlayveradi.
    """
    if not frappe.db.exists("DocType", SETTINGS):
        return None
    company = frappe.db.get_single_value(SETTINGS, "sklad_company")
    if company:
        return company
    warehouse = frappe.db.get_single_value(SETTINGS, "sklad_warehouse")
    if warehouse:
        return frappe.db.get_value("Warehouse", warehouse, "company")
    return None


def is_group_company(company):
    """Guruh kompaniyasi (masalan «O'zturk») — o'zi savdo/kassa qilmaydi."""
    return bool(company and frappe.get_cached_value("Company", company, "is_group"))


# =============================================================================
# MODE OF PAYMENT -> (kompaniya, hisob)
# =============================================================================

def get_mop_accounts(mode_of_payment):
    """MoP'ning hisob qatorlari, `idx` tartibida (deterministik).

    Qaytaradi: [{"company", "account"}, ...] — faqat hisobi ko'rsatilganlar.
    """
    if not mode_of_payment:
        return []
    return frappe.get_all(
        "Mode of Payment Account",
        filters={
            "parent": mode_of_payment,
            "parenttype": "Mode of Payment",
            "default_account": ["is", "set"],
        },
        fields=["company", "default_account as account"],
        order_by="idx asc",
    )


def resolve_mop_account(mode_of_payment, company=None, throw=True):
    """MoP + kompaniya -> {"company", "account", "companies", "ambiguous"}.

    ESKI XATO. Avval birinchi tasodifiy qator olinardi (`get_value` tartibsiz),
    kompaniya ham shu qatordan chiqarilardi. «Нахт» uch kompaniyada hisobga
    ega bo'lgani uchun kassa goh bir, goh boshqa kompaniya kitobiga yozilardi.

    Endi:
      * kompaniya berilgan  -> AYNAN shu kompaniya qatori (yo'q bo'lsa xato);
      * berilmagan, bitta qator -> o'sha qator;
      * berilmagan, bir nechta -> guruh kompaniyalari chiqarib tashlanadi
        (ular kassa yuritmaydi); baribir bir nechta qolsa — kompaniya so'raladi.
    """
    rows = get_mop_accounts(mode_of_payment)
    companies = [r.company for r in rows]
    result = {"company": "", "account": "", "companies": companies, "ambiguous": False}

    if company:
        row = next((r for r in rows if r.company == company), None)
        if not row:
            if throw:
                frappe.throw(
                    _("'{0}' kassasi '{1}' kompaniyasi uchun hisobga ega emas. "
                      "Mode of Payment «Accounts» jadvalini tekshiring yoki boshqa kassani tanlang.").format(
                        mode_of_payment, company
                    ),
                    title=_("Kassa hisobi topilmadi"),
                )
            return result
        result.update(company=row.company, account=row.account)
        return result

    candidates = rows
    if len(candidates) > 1:
        candidates = [r for r in rows if not is_group_company(r.company)]

    if len(candidates) == 1:
        result.update(company=candidates[0].company, account=candidates[0].account)
        return result

    if not candidates:
        if throw:
            frappe.throw(_("'{0}' uchun hisob (Account) bog'lanmagan. "
                           "Mode of Payment sozlamalarini tekshiring.").format(mode_of_payment))
        return result

    result["ambiguous"] = True
    if throw:
        frappe.throw(
            _("'{0}' kassasi bir nechta kompaniyada hisobga ega ({1}). "
              "Avval «Kompaniya» maydonini tanlang.").format(
                mode_of_payment, ", ".join(c.company for c in candidates)
            ),
            title=_("Kompaniya tanlanmagan"),
        )
    return result


def get_company_cash(company):
    """Kompaniyaning naqd kassasi -> (account, mode_of_payment).

    Kompaniyalararo oqimda pulning IKKINCHI tarafi (masalan filial kassadan
    Sklad'ga to'lasa — Sklad kassasi) qaysi hisobga tushishini bilish kerak.
      1. `Ozturk Settings` -> «Kompaniya kassalari» jadvali (aniq sozlama);
      2. bo'lmasa `Company.default_cash_account` (MoP'siz).
    """
    if frappe.db.exists("DocType", "Ozturk Company Cash"):
        mop = frappe.db.get_value(
            "Ozturk Company Cash",
            {"parent": SETTINGS, "parenttype": SETTINGS, "company": company},
            "mode_of_payment",
        )
        if mop:
            info = resolve_mop_account(mop, company, throw=False)
            if info["account"]:
                return info["account"], mop
            frappe.throw(
                _("Ozturk Settings: '{0}' kompaniyasi kassasi '{1}' bu kompaniya uchun hisobga ega emas.").format(
                    company, mop
                )
            )

    account = frappe.get_cached_value("Company", company, "default_cash_account")
    if not account:
        frappe.throw(
            _("'{0}' kompaniyasining kassasi sozlanmagan. «Ozturk Settings» -> «Kompaniya kassalari» "
              "jadvaliga qo'shing (yoki Company -> Default Cash Account).").format(company),
            title=_("Kassa sozlanmagan"),
        )
    return account, None


# =============================================================================
# ICHKI KONTRAGENTLAR
# =============================================================================

def get_internal_customer(company):
    """`company` ni ifodalovchi ichki Customer (yo'q bo'lsa None)."""
    return frappe.db.get_value(
        "Customer", {"is_internal_customer": 1, "represents_company": company}, "name"
    )


def get_internal_supplier(company):
    """`company` ni ifodalovchi ichki Supplier (yo'q bo'lsa None)."""
    return frappe.db.get_value(
        "Supplier", {"is_internal_supplier": 1, "represents_company": company}, "name"
    )


def get_represented_company(party_type, party):
    """Kontragent qaysi kompaniyani ifodalaydi (faqat ichki Customer/Supplier)."""
    if party_type not in ("Customer", "Supplier") or not party:
        return None
    return frappe.db.get_value(party_type, party, "represents_company") or None


def intercompany_party(counterparty_company, our_company):
    """Qarshi kompaniya BIZNING kitobimizda qaysi kontragent bo'lib turadi.

    Savdo yo'nalishiga mos (Branch Stock Transfer shunday yozadi):
      * Sklad kitobida filial  -> Customer (filial Sklad'ga qarzdor, Debtors)
      * filial kitobida Sklad  -> Supplier (filial Sklad'ga qarzdor, Creditors)
      * filial <-> filial      -> Customer (o'zaro savdo yo'q)

    NEGA. Agar ikkala tarafda ham Customer ishlatilsa, filial kitobida Sklad
    BIR VAQTDA Debtors'da (kassa to'lovlari) va Creditors'da (BST fakturalari)
    o'tirib qoladi va qarz hech qachon yopilmaydi (Jazira'da aynan shu bo'lgan).

    Qaytaradi (party_type, party). Topilmasa xato.
    """
    sklad = get_sklad_company()
    if sklad and counterparty_company == sklad and our_company != sklad:
        supplier = get_internal_supplier(counterparty_company)
        if supplier:
            return "Supplier", supplier

    customer = get_internal_customer(counterparty_company)
    if not customer:
        frappe.throw(
            _("'{0}' kompaniyasini ifodalovchi ichki Customer topilmadi "
              "(is_internal_customer=1, represents_company). `bench migrate` ichki "
              "kontragentlarni yaratadi.").format(counterparty_company)
        )
    return "Customer", customer
