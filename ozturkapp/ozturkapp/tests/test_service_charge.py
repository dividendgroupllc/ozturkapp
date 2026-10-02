# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Xizmat haqi chek chegirmasidan OLDINGI taomlar summasidan (`utils/service_charge.py`).

Ishga tushirish::

    cd /home/sherzod/frappe-bench && flock /tmp/ozturk_tests.lock \
        bench --site ozturk.local run-tests \
        --module ozturkapp.ozturkapp.tests.test_service_charge

Fikstura `test_cashier_billing.BillingCase` dan: haqiqiy `POS Invoice`, ERPNext
`save/submit`, har test SAVEPOINT ichida va oxirida orqaga qaytariladi.
"""

import json
from unittest import mock

import frappe
from frappe.utils import flt, nowdate, nowtime

from ozturkapp.ozturkapp.api import billing
from ozturkapp.ozturkapp.tests.test_cashier_billing import BillingCase
from ozturkapp.ozturkapp.utils import cashier_billing, service_charge


class ServiceChargeCase(BillingCase):
    def setUp(self):
        super().setUp()
        if not self.service["enabled"]:
            self.skipTest("Xizmat haqi sozlanmagan")
        self.rate = flt(self.service["rate"])
        self._feature(discount=True)

    def _row(self, doc):
        return self._tax(doc, self.service["account"])

    def _service(self, doc):
        row = self._row(doc)
        return flt(row.tax_amount) if row else 0.0

    def _fresh(self, name):
        return frappe.get_doc("POS Invoice", name)

    def _expected(self, total):
        return flt(total * self.rate / 100, 2)


class TestServiceChargeRule(ServiceChargeCase):
    def test_half_discount_keeps_the_service_on_the_full_food_total(self):
        doc = self._invoice(lines=((1, 322000),))

        bill = billing.apply_discount(doc.name, percent=50, reason="Doimiy mijoz")

        fresh = self._fresh(doc.name)
        self.assertEqual(flt(fresh.total), 322000)
        self.assertEqual(flt(fresh.discount_amount), 161000)
        self.assertEqual(flt(fresh.net_total), 161000)
        row = self._row(fresh)
        self.assertEqual(row.charge_type, "Actual")
        if self.rate == 12:
            self.assertEqual(flt(row.tax_amount), 38640)
            self.assertEqual(flt(fresh.grand_total), 199640)
        self.assertEqual(flt(row.tax_amount), self._expected(322000))
        self.assertEqual(flt(fresh.grand_total), 161000 + self._expected(322000))
        # Kassa ekrani va chek shu summani ko'rsatadi.
        self.assertEqual(bill["service_charge"]["amount"], self._expected(322000))
        self.assertTrue(bill["service_charge"]["is_service_charge"])
        self.assertEqual(bill["service_charge"]["rate"], self.rate)
        self.assertEqual(bill["payable"], flt(fresh.rounded_total) or flt(fresh.grand_total))

    def test_without_a_discount_nothing_changes(self):
        doc = self._invoice()  # 6 × 10 000

        self.assertEqual(flt(doc.total), 60000)
        self.assertEqual(self._service(doc), self._expected(60000))
        self.assertEqual(flt(doc.grand_total), 60000 + self._expected(60000))
        # Mahsulot qatorlari bo'yicha taqsimot jami bilan mos.
        row = self._row(doc)
        self.assertEqual(flt(row.tax_amount_after_discount_amount), flt(row.tax_amount))
        detail = json.loads(row.item_wise_tax_detail)
        self.assertAlmostEqual(sum(v[1] for v in detail.values()), flt(row.tax_amount), places=2)

    def test_amount_discount_does_not_touch_the_service(self):
        doc = self._invoice(lines=((1, 322000),))

        billing.apply_discount(doc.name, amount=100000, reason="Kechikish uchun")

        fresh = self._fresh(doc.name)
        self.assertEqual(flt(fresh.discount_amount), 100000)
        self.assertEqual(self._service(fresh), self._expected(322000))
        self.assertEqual(flt(fresh.grand_total), 222000 + self._expected(322000))

    def test_removing_the_discount_restores_the_plain_bill(self):
        doc = self._invoice(lines=((1, 322000),))
        billing.apply_discount(doc.name, percent=50, reason="x")

        billing.remove_discount(doc.name)

        fresh = self._fresh(doc.name)
        self.assertEqual(flt(fresh.discount_amount), 0)
        self.assertEqual(self._service(fresh), self._expected(322000))
        self.assertEqual(flt(fresh.grand_total), 322000 + self._expected(322000))

    def test_take_away_with_a_discount_has_no_service(self):
        doc = self._invoice(lines=((1, 322000),), order_type="Take Away", via_template=True)
        billing.apply_discount(doc.name, percent=50, reason="x")

        fresh = self._fresh(doc.name)
        self.assertIsNone(self._row(fresh))
        self.assertEqual(flt(fresh.grand_total), 161000)

    def test_item_changes_after_the_discount_recompute_the_service(self):
        doc = self._invoice(lines=((2, 50000), (1, 22000)))  # 122 000
        billing.apply_discount(doc.name, percent=50, reason="x")

        doc = self._fresh(doc.name)
        doc.items[0].qty = 4  # 200 000 + 22 000
        doc.save()
        fresh = self._fresh(doc.name)
        self.assertEqual(flt(fresh.total), 222000)
        self.assertEqual(flt(fresh.discount_amount), 111000)
        self.assertEqual(self._service(fresh), self._expected(222000))
        self.assertEqual(flt(fresh.grand_total), 111000 + self._expected(222000))

        fresh.remove(fresh.items[1])
        fresh.save()
        again = self._fresh(doc.name)
        self.assertEqual(flt(again.total), 200000)
        self.assertEqual(self._service(again), self._expected(200000))
        self.assertEqual(flt(again.grand_total), 100000 + self._expected(200000))

    def test_an_open_invoice_with_an_old_row_is_converted_on_the_next_save(self):
        """Tuzatishdan OLDIN ochilgan chek ("On Net Total" qatori) keyingi saqlashda yangi qoidaga o'tadi."""
        with mock.patch.object(service_charge, "apply", return_value=False):
            doc = self._invoice(lines=((1, 322000),))
            doc.apply_discount_on = "Net Total"
            doc.additional_discount_percentage = 50
            doc.save()
        old = self._fresh(doc.name)
        self.assertEqual(self._row(old).charge_type, "On Net Total")
        self.assertEqual(self._service(old), self._expected(161000))  # eski qoida

        old.save()

        fresh = self._fresh(doc.name)
        self.assertEqual(self._row(fresh).charge_type, "Actual")
        self.assertEqual(self._service(fresh), self._expected(322000))

    def test_grand_total_discount_is_moved_to_the_food(self):
        """URY `make_invoice(additionalDiscount=...)` chegirmasi POS Profile'dagi "Grand Total" da."""
        doc = self._invoice(lines=((1, 322000),))
        doc.apply_discount_on = "Grand Total"
        doc.additional_discount_percentage = 50
        doc.save()

        fresh = self._fresh(doc.name)
        self.assertEqual(fresh.apply_discount_on, "Net Total")
        self.assertEqual(flt(fresh.discount_amount), 161000)
        self.assertEqual(flt(fresh.grand_total), 161000 + self._expected(322000))

    def test_submitted_invoices_are_never_recomputed(self):
        with mock.patch.object(service_charge, "apply", return_value=False):
            doc = self._invoice(lines=((1, 322000),))
            doc.apply_discount_on = "Net Total"
            doc.additional_discount_percentage = 50
            doc.save()
            self._pay(doc.name)
        paid = self._fresh(doc.name)
        before = (self._row(paid).charge_type, self._service(paid), flt(paid.grand_total))

        self.assertFalse(service_charge.apply(paid))
        paid.calculate_taxes_and_totals()
        self.assertEqual((self._row(paid).charge_type, self._service(paid), flt(paid.grand_total)), before)

    def test_rate_comes_from_the_template(self):
        doc = self._invoice(lines=((1, 100000),))
        config = dict(self.service, rate=10)
        with mock.patch.object(cashier_billing, "get_service_charge_config", return_value=config):
            doc.save()
        self.assertEqual(self._service(self._fresh(doc.name)), 10000)

    def test_tip_stays_outside_the_service_and_the_discount(self):
        self._feature(tips=True)
        doc = self._invoice(lines=((1, 322000),))
        billing.apply_discount(doc.name, percent=50, reason="x")
        payable = self._payable(doc.name)

        self._pay(doc.name, amount=payable + 5000, tip=5000)

        paid = self._fresh(doc.name)
        self.assertEqual(paid.docstatus, 1)
        self.assertEqual(cashier_billing.get_tip(paid), 5000)
        self.assertEqual(self._service(paid), self._expected(322000))
        self.assertEqual(flt(paid.grand_total), 161000 + self._expected(322000) + 5000)


class TestServiceChargeSplitAndRefund(ServiceChargeCase):
    def test_split_bill_charges_each_part_on_its_own_food_total(self):
        frappe.db.set_value(
            "POS Profile", self.profile, "custom_enable_bill_split", 1, update_modified=False
        )
        doc = self._invoice(lines=((2, 100000), (1, 122000)))  # 322 000
        billing.apply_discount(doc.name, percent=50, reason="x")
        original = self._service(self._fresh(doc.name))
        moved = self._fresh(doc.name).items[1].name

        result = billing.split_bill(doc.name, json.dumps([{"name": moved, "qty": 1}]))

        parts = [self._fresh(result["source_invoice"]), self._fresh(result["new_invoice"])]
        for part in parts:
            self.assertEqual(self._row(part).charge_type, "Actual", part.name)
            self.assertEqual(self._service(part), self._expected(flt(part.total)), part.name)
            self.assertEqual(
                flt(part.grand_total), flt(part.net_total) + self._service(part), part.name
            )
        self.assertEqual(sum(flt(p.total) for p in parts), 322000)
        self.assertAlmostEqual(sum(self._service(p) for p in parts), original, places=2)

    def _refund(self, doc, qtys):
        items = [
            {"name": row.name, "qty": qtys[row.item_code]}
            for row in doc.items
            if row.item_code in qtys
        ]
        return billing.refund_invoice(doc.name, json.dumps(items), reason="Taom sovuq edi")

    def test_partial_refund_returns_a_proportional_service(self):
        self._feature(refunds=True)
        self._allow_in_returns(self.cash)
        doc = self._invoice(lines=((2, 100000), (1, 122000)))  # 322 000
        billing.apply_discount(doc.name, percent=50, reason="x")
        self._pay(doc.name)
        paid = self._fresh(doc.name)
        service = self._service(paid)

        first = self._refund(paid, {self.items[0]: 1})  # 100 000 dan
        ret = self._fresh(first["invoice"])
        self.assertEqual(self._row(ret).charge_type, "Actual")
        self.assertEqual(flt(ret.total), -100000)
        self.assertEqual(self._service(ret), -flt(service * 100000 / 322000, 2))
        self.assertEqual(flt(ret.grand_total), -50000 - flt(service * 100000 / 322000, 2))

        rest = self._refund(paid, {self.items[0]: 1, self.items[1]: 1})
        last = self._fresh(rest["invoice"])
        # Oxirgi qaytarish qoldiqni aniq yopadi: savdo va qaytarishlar yig'indisi nol.
        self.assertAlmostEqual(service + self._service(ret) + self._service(last), 0, places=6)
        refunded = abs(flt(ret.grand_total)) + abs(flt(last.grand_total))
        self.assertAlmostEqual(refunded, flt(paid.grand_total), places=2)

    def test_refund_of_an_old_style_invoice_keeps_the_old_rule(self):
        self._feature(refunds=True)
        self._allow_in_returns(self.cash)
        with mock.patch.object(service_charge, "apply", return_value=False):
            doc = self._invoice(lines=((2, 100000), (1, 122000)))
            doc.apply_discount_on = "Net Total"
            doc.additional_discount_percentage = 50
            doc.save()
            self._pay(doc.name)
        paid = self._fresh(doc.name)
        self.assertEqual(self._row(paid).charge_type, "On Net Total")

        result = self._refund(paid, {self.items[0]: 1})

        ret = self._fresh(result["invoice"])
        self.assertEqual(self._row(ret).charge_type, "On Net Total")
        self.assertEqual(self._service(ret), -self._expected(50000))


class TestConsolidation(ServiceChargeCase):
    """Smena yopilganda POS cheklar Sales Invoice'ga birlashadi — summalar teng bo'lishi shart."""

    def _merge(self, names):
        log = frappe.new_doc("POS Invoice Merge Log")
        log.posting_date = nowdate()
        log.posting_time = nowtime()
        log.customer = self.scope.default_customer
        for name in names:
            row = frappe.db.get_value(
                "POS Invoice", name, ["customer", "posting_date", "grand_total"], as_dict=True
            )
            log.append(
                "pos_invoices",
                {
                    "pos_invoice": name,
                    "customer": row.customer,
                    "posting_date": row.posting_date,
                    "grand_total": row.grand_total,
                },
            )
        log.insert(ignore_permissions=True)
        log.submit()
        return frappe.get_doc("Sales Invoice", log.consolidated_invoice)

    def test_actual_and_old_rows_merge_into_one_sales_invoice_with_equal_totals(self):
        self._feature(tips=True)
        names = []

        plain = self._invoice(lines=((1, 100000),))
        self._pay(plain.name)
        names.append(plain.name)

        half = self._invoice(lines=((1, 322000),))
        billing.apply_discount(half.name, percent=50, reason="x")
        self._pay(half.name)
        names.append(half.name)

        amount = self._invoice(lines=((3, 11111),))
        billing.apply_discount(amount.name, amount=7777, reason="x")
        payable = self._payable(amount.name)
        self._pay(amount.name, amount=payable + 3000, tip=3000)
        names.append(amount.name)

        # Eski qoida bilan to'langan chek (production'dagi ochiq smena kabi).
        with mock.patch.object(service_charge, "apply", return_value=False):
            old = self._invoice(lines=((1, 50000),))
            old.apply_discount_on = "Net Total"
            old.additional_discount_percentage = 10
            old.save()
            self._pay(old.name)
        names.append(old.name)

        docs = [self._fresh(n) for n in names]
        self.assertEqual(
            sorted(self._row(d).charge_type for d in docs), ["Actual", "Actual", "Actual", "On Net Total"]
        )

        si = self._merge(names)

        self.assertEqual(si.docstatus, 1)
        self.assertAlmostEqual(flt(si.grand_total), sum(flt(d.grand_total) for d in docs), places=2)
        self.assertAlmostEqual(flt(si.net_total), sum(flt(d.net_total) for d in docs), places=2)
        self.assertAlmostEqual(
            flt(si.total_taxes_and_charges),
            sum(flt(d.total_taxes_and_charges) for d in docs),
            places=2,
        )
        service = [t for t in si.taxes if t.account_head == self.service["account"]]
        self.assertEqual(len(service), 1)
        self.assertAlmostEqual(
            flt(service[0].tax_amount), sum(self._service(d) for d in docs), places=2
        )
        self.assertAlmostEqual(
            flt(si.rounded_total) or flt(si.grand_total),
            sum(flt(d.rounded_total) or flt(d.grand_total) for d in docs),
            places=2,
        )
        self.assertAlmostEqual(
            sum(flt(p.amount) for p in si.payments),
            sum(flt(p.amount) for d in docs for p in d.payments),
            places=2,
        )
