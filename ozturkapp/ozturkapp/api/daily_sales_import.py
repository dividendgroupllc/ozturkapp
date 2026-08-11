from typing import Dict, List, Any
from collections import defaultdict
import copy

import frappe
from frappe import _
from frappe.utils import nowdate

from ozturkapp.ozturkapp.utils import (
    calculate_file_hash,
    validate_import_prerequisites,
    validate_items_exist,
    check_duplicate_import
)
from ozturkapp.ozturkapp.services import (
    excel_service,
    bom_service,
    stock_service,
    invoice_service,
    StockEntryConfig,
    InvoiceConfig
)


@frappe.whitelist()
def get_default_warehouse(company: str) -> Dict:
    """Get default warehouse for a company."""
    if not company:
        return {"source_warehouse": None}
    
    warehouse = frappe.db.get_value(
        "Warehouse",
        {"company": company, "is_group": 0, "warehouse_type": "Stores"},
        "name"
    )
    
    if not warehouse:
        warehouse = frappe.db.get_value(
            "Warehouse",
            {"company": company, "is_group": 0},
            "name"
        )
    
    return {"source_warehouse": warehouse}


@frappe.whitelist()
def get_preview_data(doc_name: str) -> Dict:
    """Get preview of Excel data before processing."""
    doc = frappe.get_doc("Ozturk Daily Sales Import", doc_name)
    
    if not doc.excel_file:
        return {"success": False, "message": _("Excel fayl yuklanmagan")}
    
    try:
        excel_data = excel_service.read_sales_report(doc.excel_file)
        items = excel_data["items"]

        validation = validate_items_exist(items)
        valid_items = validation["valid_items"]

        for item in items:
            if item.get("item_code"):
                bom = bom_service.get_default_bom(item["item_code"])
                item["has_bom"] = bool(bom)
                item["bom"] = bom
                item["type"] = "MANUFACTURE" if bom else "DIRECT SALE"
            else:
                item["has_bom"] = False
                item["type"] = "NOT FOUND"

        found_items = [i for i in items if i.get("found")]
        summary = {
            "total_items": len(items),
            "found": len(found_items),
            "not_found": len(items) - len(found_items),
            "with_bom": len([i for i in items if i.get("has_bom")]),
            "without_bom": len([i for i in found_items if not i.get("has_bom")]),
            "total_qty": sum(i.get("qty", 0) for i in items),
            "total_amount": sum(i.get("qty", 0) * i.get("rate", 0) for i in items)
        }

        return {
            "success": True, 
            "items": items, 
            "summary": summary, 
            "excel_posting_date": excel_data["posting_date"]
        }
        
    except Exception as e:
        return {"success": False, "message": str(e)}


@frappe.whitelist()
def validate_excel_items(doc_name: str) -> Dict:
    """Validate items in uploaded Excel file."""
    doc = frappe.get_doc("Ozturk Daily Sales Import", doc_name)
    
    if not doc.excel_file:
        return {
            "success": False,
            "message": _("Excel fayl yuklanmagan"),
            "errors": [],
            "items": []
        }
    
    try:
        excel_data = excel_service.read_sales_report(doc.excel_file)
        items = excel_data["items"]

        if not items:
            return {
                "success": False,
                "message": _("Excel faylda sotuv topilmadi"),
                "errors": [],
                "items": []
            }
        
        validation = validate_items_exist(items)
        
        excel_hash = calculate_file_hash(doc.excel_file)
        duplicate = check_duplicate_import(excel_hash, doc_name)
        
        errors = validation["errors"]
        if duplicate["is_duplicate"]:
            errors.insert(0, {
                "row": 0,
                "item_name": "",
                "error": _("Bu Excel avval import qilingan: {0}").format(
                    duplicate["existing_doc"]
                )
            })
        
        totals = invoice_service.calculate_totals(validation["valid_items"])
        
        return {
            "success": len(errors) == 0,
            "message": _("{0} ta item topildi, {1} ta xato").format(
                len(validation["valid_items"]), len(errors)
            ),
            "errors": errors,
            "items": validation["valid_items"],
            "total_qty": totals["total_qty"],
            "total_amount": totals["total_amount"]
        }
        
    except Exception as e:
        return {"success": False, "message": str(e), "errors": [], "items": []}


@frappe.whitelist()
def process_import(doc_name: str, background: bool = False) -> Dict:
    """Process the import — always runs in background to prevent HTTP timeout."""
    doc = frappe.get_doc("Ozturk Daily Sales Import", doc_name)

    if doc.status == "Processed":
        return {"success": False, "message": _("Bu import allaqachon bajarilgan")}

    if doc.status == "Processing":
        return {"success": False, "message": _("Import hozir jarayonda")}

    # Quick pre-validation (lightweight — no heavy processing)
    if not doc.excel_file:
        return {"success": False, "message": _("Excel fayl yuklanmagan")}

    validation = validate_import_prerequisites(
        doc.company, doc.source_warehouse, str(doc.posting_date), doc.customer or ""
    )
    if not validation["success"]:
        return {"success": False, "message": validation["message"]}

    # Check duplicate before enqueueing
    excel_hash = calculate_file_hash(doc.excel_file)
    duplicate = check_duplicate_import(excel_hash, doc_name)
    if duplicate["is_duplicate"]:
        return {
            "success": False,
            "message": _("Bu Excel avval import qilingan: {0}").format(duplicate["existing_doc"])
        }

    # Mark as Processing before enqueueing to prevent double-clicks
    doc.db_set("status", "Processing")
    doc.db_set("error_log", "")
    doc.db_set("import_log", "")
    frappe.db.commit()

    # Always enqueue to background
    frappe.enqueue(
        _process_import_job,
        queue="long",
        timeout=3600,
        doc_name=doc_name
    )
    return {"success": True, "message": _("Import fonada boshlandi. Sahifani yangilab turing.")}


def _process_import_job(doc_name: str):
    """Background job wrapper."""
    try:
        result = _process_import_sync(doc_name)
        event = "restaurant_import_success" if result["success"] else "restaurant_import_failed"
        frappe.publish_realtime(
            event,
            {"doc_name": doc_name, "result": result},
            doctype="Ozturk Daily Sales Import",
            docname=doc_name
        )
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(f"Import Error: {doc_name}\n{str(e)}", "Daily Sales Import")
        try:
            doc = frappe.get_doc("Ozturk Daily Sales Import", doc_name)
            doc.db_set("status", "Failed")
            doc.db_set("error_log", str(e))
            frappe.db.commit()
        except Exception:
            pass
        frappe.publish_realtime(
            "restaurant_import_failed",
            {"doc_name": doc_name, "result": {"success": False, "message": str(e)}},
            doctype="Ozturk Daily Sales Import",
            docname=doc_name
        )


def _process_import_sync(doc_name: str) -> Dict:
    """Synchronous import processing with multi-date support."""
    doc = frappe.get_doc("Ozturk Daily Sales Import", doc_name)
    
    log_lines = []
    def log(msg, publish=True):
        log_lines.append(msg)
        current_log = "\n".join(log_lines)
        frappe.db.set_value("Ozturk Daily Sales Import", doc_name, "import_log", current_log, update_modified=False)
        if publish:
            frappe.publish_realtime(
                "restaurant_import_log",
                {"doc_name": doc_name, "msg": msg, "full_log": current_log},
                doctype="Ozturk Daily Sales Import",
                docname=doc_name
            )
    
    if doc.status == "Processed":
        return {"success": False, "message": _("Bu import allaqachon bajarilgan")}
    
    # Status already set to Processing by process_import(), but ensure it
    if doc.status != "Processing":
        doc.db_set("status", "Processing")
        doc.db_set("error_log", "")
        frappe.db.commit()
    
    log("=" * 50)
    log(f"IMPORT BOSHLANDI: {nowdate()}")
    log(f"Company: {doc.company}")
    log(f"Warehouse: {doc.source_warehouse}")
    log("=" * 50)
    
    try:
        # 1. Validate prerequisites
        log("\n📋 1. Tekshiruvlar...")
        validation = validate_import_prerequisites(
            doc.company, doc.source_warehouse, str(doc.posting_date), doc.customer or ""
        )
        if not validation["success"]:
            raise Exception(validation["message"])
        
        # 2. Read Excel
        log("\n📊 2. Excel o'qilmoqda...")
        excel_data = excel_service.read_sales_report(doc.excel_file)
        items = excel_data["items"]
        if not items:
            raise Exception(_("Excel faylda sotuv topilmadi"))
        log(f"   ✅ {len(items)} ta qator o'qildi")

        # 3. Check duplicate
        log("\n🔍 3. Dublikat tekshiruvi...")
        excel_hash = calculate_file_hash(doc.excel_file)
        duplicate = check_duplicate_import(excel_hash, doc_name)
        if duplicate["is_duplicate"]:
            raise Exception(_("Bu Excel avval import qilingan: {0}").format(duplicate["existing_doc"]))
        
        # 4. Validate and match items
        log("\n🔗 4. Itemlar tekshirilmoqda...")
        item_validation = validate_items_exist(items)
        if item_validation["errors"]:
            for e in item_validation["errors"]:
                log(f"   ❌ Row {e['row']}: {e['error']}")
            raise Exception(_("Itemlarni tekshirishda xatolik yuz berdi. Logga qarang."))
        
        valid_items = item_validation["valid_items"]
        log(f"   ✅ {len(valid_items)} ta item topildi")
        
        # Group items by date
        items_by_date = defaultdict(list)
        fallback_date = str(doc.posting_date)
        for item in valid_items:
            d = item.get("date") or fallback_date
            items_by_date[d].append(item)
            
        sorted_dates = sorted(items_by_date.keys())
        log(f"   📅 Jami {len(sorted_dates)} xil sana aniqlandi")

        all_se_names = []
        all_si_names = []
        total_amount = 0
        
        # Resume support: load previously committed SE/SI names from a failed run
        already_done_dates = set()
        if doc.sales_invoice:
            existing_si_names = [s.strip() for s in doc.sales_invoice.split(",") if s.strip()]
            all_si_names.extend(existing_si_names)
            # Find which dates already have SIs
            for si_name in existing_si_names:
                si_date = frappe.db.get_value("Sales Invoice", si_name, "posting_date")
                if si_date:
                    already_done_dates.add(str(si_date))
        if doc.stock_entry:
            existing_se_names = [s.strip() for s in doc.stock_entry.split(",") if s.strip()]
            all_se_names.extend(existing_se_names)
        
        if already_done_dates:
            log(f"   ♻️ Resume: {len(already_done_dates)} ta sana oldin bajarilgan, o'tkazib yuboriladi")
        
        # Process each date
        for idx, d in enumerate(sorted_dates, 1):
            date_items = items_by_date[d]
            
            # Skip already processed dates (resume mode)
            if d in already_done_dates:
                log(f"\n--- [{idx}/{len(sorted_dates)}] SANA: {d} — ⏭️ oldin bajarilgan, skip ---")
                continue
            
            log(f"\n--- [{idx}/{len(sorted_dates)}] SANA: {d} ({len(date_items)} ta item) ---")
            
            try:
                # 5. Categorize by BOM (deep copy to prevent mutation across dates)
                date_items_copy = copy.deepcopy(date_items)
                categorized = bom_service.categorize_items_by_bom(date_items_copy)
                items_with_bom = categorized["with_bom"]
                
                # 6. Create Manufacture Stock Entries
                if items_with_bom:
                    config = StockEntryConfig(
                        company=doc.company,
                        warehouse=doc.source_warehouse,
                        posting_date=d,
                        allow_negative_stock=bool(doc.allow_negative_stock)
                    )
                    se_names = stock_service.create_manufacture_entries(items_with_bom, config, submit=True)
                    all_se_names.extend(se_names)
                    log(f"   ✅ {len(se_names)} ta Stock Entry yaratildi")
                    # Update doc incrementally
                    doc.db_set("stock_entry", ", ".join(all_se_names))
                
                # 7. Create Sales Invoice
                invoice_config = InvoiceConfig(
                    company=doc.company,
                    warehouse=doc.source_warehouse,
                    posting_date=d,
                    customer=doc.customer
                )
                si_name = invoice_service.create_sales_invoice(date_items, invoice_config, submit=True)
                all_si_names.append(si_name)
                log(f"   ✅ Sales Invoice yaratildi: {si_name}")
                # Update doc incrementally
                doc.db_set("sales_invoice", ", ".join(all_si_names))
                
                totals = invoice_service.calculate_totals(date_items)
                total_amount += totals["total_amount"]
                
                frappe.db.commit()
                
            except Exception as date_err:
                frappe.db.rollback()
                log(f"   ❌ SANA {d} BO'YICHA XATO: {str(date_err)}")
                # After rollback, re-read doc to restore incremental SE/SI refs
                # that were committed in previous successful dates
                doc.reload()
                raise date_err

        # 8. Finalize
        doc.db_set("external_ref", excel_hash)
        doc.db_set("status", "Processed")
        
        log("\n" + "=" * 50)
        log("✅ IMPORT MUVAFFAQIYATLI YAKUNLANDI")
        log("=" * 50)
        log(f"📊 Jami sanalar: {len(sorted_dates)}")
        log(f"💰 Jami summa: {total_amount:,.0f} UZS")
        
        frappe.db.commit()
        
        return {
            "success": True,
            "message": _("Import muvaffaqiyatli"),
            "stock_entries": all_se_names,
            "sales_invoice": all_si_names,
            "total_items": len(valid_items),
            "total_amount": total_amount
        }
        
    except Exception as e:
        frappe.db.rollback()
        log(f"\n❌ XATO: {str(e)}")
        # Re-read doc after rollback to get last committed state
        doc.reload()
        doc.db_set("status", "Failed")
        doc.db_set("error_log", str(e))
        frappe.db.commit()
        frappe.log_error(f"Import Error: {doc_name}\n{str(e)}", "Daily Sales Import")
        return {"success": False, "message": str(e)}


@frappe.whitelist()
def cancel_import(doc_name: str) -> Dict:
    """Cancel a processed import."""
    doc = frappe.get_doc("Ozturk Daily Sales Import", doc_name)
    
    if doc.status not in ["Processed", "Failed", "Processing"]:
        return {"success": False, "message": _("Faqat 'Processed', 'Failed' yoki 'Processing' statusdagi importni bekor qilish mumkin")}
    
    try:
        # Cancel Sales Invoices
        if doc.sales_invoice:
            si_names = [si.strip() for si in doc.sales_invoice.split(",") if si.strip()]
            for si in si_names:
                invoice_service.cancel_invoice(si)
        
        # Cancel Stock Entries
        if doc.stock_entry:
            se_names = [se.strip() for se in doc.stock_entry.split(",") if se.strip()]
            stock_service.cancel_stock_entries(se_names)
        
        doc.db_set("status", "Draft")
        doc.db_set("external_ref", "")
        doc.db_set("import_log", "")
        doc.db_set("stock_entry", "")
        doc.db_set("sales_invoice", "")
        doc.db_set("error_log", "")
        
        frappe.db.commit()
        return {"success": True, "message": _("Import bekor qilindi")}
    except Exception as e:
        frappe.db.rollback()
        return {"success": False, "message": str(e)}
