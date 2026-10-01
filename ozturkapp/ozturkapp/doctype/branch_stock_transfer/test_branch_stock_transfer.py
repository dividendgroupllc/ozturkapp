# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Branch Stock Transfer: sklad kompaniyasidan filialga tan narxda o'tkazma.

Test o'z tovari va sklad qoldig'ini yaratadi (Material Receipt), shuning uchun
faqat ikkita kompaniya, ularning omborlari va ichki kontragentlar kerak —
bo'lmasa SKIP.
"""

import unittest
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, flt, get_time, today

from ozturkapp.ozturkapp.doctype.branch_stock_transfer.branch_stock_transfer import (
    BranchStockTransfer,
    get_stock_shortages,
)

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


def _gl_income_cogs(si_name):
    """SI bo'yicha (tushum, tannarx) — GL'dan."""
    income = frappe.db.sql("""select sum(credit) - sum(debit) from `tabGL Entry` gle
        join tabAccount a on a.name = gle.account
        where gle.voucher_no = %s and gle.is_cancelled = 0 and a.root_type = 'Income'""", si_name)[0][0]
    cogs = frappe.db.sql("""select sum(debit) - sum(credit) from `tabGL Entry` gle
        join tabAccount a on a.name = gle.account
        where gle.voucher_no = %s and gle.is_cancelled = 0 and a.account_type = 'Cost of Goods Sold'""",
                         si_name)[0][0]
    return flt(income), flt(cogs)


@unittest.skipUnless(_prereqs_ok(), "Kompaniyalar yoki ichki kontragentlar topilmadi")
class TestBranchStockTransferHistoricalRate(FrappeTestCase):
    """Orqa sana bilan o'tkazma: narx HUJJAT VAQTIDAGI tan narx, joriy Bin bahosi emas.

    Tarix (shu test uchun yangi tovar):
        bugun−10  kirim 10 × 1 000
        bugun     kirim 10 × 3 000   → joriy baho 2 000
        bugun−5   o'tkazma 4 dona    → to'g'ri narx 1 000 (o'sha paytdagi)
    """

    OLD_RATE, NEW_RATE = 1000, 3000

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.from_wh = _from_warehouse()
        cls.item = frappe.get_doc({
            "doctype": "Item", "item_name": "_Test BST Tarixiy " + frappe.generate_hash(length=6),
            "item_group": "All Item Groups", "stock_uom": "Kg", "is_stock_item": 1,
        }).insert(ignore_permissions=True).name
        cls._receipt(add_days(today(), -10), "10:00:00", cls.OLD_RATE)
        cls._receipt(today(), None, cls.NEW_RATE)

    @classmethod
    def _receipt(cls, posting_date, posting_time, rate):
        se = frappe.get_doc({
            "doctype": "Stock Entry", "stock_entry_type": "Material Receipt", "company": FROM_COMPANY,
            "posting_date": posting_date, "set_posting_time": 1 if posting_time else 0,
            "items": [{"item_code": cls.item, "qty": 10, "t_warehouse": cls.from_wh,
                       "basic_rate": rate, "uom": "Kg", "conversion_factor": 1}],
        })
        if posting_time:
            se.posting_time = posting_time
        se.insert(ignore_permissions=True)
        se.submit()

    def _new(self, qty=4):
        doc = frappe.new_doc("Branch Stock Transfer")
        doc.posting_date = add_days(today(), -5)
        doc.posting_time = "12:00:00"
        doc.from_company, doc.from_warehouse = FROM_COMPANY, self.from_wh
        doc.to_company, doc.to_warehouse = TO_COMPANY, TO_WAREHOUSE
        doc.append("items", {"source_type": "Item", "reference": self.item, "qty": qty})
        return doc

    def _assert_at_historical_cost(self, doc):
        si = frappe.get_doc("Sales Invoice", doc.sales_invoice)
        pi = frappe.get_doc("Purchase Invoice", doc.purchase_invoice)
        self.assertAlmostEqual(flt(si.items[0].rate), self.OLD_RATE, places=2)
        self.assertAlmostEqual(flt(pi.items[0].rate), self.OLD_RATE, places=2)
        self.assertAlmostEqual(flt(si.grand_total), flt(pi.grand_total), places=2)

        income, cogs = _gl_income_cogs(si.name)
        self.assertAlmostEqual(income, 4 * self.OLD_RATE, places=2)
        self.assertAlmostEqual(income, cogs, places=2)          # skladda foyda/zarar yo'q

        # Filialda kirim ham o'sha narxda
        in_rate = frappe.db.get_value("Stock Ledger Entry",
                                      {"voucher_no": pi.name, "is_cancelled": 0}, "incoming_rate")
        self.assertAlmostEqual(flt(in_rate), self.OLD_RATE, places=2)

        # BST qatorlari va jami yakuniy narxda
        doc.reload()
        self.assertAlmostEqual(flt(doc.items[0].rate), self.OLD_RATE, places=2)
        self.assertAlmostEqual(flt(doc.total_amount), 4 * self.OLD_RATE, places=2)

    def test_current_bin_rate_differs(self):
        """Tekshiruv sharti: joriy baho tarixiy bahodan farq qiladi."""
        bin_rate = flt(frappe.db.get_value("Bin", {"item_code": self.item, "warehouse": self.from_wh},
                                           "valuation_rate"))
        self.assertNotAlmostEqual(bin_rate, self.OLD_RATE, places=2)

    def test_backdated_transfer_uses_historical_rate(self):
        doc = self._new()
        doc.insert()
        self.assertAlmostEqual(flt(doc.items[0].rate), self.OLD_RATE, places=2)
        doc.submit()
        self._assert_at_historical_cost(doc)

    def test_si_incoming_rate_overrides_preview_rate(self):
        """Ko'rish narxi noto'g'ri bo'lsa ham (masalan joriy baho), SI narxi
        ERPNext hisoblagan incoming_rate ga tenglashadi — tushum == tannarx."""
        with patch.object(BranchStockTransfer, "_get_valuation_rate", return_value=2000):
            doc = self._new()
            doc.insert()
            self.assertAlmostEqual(flt(doc.items[0].rate), 2000, places=2)
            doc.submit()
        self._assert_at_historical_cost(doc)

    def test_negative_stock_warning(self):
        """Hujjat vaqtida qoldiq yetmasa — ro'yxat bilan ogohlantirish (taqiq emas)."""
        doc = self._new(qty=15)     # bugun−5 da faqat 10 bor
        doc.posting_time = "11:00:00"   # boshqa testlarning 12:00 dagi o'tkazmasidan oldin
        shortages = get_stock_shortages(self.from_wh, [{"item_code": self.item, "qty": 15}],
                                        doc.posting_date, doc.posting_time)
        self.assertEqual(len(shortages), 1)
        self.assertAlmostEqual(shortages[0]["available"], 10)
        self.assertAlmostEqual(shortages[0]["shortage"], 5)

        frappe.clear_messages()
        doc.insert()
        messages = " ".join(str(m) for m in frappe.get_message_log())
        self.assertIn("MANFIY", messages)

        # Yetarli bo'lsa — ogohlantirish yo'q
        self.assertEqual(get_stock_shortages(self.from_wh, [{"item_code": self.item, "qty": 4}],
                                             doc.posting_date, doc.posting_time), [])
