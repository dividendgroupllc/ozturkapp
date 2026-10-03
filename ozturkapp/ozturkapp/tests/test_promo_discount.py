# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""«Aksiya» chegirmasi ichimliklarga (Напитки) tegmaydi (`utils/promo_discount.py`).

Fikstura `test_service_charge.ServiceChargeCase` dan: haqiqiy `POS Invoice`,
har test SAVEPOINT ichida va oxirida orqaga qaytariladi.
items[0] — taom, items[1] — ichimlik (test ichida guruhi «Напитки» qilinadi).
"""

import json

import frappe
from frappe.utils import flt

from ozturkapp.ozturkapp.api import billing
from ozturkapp.ozturkapp.tests.test_service_charge import ServiceChargeCase
from ozturkapp.ozturkapp.utils import promo_discount

DRINKS = "Напитки"


class TestPromoDiscount(ServiceChargeCase):
    def setUp(self):
        super().setUp()
        if not frappe.db.exists("Item Group", DRINKS):
            frappe.get_doc({"doctype": "Item Group", "item_group_name": DRINKS,
                            "parent_item_group": "All Item Groups"}).insert(ignore_permissions=True)
        food_group = frappe.db.get_value("Item", self.items[0], "item_group")
        if food_group == DRINKS:
            frappe.db.set_value("Item", self.items[0], "item_group", "All Item Groups")
        frappe.db.set_value("Item", self.items[1], "item_group", DRINKS)
        frappe.clear_document_cache("Item", self.items[0])
        frappe.clear_document_cache("Item", self.items[1])

    def _bill(self):
        return self._invoice(lines=((1, 100000), (1, 20000)))  # taom 100 000 + ichimlik 20 000

    def _row_of(self, doc, code):
        return next(r for r in doc.items if r.item_code == code)

    def test_aksiya_skips_drinks(self):
        doc = self._bill()
        bill = billing.apply_discount(doc.name, percent=50, reason="Aksiya")

        fresh = self._fresh(doc.name)
        self.assertEqual(flt(fresh.additional_discount_percentage), 50)
        self.assertEqual(flt(fresh.discount_amount), 50000)
        self.assertEqual(flt(fresh.net_total), 70000)
        # Xizmat haqi avvalgidek chegirmagacha bo'lgan butun summadan.
        self.assertEqual(self._service(fresh), self._expected(120000))
        self.assertEqual(flt(fresh.grand_total), 70000 + self._expected(120000))
        self.assertEqual(bill["payable"], flt(fresh.rounded_total) or flt(fresh.grand_total))
        # Daromad qatorlarda: ichimlik to'liq, chegirma faqat taomda.
        self.assertEqual(flt(self._row_of(fresh, self.items[1]).net_amount), 20000)
        self.assertEqual(flt(self._row_of(fresh, self.items[0]).net_amount), 50000)

    def test_other_reasons_still_cover_the_whole_check(self):
        doc = self._bill()
        billing.apply_discount(doc.name, percent=50, reason="Doimiy mijoz")

        fresh = self._fresh(doc.name)
        self.assertEqual(flt(fresh.discount_amount), 60000)
        self.assertEqual(flt(fresh.net_total), 60000)

    def test_amount_is_converted_on_the_food_only(self):
        doc = self._bill()
        billing.apply_discount(doc.name, amount=25000, reason="Aksiya")

        fresh = self._fresh(doc.name)
        self.assertAlmostEqual(flt(fresh.additional_discount_percentage), 25, places=6)
        self.assertEqual(flt(fresh.discount_amount), 25000)

    def test_drinks_only_check_is_rejected(self):
        doc = self._invoice(lines=((1, 100000), (2, 20000)))
        doc.remove(doc.items[0])
        doc.save()
        with self.assertRaises(frappe.ValidationError):
            billing.apply_discount(doc.name, percent=50, reason="Aksiya")

    def test_adding_a_drink_later_does_not_raise_the_discount(self):
        doc = self._bill()
        billing.apply_discount(doc.name, percent=50, reason="Aksiya")

        doc = self._fresh(doc.name)
        self._row_of(doc, self.items[1]).qty = 3  # ichimlik 60 000
        doc.save()
        fresh = self._fresh(doc.name)
        self.assertEqual(flt(fresh.total), 160000)
        self.assertEqual(flt(fresh.discount_amount), 50000)
        self.assertEqual(flt(fresh.grand_total), 110000 + self._expected(160000))

        self._row_of(fresh, self.items[0]).qty = 2  # taom 200 000
        fresh.save()
        again = self._fresh(doc.name)
        self.assertEqual(flt(again.discount_amount), 100000)

    def test_removing_the_discount_restores_drinks_and_food(self):
        doc = self._bill()
        billing.apply_discount(doc.name, percent=50, reason="Aksiya")
        billing.remove_discount(doc.name)

        fresh = self._fresh(doc.name)
        self.assertEqual(flt(fresh.discount_amount), 0)
        self.assertEqual(flt(fresh.grand_total), 120000 + self._expected(120000))

    def test_refund_returns_drinks_at_full_price(self):
        self._feature(refunds=True)
        self._allow_in_returns(self.cash)
        doc = self._bill()
        billing.apply_discount(doc.name, percent=50, reason="Aksiya")
        self._pay(doc.name)
        paid = self._fresh(doc.name)

        drink = self._row_of(paid, self.items[1])
        result = billing.refund_invoice(doc.name, json.dumps([{"name": drink.name, "qty": 1}]), reason="x")
        ret = self._fresh(result["invoice"])
        self.assertEqual(flt(ret.discount_amount), 0)
        self.assertEqual(flt(ret.net_total), -20000)

        food = self._row_of(paid, self.items[0])
        result = billing.refund_invoice(doc.name, json.dumps([{"name": food.name, "qty": 1}]), reason="x")
        last = self._fresh(result["invoice"])
        self.assertEqual(flt(last.net_total), -50000)
        refunded = abs(flt(ret.grand_total)) + abs(flt(last.grand_total))
        self.assertAlmostEqual(refunded, flt(paid.grand_total), places=2)

    def test_paid_invoices_are_not_touched(self):
        doc = self._bill()
        billing.apply_discount(doc.name, percent=50, reason="Aksiya")
        self._pay(doc.name)
        paid = self._fresh(doc.name)
        self.assertEqual(paid.docstatus, 1)
        self.assertFalse(promo_discount.applies(paid))
        self.assertEqual(flt(paid.discount_amount), 50000)

    def test_consolidated_sales_invoice_keeps_the_totals_and_drink_income(self):
        from ozturkapp.ozturkapp.tests.test_service_charge import TestConsolidation

        doc = self._bill()
        billing.apply_discount(doc.name, percent=50, reason="Aksiya")
        self._pay(doc.name)
        paid = self._fresh(doc.name)

        si = TestConsolidation._merge(self, [doc.name])

        self.assertEqual(si.docstatus, 1)
        self.assertAlmostEqual(flt(si.grand_total), flt(paid.grand_total), places=2)
        self.assertAlmostEqual(flt(si.net_total), 70000, places=2)
        drink = next(r for r in si.items if r.item_code == self.items[1])
        self.assertAlmostEqual(flt(drink.net_amount), 20000, places=2)
