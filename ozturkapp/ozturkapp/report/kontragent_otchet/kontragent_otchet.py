# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""
Kontragent Otchet - Barcha kontragentlar bo'yicha sverka
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate
from urllib.parse import quote


def execute(filters=None):
    if not filters:
        filters = {}
    
    validate_filters(filters)
    
    columns = get_columns()
    data = get_data(filters)
    report_summary = get_report_summary(data)
    
    return columns, data, None, None, report_summary


def validate_filters(filters):
    if not filters.get("from_date"):
        frappe.throw(_("'Boshlanish sanasi' majburiy"))
    if not filters.get("to_date"):
        frappe.throw(_("'Tugash sanasi' majburiy"))
    if not filters.get("party_type"):
        frappe.throw(_("'Kontragent turi' majburiy"))
    if getdate(filters.get("from_date")) > getdate(filters.get("to_date")):
        frappe.throw(_("Boshlanish sanasi tugash sanasidan katta bo'lishi mumkin emas"))


def get_columns():
    return [
        {"label": _("Kontragent turi"), "fieldname": "party_type", "fieldtype": "Data", "width": 110},
        {"label": _("Kontragent"), "fieldname": "party", "fieldtype": "Dynamic Link", "options": "party_type", "width": 180},
        {"label": _("Kompaniya"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 150},
        {"label": _("Akt Sverka"), "fieldname": "akt_sverka", "fieldtype": "HTML", "width": 100},
        {"label": _("Kredit (dan oldin)"), "fieldname": "opening_credit", "fieldtype": "Currency", "width": 140},
        {"label": _("Debet (dan oldin)"), "fieldname": "opening_debit", "fieldtype": "Currency", "width": 140},
        {"label": _("Kredit (davr)"), "fieldname": "period_credit", "fieldtype": "Currency", "width": 130},
        {"label": _("Debet (davr)"), "fieldname": "period_debit", "fieldtype": "Currency", "width": 130},
        {"label": _("So'nggi Kredit"), "fieldname": "closing_credit", "fieldtype": "Currency", "width": 130},
        {"label": _("So'nggi Debet"), "fieldname": "closing_debit", "fieldtype": "Currency", "width": 130}
    ]


def get_data(filters):
    party_type = filters.get("party_type")
    party = filters.get("party")
    from_date = filters.get("from_date")
    to_date = filters.get("to_date")
    company = filters.get("company")

    results = _query_party_based(party_type, party, company, from_date, to_date)

    data = []
    totals = {"opening_credit": 0, "opening_debit": 0, "period_credit": 0, "period_debit": 0, "closing_credit": 0, "closing_debit": 0}

    for r in results:
        opening_balance = flt(r.opening_debit_raw) - flt(r.opening_credit_raw)
        opening_debit = opening_balance if opening_balance > 0 else 0
        opening_credit = abs(opening_balance) if opening_balance < 0 else 0

        period_debit = flt(r.period_debit)
        period_credit = flt(r.period_credit)

        closing_balance = opening_balance + period_debit - period_credit
        closing_debit = closing_balance if closing_balance > 0 else 0
        closing_credit = abs(closing_balance) if closing_balance < 0 else 0

        akt_link = (
            f'<a href="/app/query-report/Akt%20Sverka?'
            f'from_date={from_date}&to_date={to_date}'
            f'&company={quote(str(r.company or ""))}'
            f'&party_type={quote(str(party_type))}'
            f'&party={quote(str(r.entity or ""))}" '
            f'class="btn btn-xs btn-info">Akt Sverka</a>'
        )

        data.append({
            "party_type": party_type,
            "party": r.entity,
            "company": r.company,
            "akt_sverka": akt_link,
            "opening_credit": opening_credit,
            "opening_debit": opening_debit,
            "period_credit": period_credit,
            "period_debit": period_debit,
            "closing_credit": closing_credit,
            "closing_debit": closing_debit
        })
        
        totals["opening_credit"] += opening_credit
        totals["opening_debit"] += opening_debit
        totals["period_credit"] += period_credit
        totals["period_debit"] += period_debit
        totals["closing_credit"] += closing_credit
        totals["closing_debit"] += closing_debit
    
    # JAMI qatorini boshiga qo'shish
    if data:
        data.insert(0, {
            "party_type": "",
            "party": "JAMI",
            "company": "",
            "akt_sverka": "",
            "opening_credit": totals["opening_credit"],
            "opening_debit": totals["opening_debit"],
            "period_credit": totals["period_credit"],
            "period_debit": totals["period_debit"],
            "closing_credit": totals["closing_credit"],
            "closing_debit": totals["closing_debit"],
            "bold": 1
        })
    
    return data


def _agg_columns():
    """Opening/period uchun umumiy SUM(CASE...) ifodalari."""
    return """
        SUM(CASE WHEN gle.posting_date < %(from_date)s THEN gle.debit ELSE 0 END) as opening_debit_raw,
        SUM(CASE WHEN gle.posting_date < %(from_date)s THEN gle.credit ELSE 0 END) as opening_credit_raw,
        SUM(CASE WHEN gle.posting_date BETWEEN %(from_date)s AND %(to_date)s THEN gle.debit ELSE 0 END) as period_debit,
        SUM(CASE WHEN gle.posting_date BETWEEN %(from_date)s AND %(to_date)s THEN gle.credit ELSE 0 END) as period_credit
    """


def _query_party_based(party_type, party, company, from_date, to_date):
    """Customer/Supplier/Employee/Shareholder — party asosida, company bo'yicha ajratib."""
    conditions = ["gle.party_type = %(party_type)s", "gle.is_cancelled = 0"]
    if party:
        conditions.append("gle.party = %(party)s")
    if company:
        conditions.append("gle.company = %(company)s")

    query = f"""
        SELECT gle.party AS entity, gle.company, {_agg_columns()}
        FROM `tabGL Entry` gle
        WHERE {" AND ".join(conditions)}
        GROUP BY gle.party, gle.company
        ORDER BY gle.party, gle.company
    """
    return frappe.db.sql(query, {
        "party_type": party_type, "party": party, "company": company,
        "from_date": from_date, "to_date": to_date,
    }, as_dict=True)


def get_report_summary(data):
    if not data:
        return []
    
    jami = data[0] if data else {}
    total_closing = flt(jami.get("closing_debit", 0)) - flt(jami.get("closing_credit", 0))
    
    return [
        {"value": jami.get("opening_debit", 0) - jami.get("opening_credit", 0), "label": _("Boshlang'ich qoldiq"), "datatype": "Currency", "indicator": "blue"},
        {"value": jami.get("period_debit", 0), "label": _("Davr debeti"), "datatype": "Currency", "indicator": "orange"},
        {"value": jami.get("period_credit", 0), "label": _("Davr krediti"), "datatype": "Currency", "indicator": "green"},
        {"value": total_closing, "label": _("Yakuniy qoldiq"), "datatype": "Currency", "indicator": "red" if total_closing < 0 else "blue"}
    ]