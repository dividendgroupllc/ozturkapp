# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Branch Stock Transfer — kompaniyalar o'rtasida tovarni TAN NARXDA ko'chirish.

    O'zturk Sklad  ──(tovar)──►  O'zturk Maksim Gorkiy

Submit:
    1. Sales Invoice    (manba kompaniya, update_stock=1) → manba ombordan CHIQIM
    2. Purchase Invoice (maqsad kompaniya, update_stock=1) → maqsad omborga KIRIM
       `inter_company_invoice_reference` orqali SI ga bog'lanadi.
Cancel: teskari tartibda — avval PI, keyin SI.

Narx doim manba ombordagi tan narx (valuation), USTAMASIZ — SI va PI summalari
teng, sklad kompaniyasida foyda/zarar chiqmaydi.

Jazira (`jazira_app`) dagi shu nomli DocType'dan ko'chirilgan. Farqlar:
  * narx turi (`price_basis`) yo'q — faqat tan narx. Jazira'da "Manual" tanlansa
    narx 0 ga yozilib ketardi;
  * miqdor doim zaxira birligida (stock UOM) — boshqa birlik tanlansa
    `conversion_factor = 1` bilan miqdor noto'g'ri chiqardi.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_link_to_form, nowtime, today

from erpnext.stock.utils import validate_warehouse_company


class BranchStockTransfer(Document):
    def validate(self):
        self._set_status()
        self._validate_companies()
        self._validate_warehouses()
        self._explode_and_rate()
        self._compute_totals()

    def _set_status(self):
        self.status = {0: "Draft", 1: "Completed", 2: "Cancelled"}.get(self.docstatus, "Draft")

    def _validate_companies(self):
        if self.from_company and self.from_company == self.to_company:
            frappe.throw(
                _("Manba va maqsad kompaniya bir xil bo'lishi mumkin emas ({0}).").format(self.from_company)
            )

    def _validate_warehouses(self):
        """Har bir ombor o'z kompaniyasiga tegishli bo'lishi shart."""
        if self.from_warehouse and self.from_company:
            validate_warehouse_company(self.from_warehouse, self.from_company)
        if self.to_warehouse and self.to_company:
            validate_warehouse_company(self.to_warehouse, self.to_company)

    # ── BOM portlatish + tan narx ─────────────────────────────────
    def _explode_and_rate(self):
        """`source_type='BOM'` qatorlarni komponentlariga (bir pog'onali) yoyadi va
        har qatorga tan narx qo'yadi. Idempotent — yoyilgan qator 'Item' bo'lib
        qoladi va qayta yoyilmaydi."""
        rows = []
        for row in self.items:
            if row.source_type == "BOM":
                if not row.reference:
                    frappe.throw(_("{0}-qatorda BOM tanlanmagan.").format(row.idx))
                if flt(row.qty) <= 0:
                    frappe.throw(_("BOM {0} uchun miqdor 0 dan katta bo'lishi kerak.").format(row.reference))
                rows.extend(self._explode_bom(row.reference, flt(row.qty)))
            else:
                item = row.reference or row.item_code
                if not item:
                    frappe.throw(_("{0}-qatorda tovar tanlanmagan.").format(row.idx))
                if flt(row.qty) <= 0:
                    frappe.throw(_("{0}-qatorda miqdor 0 dan katta bo'lishi kerak.").format(row.idx))
                rows.append({
                    "source_type": "Item",
                    "reference": item,
                    "item_code": item,
                    "qty": flt(row.qty),
                    "from_bom": row.from_bom,
                })

        for r in rows:
            item = frappe.get_cached_value("Item", r["item_code"], ["item_name", "stock_uom", "is_stock_item"], as_dict=True)
            if not item:
                frappe.throw(_("Tovar topilmadi: {0}").format(r["item_code"]))
            if not item.is_stock_item:
                frappe.throw(_("{0} zaxira tovari emas — uni ombordan o'tkazib bo'lmaydi.").format(item.item_name))
            r["item_name"] = item.item_name
            r["uom"] = item.stock_uom
            r["rate"] = self._get_valuation_rate(r["item_code"], flt(r["qty"]))
            r["amount"] = flt(r["qty"]) * flt(r["rate"])

        self.set("items", rows)

    def _explode_bom(self, bom_no, bom_qty):
        """BOM'ni bir pog'onali yoyadi: komponent = bom_qty × (qty / bom.quantity)."""
        bom = frappe.get_doc("BOM", bom_no)
        base_qty = flt(bom.quantity) or 1.0
        return [
            {
                "source_type": "Item",
                "reference": c.item_code,
                "item_code": c.item_code,
                "qty": flt(bom_qty) * flt(c.stock_qty or c.qty) / base_qty,
                "from_bom": bom_no,
            }
            for c in bom.items
        ]

    def _get_valuation_rate(self, item_code, qty):
        """Manba ombordagi joriy tan narx: avval Bin, bo'lmasa ERPNext'ning chiqim narxi."""
        rate = flt(frappe.db.get_value(
            "Bin", {"item_code": item_code, "warehouse": self.from_warehouse}, "valuation_rate"
        ))
        if rate > 0:
            return rate

        from erpnext.stock.utils import get_incoming_rate

        try:
            rate = flt(get_incoming_rate(
                {
                    "item_code": item_code,
                    "warehouse": self.from_warehouse,
                    "posting_date": self.posting_date or today(),
                    "posting_time": self.posting_time or nowtime(),
                    "qty": -1 * flt(qty),
                    "company": self.from_company,
                    "voucher_type": "Sales Invoice",
                    "serial_and_batch_bundle": None,
                },
                raise_error_if_no_rate=False,
            ))
        except Exception:
            rate = 0

        if rate <= 0:
            frappe.msgprint(
                _("{0} uchun {1} omborida tan narx topilmadi — 0 olindi.").format(item_code, self.from_warehouse),
                indicator="orange",
                alert=True,
            )
        return rate

    def _compute_totals(self):
        self.total_qty = sum(flt(r.qty) for r in self.items)
        self.total_amount = sum(flt(r.amount) for r in self.items)

    # ── Submit: inter-company SI + PI ─────────────────────────────
    def on_submit(self):
        if self.sales_invoice:
            return      # ikki marta yaratmaymiz

        customer = get_internal_party("Customer", self.to_company)
        supplier = get_internal_party("Supplier", self.from_company)

        # commit yo'q — xato bo'lsa Frappe butun so'rovni qaytaradi (SI ham, PI ham)
        si = self._create_sales_invoice(customer)
        pi = self._create_purchase_invoice(si, supplier)

        self.db_set("sales_invoice", si.name)
        self.db_set("purchase_invoice", pi.name)
        self.db_set("status", "Completed")

        frappe.msgprint(
            _("Sales Invoice {0} va Purchase Invoice {1} yaratildi (tan narxda).").format(
                get_link_to_form("Sales Invoice", si.name), get_link_to_form("Purchase Invoice", pi.name)
            ),
            indicator="green",
            alert=True,
        )

    def _create_sales_invoice(self, customer):
        """Manba kompaniya: ombordan chiqim, narx = tan narx, soliqsiz."""
        si = frappe.new_doc("Sales Invoice")
        si.company = self.from_company
        si.customer = customer
        si.posting_date = self.posting_date
        si.posting_time = self.posting_time
        si.set_posting_time = 1
        si.due_date = self.posting_date
        si.update_stock = 1
        si.set_warehouse = self.from_warehouse
        si.ignore_pricing_rule = 1
        si.taxes_and_charges = None
        si.selling_price_list = None
        si.remarks = _("Branch Stock Transfer {0}").format(self.name)

        for line in self.items:
            si.append("items", {
                "item_code": line.item_code,
                "qty": flt(line.qty),
                "uom": line.uom,
                "conversion_factor": 1,
                "rate": flt(line.rate),
                "price_list_rate": flt(line.rate),
                "warehouse": self.from_warehouse,
                # tan narx 0 bo'lsa ham to'xtatmaymiz — validate'da ogohlantirilgan
                "allow_zero_valuation_rate": 1,
            })

        si.flags.ignore_permissions = True
        si.run_method("calculate_taxes_and_totals")
        si.insert(ignore_permissions=True)
        si.submit()
        return si

    def _create_purchase_invoice(self, si, supplier):
        """Maqsad kompaniya: omborga kirim, SI ga bog'langan.

        PI qo'lda tuziladi (mapper emas): ombor oldindan maqsad omborga qo'yiladi,
        aks holda `get_item_details` uni tovarning default ombori (sklad) bilan
        almashtirardi."""
        inventory_account = frappe.get_cached_value("Company", self.to_company, "default_inventory_account")

        pi = frappe.new_doc("Purchase Invoice")
        pi.company = self.to_company
        pi.supplier = supplier
        pi.is_internal_supplier = 1
        pi.represents_company = self.from_company
        pi.inter_company_invoice_reference = si.name
        pi.bill_no = si.name
        pi.bill_date = self.posting_date
        pi.posting_date = self.posting_date
        pi.posting_time = self.posting_time
        pi.set_posting_time = 1
        pi.due_date = self.posting_date
        pi.update_stock = 1
        pi.set_warehouse = self.to_warehouse
        pi.ignore_pricing_rule = 1
        pi.buying_price_list = None
        pi.taxes_and_charges = None
        pi.remarks = _("Branch Stock Transfer {0}").format(self.name)

        for si_item, line in zip(si.items, self.items):
            row = {
                "item_code": line.item_code,
                "qty": flt(line.qty),
                "uom": line.uom,
                "conversion_factor": 1,
                "rate": flt(line.rate),
                "price_list_rate": flt(line.rate),
                "warehouse": self.to_warehouse,
                "sales_invoice_item": si_item.name,
                "allow_zero_valuation_rate": 1,
            }
            if inventory_account:
                row["expense_account"] = inventory_account
            pi.append("items", row)

        pi.flags.ignore_permissions = True
        pi.run_method("calculate_taxes_and_totals")
        pi.insert(ignore_permissions=True)
        pi.submit()
        return pi

    # ── Cancel: teskari tartibda ──────────────────────────────────
    def on_cancel(self):
        _cancel_if_submitted("Purchase Invoice", self.purchase_invoice)
        _cancel_if_submitted("Sales Invoice", self.sales_invoice)
        self.db_set("status", "Cancelled")


def get_internal_party(doctype, company):
    """`company` ni ifodalovchi ichki Customer/Supplier."""
    flag = "is_internal_customer" if doctype == "Customer" else "is_internal_supplier"
    name = frappe.db.get_value(doctype, {flag: 1, "represents_company": company}, "name")
    if not name:
        frappe.throw(_("{0} kompaniyasi uchun ichki {1} topilmadi ({2}=1, represents_company={0}).").format(
            company, doctype, flag
        ))
    return name


def _cancel_if_submitted(doctype, name):
    if not name or not frappe.db.exists(doctype, name):
        return
    doc = frappe.get_doc(doctype, name)
    if doc.docstatus == 1:
        doc.flags.ignore_permissions = True
        doc.cancel()


@frappe.whitelist()
def explode_boms(docname):
    """BOM qatorlarini yoyib, narxlab saqlaydi (submitdan oldin ko'rish uchun)."""
    doc = frappe.get_doc("Branch Stock Transfer", docname)
    doc.check_permission("write")
    doc.save()
    return {"items": [r.as_dict() for r in doc.items], "total_amount": doc.total_amount}


@frappe.whitelist()
def get_item_rate(item_code, from_warehouse, from_company, qty=1, posting_date=None):
    """Bitta tovarning tan narxi (formadagi qator uchun)."""
    frappe.has_permission("Branch Stock Transfer", "read", throw=True)
    if not item_code:
        return {"rate": 0, "uom": None}
    tmp = frappe.new_doc("Branch Stock Transfer")
    tmp.from_warehouse = from_warehouse
    tmp.from_company = from_company
    tmp.posting_date = posting_date or today()
    return {
        "rate": tmp._get_valuation_rate(item_code, flt(qty)),
        "uom": frappe.get_cached_value("Item", item_code, "stock_uom"),
    }
