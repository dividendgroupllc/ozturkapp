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
        {"fieldname": "posting_date", "label": _("Сана"), "fieldtype": "Date", "width": 100},
        {"fieldname": "supplier", "label": _("Етказиб берувчи"), "fieldtype": "Data", "width": 180},
        {"fieldname": "item_name", "label": _("Товар номи"), "fieldtype": "Data", "width": 240},
        {"fieldname": "qty", "label": _("Дона"), "fieldtype": "Float", "width": 100, "precision": 3},
        {"fieldname": "rate", "label": _("Нархи"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "amount", "label": _("Суммаси"), "fieldtype": "Currency", "width": 140},
        {"fieldname": "company", "label": _("Компания"), "fieldtype": "Link", "options": "Company", "width": 150},
        {"fieldname": "purchase_invoice", "label": _("Ҳужжат"), "fieldtype": "Link", "options": "Purchase Invoice", "width": 170},
    ]


def get_data(filters):
    conditions = ["pi.docstatus = 1", "pi.posting_date BETWEEN %(from_date)s AND %(to_date)s"]
    params = {"from_date": filters["from_date"], "to_date": filters["to_date"]}

    if filters.get("company"):
        conditions.append("pi.company = %(company)s")
        params["company"] = filters["company"]
    if filters.get("supplier"):
        conditions.append("pi.supplier = %(supplier)s")
        params["supplier"] = filters["supplier"]

    where = " AND ".join(conditions)

    rows = frappe.db.sql(f"""
        SELECT
            pi.name AS purchase_invoice,
            pi.posting_date,
            pi.company,
            IFNULL(pi.supplier_name, pi.supplier) AS supplier,
            pii.item_name,
            pii.qty,
            pii.rate,
            pii.amount
        FROM `tabPurchase Invoice Item` pii
        INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
        WHERE {where}
        ORDER BY pi.posting_date, pi.name, pii.idx
    """, params, as_dict=True)

    if rows:
        total_qty = sum(flt(r.qty) for r in rows)
        total_amount = sum(flt(r.amount) for r in rows)
        rows.append({
            "purchase_invoice": None,
            "posting_date": None,
            "company": None,
            "supplier": None,
            "item_name": _("ЖАМИ"),
            "qty": total_qty,
            "rate": None,
            "amount": total_amount,
        })

    return rows
