# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Sotuv dashboardi va chegirma logi testlari.

Ishga tushirish::

    cd /home/sherzod/frappe-bench && flock /tmp/ozturk_tests.lock \
        bench --site ozturk.local run-tests \
        --module ozturkapp.ozturkapp.tests.test_sales_dashboard

Fikstura `test_cashier_billing.BillingCase` niki: haqiqiy POS Invoice, har test
o'z SAVEPOINT'ida, oxirida orqaga qaytariladi. Saytdagi bugungi boshqa cheklar
ham dashboardga tushadi — shuning uchun testlar faqat O'Z chekini va har doim
to'g'ri bo'lishi kerak bo'lgan ayniyatlarni (tushum = to'lovlar) tekshiradi.
"""

import json

import frappe
from frappe.utils import flt, today

from ozturkapp.ozturkapp.api import billing
from ozturkapp.ozturkapp.api import sales_dashboard
from ozturkapp.ozturkapp.tests.test_cashier_billing import BillingCase
from ozturkapp.ozturkapp.utils import cashier_billing


class DashboardCase(BillingCase):
    def setUp(self):
        super().setUp()
        self._feature(discount=True, split_payment=True)

    def _dashboard(self, **kwargs):
        frappe.set_user("Administrator")
        return sales_dashboard.get_dashboard(today(), today(), **kwargs)

    def _discounted_and_split_paid(self, percent=10, reason="Aksiya"):
        """Kassir chegirma beradi, chek karta + naqd (qaytim bilan) to'lanadi."""
        doc = self._invoice()
        self._as_cashier()
        billing.apply_discount(doc.name, percent=percent, reason=reason)
        payable = self._payable(doc.name)
        payments = [
            {"mode_of_payment": self.CARD, "amount": 20000},
            {"mode_of_payment": self.cash, "amount": payable - 20000 + 5000},
        ]
        billing.submit_payment(doc.name, json.dumps(payments))
        return frappe.get_doc("POS Invoice", doc.name)

    def _row(self, data, invoice):
        return next(r for r in data["discounts"]["rows"] if r["invoice"] == invoice)


class TestDiscountLog(DashboardCase):
    def _comments(self, invoice):
        return frappe.get_all(
            "Comment",
            filters={"reference_doctype": "POS Invoice", "reference_name": invoice, "comment_type": "Comment"},
            pluck="content",
        )

    def test_discount_records_who_and_when(self):
        doc = self._invoice()
        self._as_cashier()

        billing.apply_discount(doc.name, percent=15, reason="Doimiy mijoz")

        fresh = frappe.get_doc("POS Invoice", doc.name)
        self.assertEqual(fresh.custom_discount_by, self.CASHIER)
        self.assertTrue(fresh.custom_discount_at)
        self.assertFalse(fresh.custom_discount_approved_by)
        self.assertTrue(any("Doimiy mijoz" in c for c in self._comments(doc.name)))

    def test_remove_clears_who_and_leaves_a_comment(self):
        doc = self._invoice()
        self._as_cashier()
        billing.apply_discount(doc.name, percent=15, reason="x")

        billing.remove_discount(doc.name)

        fresh = frappe.get_doc("POS Invoice", doc.name)
        self.assertFalse(fresh.custom_discount_by)
        self.assertFalse(fresh.custom_discount_at)
        self.assertTrue(any("olib tashlandi" in c for c in self._comments(doc.name)))

    def test_cashier_cannot_forge_the_giver(self):
        doc = self._invoice()
        self._as_cashier()
        billing.apply_discount(doc.name, percent=5, reason="x")

        forged = frappe.get_doc("POS Invoice", doc.name)
        forged.custom_discount_by = self.MANAGER
        with self.assertRaises(frappe.PermissionError):
            forged.save()

    def test_form_save_with_the_date_as_text_is_not_a_change(self):
        """Desk forma sanani satr qilib qaytaradi — bu audit maydonini o'zgartirish emas."""
        doc = self._invoice()
        self._as_cashier()
        billing.apply_discount(doc.name, percent=5, reason="x")

        again = frappe.get_doc("POS Invoice", doc.name)
        again.custom_discount_at = str(again.custom_discount_at)
        cashier_billing._guard_audit_fields(again, frappe.get_doc("POS Invoice", doc.name))


class TestDashboard(DashboardCase):
    def test_revenue_equals_the_payments(self):
        self._discounted_and_split_paid()
        data = self._dashboard()

        checks = data["checks"]
        self.assertAlmostEqual(checks["revenue"], sum(p["amount"] for p in data["payments"]), places=2)
        self.assertAlmostEqual(checks["net"], checks["gross"] - checks["discount"], places=2)

    def test_discount_row_shows_who_reason_items_and_payment(self):
        doc = self._discounted_and_split_paid(percent=10, reason="Aksiya")

        row = self._row(self._dashboard(), doc.name)

        self.assertEqual(row["user"], self.CASHIER)
        self.assertEqual(row["reason"], "Aksiya")
        self.assertAlmostEqual(row["percent"], 10, places=2)
        self.assertAlmostEqual(row["discount"], flt(doc.discount_amount), places=2)
        self.assertAlmostEqual(sum(i["discount"] for i in row["items"]), row["discount"], places=0)
        self.assertEqual(len(row["items"]), len(doc.items))
        modes = {p["mode"]: p["amount"] for p in row["payments"]}
        self.assertEqual(modes[self.CARD], 20000)
        # Qaytim naqd qatordan ayirilgan — to'lovlar chek summasiga teng.
        self.assertAlmostEqual(sum(modes.values()), row["amount"], places=2)

    def test_discount_is_split_across_payment_modes(self):
        doc = self._discounted_and_split_paid()
        data = self._dashboard()

        row = self._row(data, doc.name)
        by_mode = {m["mode"]: m["discount"] for m in data["discounts"]["by_mode"]}
        self.assertIn(self.CARD, by_mode)
        self.assertAlmostEqual(
            sum(by_mode.values()), sum(r["discount"] for r in data["discounts"]["rows"]), places=2
        )
        self.assertAlmostEqual(by_mode[self.CARD], row["discount"] * 20000 / row["amount"], places=2)

    def test_discount_by_reason_and_payment_mode(self):
        """Tur × to'lov usuli jadvali: ustunlar `by_mode` ga, qatorlar `by_reason` ga teng."""
        self._discounted_and_split_paid()
        d = self._dashboard()["discounts"]

        matrix = d["by_reason_mode"]
        by_mode = {m["mode"]: m["discount"] for m in d["by_mode"]}
        columns = {}
        for line in matrix:
            self.assertAlmostEqual(sum(line["modes"].values()), line["discount"], places=2)
            for mode, amount in line["modes"].items():
                columns[mode] = columns.get(mode, 0) + amount
        self.assertEqual(set(columns), set(by_mode))
        for mode, amount in by_mode.items():
            self.assertAlmostEqual(columns[mode], amount, places=2)

        by_reason = {r["reason"]: r["discount"] for r in d["by_reason"]}
        self.assertEqual({line["reason"]: round(line["discount"], 2) for line in matrix},
                         {k: round(v, 2) for k, v in by_reason.items()})
        aksiya = next(line for line in matrix if line["reason"] == "Aksiya")
        self.assertIn(self.CARD, aksiya["modes"])

    def _version(self, invoice, owner, data):
        version = frappe.get_doc({
            "doctype": "Version", "ref_doctype": "POS Invoice", "docname": invoice,
            "data": json.dumps(data),
        }).insert(ignore_permissions=True)
        frappe.db.set_value("Version", version.name, "owner", owner, update_modified=False)

    def test_old_discount_without_the_field_falls_back_to_history(self):
        """Maydon paydo bo'lishidan oldingi chek — «kim» Version tarixidan.

        Testda Frappe Version yozmaydi (`flags.in_test`), shuning uchun yozuvlar
        productiondagi shaklda qo'lda yaratiladi.
        """
        doc = self._discounted_and_split_paid()
        frappe.db.set_value(
            "POS Invoice", doc.name, {"custom_discount_by": None, "custom_discount_at": None},
            update_modified=False,
        )
        self._version(doc.name, self.CASHIER, {"changed": [["additional_discount_percentage", 0, 10]]})
        # Keyingi yozuvda maydon nomi uchraydi, lekin chegirma bermaydi — e'tiborsiz.
        self._version(doc.name, self.MANAGER, {
            "changed": [["status", "Draft", "Paid"]],
            "added": [["payments", {"note": "additional_discount_percentage"}]],
        })

        row = self._row(self._dashboard(), doc.name)

        self.assertEqual(row["user"], self.CASHIER)

    def test_parent_item_group_includes_its_children(self):
        self._discounted_and_split_paid()
        group = frappe.db.get_value("Item", self.items[0], "item_group")
        parent = frappe.db.get_value("Item Group", group, "parent_item_group")
        if not parent:
            self.skipTest("Mahsulot guruhining otasi yo'q")

        child = self._dashboard(item_group=group)
        whole = self._dashboard(item_group=parent)

        self.assertTrue(whole["group_filter"])
        self.assertIn(self.items[0], {r.item_code for r in whole["items"]})
        self.assertGreaterEqual(whole["totals"]["net_amount"], child["totals"]["net_amount"])

    def test_excel_builds(self):
        self._discounted_and_split_paid()
        frappe.set_user("Administrator")

        sales_dashboard.download_excel(today(), today())

        self.assertTrue(frappe.response["filecontent"].startswith(b"PK"))
