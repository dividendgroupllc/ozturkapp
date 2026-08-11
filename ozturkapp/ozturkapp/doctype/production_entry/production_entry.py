# Copyright (c) 2026, Ozturkapp and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class ProductionEntry(Document):
    def validate(self):
        self.set_status()
        self.validate_qty()
        self.validate_bom()
        self.update_available_qty()

    def on_submit(self):
        self.db_set("status", "Submitted")
        self.create_stock_entry()

    def on_cancel(self):
        self.db_set("status", "Cancelled")
        self.cancel_stock_entry()

    def set_status(self, status=None):
        if status:
            self.status = status
        elif self.docstatus == 0:
            self.status = "Draft"
        elif self.docstatus == 1:
            self.status = "Submitted"
        elif self.docstatus == 2:
            self.status = "Cancelled"

    def validate_qty(self):
        if flt(self.qty_to_manufacture) <= 0:
            frappe.throw(_("Ishlab chiqarish miqdori 0 dan katta bo'lishi kerak"))

        insufficient = []
        for item in self.items:
            if flt(item.required_qty) <= 0:
                frappe.throw(
                    _("{0} uchun kerakli miqdor 0 dan katta bo'lishi kerak").format(item.item_code)
                )
            if flt(item.available_qty) < flt(item.required_qty):
                insufficient.append(
                    f"{item.item_code}: kerak {flt(item.required_qty):.2f}, "
                    f"mavjud {flt(item.available_qty):.2f}"
                )

        if insufficient:
            frappe.msgprint(
                _("Quyidagi materiallarda yetarli miqdor yo'q:\n") + "\n".join(insufficient),
                title=_("Ogohlantirish"),
                indicator="orange",
            )

    def validate_bom(self):
        if self.bom_no and self.item_to_manufacture:
            bom_item, is_active, docstatus = frappe.db.get_value(
                "BOM", self.bom_no, ["item", "is_active", "docstatus"]
            )
            if bom_item != self.item_to_manufacture:
                frappe.throw(
                    _("BOM {0} {1} mahsuloti uchun emas").format(self.bom_no, self.item_to_manufacture)
                )
            if not is_active or docstatus != 1:
                frappe.throw(_("BOM {0} aktiv yoki tasdiqlanmagan").format(self.bom_no))

    def update_available_qty(self):
        """Barcha materiallar uchun mavjud miqdorni yangilash."""
        for item in self.items:
            if item.item_code and item.source_warehouse:
                item.available_qty = _get_stock_balance(
                    item.item_code,
                    item.source_warehouse,
                    self.posting_date,
                    self.posting_time,
                )

    def create_stock_entry(self):
        """Submit bo'lganda Stock Entry (Manufacture) yaratish."""
        if self.stock_entry and frappe.db.exists("Stock Entry", self.stock_entry):
            frappe.throw(
                _("Stock Entry {0} allaqachon mavjud").format(self.stock_entry)
            )

        se = frappe.new_doc("Stock Entry")
        se.stock_entry_type = "Manufacture"
        se.posting_date = self.posting_date
        se.posting_time = self.posting_time
        se.set_posting_time = 1
        se.company = self.company
        se.from_bom = 1
        se.bom_no = self.bom_no
        se.fg_completed_qty = self.qty_to_manufacture
        se.custom_production_entry = self.name

        # Xom ashyolar (chiqim)
        for item in self.items:
            uom = item.uom or frappe.get_cached_value("Item", item.item_code, "stock_uom")
            se.append("items", {
                "item_code": item.item_code,
                "qty": item.required_qty,
                "s_warehouse": item.source_warehouse,
                "uom": uom,
                "stock_uom": uom,
                "conversion_factor": 1,
            })

        # Tayyor mahsulot (kirim)
        fg_uom = frappe.get_cached_value("Item", self.item_to_manufacture, "stock_uom")
        se.append("items", {
            "item_code": self.item_to_manufacture,
            "qty": self.qty_to_manufacture,
            "t_warehouse": self.target_warehouse,
            "is_finished_item": 1,
            "uom": fg_uom,
            "stock_uom": fg_uom,
            "conversion_factor": 1,
        })

        se.flags.ignore_permissions = True
        se.insert()
        se.submit()

        self.db_set("stock_entry", se.name)
        frappe.msgprint(
            _("Stock Entry {0} yaratildi").format(
                frappe.utils.get_link_to_form("Stock Entry", se.name)
            )
        )

    def cancel_stock_entry(self):
        """Bog'liq Stock Entry ni bekor qilish."""
        if not self.stock_entry:
            return
        if not frappe.db.exists("Stock Entry", self.stock_entry):
            frappe.msgprint(
                _("Stock Entry {0} topilmadi — o'tkazib yuborildi").format(self.stock_entry),
                indicator="orange",
            )
            return
        se = frappe.get_doc("Stock Entry", self.stock_entry)
        if se.docstatus == 1:
            se.flags.ignore_permissions = True
            se.cancel()
            frappe.msgprint(_("Stock Entry {0} bekor qilindi").format(self.stock_entry))
        elif se.docstatus == 2:
            frappe.msgprint(
                _("Stock Entry {0} allaqachon bekor qilingan").format(self.stock_entry),
                indicator="orange",
            )


# ─────────────────────────────────────────────────────
#  Whitelist API
# ─────────────────────────────────────────────────────

@frappe.whitelist()
def get_bom_for_item(item_code):
    """Item uchun default (yoki birinchi aktiv) BOM ni qaytarish."""
    if not item_code:
        return None

    bom = frappe.db.get_value(
        "BOM",
        {"item": item_code, "is_active": 1, "is_default": 1, "docstatus": 1},
        "name",
    )
    if not bom:
        bom = frappe.db.get_value(
            "BOM",
            {"item": item_code, "is_active": 1, "docstatus": 1},
            "name",
            order_by="creation desc",
        )
    return bom


@frappe.whitelist()
def get_bom_items(bom_no, qty_to_manufacture, posting_date=None, posting_time=None, source_warehouse=None):
    """BOM dan materiallarni olish, required_qty ni hisoblash."""
    if not bom_no:
        return []

    qty_to_manufacture = flt(qty_to_manufacture)
    if qty_to_manufacture <= 0:
        frappe.throw(_("Ishlab chiqarish miqdori 0 dan katta bo'lishi kerak"))

    bom = frappe.get_doc("BOM", bom_no)
    bom_qty = flt(bom.quantity) or 1

    result = []
    for bom_item in bom.items:
        required_qty = flt(bom_item.qty) * qty_to_manufacture / bom_qty
        warehouse = source_warehouse or ""
        available_qty = _get_stock_balance(bom_item.item_code, warehouse, posting_date, posting_time)

        result.append({
            "item_code":       bom_item.item_code,
            "item_name":       bom_item.item_name,
            "source_warehouse": warehouse,
            "required_qty":    required_qty,
            "available_qty":   available_qty,
            "uom":             bom_item.stock_uom or bom_item.uom,
        })

    return result


@frappe.whitelist()
def get_available_qty_for_item(item_code, warehouse, posting_date=None, posting_time=None):
    """Bitta material uchun mavjud miqdor (frontend dan chaqiriladi)."""
    return _get_stock_balance(item_code, warehouse, posting_date, posting_time)


def _get_stock_balance(item_code, warehouse, posting_date=None, posting_time=None):
    if not item_code or not warehouse:
        return 0
    if not posting_date:
        posting_date = frappe.utils.today()
    if not posting_time:
        posting_time = frappe.utils.nowtime()

    from erpnext.stock.utils import get_stock_balance
    return flt(get_stock_balance(item_code, warehouse, posting_date, posting_time))
