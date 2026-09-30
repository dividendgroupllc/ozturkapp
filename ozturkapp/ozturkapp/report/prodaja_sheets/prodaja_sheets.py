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
        {"fieldname": "rate", "label": _("Нарх"), "fieldtype": "Currency", "options": "currency", "width": 110},
        {"fieldname": "amount", "label": _("Сумма"), "fieldtype": "Currency", "options": "currency", "width": 130},
        {"fieldname": "customer", "label": _("Клиент"), "fieldtype": "Data", "width": 150},
        {"fieldname": "item_group", "label": _("Тип"), "fieldtype": "Data", "width": 120},
        {"fieldname": "cost_rate", "label": _("СС товар"), "fieldtype": "Currency", "options": "currency", "width": 110},
        {"fieldname": "cost_amount", "label": _("СС Сумма"), "fieldtype": "Currency", "options": "currency", "width": 130},
        {"fieldname": "markup", "label": _("Наценка"), "fieldtype": "Percent", "width": 100},
        {"fieldname": "margin", "label": _("Маржа"), "fieldtype": "Percent", "width": 100},
        {"fieldname": "is_return", "label": _("Қайтариш"), "fieldtype": "Check", "width": 80},
        {"fieldname": "branch", "label": _("Филиал"), "fieldtype": "Data", "width": 120},
        {"fieldname": "remarks", "label": _("Изоҳ"), "fieldtype": "Data", "width": 150},
        {"fieldname": "voucher_type", "label": _("Ҳужжат тури"), "fieldtype": "Data", "width": 110},
        {"fieldname": "sales_invoice", "label": _("Ҳужжат"), "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 150},
        {"fieldname": "currency", "label": _("Валюта"), "fieldtype": "Link", "options": "Currency", "hidden": 1},
    ]


def get_conditions(filters, alias, item_alias, params):
    """Sales Invoice va POS Invoice uchun umumiy shartlar."""
    conditions = [
        f"{alias}.docstatus = 1",
        f"{alias}.posting_date BETWEEN %(from_date)s AND %(to_date)s",
    ]
    if filters.get("company"):
        conditions.append(f"{alias}.company = %(company)s")
        params["company"] = filters["company"]
    if filters.get("customer"):
        conditions.append(f"{alias}.customer = %(customer)s")
        params["customer"] = filters["customer"]
    if filters.get("branch"):
        conditions.append(f"{alias}.branch = %(branch)s")
        params["branch"] = filters["branch"]
    if filters.get("item_group"):
        ig = frappe.db.get_value("Item Group", filters["item_group"], ["lft", "rgt"], as_dict=True)
        if not ig:
            frappe.throw(_("Товар гуруҳи топилмади: {0}").format(filters["item_group"]))
        conditions.append(
            f"{item_alias}.item_group IN (SELECT name FROM `tabItem Group` WHERE lft >= %(ig_lft)s AND rgt <= %(ig_rgt)s)"
        )
        params.update({"ig_lft": ig.lft, "ig_rgt": ig.rgt})
    return " AND ".join(conditions)


def get_rows(filters):
    params = {"from_date": filters["from_date"], "to_date": filters["to_date"]}
    si_where = get_conditions(filters, "si", "sii", params)
    pos_where = get_conditions(filters, "pi", "pii", params)

    # Summa - kompaniya valyutasida, chek chegirmasidan keyin (base_net_amount).
    # Tannarx: incoming_rate - ombor o'lchov birligi uchun, shuning uchun stock_qty ga ko'paytiriladi.
    # POS Invoice: faqat hali Sales Invoice'ga konsolidatsiya qilinmaganlari
    # (yoki konsolidatsiya SI bekor qilingan) - ikki marta hisoblanmasligi uchun.
    # POS Invoice Item'da incoming_rate yo'q - Bin/Item valuation_rate olinadi.
    return frappe.db.sql(f"""
        SELECT
            si.posting_date,
            si.posting_time,
            sii.idx,
            sii.item_name,
            sii.qty,
            sii.base_net_amount AS amount,
            IFNULL(si.customer_name, si.customer) AS customer,
            sii.item_group,
            sii.incoming_rate * sii.stock_qty AS cost_amount,
            si.is_return,
            COALESCE(NULLIF(si.branch, ''), si.company) AS branch,
            si.remarks,
            'Sales Invoice' AS voucher_type,
            sii.parent AS sales_invoice,
            comp.default_currency AS currency
        FROM `tabSales Invoice Item` sii
        INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
        INNER JOIN `tabCompany` comp ON comp.name = si.company
        WHERE {si_where}

        UNION ALL

        SELECT
            pi.posting_date,
            pi.posting_time,
            pii.idx,
            pii.item_name,
            pii.qty,
            pii.base_net_amount AS amount,
            IFNULL(pi.customer_name, pi.customer) AS customer,
            pii.item_group,
            COALESCE(
                NULLIF((SELECT b.valuation_rate FROM `tabBin` b
                        WHERE b.item_code = pii.item_code AND b.warehouse = pii.warehouse), 0),
                item.valuation_rate, 0
            ) * pii.stock_qty AS cost_amount,
            pi.is_return,
            COALESCE(NULLIF(pi.branch, ''), pi.company) AS branch,
            pi.remarks,
            'POS Invoice' AS voucher_type,
            pii.parent AS sales_invoice,
            comp.default_currency AS currency
        FROM `tabPOS Invoice Item` pii
        INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
        INNER JOIN `tabCompany` comp ON comp.name = pi.company
        LEFT JOIN `tabItem` item ON item.name = pii.item_code
        WHERE {pos_where}
          AND (
              IFNULL(pi.consolidated_invoice, '') = ''
              OR NOT EXISTS (
                  SELECT 1 FROM `tabSales Invoice` csi
                  WHERE csi.name = pi.consolidated_invoice AND csi.docstatus = 1
              )
          )

        ORDER BY posting_date, posting_time, sales_invoice, idx
    """, params, as_dict=True)


def get_data(filters):
    rows = get_rows(filters)

    data = []
    tot_amount = 0
    tot_cost = 0
    tot_qty = 0
    currencies = set()

    for r in rows:
        qty = flt(r.qty)
        amount = flt(r.amount)
        cost_amount = flt(r.cost_amount)
        profit = amount - cost_amount
        # Qaytarishlarda (manfiy summa) ham belgi to'g'ri chiqishi uchun abs
        markup = (profit / abs(cost_amount) * 100) if cost_amount else None
        margin = (profit / abs(amount) * 100) if amount else None

        tot_amount += amount
        tot_cost += cost_amount
        tot_qty += qty
        currencies.add(r.currency)

        data.append({
            "posting_date": r.posting_date,
            "item_name": r.item_name,
            "qty": qty,
            "rate": (amount / qty) if qty else 0,
            "amount": amount,
            "customer": r.customer,
            "item_group": r.item_group,
            "cost_rate": (cost_amount / qty) if qty else 0,
            "cost_amount": cost_amount,
            "markup": markup,
            "margin": margin,
            "is_return": r.is_return,
            "branch": r.branch,
            "remarks": r.remarks,
            "voucher_type": r.voucher_type,
            "sales_invoice": r.sales_invoice,
            "currency": r.currency,
        })

    if data:
        t_profit = tot_amount - tot_cost
        data.append({
            "item_name": _("ЖАМИ"),
            "qty": tot_qty,
            "amount": tot_amount,
            "cost_amount": tot_cost,
            "markup": (t_profit / abs(tot_cost) * 100) if tot_cost else None,
            "margin": (t_profit / abs(tot_amount) * 100) if tot_amount else None,
            "currency": currencies.pop() if len(currencies) == 1 else None,
            "is_total": 1,
        })

    return data
