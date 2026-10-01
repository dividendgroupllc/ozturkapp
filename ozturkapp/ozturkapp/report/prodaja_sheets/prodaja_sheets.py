# Copyright (c) 2026, Ozturkapp and contributors
# For license information, please see license.txt

"""Prodaja Sheets — sotuv qatorlari: summa, tannarx (СС), ustama va marja.

Manba: Sales Invoice (shu jumladan POS yopilishidagi konsolidatsiya SI) va hali
konsolidatsiya qilinmagan POS Invoice.

* Taom (Product Bundle) tannarxi masalliqlar yig'indisidan olinadi —
  qarang `attach_costs`.
* Ichki mijozlar (filial/sklad, Branch Stock Transfer SI'lari) standart
  holatda CHIQARILADI: bu sotuv emas, tovarning tan narxda ko'chishi.
  "Ички мижозларни қўшиш" belgilansa qo'shiladi va "Ички" ustunida belgilanadi.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from ozturkapp.ozturkapp.report.internal_parties import internal_customer_sql


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
        {"fieldname": "is_internal", "label": _("Ички"), "fieldtype": "Check", "width": 60},
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
    # Ichki mijoz (filial/sklad) — Branch Stock Transfer SI'lari sotuv emas,
    # tovarning tan narxda ko'chishi. Standart holatda chiqarib tashlanadi
    # (mijoz aniq tanlangan bo'lsa — foydalanuvchi aynan shuni so'ragan).
    if not cint(filters.get("include_internal")) and not filters.get("customer"):
        voucher_col = f"{alias}.name" if alias == "si" else None
        conditions.append(f"NOT {internal_customer_sql(alias + '.customer', voucher_col)}")
    return " AND ".join(conditions)


def get_rows(filters):
    params = {"from_date": filters["from_date"], "to_date": filters["to_date"]}
    si_where = get_conditions(filters, "si", "sii", params)
    pos_where = get_conditions(filters, "pi", "pii", params)

    # Summa - kompaniya valyutasida, chek chegirmasidan keyin (base_net_amount).
    # Tannarx keyin, Python'da hisoblanadi (`attach_costs`) — qarang o'sha yerdagi izoh.
    # POS Invoice: faqat hali Sales Invoice'ga konsolidatsiya qilinmaganlari
    # (yoki konsolidatsiya SI bekor qilingan) - ikki marta hisoblanmasligi uchun.
    return frappe.db.sql(f"""
        SELECT
            si.posting_date,
            si.posting_time,
            sii.idx,
            sii.name AS row_name,
            sii.item_code,
            sii.item_name,
            sii.qty,
            sii.stock_qty,
            sii.warehouse,
            sii.base_net_amount AS amount,
            IFNULL(si.customer_name, si.customer) AS customer,
            {internal_customer_sql("si.customer", "si.name")} AS is_internal,
            sii.item_group,
            IFNULL(sii.incoming_rate, 0) * sii.stock_qty AS item_cost,
            si.update_stock,
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
            pii.name AS row_name,
            pii.item_code,
            pii.item_name,
            pii.qty,
            pii.stock_qty,
            pii.warehouse,
            pii.base_net_amount AS amount,
            IFNULL(pi.customer_name, pi.customer) AS customer,
            {internal_customer_sql("pi.customer")} AS is_internal,
            pii.item_group,
            0 AS item_cost,
            0 AS update_stock,
            pi.is_return,
            COALESCE(NULLIF(pi.branch, ''), pi.company) AS branch,
            pi.remarks,
            'POS Invoice' AS voucher_type,
            pii.parent AS sales_invoice,
            comp.default_currency AS currency
        FROM `tabPOS Invoice Item` pii
        INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
        INNER JOIN `tabCompany` comp ON comp.name = pi.company
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


# =============================================================================
# TANNARX
# =============================================================================
#
# Taom — zaxirasiz (non-stock) tovar + Product Bundle. ERPNext tannarxni
# (`incoming_rate`) taom qatoriga EMAS, uning `Packed Item` qatorlariga
# (masalliqlarga) yozadi; taom qatorida incoming_rate = 0. Avvalgi versiya
# faqat `sii.incoming_rate` ni o'qigani uchun taomlar tannarxi 0 chiqardi.
#
# Sales Invoice qatori tannarxi (ustuvorlik tartibida):
#   1. update_stock=1 bo'lsa — shu qatorga yozilgan Stock Ledger Entry'lar
#      (`voucher_detail_no` = qator nomi, masalliqlar ham shu nom bilan
#      yoziladi): −Σ stock_value_difference. Bu GL'dagi COGS bilan AYNAN
#      teng va orqa sana bilan qayta baholash (repost) bo'lsa ham yangilanadi.
#      Submit paytida u Σ(packed.incoming_rate × packed.qty) ga teng.
#   2. SLE yo'q bo'lsa — Σ(packed.incoming_rate × packed.qty)
#      (`parent_detail_docname` bo'yicha) + qatorning o'z incoming_rate × stock_qty.
#
# POS Invoice (hali konsolidatsiya qilinmagan) — ombor harakati hali yo'q,
# shuning uchun JORIY baho: masalliq qatorlari (Packed Item) bo'lsa ular,
# bo'lmasa Product Bundle tarkibi × miqdor; oddiy tovar — o'zi. Baho: Bin
# (masalliq ombori) valuation_rate, bo'lmasa Item.valuation_rate.

def attach_costs(rows):
    si_rows = [r for r in rows if r.voucher_type == "Sales Invoice"]
    pos_rows = [r for r in rows if r.voucher_type == "POS Invoice"]

    if si_rows:
        si_names = list({r.sales_invoice for r in si_rows})
        sle_cost = _sle_costs(si_names)
        packed_si = _packed_rows("Sales Invoice", si_names)
        for r in si_rows:
            key = (r.sales_invoice, r.row_name)
            if cint(r.update_stock) and key in sle_cost:
                r.cost_amount = sle_cost[key]
            else:
                packed = packed_si.get(key) or []
                r.cost_amount = flt(r.item_cost) + sum(
                    flt(p.incoming_rate) * flt(p.qty) for p in packed
                )

    if pos_rows:
        pos_names = list({r.sales_invoice for r in pos_rows})
        packed_pos = _packed_rows("POS Invoice", pos_names)
        bundles = _bundle_components({r.item_code for r in pos_rows})
        valuation = _ValuationCache()
        for r in pos_rows:
            packed = packed_pos.get((r.sales_invoice, r.row_name))
            if packed:
                r.cost_amount = sum(
                    flt(p.qty) * valuation.get(p.item_code, p.warehouse or r.warehouse) for p in packed
                )
            elif r.item_code in bundles:
                r.cost_amount = sum(
                    flt(c.qty) * flt(r.stock_qty) * valuation.get(c.item_code, r.warehouse)
                    for c in bundles[r.item_code]
                )
            else:
                r.cost_amount = flt(r.stock_qty) * valuation.get(r.item_code, r.warehouse)


def _chunks(seq, size=500):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _sle_costs(si_names):
    """{(SI, qator nomi): tannarx} — SLE'dan, masalliqlar ham qatorga yig'iladi."""
    result = {}
    for chunk in _chunks(si_names):
        for r in frappe.db.sql("""
            SELECT voucher_no, voucher_detail_no, -SUM(stock_value_difference) AS cost
            FROM `tabStock Ledger Entry`
            WHERE voucher_type = 'Sales Invoice'
              AND voucher_no IN %(names)s
              AND is_cancelled = 0
            GROUP BY voucher_no, voucher_detail_no
        """, {"names": tuple(chunk)}, as_dict=True):
            result[(r.voucher_no, r.voucher_detail_no)] = flt(r.cost)
    return result


def _packed_rows(parenttype, parents):
    """{(hujjat, ota qator nomi): [Packed Item]}."""
    result = {}
    for chunk in _chunks(parents):
        for p in frappe.db.sql("""
            SELECT parent, parent_detail_docname, item_code, warehouse, qty, incoming_rate
            FROM `tabPacked Item`
            WHERE parenttype = %(pt)s AND parent IN %(names)s
        """, {"pt": parenttype, "names": tuple(chunk)}, as_dict=True):
            result.setdefault((p.parent, p.parent_detail_docname), []).append(p)
    return result


def _bundle_components(item_codes):
    """{taom: [Product Bundle Item]} — faqat faol bundle'lar."""
    item_codes = [i for i in item_codes if i]
    if not item_codes:
        return {}
    result = {}
    for c in frappe.db.sql("""
        SELECT pb.new_item_code AS parent_item, pbi.item_code, pbi.qty
        FROM `tabProduct Bundle Item` pbi
        INNER JOIN `tabProduct Bundle` pb ON pb.name = pbi.parent
        WHERE pb.new_item_code IN %(items)s AND IFNULL(pb.disabled, 0) = 0
    """, {"items": tuple(item_codes)}, as_dict=True):
        result.setdefault(c.parent_item, []).append(c)
    return result


class _ValuationCache:
    """Joriy baho: Bin.valuation_rate (ombor bo'yicha), bo'lmasa Item.valuation_rate."""

    def __init__(self):
        self._bin = {}
        self._item = {}

    def get(self, item_code, warehouse):
        key = (item_code, warehouse)
        if key not in self._bin:
            self._bin[key] = flt(frappe.db.get_value(
                "Bin", {"item_code": item_code, "warehouse": warehouse}, "valuation_rate"
            )) if warehouse else 0
        if self._bin[key] > 0:
            return self._bin[key]
        if item_code not in self._item:
            self._item[item_code] = flt(frappe.db.get_value("Item", item_code, "valuation_rate"))
        return self._item[item_code]


def get_data(filters):
    rows = get_rows(filters)
    attach_costs(rows)

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
            "is_internal": cint(r.is_internal),
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
