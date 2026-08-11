# Copyright (c) 2026, Ozturkapp and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, getdate


def execute(filters=None):
    filters = filters or {}
    validate_filters(filters)
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def validate_filters(filters):
    if not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw(_("Сана оралиғи мажбурий (from_date / to_date)"))
    if getdate(filters["from_date"]) > getdate(filters["to_date"]):
        frappe.throw(_("Бошланиш санаси тугаш санасидан катта бўлиши мумкин эмас"))


def get_columns():
    return [
        {"fieldname": "posting_date", "label": _("Сана"), "fieldtype": "Date", "width": 95},
        {"fieldname": "item_name", "label": _("Номланиши"), "fieldtype": "Data", "width": 220},
        {"fieldname": "qty", "label": _("Сони"), "fieldtype": "Float", "width": 80, "precision": 3},
        {"fieldname": "rate", "label": _("Нарх"), "fieldtype": "Currency", "width": 110},
        {"fieldname": "amount", "label": _("Сумма"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "customer", "label": _("Клиент"), "fieldtype": "Data", "width": 150},
        {"fieldname": "item_group", "label": _("Тип"), "fieldtype": "Data", "width": 120},
        {"fieldname": "cost_rate", "label": _("СС товар"), "fieldtype": "Currency", "width": 110},
        {"fieldname": "cost_amount", "label": _("СС Сумма"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "markup", "label": _("Наценка"), "fieldtype": "Percent", "width": 100},
        {"fieldname": "margin", "label": _("Маржа"), "fieldtype": "Percent", "width": 100},
        {"fieldname": "remarks", "label": _("Изоҳ"), "fieldtype": "Data", "width": 150},
        {"fieldname": "sales_invoice", "label": _("Ҳужжат"), "fieldtype": "Link", "options": "Sales Invoice", "width": 150},
    ]


def get_data(filters):
    conditions = ["si.docstatus = 1", "si.posting_date BETWEEN %(from_date)s AND %(to_date)s"]
    params = {"from_date": filters["from_date"], "to_date": filters["to_date"]}

    if filters.get("company"):
        conditions.append("si.company = %(company)s")
        params["company"] = filters["company"]
    if filters.get("customer"):
        conditions.append("si.customer = %(customer)s")
        params["customer"] = filters["customer"]
    if filters.get("branch"):
        conditions.append("si.branch = %(branch)s")
        params["branch"] = filters["branch"]
    if filters.get("item_group"):
        conditions.append("sii.item_group = %(item_group)s")
        params["item_group"] = filters["item_group"]

    where = " AND ".join(conditions)

    rows = frappe.db.sql(f"""
        SELECT
            si.posting_date,
            sii.item_name,
            sii.qty,
            sii.rate,
            sii.amount,
            IFNULL(si.customer_name, si.customer) AS customer,
            sii.item_group,
            sii.incoming_rate AS cost_rate,
            COALESCE(NULLIF(si.branch, ''), si.company) AS branch,
            si.remarks,
            sii.parent AS sales_invoice
        FROM `tabSales Invoice Item` sii
        INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
        WHERE {where}
        ORDER BY si.posting_date, si.name, sii.idx
    """, params, as_dict=True)

    data = []
    tot_amount = 0
    tot_cost = 0
    tot_qty = 0

    for r in rows:
        amount = flt(r.amount)
        cost_amount = flt(r.cost_rate) * flt(r.qty)
        profit = amount - cost_amount
        markup = (profit / cost_amount * 100) if cost_amount else None
        margin = (profit / amount * 100) if amount else None

        tot_amount += amount
        tot_cost += cost_amount
        tot_qty += flt(r.qty)

        data.append({
            "posting_date": r.posting_date,
            "item_name": r.item_name,
            "qty": flt(r.qty),
            "rate": flt(r.rate),
            "amount": amount,
            "customer": r.customer,
            "item_group": r.item_group,
            "cost_rate": flt(r.cost_rate),
            "cost_amount": cost_amount,
            "branch": r.branch,
            "markup": markup,
            "margin": margin,
            "remarks": r.remarks,
            "sales_invoice": r.sales_invoice,
        })

    if data:
        t_profit = tot_amount - tot_cost
        data.append({
            "item_name": _("ЖАМИ"),
            "qty": tot_qty,
            "amount": tot_amount,
            "cost_amount": tot_cost,
            "markup": (t_profit / tot_cost * 100) if tot_cost else None,
            "margin": (t_profit / tot_amount * 100) if tot_amount else None,
            "is_total": 1,
        })

    return data
