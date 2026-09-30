# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Branch Stock Transfer: sklad kompaniyasidan filialga tan narxda o'tkazma.

Test o'z tovari va sklad qoldig'ini yaratadi (Material Receipt), shuning uchun
faqat ikkita kompaniya, ularning omborlari va ichki kontragentlar kerak —
bo'lmasa SKIP.
"""

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, get_time, today

FROM_COMPANY = "O'zturk Sklad"
TO_COMPANY = "O'zturk Maksim Gorkiy"
TO_WAREHOUSE = "Sklad Maksim Gorkiy - OMG"
ITEM = "_Test BST Xomashyo"
RATE = 12500


def _from_warehouse():
    return frappe.db.get_value("Warehouse", {"company": FROM_COMPANY, "is_group": 0}, "name",
                               order_by="name asc")


def _prereqs_ok():
    return bool(
        frappe.db.exists("Company", FROM_COMPANY)
        and frappe.db.exists("Company", TO_COMPANY)
        and frappe.db.exists("Warehouse", TO_WAREHOUSE)
        and _from_warehouse()
        and frappe.db.exists("Customer", {"is_internal_customer": 1, "represents_company": TO_COMPANY})
        and frappe.db.exists("Supplier", {"is_internal_supplier": 1, "represents_company": FROM_COMPANY})
    )


def _bin_qty(item, warehouse):
    return flt(frappe.db.get_value("Bin", {"item_code": item, "warehouse": warehouse}, "actual_qty"))


@unittest.skipUnless(_prereqs_ok(), "Kompaniyalar yoki ichki kontragentlar topilmadi")
class TestBranchStockTransfer(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.from_wh = _from_warehouse()
        if not frappe.db.exists("Item", {"item_name": ITEM}):
            frappe.get_doc({
                "doctype": "Item", "item_name": ITEM, "item_group": "All Item Groups",
                "stock_uom": "Kg", "is_stock_item": 1,
            }).insert(ignore_permissions=True)
        cls.item = frappe.db.get_value("Item", {"item_name": ITEM}, "name")
        se = frappe.get_doc({
            "doctype": "Stock Entry", "stock_entry_type": "Material Receipt", "company": FROM_COMPANY,
            "items": [{"item_code": cls.item, "qty": 10, "t_warehouse": cls.from_wh,
                       "basic_rate": RATE, "uom": "Kg", "conversion_factor": 1}],
        })
        se.insert(ignore_permissions=True)
        se.submit()

    def _new(self, qty=4, row=None):
        doc = frappe.new_doc("Branch Stock Transfer")
        doc.posting_date = today()
        doc.from_company, doc.from_warehouse = FROM_COMPANY, self.from_wh
        doc.to_company, doc.to_warehouse = TO_COMPANY, TO_WAREHOUSE
        doc.append("items", row or {"source_type": "Item", "reference": self.item, "qty": qty})
        return doc

    def test_transfer_at_cost_and_cancel(self):
        src_before, dst_before = _bin_qty(self.item, self.from_wh), _bin_qty(self.item, TO_WAREHOUSE)

        doc = self._new(qty=4)
        doc.posting_time = "18:30:00"
        doc.insert()
        self.assertAlmostEqual(flt(doc.items[0].rate), RATE, places=2)      # tan narx, ustamasiz
        self.assertEqual(doc.items[0].uom, "Kg")                            # zaxira birligi
        doc.submit()

        si = frappe.get_doc("Sales Invoice", doc.sales_invoice)
        pi = frappe.get_doc("Purchase Invoice", doc.purchase_invoice)
        self.assertEqual((si.company, pi.company), (FROM_COMPANY, TO_COMPANY))
        self.assertEqual((si.update_stock, pi.update_stock), (1, 1))
        self.assertAlmostEqual(flt(si.grand_total), 4 * RATE, places=2)
        self.assertAlmostEqual(flt(si.grand_total), flt(pi.grand_total), places=2)
        self.assertEqual(pi.inter_company_invoice_reference, si.name)
        self.assertEqual(pi.items[0].warehouse, TO_WAREHOUSE)
        # Hujjat vaqti SI va PI ga o'tadi (orqa sana bilan o'tkazish uchun)
        self.assertEqual(get_time(si.posting_time), get_time("18:30:00"))
        self.assertEqual(get_time(pi.posting_time), get_time("18:30:00"))

        # Tovar skladdan chiqib filialga kirdi
        self.assertAlmostEqual(_bin_qty(self.item, self.from_wh), src_before - 4)
        self.assertAlmostEqual(_bin_qty(self.item, TO_WAREHOUSE), dst_before + 4)

        # Filialda kirim tan narxda baholangan
        in_rate = frappe.db.get_value("Stock Ledger Entry",
                                      {"voucher_no": pi.name, "is_cancelled": 0}, "incoming_rate")
        self.assertAlmostEqual(flt(in_rate), RATE, places=2)

        # Sklad kompaniyasida foyda yo'q: tushum = tannarx
        income = frappe.db.sql("""select sum(credit) - sum(debit) from `tabGL Entry` gle
            join tabAccount a on a.name = gle.account
            where gle.voucher_no = %s and gle.is_cancelled = 0 and a.root_type = 'Income'""", si.name)[0][0]
        cogs = frappe.db.sql("""select sum(debit) - sum(credit) from `tabGL Entry` gle
            join tabAccount a on a.name = gle.account
            where gle.voucher_no = %s and gle.is_cancelled = 0 and a.account_type = 'Cost of Goods Sold'""",
                             si.name)[0][0]
        self.assertAlmostEqual(flt(income), flt(cogs), places=2)

        doc.cancel()
        self.assertEqual(doc.status, "Cancelled")
        self.assertEqual(frappe.db.get_value("Sales Invoice", si.name, "docstatus"), 2)
        self.assertEqual(frappe.db.get_value("Purchase Invoice", pi.name, "docstatus"), 2)
        self.assertAlmostEqual(_bin_qty(self.item, self.from_wh), src_before)
        self.assertAlmostEqual(_bin_qty(self.item, TO_WAREHOUSE), dst_before)

    def test_same_company_rejected(self):
        doc = self._new()
        doc.to_company, doc.to_warehouse = FROM_COMPANY, self.from_wh
        self.assertRaises(frappe.ValidationError, doc.insert)

    def test_warehouse_must_belong_to_company(self):
        doc = self._new()
        doc.to_warehouse = self.from_wh     # sklad ombori filial kompaniyasiga tegishli emas
        self.assertRaises(frappe.ValidationError, doc.insert)

    def test_bom_row_is_exploded(self):
        product = frappe.get_doc({
            "doctype": "Item", "item_name": "_Test BST Yarim tayyor", "item_group": "All Item Groups",
            "stock_uom": "Kg", "is_stock_item": 1,
        }).insert(ignore_permissions=True)
        bom = frappe.get_doc({
            "doctype": "BOM", "item": product.name, "company": FROM_COMPANY, "quantity": 2,
            "items": [{"item_code": self.item, "qty": 1, "uom": "Kg", "rate": RATE}],
        }).insert(ignore_permissions=True)
        doc = self._new(row={"source_type": "BOM", "reference": bom.name, "qty": 4})
        doc.insert()
        self.assertEqual(len(doc.items), 1)
        self.assertEqual(doc.items[0].source_type, "Item")
        self.assertEqual(doc.items[0].from_bom, bom.name)
        self.assertAlmostEqual(flt(doc.items[0].qty), 2)    # 4 × 1 / 2
