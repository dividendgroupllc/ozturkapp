# Copyright (c) 2026, Ozturkapp and contributors
# For license information, please see license.txt

"""
Intercompany Sverka — guruh kompaniyalari o'rtasidagi o'zaro hisob-kitobni solishtirish.

Jazira'ning shu nomli hisobotidan moslashtirilgan; kompaniya nomlari
qattiq yozilmagan — juftliklar ichki kontragentlardan (Customer /
Supplier `represents_company`) va Company daraxtidan olinadi.

Ikki bo'lim
───────────
1. QOLDIQLAR (har bir yo'nalish A → B uchun):
     A kitobida — B ni ifodalovchi ichki MIJOZ (Customer) bo'yicha debitor qarz;
     B kitobida — A ni ifodalovchi ichki TA'MINOTCHI (Supplier) bo'yicha kreditor qarz.
   Ikkovi har doim teng bo'lishi kerak: Branch Stock Transfer (SI ↔ PI),
   Expense Allocation (Дт Debtors ↔ Кт Creditors) va to'lovlar ikkala
   kitobda ham yoziladi. Farq — bir tomonda hujjat tushib qolgan yoki
   bekor qilingan.

2. HUJJATLAR (davr ichida): ichki mijozga Sales Invoice ↔ unga bog'langan
   Purchase Invoice (`inter_company_invoice_reference`, BST shuni to'ldiradi),
   bog'lanmaganlari — sana + summa bo'yicha. Har qatorda Branch Stock Transfer
   havolasi. Jufti yo'q hujjat "⚠ jufti yo'q" bo'lib chiqadi.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from ozturkapp.ozturkapp.report.internal_parties import get_default_group_company, get_leaf_companies


def execute(filters=None):
    filters = frappe._dict(filters or {})
    validate_filters(filters)
    companies = get_leaf_companies(filters.get("company") or get_default_group_company())

    rows = build_balance_rows(filters, companies)
    rows += build_document_rows(filters, companies)
    return get_columns(), rows


def validate_filters(filters):
    if not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw(_("Сана оралиғи мажбурий"))
    if getdate(filters["from_date"]) > getdate(filters["to_date"]):
        frappe.throw(_("Бошланиш санаси тугаш санасидан катта бўлиши мумкин эмас"))


def get_columns():
    return [
        {"fieldname": "label", "label": _("Йўналиш / кўрсаткич"), "fieldtype": "Data", "width": 300},
        {"fieldname": "branch_stock_transfer", "label": _("Branch Stock Transfer"),
         "fieldtype": "Link", "options": "Branch Stock Transfer", "width": 150},
        {"fieldname": "sales_invoice", "label": _("Сотувчи ҳужжати"),
         "fieldtype": "Link", "options": "Sales Invoice", "width": 170},
        {"fieldname": "seller_amount", "label": _("Сотувчи (дебитор)"), "fieldtype": "Currency", "width": 150},
        {"fieldname": "purchase_invoice", "label": _("Олувчи ҳужжати"),
         "fieldtype": "Link", "options": "Purchase Invoice", "width": 170},
        {"fieldname": "buyer_amount", "label": _("Олувчи (кредитор)"), "fieldtype": "Currency", "width": 150},
        {"fieldname": "diff", "label": _("ФАРҚ"), "fieldtype": "Currency", "width": 130},
    ]


def _mk(label, row_type, indent, seller=None, buyer=None, **extra):
    row = {
        "label": label,
        "row_type": row_type,
        "indent": indent,
        "seller_amount": seller,
        "buyer_amount": buyer,
        "diff": (flt(seller) - flt(buyer)) if (seller is not None or buyer is not None) else None,
    }
    row.update(extra)
    return row


def _pass_pair(filters, seller, buyer):
    if filters.get("seller") and filters.seller != seller:
        return False
    if filters.get("buyer") and filters.buyer != buyer:
        return False
    return True


# ─── 1. Qoldiqlar ────────────────────────────────────────────────────────────

def _internal_parties(doctype, flag, companies):
    """{ifodalangan kompaniya: [kontragentlar]}."""
    result = {}
    for r in frappe.get_all(
        doctype,
        filters={flag: 1, "represents_company": ["in", companies]},
        fields=["name", "represents_company"],
    ):
        result.setdefault(r.represents_company, []).append(r.name)
    return result


def _party_balances(company, party_type, parties, from_date, to_date):
    """(boshlang'ich, davr debet, davr kredit, yakuniy) — debet − kredit ko'rinishida."""
    if not parties:
        return 0.0, 0.0, 0.0, 0.0
    r = frappe.db.sql("""
        SELECT
            SUM(CASE WHEN posting_date < %(from_date)s THEN debit - credit ELSE 0 END) AS opening,
            SUM(CASE WHEN posting_date >= %(from_date)s THEN debit ELSE 0 END) AS dr,
            SUM(CASE WHEN posting_date >= %(from_date)s THEN credit ELSE 0 END) AS cr
        FROM `tabGL Entry`
        WHERE company = %(company)s AND party_type = %(party_type)s AND party IN %(parties)s
          AND is_cancelled = 0 AND posting_date <= %(to_date)s
    """, {
        "company": company, "party_type": party_type, "parties": tuple(parties),
        "from_date": from_date, "to_date": to_date,
    }, as_dict=True)[0]
    opening, dr, cr = flt(r.opening), flt(r.dr), flt(r.cr)
    return opening, dr, cr, opening + dr - cr


def build_balance_rows(filters, companies):
    customers = _internal_parties("Customer", "is_internal_customer", companies)
    suppliers = _internal_parties("Supplier", "is_internal_supplier", companies)
    only_diff = cint(filters.get("only_differences"))

    rows = [_mk(_("ҚОЛДИҚЛАР (дебитор ↔ кредитор)"), "section", 0)]
    t_seller = t_buyer = 0.0
    for seller in companies:
        for buyer in companies:
            if seller == buyer or not _pass_pair(filters, seller, buyer):
                continue
            s_op, s_dr, s_cr, s_cl = _party_balances(
                seller, "Customer", customers.get(buyer), filters.from_date, filters.to_date)
            # Kreditor qarz: kredit − debet (musbat = biz qarzdormiz)
            b_op, b_dr, b_cr, b_cl = _party_balances(
                buyer, "Supplier", suppliers.get(seller), filters.from_date, filters.to_date)
            b_op, b_cl = -b_op, -b_cl
            if not any(abs(v) > 0.005 for v in (s_op, s_dr, s_cr, b_op, b_dr, b_cr)):
                continue
            if only_diff and abs(s_cl - b_cl) < 0.01 and abs(s_op - b_op) < 0.01:
                continue

            t_seller += s_cl
            t_buyer += b_cl
            rows.append(_mk(f"{seller}  →  {buyer}", "root", 1, s_cl, b_cl))
            rows.append(_mk(_("Бошланғич қолдиқ"), "detail", 2, s_op, b_op))
            rows.append(_mk(_("Давр: ҳисобланди (сотув, тақсимот)"), "detail", 2, s_dr, b_cr))
            rows.append(_mk(_("Давр: ёпилди (тўлов, қайтариш)"), "detail", 2, s_cr, b_dr))
            rows.append(_mk(_("Охирги қолдиқ"), "sub", 2, s_cl, b_cl))
            if not customers.get(buyer):
                rows.append(_mk(_("⚠ {0} учун ички мижоз йўқ").format(buyer), "warn", 2))
            if not suppliers.get(seller):
                rows.append(_mk(_("⚠ {0} учун ички таъминотчи йўқ").format(seller), "warn", 2))

    rows.append(_mk(_("Қолдиқлар жами"), "result", 1, t_seller, t_buyer))
    return rows


# ─── 2. Hujjatlar ────────────────────────────────────────────────────────────

def fetch_sales(filters, companies):
    """Ichki mijozga yozilgan Sales Invoice — sotuvchi tomoni."""
    return frappe.db.sql("""
        SELECT si.name, si.posting_date, si.company AS seller,
               c.represents_company AS buyer, si.base_grand_total AS amount,
               bst.name AS bst
        FROM `tabSales Invoice` si
        JOIN `tabCustomer` c ON c.name = si.customer
        LEFT JOIN `tabBranch Stock Transfer` bst ON bst.sales_invoice = si.name AND bst.docstatus = 1
        WHERE si.docstatus = 1
          AND c.is_internal_customer = 1
          AND c.represents_company IN %(companies)s
          AND si.company IN %(companies)s
          AND c.represents_company != si.company
          AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
        ORDER BY si.posting_date, si.name
    """, {"companies": tuple(companies), "from_date": filters.from_date, "to_date": filters.to_date},
        as_dict=True)


def fetch_purchases(filters, companies):
    """Ichki ta'minotchidan Purchase Invoice — oluvchi tomoni."""
    return frappe.db.sql("""
        SELECT pi.name, pi.posting_date, s.represents_company AS seller,
               pi.company AS buyer, pi.base_grand_total AS amount,
               IFNULL(pi.inter_company_invoice_reference, '') AS si_ref,
               bst.name AS bst
        FROM `tabPurchase Invoice` pi
        JOIN `tabSupplier` s ON s.name = pi.supplier
        LEFT JOIN `tabBranch Stock Transfer` bst ON bst.purchase_invoice = pi.name AND bst.docstatus = 1
        WHERE pi.docstatus = 1
          AND s.is_internal_supplier = 1
          AND s.represents_company IN %(companies)s
          AND pi.company IN %(companies)s
          AND s.represents_company != pi.company
          AND pi.posting_date BETWEEN %(from_date)s AND %(to_date)s
        ORDER BY pi.posting_date, pi.name
    """, {"companies": tuple(companies), "from_date": filters.from_date, "to_date": filters.to_date},
        as_dict=True)


def match_documents(sales, purchases):
    """SI ↔ PI juftlash: avval `inter_company_invoice_reference`, keyin sana+summa.

    Qaytaradi: (juftlar [(si, pi)], jufti yo'q SI'lar, jufti yo'q PI'lar)."""
    by_ref = {}
    rest = []
    for p in purchases:
        if p.si_ref:
            by_ref.setdefault(p.si_ref, []).append(p)
        else:
            rest.append(p)

    pairs, unmatched_sales = [], []
    pool = {}
    for p in rest:
        pool.setdefault((str(p.posting_date), round(flt(p.amount), 2)), []).append(p)

    for s in sales:
        if by_ref.get(s.name):
            pairs.append((s, by_ref[s.name].pop(0)))
            continue
        key = (str(s.posting_date), round(flt(s.amount), 2))
        if pool.get(key):
            pairs.append((s, pool[key].pop(0)))
        else:
            unmatched_sales.append(s)

    # Havolasi bor, lekin SI davrda topilmagan (yoki bekor) PI'lar ham jufti yo'q
    unmatched_purchases = [p for lst in pool.values() for p in lst]
    unmatched_purchases += [p for lst in by_ref.values() for p in lst]
    return pairs, unmatched_sales, unmatched_purchases


def build_document_rows(filters, companies):
    sales = fetch_sales(filters, companies)
    purchases = fetch_purchases(filters, companies)
    only_diff = cint(filters.get("only_differences"))

    rows = [{"label": "", "row_type": "divider", "indent": 0},
            _mk(_("ҲУЖЖАТЛАР (давр ичида: SI ↔ PI)"), "section", 0)]
    directions = sorted({(r.seller, r.buyer) for r in sales} | {(r.seller, r.buyer) for r in purchases})
    t_s = t_p = 0.0

    for seller, buyer in directions:
        if not _pass_pair(filters, seller, buyer):
            continue
        d_sales = [r for r in sales if (r.seller, r.buyer) == (seller, buyer)]
        d_purch = [r for r in purchases if (r.seller, r.buyer) == (seller, buyer)]
        s_amt = sum(flt(r.amount) for r in d_sales)
        p_amt = sum(flt(r.amount) for r in d_purch)
        pairs, u_sales, u_purch = match_documents(d_sales, d_purch)
        mismatched = [x for x in pairs if abs(flt(x[0].amount) - flt(x[1].amount)) >= 0.01]
        if only_diff and not (u_sales or u_purch or mismatched):
            continue

        t_s += s_amt
        t_p += p_amt
        rows.append(_mk(f"{seller}  →  {buyer}", "root", 1, s_amt, p_amt))

        shown = mismatched if only_diff else pairs
        for s, p in sorted(shown, key=lambda x: (str(x[0].posting_date), x[0].name)):
            rows.append(_mk(
                str(s.posting_date), "detail", 2, flt(s.amount), flt(p.amount),
                sales_invoice=s.name, purchase_invoice=p.name,
                branch_stock_transfer=s.bst or p.bst,
            ))
        for s in u_sales:
            rows.append(_mk(f"⚠ {s.posting_date} · жуфти йўқ", "warn", 2, flt(s.amount), 0,
                            sales_invoice=s.name, branch_stock_transfer=s.bst))
        for p in u_purch:
            rows.append(_mk(f"⚠ {p.posting_date} · жуфти йўқ", "warn", 2, 0, flt(p.amount),
                            purchase_invoice=p.name, branch_stock_transfer=p.bst))

    rows.append(_mk(_("Ҳужжатлар жами"), "result", 1, t_s, t_p))
    return rows
