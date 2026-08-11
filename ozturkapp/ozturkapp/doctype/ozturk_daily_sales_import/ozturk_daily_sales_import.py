import frappe
from frappe import _
from frappe.model.document import Document

from ozturkapp.ozturkapp.utils.validators import validate_warehouse_company, ValidationError


class OzturkDailySalesImport(Document):
    """
    Restaurant Daily Sales Import Document.
    
    Workflow:
    1. User uploads Excel file
    2. Preview shows items with BOM status
    3. Process Import creates:
       - Manufacture Stock Entries (for BOM items)
       - Sales Invoice (for all items)
    """
    
    def validate(self):
        """Validate document before save."""
        self._validate_warehouse()
    
    def _validate_warehouse(self):
        """Ensure warehouse belongs to company."""
        if self.source_warehouse and self.company:
            try:
                validate_warehouse_company(self.source_warehouse, self.company)
            except ValidationError as e:
                frappe.throw(str(e))
    
    def before_submit(self):
        """Prevent manual submission."""
        frappe.throw(
            _("This document cannot be submitted. Use 'Process Import' button.")
        )
    
    def on_trash(self):
        """Cleanup before deletion."""
        if self.status == "Processed":
            frappe.throw(
                _("Cannot delete processed import. Cancel it first.")
            )


# =============================================================================
# WHITELISTED METHODS (Delegated to API module)
# =============================================================================
# 
# All whitelisted methods are in:
# ozturkapp/api/daily_sales_import.py
#
# Client Script calls:
#   frappe.call({
#       method: 'ozturkapp.api.daily_sales_import.process_import',
#       args: { doc_name: frm.doc.name }
#   });
#
# =============================================================================
