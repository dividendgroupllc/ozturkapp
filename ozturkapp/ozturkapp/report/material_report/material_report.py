# -*- coding: utf-8 -*-
# Copyright (c) 2024, Ozturkapp and contributors
# For license information, please see license.txt

"""
Material Report v3.0
====================

Ombor harakatlari hisoboti.

Data Source: Stock Ledger Entry ONLY (bitta so'rov, N+1 yo'q)

Ustunlar:
- Item Group, Item
- Boshlang'ich qoldiq (Opening - from_date dan oldin)
- Kirim (Purchase Receipt / Purchase Invoice / Stock Entry Material Receipt)
- Ta'minotchiga qaytarish (Purchase qaytarish, actual_qty < 0)
- Ishlab chiqarilgan (Stock Entry Manufacture / Repack / Disassemble, actual_qty > 0)
- Sarflangan (Stock Entry Manufacture / Repack / Disassemble / Material Consumption, actual_qty < 0)
- Hisobdan chiqarish (Stock Entry Material Issue)
- Ko'chirish kirim / chiqim (Material Transfer, Transfer for Manufacture, Send to Subcontractor)
- Ichki kirim / chiqim (filial/sklad) — kompaniyalararo o'tkazma: ichki mijozga
  yozilgan SI/DN, ichki ta'minotchidan PI/PR yoki Branch Stock Transfer
  yaratgan hujjat. Ular sotuv/xarid EMAS — tovar guruh ichida tan narxda
  ko'chadi, shuning uchun "Kirim"/"Sotilgan" ustunlariga tushmaydi.
- Sotilgan (Sales Invoice / Delivery Note / POS Invoice, actual_qty < 0)
- Mijoz qaytargan (Sales qaytarish, actual_qty > 0)
- Inventarizatsiya (Stock Reconciliation: qty_after_transaction - oldingi qoldiq)
- Boshqa (qolgan barcha harakatlar)
- Yakuniy qoldiq (Closing)

Formula:
Yakuniy = Boshlang'ich + Kirim - Qaytarish(ta'm.) + Ishlab chiqarilgan - Sarflangan
          - Hisobdan chiqarish + Ko'chirish kirim - Ko'chirish chiqim
          + Ichki kirim - Ichki chiqim
          - Sotilgan + Mijoz qaytargan + Inventarizatsiya + Boshqa

Stock Reconciliation: ERPNext v15 da oddiy (batch/serial'siz) tovarlar uchun
SLE.actual_qty = 0, haqiqiy sanalgan qoldiq qty_after_transaction da turadi.
Shuning uchun har bir (item, warehouse) bo'yicha running balance yuritiladi va
farq = qty_after_transaction - oldingi qoldiq sifatida hisoblanadi
(ERPNext Stock Balance report bilan bir xil mantiq).

Stock Entry turi stock_entry_type nomi bo'yicha emas, `purpose` bo'yicha aniqlanadi.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, getdate

from ozturkapp.ozturkapp.report.internal_parties import get_internal_vouchers


SALES_VOUCHERS = ("Sales Invoice", "Delivery Note", "POS Invoice")
PURCHASE_VOUCHERS = ("Purchase Receipt", "Purchase Invoice")
PRODUCTION_PURPOSES = ("Manufacture", "Repack", "Disassemble", "Material Consumption for Manufacture")
TRANSFER_PURPOSES = ("Material Transfer", "Material Transfer for Manufacture", "Send to Subcontractor")

# (fieldname, label, closing'ga ta'sir belgisi)
MOVEMENT_FIELDS = [
    ("purchase_qty", "Kirim", 1),
    ("purchase_return_qty", "Ta'minotchiga qaytarish", -1),
    ("manufacture_in_qty", "Ishlab chiqarilgan", 1),
    ("manufacture_out_qty", "Sarflangan", -1),
    ("issue_qty", "Hisobdan chiqarish", -1),
    ("transfer_in_qty", "Ko'chirish (kirim)", 1),
    ("transfer_out_qty", "Ko'chirish (chiqim)", -1),
    ("ic_in_qty", "Ichki kirim (filial/sklad)", 1),
    ("ic_out_qty", "Ichki chiqim (filial/sklad)", -1),
    ("sales_qty", "Sotilgan", -1),
    ("sales_return_qty", "Mijoz qaytargan", 1),
    ("reconciliation_qty", "Inventarizatsiya", 1),
    ("other_qty", "Boshqa", 1),
]

MOVEMENT_LABELS = {
    "purchase": "Kirim",
    "purchase_return": "Ta'minotchiga qaytarish",
    "manufacture_in": "Ishlab chiqarilgan",
    "manufacture_out": "Sarflangan",
    "issue": "Hisobdan chiqarish",
    "transfer_in": "Ko'chirish (kirim)",
    "transfer_out": "Ko'chirish (chiqim)",
    "ic_in": "Ichki kirim (filial/sklad)",
    "ic_out": "Ichki chiqim (filial/sklad)",
    "sales": "Sotilgan",
    "sales_return": "Mijoz qaytargan",
    "reconciliation": "Inventarizatsiya",
    "other": "Boshqa",
}


def execute(filters=None):
    """Main entry point."""
    filters = frappe._dict(filters or {})
    validate_filters(filters)
    columns = get_columns(filters)
    data = get_data(filters)
    return columns, data


def validate_filters(filters):
    """Validate required filters."""
    if not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw(_("Sana oralig'i majburiy"))

    if getdate(filters.from_date) > getdate(filters.to_date):
        frappe.throw(_("Boshlanish sanasi tugash sanasidan katta bo'lishi mumkin emas"))


def get_columns(filters):
    """Define report columns."""
    columns = [
        {
            "fieldname": "item_group",
            "label": _("Tovar guruhi"),
            "fieldtype": "Link",
            "options": "Item Group",
            "width": 160
        },
        {
            "fieldname": "item_code",
            "label": _("Tovar"),
            "fieldtype": "Link",
            "options": "Item",
            "width": 120
        },
        {
            "fieldname": "item_name",
            "label": _("Tovar nomi"),
            "fieldtype": "Data",
            "width": 180
        },
        {
            "fieldname": "opening_qty",
            "label": _("Boshlang'ich qoldiq"),
            "fieldtype": "Float",
            "width": 130,
            "precision": 3
        },
    ]

    for fieldname, label, _sign in MOVEMENT_FIELDS:
        columns.append({
            "fieldname": fieldname,
            "label": _(label),
            "fieldtype": "Float",
            "width": 120,
            "precision": 3
        })

    columns.append({
        "fieldname": "closing_qty",
        "label": _("Yakuniy qoldiq"),
        "fieldtype": "Float",
        "width": 130,
        "precision": 3
    })
    return columns


# =============================================================================
# SLE FETCH + CLASSIFICATION
# =============================================================================

def get_tree_bounds(doctype, name):
    """Tree doctype uchun (lft, rgt) qaytaradi, topilmasa throw."""
    bounds = frappe.db.get_value(doctype, name, ["lft", "rgt"], as_dict=True)
    if not bounds:
        frappe.throw(_("{0} {1} topilmadi").format(_(doctype), name))
    return bounds


def get_sle_entries(filters):
    """
    to_date gacha bo'lgan barcha SLE'larni (running balance uchun boshidan)
    bitta so'rov bilan oladi, xronologik tartibda.
    """
    conditions = [
        "sle.is_cancelled = 0",
        "sle.posting_date <= %(to_date)s",
    ]
    params = {"to_date": filters.to_date}

    if filters.get("company"):
        conditions.append("sle.company = %(company)s")
        params["company"] = filters.company

    if filters.get("warehouse"):
        wh = get_tree_bounds("Warehouse", filters.warehouse)
        conditions.append(
            "sle.warehouse IN (SELECT name FROM `tabWarehouse` WHERE lft >= %(wh_lft)s AND rgt <= %(wh_rgt)s)"
        )
        params.update({"wh_lft": wh.lft, "wh_rgt": wh.rgt})

    if filters.get("item_code"):
        conditions.append("sle.item_code = %(item_code)s")
        params["item_code"] = filters.item_code

    if filters.get("item_group"):
        ig = get_tree_bounds("Item Group", filters.item_group)
        conditions.append(
            "item.item_group IN (SELECT name FROM `tabItem Group` WHERE lft >= %(ig_lft)s AND rgt <= %(ig_rgt)s)"
        )
        params.update({"ig_lft": ig.lft, "ig_rgt": ig.rgt})

    where_clause = " AND ".join(conditions)

    entries = frappe.db.sql("""
        SELECT
            sle.item_code,
            item.item_name,
            item.item_group,
            sle.warehouse,
            sle.posting_date,
            sle.voucher_type,
            sle.voucher_no,
            sle.actual_qty,
            sle.qty_after_transaction,
            sle.valuation_rate,
            sle.batch_no,
            sle.serial_no,
            sle.serial_and_batch_bundle,
            se.purpose AS stock_entry_purpose
        FROM `tabStock Ledger Entry` sle
        INNER JOIN `tabItem` item ON item.name = sle.item_code
        LEFT JOIN `tabStock Entry` se
            ON sle.voucher_type = 'Stock Entry' AND se.name = sle.voucher_no
        WHERE {where_clause}
        ORDER BY sle.posting_date, sle.posting_time, sle.creation
    """.format(where_clause=where_clause), params, as_dict=True)

    # Kompaniyalararo (ichki) hujjatlarni belgilash — bitta batch so'rov
    internal = get_internal_vouchers({(e.voucher_type, e.voucher_no) for e in entries})
    for e in entries:
        e.is_internal = (e.voucher_type, e.voucher_no) in internal
    return entries


def categorize_movement(entry, qty=None):
    """Bitta SLE uchun kategoriya (qty - ishorali miqdor)."""
    voucher_type = entry.voucher_type
    qty = flt(entry.actual_qty) if qty is None else qty

    # Ichki o'tkazma (filial/sklad) — qaytarish ham shu yerga: belgisiga qarab
    if entry.get("is_internal"):
        return "ic_in" if qty > 0 else "ic_out"

    if voucher_type in PURCHASE_VOUCHERS:
        return "purchase" if qty > 0 else "purchase_return"

    if voucher_type in SALES_VOUCHERS:
        return "sales" if qty < 0 else "sales_return"

    if voucher_type == "Stock Reconciliation":
        return "reconciliation"

    if voucher_type == "Stock Entry":
        purpose = entry.stock_entry_purpose
        if purpose == "Material Receipt" and qty > 0:
            return "purchase"
        if purpose == "Material Issue" and qty < 0:
            return "issue"
        if purpose in PRODUCTION_PURPOSES:
            return "manufacture_in" if qty > 0 else "manufacture_out"
        if purpose in TRANSFER_PURPOSES:
            return "transfer_in" if qty > 0 else "transfer_out"

    return "other"


def iter_classified_entries(entries):
    """
    SLE'larni xronologik aylanib, har biriga haqiqiy qty o'zgarishini
    (Stock Reconciliation uchun running balance orqali) va kategoriyani beradi.
    """
    balances = defaultdict(float)  # (item_code, warehouse) -> qty

    for entry in entries:
        key = (entry.item_code, entry.warehouse)

        if entry.voucher_type == "Stock Reconciliation" and not (
            entry.batch_no or entry.serial_no or entry.serial_and_batch_bundle
        ):
            qty_diff = flt(entry.qty_after_transaction) - balances[key]
        else:
            qty_diff = flt(entry.actual_qty)

        balances[key] += qty_diff
        yield entry, qty_diff, categorize_movement(entry, qty_diff)


def empty_row(entry):
    row = {
        "item_group": entry.item_group,
        "item_code": entry.item_code,
        "item_name": entry.item_name,
        "opening_qty": 0.0,
        "closing_qty": 0.0,
    }
    for fieldname, _label, _sign in MOVEMENT_FIELDS:
        row[fieldname] = 0.0
    return row


def build_item_map(filters, entries):
    """Item bo'yicha opening + davr harakatlarini yig'adi."""
    from_date = getdate(filters.from_date)
    item_map = {}

    for entry, qty_diff, category in iter_classified_entries(entries):
        row = item_map.get(entry.item_code)
        if row is None:
            row = item_map[entry.item_code] = empty_row(entry)

        if getdate(entry.posting_date) < from_date:
            row["opening_qty"] += qty_diff
            continue

        fieldname = category + "_qty"
        if category == "other" or category == "reconciliation":
            row[fieldname] += qty_diff
        else:
            row[fieldname] += abs(qty_diff)

    for row in item_map.values():
        row["closing_qty"] = row["opening_qty"] + sum(
            sign * row[fieldname] for fieldname, _label, sign in MOVEMENT_FIELDS
        )

    return item_map


def get_data(filters):
    """Get report data - unique items with aggregated movements."""
    entries = get_sle_entries(filters)
    if not entries:
        return []

    item_map = build_item_map(filters, entries)
    numeric_fields = ["opening_qty", "closing_qty"] + [f[0] for f in MOVEMENT_FIELDS]

    data = []
    for row in item_map.values():
        # Float shovqinini tozalash
        for f in numeric_fields:
            row[f] = flt(row[f], 6)
        if any(row[f] for f in numeric_fields):
            data.append(row)

    data.sort(key=lambda x: (x.get("item_group") or "", x.get("item_code") or ""))

    if data:
        total_row = {
            "item_group": None,
            "item_code": None,
            "item_name": _("Jami"),
            "is_total": 1,
        }
        for f in numeric_fields:
            total_row[f] = flt(sum(d.get(f, 0) for d in data), 6)
        data.append(total_row)

    return data


# =============================================================================
# WHITELISTED HELPER METHODS
# =============================================================================

def _check_permission():
    frappe.has_permission("Stock Ledger Entry", "read", throw=True)


@frappe.whitelist()
def get_item_stock_summary(item_code, from_date, to_date, warehouse=None):
    """
    Get item stock summary for API usage.

    Returns dict with all movement categories.
    """
    _check_permission()
    filters = frappe._dict({
        "from_date": from_date,
        "to_date": to_date,
        "warehouse": warehouse,
        "item_code": item_code
    })
    validate_filters(filters)

    item_map = build_item_map(filters, get_sle_entries(filters))
    row = item_map.get(item_code) or {}

    result = {
        "item_code": item_code,
        "opening_qty": flt(row.get("opening_qty")),
        "closing_qty": flt(row.get("closing_qty")),
    }
    for fieldname, _label, _sign in MOVEMENT_FIELDS:
        result[fieldname] = flt(row.get(fieldname))
    return result


@frappe.whitelist()
def get_stock_movement_details(item_code, from_date, to_date, warehouse=None, movement_type=None):
    """
    Get detailed stock movements for drill-down.

    movement_type: purchase / purchase_return / manufacture_in / manufacture_out /
                   issue / transfer_in / transfer_out / ic_in / ic_out / sales / sales_return /
                   reconciliation / other
    """
    _check_permission()
    filters = frappe._dict({
        "from_date": from_date,
        "to_date": to_date,
        "warehouse": warehouse,
        "item_code": item_code
    })
    validate_filters(filters)
    start = getdate(from_date)

    result = []
    for entry, qty_diff, category in iter_classified_entries(get_sle_entries(filters)):
        if getdate(entry.posting_date) < start:
            continue
        if movement_type and category != movement_type:
            continue

        result.append({
            "posting_date": entry.posting_date,
            "warehouse": entry.warehouse,
            "voucher_type": entry.voucher_type,
            "voucher_no": entry.voucher_no,
            "actual_qty": qty_diff,
            "qty_after_transaction": entry.qty_after_transaction,
            "valuation_rate": entry.valuation_rate,
            "stock_entry_purpose": entry.stock_entry_purpose,
            "movement_type": category,
            "movement_type_label": get_movement_type_label(category),
        })

    return result


def get_movement_type_label(movement_type):
    """Get label for movement type."""
    return MOVEMENT_LABELS.get(movement_type, movement_type)
