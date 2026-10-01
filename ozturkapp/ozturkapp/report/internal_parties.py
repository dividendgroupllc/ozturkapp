# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp and contributors
# For license information, please see license.txt

"""Guruh ichidagi (ichki) kontragent va hujjatlarni aniqlash — hisobotlar uchun umumiy.

Nega kerak
──────────
Sklad → filial tovar harakati `Branch Stock Transfer` orqali TAN NARXDA yuradi:
manba kompaniyada Sales Invoice (ichki mijozga), maqsad kompaniyada Purchase
Invoice (ichki ta'minotchidan). Buxgalteriya uchun bu oddiy savdo/xarid, lekin
biznes uchun — tovarning bir cho'ntakdan ikkinchisiga o'tishi. Hisobotlar uni
haqiqiy sotuv/xarid bilan aralashtirsa: sotuv "shishadi", tannarx noto'g'ri
chiqadi, xarid hisobotida sklad "ta'minotchi" bo'lib ko'rinadi.

Kassa/BST uchun umumiy yordamchilar (Sklad kompaniyasi, X kompaniyani
ifodalovchi kontragent) — `utils/intercompany.py`. Bu modul faqat HISOBOTLAR
uchun: ko'p qatorni bir so'rovda tekshirish (to'plamlar, SQL bo'laklari).

Ichki deb nima hisoblanadi (kompaniya NOMIGA bog'lanmagan):
  * Customer.is_internal_customer = 1 / Supplier.is_internal_supplier = 1;
  * submit qilingan Branch Stock Transfer'ga bog'langan SI/PI (zaxira yo'l —
    kontragent bayrog'i keyin o'chirilsa ham hujjat ichki bo'lib qoladi).
"""

import frappe
from frappe import _

# Hisobotlarda ichki kontragentlar toifasi (lotin va kirill yozuvli hisobotlar uchun)
INTERNAL_LABEL = "Ichki (filial/sklad)"
INTERNAL_LABEL_CYR = "Ички (филиал/склад)"

# SQL bo'laklari — har biri o'zi to'liq (tashqi parametr talab qilmaydi)
INTERNAL_CUSTOMERS_SQL = "SELECT name FROM `tabCustomer` WHERE is_internal_customer = 1"
INTERNAL_SUPPLIERS_SQL = "SELECT name FROM `tabSupplier` WHERE is_internal_supplier = 1"
BST_SALES_INVOICES_SQL = (
    "SELECT sales_invoice FROM `tabBranch Stock Transfer` "
    "WHERE docstatus = 1 AND IFNULL(sales_invoice, '') != ''"
)
BST_PURCHASE_INVOICES_SQL = (
    "SELECT purchase_invoice FROM `tabBranch Stock Transfer` "
    "WHERE docstatus = 1 AND IFNULL(purchase_invoice, '') != ''"
)

# Hujjat turi → (kontragent turi, kontragent maydoni)
VOUCHER_PARTY = {
    "Sales Invoice": ("Customer", "customer"),
    "POS Invoice": ("Customer", "customer"),
    "Delivery Note": ("Customer", "customer"),
    "Purchase Invoice": ("Supplier", "supplier"),
    "Purchase Receipt": ("Supplier", "supplier"),
}


def internal_customer_sql(customer_col, voucher_col=None):
    """`customer_col` ichki mijozmi (yoki `voucher_col` BST yaratgan SI'mi) — SQL shart."""
    cond = f"{customer_col} IN ({INTERNAL_CUSTOMERS_SQL})"
    if voucher_col:
        cond += f" OR {voucher_col} IN ({BST_SALES_INVOICES_SQL})"
    return f"({cond})"


def internal_supplier_sql(supplier_col, voucher_col=None):
    """`supplier_col` ichki ta'minotchimi (yoki `voucher_col` BST yaratgan PI'mi) — SQL shart."""
    cond = f"{supplier_col} IN ({INTERNAL_SUPPLIERS_SQL})"
    if voucher_col:
        cond += f" OR {voucher_col} IN ({BST_PURCHASE_INVOICES_SQL})"
    return f"({cond})"


def internal_gl_sql(alias="gle"):
    """GL Entry qatori ichki harakatga tegishlimi — SQL shart.

    Xarajat/daromad qatorlarida odatda kontragent bo'lmaydi (u faqat debitor/
    kreditor qatorida turadi), shuning uchun HUJJAT darajasida ham tekshiriladi:
    faktura kontragenti, Payment Entry kontragenti, Journal Entry'ning istalgan
    qatoridagi kontragent.
    """
    a = alias
    return f"""(
        ({a}.party_type = 'Customer' AND {a}.party IN ({INTERNAL_CUSTOMERS_SQL}))
        OR ({a}.party_type = 'Supplier' AND {a}.party IN ({INTERNAL_SUPPLIERS_SQL}))
        OR ({a}.voucher_type = 'Sales Invoice' AND {a}.voucher_no IN (
            SELECT name FROM `tabSales Invoice` WHERE customer IN ({INTERNAL_CUSTOMERS_SQL})))
        OR ({a}.voucher_type = 'Purchase Invoice' AND {a}.voucher_no IN (
            SELECT name FROM `tabPurchase Invoice` WHERE supplier IN ({INTERNAL_SUPPLIERS_SQL})))
        OR ({a}.voucher_type = 'Payment Entry' AND {a}.voucher_no IN (
            SELECT name FROM `tabPayment Entry`
            WHERE (party_type = 'Customer' AND party IN ({INTERNAL_CUSTOMERS_SQL}))
               OR (party_type = 'Supplier' AND party IN ({INTERNAL_SUPPLIERS_SQL}))))
        OR ({a}.voucher_type = 'Journal Entry' AND {a}.voucher_no IN (
            SELECT parent FROM `tabJournal Entry Account`
            WHERE (party_type = 'Customer' AND party IN ({INTERNAL_CUSTOMERS_SQL}))
               OR (party_type = 'Supplier' AND party IN ({INTERNAL_SUPPLIERS_SQL}))))
        OR ({a}.voucher_type = 'Sales Invoice' AND {a}.voucher_no IN ({BST_SALES_INVOICES_SQL}))
        OR ({a}.voucher_type = 'Purchase Invoice' AND {a}.voucher_no IN ({BST_PURCHASE_INVOICES_SQL}))
    )"""


def get_internal_parties():
    """{"Customer": {nom: represents_company}, "Supplier": {...}} — ichki kontragentlar.

    Lug'at — `party in result["Customer"]` tekshiruvi ham, ifodalangan
    kompaniya nomi ham (yorliq uchun) bitta so'rovdan."""
    return {
        "Customer": {
            r.name: r.represents_company
            for r in frappe.get_all("Customer", filters={"is_internal_customer": 1},
                                    fields=["name", "represents_company"])
        },
        "Supplier": {
            r.name: r.represents_company
            for r in frappe.get_all("Supplier", filters={"is_internal_supplier": 1},
                                    fields=["name", "represents_company"])
        },
    }


def is_internal_party(party_type, party, parties=None):
    if not party or party_type not in ("Customer", "Supplier"):
        return False
    parties = parties if parties is not None else get_internal_parties()
    return party in parties.get(party_type, ())


def get_internal_vouchers(vouchers):
    """Berilgan hujjatlardan qaysilari ichki — {(voucher_type, voucher_no)} to'plami.

    Args:
        vouchers: (voucher_type, voucher_no) juftliklari (istalgan iterable).
    """
    by_type = {}
    for vt, vn in vouchers:
        if vt in VOUCHER_PARTY and vn:
            by_type.setdefault(vt, set()).add(vn)
    if not by_type:
        return set()

    parties = get_internal_parties()
    result = set()
    for vt, names in by_type.items():
        party_type, field = VOUCHER_PARTY[vt]
        internal = parties[party_type]
        for r in frappe.get_all(vt, filters={"name": ["in", list(names)]}, fields=["name", field]):
            if r.get(field) in internal:
                result.add((vt, r.name))

    # BST bog'lagan SI/PI — kontragent bayrog'idan qat'i nazar
    for field, vt in (("sales_invoice", "Sales Invoice"), ("purchase_invoice", "Purchase Invoice")):
        names = by_type.get(vt)
        if not names:
            continue
        for name in frappe.get_all(
            "Branch Stock Transfer",
            filters={"docstatus": 1, field: ["in", list(names)]},
            pluck=field,
        ):
            result.add((vt, name))
    return result


def get_leaf_companies(company=None):
    """`company` ostidagi ishchi (guruh bo'lmagan) kompaniyalar — Company daraxti bo'yicha.

    `company` guruh bo'lmasa — faqat o'zi. Berilmasa — barcha ishchi kompaniyalar.
    Nomlar qattiq yozilmaydi: yangi filial ochilsa o'zi qo'shilib chiqadi.
    """
    if not company:
        return frappe.get_all("Company", filters={"is_group": 0}, pluck="name", order_by="name")
    bounds = frappe.db.get_value("Company", company, ["lft", "rgt", "is_group"], as_dict=True)
    if not bounds:
        frappe.throw(_("Kompaniya topilmadi: {0}").format(company))
    if not bounds.is_group:
        return [company]
    return frappe.get_all(
        "Company",
        filters={"lft": [">=", bounds.lft], "rgt": ["<=", bounds.rgt], "is_group": 0},
        pluck="name",
        order_by="name",
    )


def get_default_group_company():
    """Hisobot uchun standart guruh kompaniya: foydalanuvchi kompaniyasining
    eng yuqori ajdodi (guruh), bo'lmasa birinchi ildiz guruh."""
    company = frappe.defaults.get_user_default("Company")
    while company:
        parent = frappe.db.get_value("Company", company, "parent_company")
        if not parent:
            break
        company = parent
    if company and frappe.db.get_value("Company", company, "is_group"):
        return company
    return frappe.db.get_value("Company", {"is_group": 1, "parent_company": ["is", "not set"]}, "name")


# =============================================================================
# XARAJAT TAQSIMOTI (Expense Allocation) JE'LARI
# =============================================================================
#
# `Expense Allocation` doctype'i oy oxirida Sklad ma'muriy xarajatini
# filiallarga qayta yozadi: Sklad kitobida Кт xarajat (Дт Debtors[ichki
# mijoz]), filial kitobida Дт xarajat (Кт Creditors[ichki ta'minotchi]).
# Bu JE'lar `Journal Entry.custom_expense_allocation` bilan belgilanadi.
# Guruh darajasida ular NOLGA teng (Sklad −X, filiallar +X); kompaniya
# darajasida esa xarajatni bir joydan ikkinchisiga ko'chiradi — shuning
# uchun hisobotlarda alohida toifa sifatida ko'rsatiladi.

ALLOCATION_LABEL = "Taqsimlangan (sklad xarajati)"
ALLOCATION_LABEL_CYR = "Тақсимланган (склад харажати)"
ALLOCATION_LINK_FIELD = "custom_expense_allocation"
ALLOCATION_REMARK_PREFIX = "Expense Allocation:"


def has_allocation_field():
    return bool(frappe.db.has_column("Journal Entry", ALLOCATION_LINK_FIELD))


def allocation_gl_sql(alias="gle"):
    """GL Entry qatori Expense Allocation JE'siga tegishlimi — SQL shart.

    Maydon hali migrate qilinmagan muhitda — izoh prefiksi bo'yicha (zaxira)."""
    link_cond = f"IFNULL(je.{ALLOCATION_LINK_FIELD}, '') != '' OR " if has_allocation_field() else ""
    return f"""({alias}.voucher_type = 'Journal Entry' AND {alias}.voucher_no IN (
        SELECT je.name FROM `tabJournal Entry` je
        WHERE {link_cond}IFNULL(je.user_remark, '') LIKE '{ALLOCATION_REMARK_PREFIX}%%'))"""
