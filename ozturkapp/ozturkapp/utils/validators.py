from typing import Dict, List, Optional
import frappe
from frappe import _

class ValidationError(Exception):
    """Custom exception for validation errors."""
    pass

# Hardcoded mapping for known misspellings or common variations
ITEM_MAPPING = {
    'Картошка чипс 100 гр': 'Картошка чипс зг',
    'Ок соус (собой)': 'Ок соус (стол)',
    'Пицца гуштли катта': 'Пицца гуштли',
    'Кизил соус (собой)': 'Кизил соус (стол)',

    'Фанта 2л (товар)': 'Фанта 2л',
    'Пизза Пеперони': 'Пицца Пеперони',
    'Пизза Пеперони кичик': 'Пицца Пеперони кичик',
    'Бардак чой чойнак': 'Бардак чой',
    'гошт 100 гр': 'гошт 50 гр', # Closest match if 100 is not there
    'Хот-дог булочкали (15000)': 'Хот-дог',
}

def validate_import_prerequisites(
    company: str,
    source_warehouse: str,
    posting_date: str,
    customer: str = ""
) -> Dict:
    """Validate all prerequisites before import."""
    errors = []
    if not company: errors.append(_("Company tanlanmagan"))
    if not source_warehouse: errors.append(_("Ombor tanlanmagan"))
    if not posting_date: errors.append(_("Sana tanlanmagan"))
    if not customer: errors.append(_("Mijoz tanlanmagan"))

    if company and source_warehouse:
        wh_company = frappe.db.get_value("Warehouse", source_warehouse, "company")
        if wh_company and wh_company != company:
            errors.append(_("Warehouse '{0}' kompaniyaga tegishli emas: {1}").format(source_warehouse, company))

    if customer and not frappe.db.exists("Customer", customer):
        errors.append(_("'{0}' nomli mijoz topilmadi").format(customer))

    return {"success": len(errors) == 0, "message": "\n".join(errors), "errors": errors}

def validate_warehouse_company(warehouse: str, company: str):
    """Validate that warehouse belongs to the given company."""
    wh_company = frappe.db.get_value("Warehouse", warehouse, "company")
    if wh_company and wh_company != company:
        raise ValidationError(
            _("Warehouse '{0}' kompaniyaga tegishli emas: {1}").format(warehouse, company)
        )

def validate_items_exist(items: List[Dict]) -> Dict:
    """Validate that all items exist in ERPNext, with mapping support."""
    valid_items = []
    errors = []
    
    # Cache for found items to speed up 11k rows
    item_cache = {}
    
    for item in items:
        original_name = item.get("item_name", "").strip()
        row_num = item.get("row_num", 0)
        
        if not original_name:
            continue

        if original_name in item_cache:
            item_code = item_cache[original_name]
        else:
            # 1. Apply mapping
            search_name = ITEM_MAPPING.get(original_name, original_name)
            
            # 2. Try exact match on name (Item Code)
            item_code = frappe.db.get_value("Item", {"name": search_name}, "name")
            
            # 3. Try exact match on item_name
            if not item_code:
                item_code = frappe.db.get_value("Item", {"item_name": search_name}, "name")
            
            # 4. Try partial match if still not found
            if not item_code:
                item_code = frappe.db.get_value("Item", {"item_name": ["like", f"%{search_name}%"]}, "name")
            
            item_cache[original_name] = item_code

        if item_code:
            item["item_code"] = item_code
            item["found"] = True
            valid_items.append(item)
        else:
            errors.append({
                "row": row_num,
                "item_name": original_name,
                "error": _("Item topilmadi: '{0}'").format(original_name)
            })
    
    return {
        "valid_items": valid_items,
        "errors": errors,
        "success": len(errors) == 0
    }

def check_duplicate_import(excel_hash: str, current_doc_name: str) -> Dict:
    """Check if this Excel file was already imported."""
    if not excel_hash:
        return {"is_duplicate": False, "existing_doc": None}
    
    existing = frappe.db.exists(
        "Ozturk Daily Sales Import",
        {
            "external_ref": excel_hash,
            "name": ["!=", current_doc_name],
            "status": "Processed"
        }
    )
    return {"is_duplicate": bool(existing), "existing_doc": existing}
