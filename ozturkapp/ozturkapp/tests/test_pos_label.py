# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""To'lov usulining «POS'dagi nomi» (`Mode of Payment.custom_pos_label`).

Kassa moduli naqd egalarini ajratish uchun usul «Нахт Davron» deb nomlanadi,
kassir ekrani va chekda esa qisqa «Нахт» ko'rinishi kerak. Ma'lumotda doim
HAQIQIY nom qoladi.

    cd /home/sherzod/frappe-bench && flock /tmp/ozturk_tests.lock \
        bench --site ozturk.local run-tests \
        --module ozturkapp.ozturkapp.tests.test_pos_label
"""

import json

import frappe

from ozturkapp.ozturkapp.api import billing
from ozturkapp.ozturkapp.tests.test_cashier_billing import BillingCase
from ozturkapp.ozturkapp.tests.test_printing import _decode_escpos
from ozturkapp.ozturkapp.utils import cashier_billing, cashier_permissions, escpos, shift_report

LABEL = "Naqd Ekran"
PRINTER = {"paper_width": "80", "codepage": "cp866", "codepage_number": 17, "cut_paper": 1}


class TestPosLabel(BillingCase):
    def setUp(self):
        super().setUp()
        self._set_label(self.cash, LABEL)

    @staticmethod
    def _flush_caches():
        BillingCase._flush_caches()
        frappe.local._ozturk_pos_labels = {}

    def _set_label(self, mode, label):
        frappe.db.set_value("Mode of Payment", mode, "custom_pos_label", label)
        self._flush_caches()

    def _methods(self):
        return {m["mode_of_payment"]: m for m in billing.get_payment_modes()["methods"]}

    # ── API ─────────────────────────────────────────────────────────────

    def test_field_exists(self):
        self.assertTrue(frappe.get_meta("Mode of Payment").has_field("custom_pos_label"))

    def test_payment_modes_return_label(self):
        methods = self._methods()
        self.assertIn(self.cash, methods)  # haqiqiy nom saqlanadi
        self.assertEqual(methods[self.cash]["mode_of_payment"], self.cash)
        self.assertEqual(methods[self.cash]["label"], LABEL)

    def test_label_falls_back_to_name(self):
        self._set_label(self.cash, "")
        self.assertEqual(self._methods()[self.cash]["label"], self.cash)
        self.assertEqual(cashier_billing.pos_label(self.cash), self.cash)
        # Yorlig'i yo'q usul (Test Karta) — o'z nomi.
        self.assertEqual(self._methods()[self.CARD]["label"], self.CARD)
        self.assertEqual(cashier_billing.pos_label(""), "")

    def test_pos_labels_bulk(self):
        labels = cashier_billing.pos_labels([self.cash, self.CARD, None])
        self.assertEqual(labels, {self.cash: LABEL, self.CARD: self.CARD})

    # ── To'lov va chek ──────────────────────────────────────────────────

    def test_submit_payment_uses_real_name(self):
        doc = self._invoice(printed=True)
        payable = self._payable(doc.name)

        # Yorliq to'lov usuli EMAS — server rad etadi.
        with self.assertRaises(frappe.ValidationError):
            billing.submit_payment(
                doc.name, json.dumps([{"mode_of_payment": LABEL, "amount": payable}])
            )

        self._pay(doc.name)
        doc = frappe.get_doc("POS Invoice", doc.name)
        self.assertEqual(doc.docstatus, 1)
        self.assertEqual([p.mode_of_payment for p in doc.payments if p.amount], [self.cash])

    def test_receipt_prints_label(self):
        doc = self._invoice(printed=True)
        self._pay(doc.name)
        self._flush_caches()

        bill = cashier_billing.build_bill(doc.name, include_kitchen=False)
        paid = [p for p in bill["payments"] if p["amount"]]
        self.assertEqual(paid[0]["mode_of_payment"], self.cash)
        self.assertEqual(paid[0]["label"], LABEL)

        text = _decode_escpos(escpos.build_bill(bill, PRINTER, {"line1": "TEST"}))
        self.assertIn(f"{LABEL}:", text)
        self.assertNotIn(f"{self.cash}:", text)

    def test_shift_report_uses_label(self):
        doc = self._invoice(printed=True)
        self._pay(doc.name)
        self._flush_caches()

        report = shift_report.build_report(shift_report.KIND_X, self.scope)
        row = next(p for p in report["payments"] if p["mode_of_payment"] == self.cash)
        self.assertEqual(row["label"], LABEL)

        text = _decode_escpos(escpos.build_shift_report(report, PRINTER, {"line1": "TEST"}))
        self.assertIn(LABEL, text)
        self.assertNotIn(f"{self.cash} (", text)

    def test_history_rows_carry_label(self):
        from ozturkapp.ozturkapp.api import order

        doc = self._invoice(printed=True)
        self._pay(doc.name)
        self._flush_caches()

        rows = order.get_paid_orders(shift=cashier_permissions.open_shift_name(self.scope))
        row = next((r for r in rows if r["invoice"] == doc.name), None)
        self.assertIsNotNone(row)
        pay = next(p for p in row["payments"] if p["mode_of_payment"] == self.cash)
        self.assertEqual(pay["label"], LABEL)

    def test_refundable_rows_carry_label(self):
        from ozturkapp.ozturkapp.utils import refunds

        doc = self._invoice(printed=True)
        self._pay(doc.name)
        self._flush_caches()

        info = refunds.get_refundable(frappe.get_doc("POS Invoice", doc.name))
        row = next(p for p in info["paid"] if p["mode_of_payment"] == self.cash)
        self.assertEqual(row["label"], LABEL)
