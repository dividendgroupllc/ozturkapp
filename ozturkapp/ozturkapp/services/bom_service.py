from typing import Dict, List, Optional
from dataclasses import dataclass

import frappe


@dataclass
class RawMaterial:
    """Raw material item from BOM."""
    item_code: str
    qty: float
    uom: str


class BOMService:
    """
    Service for BOM operations.
    
    Handles:
    - Finding default BOM for items
    - Exploding BOM to get raw materials
    - Calculating required quantities
    """
    
    def get_default_bom(self, item_code: str) -> Optional[str]:
        """
        Get default active BOM for an item.
        
        Args:
            item_code: Item code
            
        Returns:
            BOM name or None if not found
        """
        return frappe.db.get_value(
            "BOM",
            {
                "item": item_code,
                "is_default": 1,
                "is_active": 1,
                "docstatus": 1
            },
            "name"
        )
    
    def get_raw_materials(self, bom_name: str, qty: float, company: str) -> List[RawMaterial]:
        """
        Get exploded raw materials from BOM with calculated quantities.

        Uses ERPNext's BOM Explosion Item table (fetch_exploded=1) so that
        semi-finished sub-assemblies are resolved down to their underlying
        raw materials, instead of stopping at the BOM's direct 1st-level rows.

        Args:
            bom_name: BOM document name
            qty: Required quantity of finished item
            company: Company (required by ERPNext's BOM explosion helper)

        Returns:
            List of RawMaterial objects
        """
        if not bom_name or qty <= 0:
            return []

        from erpnext.manufacturing.doctype.bom.bom import get_bom_items_as_dict

        item_dict = get_bom_items_as_dict(
            bom=bom_name,
            company=company,
            qty=qty,
            fetch_exploded=1
        )

        return [
            RawMaterial(
                item_code=item["item_code"],
                qty=item["qty"],
                uom=item["stock_uom"]
            )
            for item in item_dict.values()
        ]
    
    def categorize_items_by_bom(self, items: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Categorize items by BOM availability.
        
        Args:
            items: List of items with 'item_code' key
            
        Returns:
            {
                "with_bom": items that have BOM,
                "without_bom": items without BOM
            }
        """
        with_bom = []
        without_bom = []
        
        for item in items:
            item_code = item.get("item_code")
            if not item_code:
                continue
            
            bom = self.get_default_bom(item_code)
            
            if bom:
                item["bom"] = bom
                item["has_bom"] = True
                with_bom.append(item)
            else:
                item["has_bom"] = False
                without_bom.append(item)
        
        return {
            "with_bom": with_bom,
            "without_bom": without_bom
        }


# Singleton instance
bom_service = BOMService()
