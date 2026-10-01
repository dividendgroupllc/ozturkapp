# Copyright (c) 2026, Ozturkapp and contributors
# For license information, please see license.txt

"""Prixod Sheets — ta'minotchilardan kirim (Purchase Invoice qatorlari).

Ichki ta'minotchi (sklad/filial) dan kelgan PI — Branch Stock Transfer
o'tkazmasi, haqiqiy xarid emas. Standart holatda CHIQARILADI; "Ички
таъминотчиларни қўшиш" belgilansa qo'shiladi va "Ички" ustunida belgilanadi.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from ozturkapp.ozturkapp.report.internal_parties import internal_supplier_sql


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
        {"fieldname": "stock_uom", "label": _("Ўлчов"), "fieldtype": "Data", "width": 70},
        {"fieldname": "rate", "label": _("Нархи"), "fieldtype": "Currency", "options": "currency", "width": 120},
        {"fieldname": "amount", "label": _("Суммаси"), "fieldtype": "Currency", "options": "currency", "width": 140},
        {"fieldname": "is_return", "label": _("Қайтариш"), "fieldtype": "Check", "width": 80},
        {"fieldname": "is_internal", "label": _("Ички"), "fieldtype": "Check", "width": 60},
        {"fieldname": "company", "label": _("Компания"), "fieldtype": "Link", "options": "Company", "width": 150},
        {"fieldname": "purchase_invoice", "label": _("Ҳужжат"), "fieldtype": "Link", "options": "Purchase Invoice", "width": 170},
        {"fieldname": "currency", "label": _("Валюта"), "fieldtype": "Link", "options": "Currency", "hidden": 1},
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
    if filters.get("item_code"):
        conditions.append("pii.item_code = %(item_code)s")
        params["item_code"] = filters["item_code"]
    if filters.get("item_group"):
        ig = frappe.db.get_value("Item Group", filters["item_group"], ["lft", "rgt"], as_dict=True)
        if not ig:
            frappe.throw(_("Товар гуруҳи топилмади: {0}").format(filters["item_group"]))
        conditions.append(
            "pii.item_group IN (SELECT name FROM `tabItem Group` WHERE lft >= %(ig_lft)s AND rgt <= %(ig_rgt)s)"
        )
        params.update({"ig_lft": ig.lft, "ig_rgt": ig.rgt})

    # Ichki ta'minotchi (filial/sklad) — tovarning tan narxda ko'chishi, xarid emas.
    # Ta'minotchi aniq tanlangan bo'lsa — foydalanuvchi aynan shuni so'ragan.
    if not cint(filters.get("include_internal")) and not filters.get("supplier"):
        conditions.append(f"NOT {internal_supplier_sql('pi.supplier', 'pi.name')}")

    where = " AND ".join(conditions)

    # Miqdor - ombor o'lchov birligida (stock_qty), summa - kompaniya valyutasida (base_*)
    rows = frappe.db.sql(f"""
        SELECT
            pi.name AS purchase_invoice,
            pi.posting_date,
            pi.company,
            IFNULL(pi.supplier_name, pi.supplier) AS supplier,
            pii.item_name,
            pii.stock_qty AS qty,
            pii.stock_uom,
            pii.base_amount / NULLIF(pii.stock_qty, 0) AS rate,
            pii.base_amount AS amount,
            pi.is_return,
            {internal_supplier_sql("pi.supplier", "pi.name")} AS is_internal,
            comp.default_currency AS currency
        FROM `tabPurchase Invoice Item` pii
        INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
        INNER JOIN `tabCompany` comp ON comp.name = pi.company
        WHERE {where}
        ORDER BY pi.posting_date, pi.name, pii.idx
    """, params, as_dict=True)

    if rows:
        total_qty = sum(flt(r.qty) for r in rows)
        total_amount = sum(flt(r.amount) for r in rows)
        currencies = {r.currency for r in rows}
        rows.append({
            "purchase_invoice": None,
            "posting_date": None,
            "company": None,
            "supplier": None,
            "item_name": _("ЖАМИ"),
            "qty": total_qty,
            "rate": None,
            "amount": total_amount,
            "currency": currencies.pop() if len(currencies) == 1 else None,
            "is_total": 1,
        })

    return rows
