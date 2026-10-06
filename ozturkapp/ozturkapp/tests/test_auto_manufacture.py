# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Buyurtma bo'yicha avto ishlab chiqarish testlari (utils/auto_manufacture.py).

Ishga tushirish::

    cd /home/sherzod/frappe-bench && flock /tmp/ozturk_tests.lock \
        bench --site ozturk.local run-tests \
        --module ozturkapp.ozturkapp.tests.test_auto_manufacture

FIKSTURA
========
Har test o'z SAVEPOINT'ida: sinov xomashyosi (un — Kg, tuxum — Nos),
yarim tayyor "xamir" (BOM: 5 kg <- 3 kg un + 5 dona tuxum), Product Bundle
taom (0.1 kg xamir) va haqiqiy `POS Invoice`. Fon vazifasi o'rniga
`_reconcile` to'g'ridan-to'g'ri chaqiriladi (u commit qilmaydi).
"""

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from ozturkapp.ozturkapp.utils import auto_manufacture as am
from ozturkapp.ozturkapp.utils import cashier_billing, cashier_permissions

SAVEPOINT = "ozturk_auto_mfg_test"


class TestAutoManufacture(FrappeTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint(SAVEPOINT)
        frappe.local._ozturk_payment_methods = {}
        frappe.local._ozturk_scope_cache = {}

        self.scope = cashier_permissions.resolve_scope()
        self.cash = (cashier_billing.cash_modes(self.scope.pos_profile) or [None])[0]
        if not self.cash:
            self.skipTest("POS Profile'da naqd usul yo'q")
        if not cashier_permissions.open_shift_name(self.scope):
            self.skipTest("Ochiq kassa smenasi yo'q")
        if not frappe.db.has_column("Item", "custom_auto_manufacture"):
            self.skipTest("migrate qilinmagan: Item.custom_auto_manufacture yo'q")

        frappe.db.set_value("UOM", "Nos", "must_be_whole_number", 1)
        self.warehouse = frappe.db.get_value("POS Profile", self.scope.pos_profile, "warehouse")

        self.flour = self._item("AM Test Un", "Kg", rate=10000)
        self.egg = self._item("AM Test Tuxum", "Nos", rate=1500)
        self.dough = self._item("AM Test Xamir", "Kg", rate=8000, auto=1)
        self.dish = self._item("AM Test Taom", "Nos", stock=0)
        self.bom = self._bom(self.dough, 5, [(self.flour, 3, "Kg"), (self.egg, 5, "Nos")])
        frappe.get_doc(
            {
                "doctype": "Product Bundle",
                "new_item_code": self.dish,
                "items": [{"item_code": self.dough, "qty": 0.1, "uom": "Kg"}],
            }
        ).insert(ignore_permissions=True)

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point=SAVEPOINT)

    # ── fikstura ──────────────────────────────────────────────────────

    def _item(self, name, uom, rate=0, stock=1, auto=0):
        doc = frappe.get_doc(
            {
                "doctype": "Item",
                "item_name": name,
                "item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
                "stock_uom": uom,
                "is_stock_item": stock,
                "is_sales_item": 1,
                "valuation_rate": rate,
                "custom_auto_manufacture": auto,
            }
        ).insert(ignore_permissions=True)
        return doc.name

    def _bom(self, item, qty, rows):
        doc = frappe.get_doc(
            {
                "doctype": "BOM",
                "item": item,
                "company": self.scope.company,
                "quantity": qty,
                "is_default": 1,
                "is_active": 1,
                "rm_cost_as_per": "Valuation Rate",
                "items": [{"item_code": c, "qty": q, "uom": u} for c, q, u in rows],
            }
        )
        doc.insert(ignore_permissions=True)
        doc.submit()
        return doc.name

    def _invoice(self, lines):
        doc = frappe.new_doc("POS Invoice")
        doc.update(
            {
                "customer": self.scope.default_customer,
                "pos_profile": self.scope.pos_profile,
                "company": self.scope.company,
                "branch": self.scope.branch,
                "restaurant": self.scope.restaurant,
                "order_type": "Take Away",
            }
        )
        for code, qty in lines:
            doc.append("items", {"item_code": code, "qty": qty, "rate": 1000, "warehouse": self.warehouse})
        doc.append("payments", {"mode_of_payment": self.cash, "amount": 0})
        with mock.patch.object(am, "enqueue"):
            doc.insert(ignore_permissions=True)
        return doc

    def _set_qty(self, doc, code, qty):
        doc.reload()
        for row in doc.items:
            if row.item_code == code:
                row.qty = qty
        with mock.patch.object(am, "enqueue"):
            doc.save(ignore_permissions=True)

    def _produced(self, invoice):
        return am._produced_totals(am.produced_entries(invoice)).get(self.dough, 0)

    # ── testlar ───────────────────────────────────────────────────────

    def test_required_qty_from_bundle_and_direct_sale(self):
        doc = self._invoice([(self.dish, 2), (self.dough, 1)])
        self.assertEqual(am.required_qty(doc), {self.dough: 1.2})

    def test_cancelled_or_return_invoice_needs_nothing(self):
        doc = self._invoice([(self.dish, 2)])
        doc.custom_cancelled = 1
        self.assertEqual(am.required_qty(doc), {})
        doc.custom_cancelled = 0
        doc.is_return = 1
        self.assertEqual(am.required_qty(doc), {})

    def test_order_manufactures_with_fractional_egg(self):
        doc = self._invoice([(self.dish, 2)])
        result = am._reconcile(doc.name)

        self.assertEqual(len(result["created"]), 1)
        se = frappe.get_doc("Stock Entry", result["created"][0])
        self.assertEqual(se.docstatus, 1)
        self.assertEqual(se.custom_pos_invoice, doc.name)
        rows = {r.item_code: r for r in se.items}
        self.assertAlmostEqual(flt(rows[self.dough].qty), 0.2)
        self.assertEqual(rows[self.dough].t_warehouse, self.warehouse)
        self.assertAlmostEqual(flt(rows[self.flour].qty), 0.12)
        # 0.2 kg xamir -> 0.2 dona tuxum: Nos butun son bo'lmasa ham o'tadi
        self.assertAlmostEqual(flt(rows[self.egg].qty), 0.2)

    def test_reconcile_is_idempotent(self):
        doc = self._invoice([(self.dish, 2)])
        am._reconcile(doc.name)
        again = am._reconcile(doc.name)
        self.assertEqual(again, {"created": [], "cancelled": []})
        self.assertAlmostEqual(self._produced(doc.name), 0.2)

    def test_added_dish_manufactures_only_the_difference(self):
        doc = self._invoice([(self.dish, 2)])
        am._reconcile(doc.name)
        self._set_qty(doc, self.dish, 5)

        result = am._reconcile(doc.name)
        self.assertEqual(result["cancelled"], [])
        self.assertEqual(len(result["created"]), 1)
        self.assertAlmostEqual(
            flt(frappe.db.get_value("Stock Entry", result["created"][0], "fg_completed_qty")), 0.3
        )
        self.assertAlmostEqual(self._produced(doc.name), 0.5)

    def test_removed_dish_reverses_and_reproduces_rest(self):
        doc = self._invoice([(self.dish, 5)])
        first = am._reconcile(doc.name)["created"]
        self._set_qty(doc, self.dish, 1)

        result = am._reconcile(doc.name)
        self.assertEqual(result["cancelled"], first)
        self.assertEqual(frappe.db.get_value("Stock Entry", first[0], "docstatus"), 2)
        self.assertAlmostEqual(self._produced(doc.name), 0.1)

    def test_cancelled_order_reverses_all(self):
        doc = self._invoice([(self.dish, 3)])
        created = am._reconcile(doc.name)["created"]
        frappe.db.set_value("POS Invoice", doc.name, "custom_cancelled", 1)

        with mock.patch.object(am, "enqueue") as enqueue:
            am.schedule(doc.name)
        enqueue.assert_called_once_with(doc.name)

        result = am._reconcile(doc.name)
        self.assertEqual(result["cancelled"], created)
        self.assertEqual(self._produced(doc.name), 0)

    def test_hook_enqueues_only_when_out_of_sync(self):
        doc = self._invoice([(self.dish, 2)])
        with mock.patch.object(am, "enqueue") as enqueue:
            am.on_doc_change(doc)
        enqueue.assert_called_once_with(doc.name)

        am._reconcile(doc.name)
        with mock.patch.object(am, "enqueue") as enqueue:
            am.on_doc_change(doc)
        enqueue.assert_not_called()

    def test_invoice_without_auto_items_is_ignored(self):
        plain = self._item("AM Test Ichimlik", "Nos", rate=100)
        doc = self._invoice([(plain, 1)])
        with mock.patch.object(am, "enqueue") as enqueue:
            am.on_doc_change(doc)
        enqueue.assert_not_called()
        self.assertEqual(am._reconcile(doc.name), {"created": [], "cancelled": []})

    def test_manual_stock_entry_still_requires_whole_nos(self):
        se = frappe.get_doc(
            {
                "doctype": "Stock Entry",
                "stock_entry_type": "Material Issue",
                "company": self.scope.company,
                "items": [
                    {"item_code": self.egg, "qty": 0.5, "uom": "Nos", "s_warehouse": self.warehouse}
                ],
            }
        )
        with self.assertRaises(frappe.ValidationError):
            se.insert(ignore_permissions=True)
