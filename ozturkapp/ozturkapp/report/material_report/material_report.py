# -*- coding: utf-8 -*-
# Copyright (c) 2024, Ozturkapp and contributors
# For license information, please see license.txt

"""
Material Report v2.0
====================

Ombor harakatlari hisoboti - barcha kompaniyalar bo'yicha.

Data Source: Stock Ledger Entry ONLY

Ustunlar:
- Item Group
- Item
- Ostatok na nachalo (Opening - before from_date)
- Kirim tovar (Purchase Receipt / Purchase Invoice)
- Ishlab chiqarilgan tovar (Stock Entry Manufacture - finished goods)
- Rasxod tovar ishlab chiqarishga (Stock Entry Manufacture - raw materials)
- Sotuv tovar (Sales Invoice / Delivery Note)
- Stock Reconciliation (inventarizatsiya)
- Ostatok kones (Closing)

Formula:
Ostatok kones = Ostatok na nachalo + Kirim + Ishlab chiqarilgan - Rasxod - Sotuv ± Stock Reconciliation

Voucher Type Mapping:
- Purchase Receipt / Purchase Invoice → Kirim tovar
- Stock Entry (Manufacture) actual_qty > 0 → Ishlab chiqarilgan tovar
- Stock Entry (Manufacture) actual_qty < 0 → Rasxod tovar
- Sales Invoice / Delivery Note → Sotuv tovar
- Stock Reconciliation → Stock Reconciliation
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate


def execute(filters=None):
    """Main entry point."""
    validate_filters(filters)
    columns = get_columns(filters)
    data = get_data(filters)
    return columns, data


def validate_filters(filters):
    """Validate required filters."""
    if not filters:
        frappe.throw(_("Filtrlar kiritilmagan"))
    
    if not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw(_("Sana oralig'i majburiy"))
    
    if getdate(filters.from_date) > getdate(filters.to_date):
        frappe.throw(_("Boshlanish sanasi tugash sanasidan katta bo'lishi mumkin emas"))


def get_columns(filters):
    """Define report columns."""
    return [
        {
            "fieldname": "item_group",
            "label": _("Tovar guruhi"),
            "fieldtype": "Link",
            "options": "Item Group",
            "width": 180
        },
        {
            "fieldname": "item_code",
            "label": _("Tovar"),
            "fieldtype": "Link",
            "options": "Item",
            "width": 200
        },
        {
            "fieldname": "opening_qty",
            "label": _("Boshlang'ich qoldiq"),
            "fieldtype": "Float",
            "width": 140,
            "precision": 3
        },
        {
            "fieldname": "purchase_qty",
            "label": _("Kirim"),
            "fieldtype": "Float",
            "width": 100,
            "precision": 3
        },
        {
            "fieldname": "manufacture_in_qty",
            "label": _("Ishlab chiqarilgan"),
            "fieldtype": "Float",
            "width": 140,
            "precision": 3
        },
        {
            "fieldname": "manufacture_out_qty",
            "label": _("Sarflangan"),
            "fieldtype": "Float",
            "width": 120,
            "precision": 3
        },
        {
            "fieldname": "sales_qty",
            "label": _("Sotilgan"),
            "fieldtype": "Float",
            "width": 100,
            "precision": 3
        },
        {
            "fieldname": "reconciliation_qty",
            "label": _("Inventarizatsiya"),
            "fieldtype": "Float",
            "width": 130,
            "precision": 3
        },
        {
            "fieldname": "closing_qty",
            "label": _("Yakuniy qoldiq"),
            "fieldtype": "Float",
            "width": 130,
            "precision": 3
        }
    ]


def get_data(filters):
    """Get report data - unique items with aggregated movements."""
    
    # Get all unique items that have any movement
    items = get_items_with_movement(filters)
    
    if not items:
        return []
    
    data = []
    
    for item in items:
        item_code = item.item_code
        
        # Get opening balance (before from_date)
        opening_qty = get_opening_qty(filters, item_code)
        
        # Get period movements by category
        movements = get_period_movements_by_category(filters, item_code)
        
        # Calculate closing
        closing_qty = (
            opening_qty 
            + movements["purchase_qty"]
            + movements["manufacture_in_qty"]
            - movements["manufacture_out_qty"]
            - movements["sales_qty"]
            + movements["reconciliation_qty"]
        )
        
        # Skip items with no movement at all
        has_movement = (
            opening_qty != 0 or
            movements["purchase_qty"] != 0 or
            movements["manufacture_in_qty"] != 0 or
            movements["manufacture_out_qty"] != 0 or
            movements["sales_qty"] != 0 or
            movements["reconciliation_qty"] != 0 or
            closing_qty != 0
        )
        
        if not has_movement:
            continue
        
        row = {
            "item_group": item.item_group,
            "item_code": item_code,
            "opening_qty": opening_qty,
            "purchase_qty": movements["purchase_qty"],
            "manufacture_in_qty": movements["manufacture_in_qty"],
            "manufacture_out_qty": movements["manufacture_out_qty"],
            "sales_qty": movements["sales_qty"],
            "reconciliation_qty": movements["reconciliation_qty"],
            "closing_qty": closing_qty
        }
        
        data.append(row)
    
    # Sort by item_group, item_code
    data.sort(key=lambda x: (x.get("item_group") or "", x.get("item_code") or ""))
    
    # Calculate totals
    if data:
        total_row = {
            "item_group": "Total",
            "item_code": "",
            "opening_qty": sum(d.get("opening_qty", 0) for d in data),
            "purchase_qty": sum(d.get("purchase_qty", 0) for d in data),
            "manufacture_in_qty": sum(d.get("manufacture_in_qty", 0) for d in data),
            "manufacture_out_qty": sum(d.get("manufacture_out_qty", 0) for d in data),
            "sales_qty": sum(d.get("sales_qty", 0) for d in data),
            "reconciliation_qty": sum(d.get("reconciliation_qty", 0) for d in data),
            "closing_qty": sum(d.get("closing_qty", 0) for d in data)
        }
        data.append(total_row)
    
    return data


def get_items_with_movement(filters):
    """Get unique items that have any SLE movement."""
    conditions = [
        "sle.is_cancelled = 0",
        "sle.item_code IS NOT NULL",
        "sle.item_code != ''",
        "item.item_group IS NOT NULL",
        "item.item_group != ''"
    ]
    
    # No company filter - all companies
    
    if filters.get("warehouse"):
        conditions.append("sle.warehouse = %(warehouse)s")
    
    if filters.get("item_code"):
        conditions.append("sle.item_code = %(item_code)s")
    
    # Items with any movement up to to_date
    conditions.append("sle.posting_date <= %(to_date)s")
    
    where_clause = " AND ".join(conditions)
    
    return frappe.db.sql("""
        SELECT DISTINCT
            sle.item_code,
            item.item_name,
            item.item_group,
            item.stock_uom
        FROM `tabStock Ledger Entry` sle
        INNER JOIN `tabItem` item ON item.name = sle.item_code
        WHERE {where_clause}
        ORDER BY item.item_group, sle.item_code
    """.format(where_clause=where_clause), filters, as_dict=True)


def get_opening_qty(filters, item_code):
    """
    Get opening quantity BEFORE from_date.
    
    Sum of all actual_qty before from_date across all warehouses.
    """
    conditions = [
        "item_code = %(item_code)s",
        "posting_date < %(from_date)s",
        "is_cancelled = 0"
    ]
    
    if filters.get("warehouse"):
        conditions.append("warehouse = %(warehouse)s")
    
    where_clause = " AND ".join(conditions)
    
    result = frappe.db.sql("""
        SELECT COALESCE(SUM(actual_qty), 0) as opening_qty
        FROM `tabStock Ledger Entry`
        WHERE {where_clause}
    """.format(where_clause=where_clause), {
        "item_code": item_code,
        "from_date": filters.from_date,
        "warehouse": filters.get("warehouse")
    }, as_dict=True)
    
    return flt(result[0].opening_qty) if result else 0


def get_period_movements_by_category(filters, item_code):
    """
    Get movements within period, categorized by type.
    
    Categories:
    - purchase_qty: Purchase Receipt, Purchase Invoice (actual_qty > 0)
    - manufacture_in_qty: Stock Entry (Manufacture) where actual_qty > 0 (finished goods)
    - manufacture_out_qty: Stock Entry (Manufacture) where actual_qty < 0 (raw materials)
    - sales_qty: Sales Invoice, Delivery Note (actual_qty < 0, return absolute)
    - reconciliation_qty: Stock Reconciliation (can be +/-)
    """
    conditions = [
        "sle.item_code = %(item_code)s",
        "sle.posting_date >= %(from_date)s",
        "sle.posting_date <= %(to_date)s",
        "sle.is_cancelled = 0"
    ]
    
    if filters.get("warehouse"):
        conditions.append("sle.warehouse = %(warehouse)s")
    
    where_clause = " AND ".join(conditions)
    
    # Get all SLE entries with Stock Entry type info
    entries = frappe.db.sql("""
        SELECT 
            sle.voucher_type,
            sle.voucher_no,
            sle.actual_qty,
            CASE 
                WHEN sle.voucher_type = 'Stock Entry' THEN
                    (SELECT stock_entry_type FROM `tabStock Entry` WHERE name = sle.voucher_no)
                ELSE NULL
            END as stock_entry_type
        FROM `tabStock Ledger Entry` sle
        WHERE {where_clause}
    """.format(where_clause=where_clause), {
        "item_code": item_code,
        "from_date": filters.from_date,
        "to_date": filters.to_date,
        "warehouse": filters.get("warehouse")
    }, as_dict=True)
    
    result = {
        "purchase_qty": 0,
        "manufacture_in_qty": 0,
        "manufacture_out_qty": 0,
        "sales_qty": 0,
        "reconciliation_qty": 0
    }
    
    for entry in entries:
        voucher_type = entry.voucher_type
        qty = flt(entry.actual_qty)
        se_type = entry.stock_entry_type
        
        # Purchase Receipt or Purchase Invoice
        if voucher_type in ("Purchase Receipt", "Purchase Invoice"):
            if qty > 0:
                result["purchase_qty"] += qty
        
        # Stock Entry
        elif voucher_type == "Stock Entry":
            if se_type == "Manufacture":
                if qty > 0:
                    # Finished goods produced
                    result["manufacture_in_qty"] += qty
                else:
                    # Raw materials consumed
                    result["manufacture_out_qty"] += abs(qty)
            elif se_type == "Material Receipt":
                # Material receipt = kirim
                if qty > 0:
                    result["purchase_qty"] += qty
            # Other stock entry types (Transfer, etc.) - ignore for this report
        
        # Sales Invoice or Delivery Note
        elif voucher_type in ("Sales Invoice", "Delivery Note"):
            if qty < 0:
                result["sales_qty"] += abs(qty)
        
        # Stock Reconciliation
        elif voucher_type == "Stock Reconciliation":
            result["reconciliation_qty"] += qty
    
    return result


# =============================================================================
# WHITELISTED HELPER METHODS
# =============================================================================

@frappe.whitelist()
def get_item_stock_summary(item_code, from_date, to_date, warehouse=None):
    """
    Get item stock summary for API usage.
    
    Returns dict with all movement categories.
    """
    filters = frappe._dict({
        "from_date": from_date,
        "to_date": to_date,
        "warehouse": warehouse,
        "item_code": item_code
    })
    
    opening = get_opening_qty(filters, item_code)
    movements = get_period_movements_by_category(filters, item_code)
    
    closing = (
        opening 
        + movements["purchase_qty"]
        + movements["manufacture_in_qty"]
        - movements["manufacture_out_qty"]
        - movements["sales_qty"]
        + movements["reconciliation_qty"]
    )
    
    return {
        "item_code": item_code,
        "opening_qty": opening,
        "purchase_qty": movements["purchase_qty"],
        "manufacture_in_qty": movements["manufacture_in_qty"],
        "manufacture_out_qty": movements["manufacture_out_qty"],
        "sales_qty": movements["sales_qty"],
        "reconciliation_qty": movements["reconciliation_qty"],
        "closing_qty": closing
    }


@frappe.whitelist()
def get_stock_movement_details(item_code, from_date, to_date, warehouse=None, movement_type=None):
    """
    Get detailed stock movements for drill-down.
    
    Args:
        item_code: Item code
        from_date: Start date
        to_date: End date
        warehouse: Optional warehouse filter
        movement_type: Optional filter (purchase/manufacture_in/manufacture_out/sales/reconciliation)
    
    Returns list of movements.
    """
    conditions = [
        "sle.item_code = %(item_code)s",
        "sle.posting_date >= %(from_date)s",
        "sle.posting_date <= %(to_date)s",
        "sle.is_cancelled = 0"
    ]
    
    if warehouse:
        conditions.append("sle.warehouse = %(warehouse)s")
    
    where_clause = " AND ".join(conditions)
    
    entries = frappe.db.sql("""
        SELECT 
            sle.posting_date,
            sle.warehouse,
            sle.voucher_type,
            sle.voucher_no,
            sle.actual_qty,
            sle.qty_after_transaction,
            sle.valuation_rate,
            CASE 
                WHEN sle.voucher_type = 'Stock Entry' THEN
                    (SELECT stock_entry_type FROM `tabStock Entry` WHERE name = sle.voucher_no)
                ELSE NULL
            END as stock_entry_type
        FROM `tabStock Ledger Entry` sle
        WHERE {where_clause}
        ORDER BY sle.posting_date, sle.posting_time, sle.creation
    """.format(where_clause=where_clause), {
        "item_code": item_code,
        "from_date": from_date,
        "to_date": to_date,
        "warehouse": warehouse
    }, as_dict=True)
    
    # Categorize and filter
    result = []
    for entry in entries:
        category = categorize_movement(entry)
        
        if movement_type and category != movement_type:
            continue
        
        entry["movement_type"] = category
        entry["movement_type_label"] = get_movement_type_label(category)
        result.append(entry)
    
    return result


def categorize_movement(entry):
    """Categorize a single SLE entry."""
    voucher_type = entry.voucher_type
    qty = flt(entry.actual_qty)
    se_type = entry.stock_entry_type
    
    if voucher_type in ("Purchase Receipt", "Purchase Invoice"):
        return "purchase"
    elif voucher_type == "Stock Entry":
        if se_type == "Manufacture":
            return "manufacture_in" if qty > 0 else "manufacture_out"
        elif se_type == "Material Receipt":
            return "purchase"
        else:
            return "transfer"
    elif voucher_type in ("Sales Invoice", "Delivery Note"):
        return "sales"
    elif voucher_type == "Stock Reconciliation":
        return "reconciliation"
    else:
        return "other"


def get_movement_type_label(movement_type):
    """Get label for movement type."""
    labels = {
        "purchase": "Kirim",
        "manufacture_in": "Ishlab chiqarilgan",
        "manufacture_out": "Sarflangan",
        "sales": "Sotilgan",
        "reconciliation": "Inventarizatsiya",
        "transfer": "O'tkazma",
        "other": "Boshqa"
    }
    return labels.get(movement_type, movement_type)
