from typing import Dict, List, Optional
from dataclasses import dataclass
from contextlib import contextmanager

import frappe
from frappe import _
from frappe.utils import flt

from ozturkapp.ozturkapp.services.bom_service import bom_service


@dataclass
class StockEntryConfig:
    """Configuration for Stock Entry creation."""
    company: str
    warehouse: str
    posting_date: str
    posting_time: str = "23:59:59"
    allow_negative_stock: bool = True


class StockService:
    """
    Service for Stock Entry operations.
    
    Handles Manufacture Stock Entry creation for restaurant workflow:
    - Single warehouse mode (Variant A)
    - Batched items (Multiple finished items in one Stock Entry if supported)
    """
    
    @contextmanager
    def _stock_flags(self, allow_negative: bool = True, mute_messages: bool = True):
        """Context manager for stock operation flags."""
        original_negative = getattr(frappe.flags, "allow_negative_stock", False)
        original_mute = getattr(frappe.flags, "mute_messages", False)
        
        try:
            frappe.flags.allow_negative_stock = allow_negative
            frappe.flags.mute_messages = mute_messages
            yield
        finally:
            frappe.flags.allow_negative_stock = original_negative
            frappe.flags.mute_messages = original_mute
    
    def create_manufacture_entries(
        self,
        items: List[Dict],
        config: StockEntryConfig,
        submit: bool = True,
        **kwargs
    ) -> List[str]:
        """
        Create Manufacture Stock Entries for items with BOM.
        
        Creates one Stock Entry per item (ERPNext requires exactly 1
        finished item per Manufacture Stock Entry).
        
        Args:
            items: List of items with 'item_code', 'qty', 'bom' keys
            config: Stock entry configuration
            submit: Whether to submit entries
            
        Returns:
            List of created Stock Entry names
        """
        if not items:
            return []
        
        created_entries = []
        
        for item in items:
            with self._stock_flags(config.allow_negative_stock, mute_messages=True):
                entry_name = self._create_single_manufacture_entry(item, config, submit)
                if entry_name:
                    created_entries.append(entry_name)
        
        return created_entries
    
    def _create_single_manufacture_entry(
        self,
        item: Dict,
        config: StockEntryConfig,
        submit: bool
    ) -> Optional[str]:
        """Create a single Stock Entry for one finished item."""
        item_code = item.get("item_code")
        qty = item.get("qty", 0)
        bom = item.get("bom")
        
        if not all([item_code, qty > 0, bom]):
            return None
        
        # Get raw materials from BOM (fully exploded down to raw materials)
        raw_materials = bom_service.get_raw_materials(bom, qty, config.company)
        if not raw_materials:
            return None
        
        se = frappe.new_doc("Stock Entry")
        se.stock_entry_type = "Manufacture"
        se.set_posting_time = 1
        se.company = config.company
        se.posting_date = config.posting_date
        se.posting_time = config.posting_time
        se.from_warehouse = config.warehouse
        se.to_warehouse = config.warehouse
        se.bom_no = bom
        se.fg_completed_qty = qty
        
        # Add raw materials (consumed). Exploded BOM rows that round to zero
        # (e.g. a trace ingredient at low sold qty) are skipped — ERPNext
        # rejects the whole Stock Entry if any row's qty rounds to 0.
        for rm in raw_materials:
            if flt(rm.qty, 3) <= 0:
                continue
            se.append("items", {
                "item_code": rm.item_code,
                "qty": rm.qty,
                "uom": rm.uom,
                "s_warehouse": config.warehouse,
                "t_warehouse": None,
                "is_finished_item": 0,
                "allow_zero_valuation_rate": 1
            })
        
        # Add finished item (produced)
        item_uom = frappe.db.get_value("Item", item_code, "stock_uom") or "Nos"
        se.append("items", {
            "item_code": item_code,
            "qty": qty,
            "uom": item_uom,
            "s_warehouse": None,
            "t_warehouse": config.warehouse,
            "is_finished_item": 1,
            "bom_no": bom,
        })
        
        # Save and submit
        se.flags.ignore_permissions = True
        se.insert()
        
        if submit:
            se.submit()
            
        return se.name

    def cancel_stock_entries(self, entry_names: List[str]) -> int:
        """Cancel multiple Stock Entries."""
        cancelled = 0
        for name in entry_names:
            if not name or not frappe.db.exists("Stock Entry", name):
                continue
            se = frappe.get_doc("Stock Entry", name)
            if se.docstatus == 1:
                se.flags.ignore_permissions = True
                se.cancel()
                cancelled += 1
        return cancelled


# Singleton instance
stock_service = StockService()
