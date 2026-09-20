# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""3-to'lqin sharhi (REVIEWER-MONEY): pul, tasdiq va ko'r sanoq bo'yicha hujum testlari.

Har bir test BIR HUJUMNI takrorlaydi va uning to'silganini isbotlaydi. Testlar
kassir foydalanuvchi sifatida `frappe.handler.execute_cmd` orqali ishlaydi
(ya'ni `/api/method/...` so'rovi qanday o'tsa, shunday), shuning uchun
`frappe.client.*` va URY'ning o'z whitelisted metodlari ham tekshiriladi.

Ishga tushirish::

    cd /home/sherzod/frappe-bench && flock /tmp/ozturk_tests.lock \
        bench --site ozturk.local run-tests \
        --module ozturkapp.ozturkapp.tests.test_cashier_review_money

Saytdagi ma'lumotga tegilmaydi: har test o'z SAVEPOINT'ida ishlaydi va orqaga
qaytariladi (`commit` yo'q).
"""

import ast
import json
import os
import random
import unittest
from contextlib import contextmanager
from unittest import mock

import frappe
from frappe.utils import add_to_date, flt, now_datetime, nowdate, nowtime

from ozturkapp.ozturkapp.api import billing, cash_movements, cashier as cashier_api, printing
from ozturkapp.ozturkapp.setup import cashier_features
from ozturkapp.ozturkapp.tests.test_cashier_billing import BillingCase
from ozturkapp.ozturkapp.utils import (
    cashier_billing,
    cashier_permissions,
    escpos,
    manager_approval,
    print_queue,
)
from ozturkapp.ozturkapp.utils import shift_report as shift_report_module
from ozturkapp.ozturkapp.utils.manager_approval import ApprovalRequired

SAVEPOINT_INNER = "ozturk_review_money_inner"


@contextmanager
def http_request(method="POST"):
    """`frappe.handler` kutadigan so'rov holati (HTTP usuli)."""
    saved = (getattr(frappe.local, "request", None), frappe.local.form_dict)
    frappe.local.request = frappe._dict(method=method)
    try:
        yield
    finally:
        frappe.local.request, frappe.local.form_dict = saved


def http(cmd, http_method="POST", **kwargs):
    """`/api/method/<cmd>` chaqiruvini shu jarayonda takrorlaydi (kassir sifatida)."""
    with http_request(http_method):
        frappe.local.form_dict = frappe._dict(kwargs)
        return frappe.handler.execute_cmd(cmd)


def json_of(doc) -> dict:
    return json.loads(frappe.as_json(doc))


class MoneyCase(BillingCase):
    """`BillingCase` + qo'shimcha foydalanuvchilar va hujum yordamchilari."""

    MANAGER2 = "rm-manager2@example.com"
    MANAGER3 = "rm-manager3@example.com"
    OTHER_CASHIER = "rm-cashier2@example.com"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._make_user(cls.MANAGER2, ["URY Manager"], pin="1111")
        cls._make_user(cls.MANAGER3, ["URY Manager"], pin="2222")
        cls._make_user(cls.OTHER_CASHIER, ["URY Cashier"])

    def setUp(self):
        super().setUp()
        for user in (self.MANAGER2, self.MANAGER3):
            manager_approval.reset_attempts(user)
        manager_approval.reset_requester(self.CASHIER)

    def tearDown(self):
        for user in (self.MANAGER, self.MANAGER2, self.MANAGER3, self.OTHER_CASHIER, self.NOBODY,
                     "Administrator", "Guest"):
            manager_approval.reset_attempts(user)
        for user in (self.CASHIER, self.OTHER_CASHIER):
            manager_approval.reset_requester(user)
        super().tearDown()

    def _associate(self, user):
        """URY o'z metodlarida `getBranch()` uchun foydalanuvchini filialga biriktiradi."""
        branch = frappe.get_doc("Branch", self.scope.branch)
        branch.append("user", {"user": user})
        branch.save(ignore_permissions=True)

    def _rejected(self, cmd, exc=frappe.PermissionError, **kwargs):
        """Buyruq `exc` bilan RAD ETILADI va bazada hech narsa o'zgarmaydi."""
        frappe.db.savepoint(SAVEPOINT_INNER)
        try:
            with self.assertRaises(exc):
                http(cmd, **kwargs)
        finally:
            frappe.db.rollback(save_point=SAVEPOINT_INNER)

    def _draft_return(self, paid):
        """Kassir REST orqali yaratadigan qaytarish qoralamasi (Administrator sifatida tayyorlanadi)."""
        ret = http("erpnext.accounts.doctype.pos_invoice.pos_invoice.make_sales_return",
                   source_name=paid.name)
        return json_of(ret)


# ═══════════════════════════════════════════════════════════════════
#  Hujjat darajasidagi aylanib o'tish (generic REST / URY metodlari)
# ═══════════════════════════════════════════════════════════════════

class TestDocumentLevelBypass(MoneyCase):
    """URY `URY Cashier`ga POS Invoice'da write/submit/cancel bergan: API qoidalari
    `frappe.client.*` orqali aylanib o'tib bo'lmasligi kerak."""

    def test_generic_set_value_cannot_grant_a_discount_while_the_feature_is_off(self):
        doc = self._invoice(printed=True)
        self._as_cashier()

        for fieldname, value in (("additional_discount_percentage", 50), ("discount_amount", 10000)):
            with self.subTest(fieldname=fieldname):
                self._rejected(
                    "frappe.client.set_value",
                    doctype="POS Invoice", name=doc.name, fieldname=fieldname, value=value,
                )
        row = frappe.db.get_value(
            "POS Invoice", doc.name, ["additional_discount_percentage", "discount_amount", "grand_total"],
            as_dict=True,
        )
        self.assertEqual(flt(row.discount_amount), 0)
        self.assertEqual(flt(row.additional_discount_percentage), 0)
        self.assertEqual(flt(row.grand_total), self._payable(doc.name))

    def test_ury_make_invoice_cannot_smuggle_a_discount_while_the_feature_is_off(self):
        """URY'ning `make_invoice(additionalDiscount=99)` — 99% chegirma bilan yopish."""
        doc = self._invoice(printed=True)
        self._associate(self.CASHIER)
        self._as_cashier()

        self._rejected(
            "ury.ury.doctype.ury_order.ury_order.make_invoice",
            customer=doc.customer, payments=[{"mode_of_payment": self.cash, "amount": 100000}],
            cashier="x", pos_profile=self.profile, owner="x", additionalDiscount=99, invoice=doc.name,
        )
        self.assertEqual(frappe.db.get_value("POS Invoice", doc.name, "docstatus"), 0)

    def test_ury_make_invoice_without_a_discount_still_works(self):
        """Desktop POS oddiy to'lovi (chegirmasiz) BUZILMAYDI."""
        doc = self._invoice(printed=True)
        payable = self._payable(doc.name)
        self._associate(self.CASHIER)
        self._as_cashier()

        http("ury.ury.doctype.ury_order.ury_order.make_invoice",
             customer=doc.customer, payments=[{"mode_of_payment": self.cash, "amount": payable}],
             cashier="x", pos_profile=self.profile, owner="x", additionalDiscount=None, invoice=doc.name)

        self.assertEqual(frappe.db.get_value("POS Invoice", doc.name, "docstatus"), 1)

    def test_discount_feature_on_leaves_the_ury_pos_flow_untouched(self):
        """Yoqilgan bo'lsa URY POS o'z chegirmasini qo'llaydi (qoldiq xavf hisobotda)."""
        self._feature(discount=True)
        doc = self._invoice(printed=True)
        self._as_cashier()

        http("frappe.client.set_value",
             doctype="POS Invoice", name=doc.name, fieldname="additional_discount_percentage", value=5)

        self.assertEqual(flt(frappe.db.get_value("POS Invoice", doc.name, "additional_discount_percentage")), 5)

    def test_approval_audit_fields_cannot_be_forged(self):
        """`custom_discount_approved_by` faqat API yozadi — kassir menejer nomini qo'ya olmaydi."""
        doc = self._invoice(printed=True)
        self._as_cashier()
        self._rejected(
            "frappe.client.set_value",
            doctype="POS Invoice", name=doc.name, fieldname="custom_discount_approved_by",
            value=self.MANAGER,
        )

    def test_approval_audit_fields_cannot_be_rewritten_after_payment(self):
        """Maydon `allow_on_submit` — to'langan chekda ham o'zgartirib bo'lmaydi."""
        paid, _result = self._paid()
        self._as_cashier()
        for fieldname, value in (("custom_discount_approved_by", self.MANAGER),
                                 ("custom_discount_reason", "keyin yozildi")):
            with self.subTest(fieldname=fieldname):
                self._rejected(
                    "frappe.client.set_value",
                    doctype="POS Invoice", name=paid.name, fieldname=fieldname, value=value,
                )

    def test_refund_approval_cannot_be_bypassed_with_a_hand_made_return(self):
        """`make_sales_return` + `insert` + `submit` — PIN'siz pul qaytarish."""
        self._feature(refunds=True)
        paid, _result = self._paid()
        self._as_cashier()

        draft = self._draft_return(paid)
        frappe.db.savepoint(SAVEPOINT_INNER)
        try:
            with self.assertRaises(frappe.PermissionError):
                http("frappe.client.insert", doc=json.dumps(draft))
        finally:
            frappe.db.rollback(save_point=SAVEPOINT_INNER)
        self.assertFalse(
            frappe.db.exists("POS Invoice", {"is_return": 1, "return_against": paid.name})
        )

    def test_hand_made_return_is_blocked_even_when_refunds_are_off(self):
        paid, _result = self._paid()
        self._as_cashier()
        with self.assertRaises(frappe.PermissionError):
            http("frappe.client.insert", doc=json.dumps(self._draft_return(paid)))

    def test_a_paid_sale_cannot_be_voided_by_the_cashier(self):
        """`frappe.client.cancel` — pulni cho'ntakka urib, chekni bekor qilish."""
        paid, _result = self._paid()
        self._as_cashier()

        self._rejected("frappe.client.cancel", doctype="POS Invoice", name=paid.name)
        self.assertEqual(frappe.db.get_value("POS Invoice", paid.name, "docstatus"), 1)

    def test_managers_and_administrators_keep_their_desk_powers(self):
        """Menejer/Administrator chegirma va bekor qilishni Desk'da davom ettiradi."""
        doc = self._invoice(printed=True)
        frappe.set_user(self.MANAGER)
        self._flush_caches()
        http("frappe.client.set_value",
             doctype="POS Invoice", name=doc.name, fieldname="additional_discount_percentage", value=20)
        self.assertEqual(flt(frappe.db.get_value("POS Invoice", doc.name, "additional_discount_percentage")), 20)

        paid, _result = self._paid()
        frappe.set_user(self.MANAGER)
        http("frappe.client.cancel", doctype="POS Invoice", name=paid.name)
        self.assertEqual(frappe.db.get_value("POS Invoice", paid.name, "docstatus"), 2)

    def test_legacy_discount_does_not_block_ordinary_cashier_work(self):
        """Funksiya o'chiq, lekin chekda ESKI chegirma bor: to'lov va qayta hisob buzilmaydi."""
        doc = self._invoice(printed=True)
        frappe.db.set_value("POS Invoice", doc.name, "additional_discount_percentage", 10)
        frappe.get_doc("POS Invoice", doc.name).save()
        payable = self._payable(doc.name)
        self._as_cashier()

        result = self._pay(doc.name, amount=payable)

        self.assertEqual(result["docstatus"], 1)

    def test_adding_items_to_a_discounted_draft_is_not_treated_as_a_new_discount(self):
        """Foiz o'zgarmasa, mahsulot qo'shilganda chegirma SUMMASI o'sishi to'siq emas."""
        doc = self._invoice(printed=True)
        frappe.db.set_value("POS Invoice", doc.name, "additional_discount_percentage", 10)
        frappe.get_doc("POS Invoice", doc.name).save()
        self._as_cashier()

        draft = frappe.get_doc("POS Invoice", doc.name)
        draft.append("items", {"item_code": self.items[0], "qty": 5, "rate": 10000,
                               "warehouse": self.scope.warehouse})
        draft.save()

        self.assertGreater(len(frappe.get_doc("POS Invoice", doc.name).items), 2)

    def test_trusted_marker_cannot_be_set_by_a_client(self):
        """Marker `frappe.flags` da — so'rov parametri uni yoqolmaydi."""
        doc = self._invoice(printed=True)
        self._as_cashier()
        self._rejected(
            "frappe.client.set_value",
            doctype="POS Invoice", name=doc.name, fieldname="additional_discount_percentage",
            value=50, **{cashier_billing.TRUSTED_FLAG: 1},
        )

    def test_trusted_marker_is_always_reset(self):
        self.assertFalse(frappe.flags.get(cashier_billing.TRUSTED_FLAG))
        with self.assertRaises(RuntimeError):
            with cashier_billing.trusted_billing():
                self.assertTrue(frappe.flags.get(cashier_billing.TRUSTED_FLAG))
                raise RuntimeError("xato")
        self.assertFalse(frappe.flags.get(cashier_billing.TRUSTED_FLAG))

    def test_own_api_flows_pass_the_guards(self):
        """Discount API, qaytarish API va to'lov API kassir sifatida ishlashda davom etadi."""
        self._feature(discount=True, refunds=True)
        self._allow_in_returns(self.cash, True)
        doc = self._invoice(printed=True)
        self._as_cashier()

        billing.apply_discount(doc.name, percent=5, reason="doimiy mijoz")
        self.assertEqual(flt(frappe.db.get_value("POS Invoice", doc.name, "additional_discount_percentage")), 5)
        billing.remove_discount(doc.name)
        self._pay(doc.name)

        result = billing.refund_invoice(
            doc.name, json.dumps([{"name": doc.items[0].name, "qty": 1}]), "shikoyat",
            approval={"user": self.MANAGER, "pin": self.PIN},
        )
        self.assertEqual(result["docstatus"], 1)
        self.assertEqual(frappe.db.get_value("POS Invoice", result["invoice"], "is_return"), 1)
        self.assertFalse(frappe.flags.get(cashier_billing.TRUSTED_FLAG))

    def test_ury_split_flag_lets_a_discounted_invoice_be_copied(self):
        """URY hisobni bo'lganda yangi chek eski chegirmani meros qiladi (`ury_bill_split`)."""
        doc = self._invoice(printed=True)
        self._as_cashier()
        draft = frappe.get_doc("POS Invoice", doc.name)
        draft.additional_discount_percentage = 30

        with self.assertRaises(frappe.PermissionError):
            draft.save()
        frappe.flags.ury_bill_split = True
        try:
            draft = frappe.get_doc("POS Invoice", doc.name)
            draft.additional_discount_percentage = 30
            draft.save()
        finally:
            frappe.flags.ury_bill_split = False
        self.assertEqual(flt(frappe.db.get_value("POS Invoice", doc.name, "additional_discount_percentage")), 30)

    def test_cash_movements_cannot_be_posted_directly(self):
        shift = cashier_permissions.open_shift_name(self.scope)
        payload = {
            "doctype": "Ozturk Cash Movement", "pos_opening_entry": shift, "kind": "Out",
            "category": "Xarajat", "amount": 1000, "mode_of_payment": self.cash, "reason": "to'g'ridan",
        }
        self._as_cashier()
        self._rejected("frappe.client.insert", doc=json.dumps(payload))

    def test_print_jobs_cannot_be_forged_by_a_cashier(self):
        """G'aladon topshirig'ini qo'lda yaratib (chekka bog'lab) hisobotdan yashirish."""
        printer = self._printer()
        invoice = self._invoice().name
        payload = {
            "doctype": "Ozturk Print Job", "branch": self.scope.branch, "printer": printer,
            "job_type": "Drawer", "status": "Pending", "payload": "G3AAGfo=",
            "ref_doctype": "POS Invoice", "ref_name": invoice,
        }
        self._as_cashier()

        frappe.db.savepoint(SAVEPOINT_INNER)
        try:
            with self.assertRaises(frappe.ValidationError):
                http("frappe.client.insert", doc=json.dumps(payload))
        finally:
            frappe.db.rollback(save_point=SAVEPOINT_INNER)

    def test_print_job_payload_is_invisible_to_a_cashier(self):
        """Menejer hisoboti (kutilgan summa) kassirga REST orqali ham, FILTR orqali ham sizmaydi.

        Frappe ro'yxat filtrida o'qib bo'lmaydigan maydonni tekshirmaydi: `payload like 'AB%'`
        bir belgidan-bir belgi taxmin qilib butun hisobotni tiklash imkonini berardi."""
        printer = self._printer()
        marker = "ZXQ" + frappe.generate_hash(length=6)
        prefix = escpos.to_b64(marker.encode())[:8]
        report = print_queue.enqueue(
            "Shift Report", {"name": printer}, marker.encode(), self.scope.branch,
            title="X-hisobot | test",
        )
        bill = print_queue.enqueue(
            "Bill", {"name": printer}, marker.encode(), self.scope.branch, title="Chek"
        )
        self._as_cashier()

        def listing(**kwargs):
            return [row["name"] for row in http(
                "frappe.client.get_list", http_method="GET", doctype="Ozturk Print Job",
                fields=json.dumps(["name", "payload"]), **kwargs)]

        # Hisobot topshirig'i ro'yxatda ham, filtrda ham, hisoblagichda ham yo'q.
        self.assertNotIn(report, listing(limit_page_length=100))
        self.assertEqual(listing(filters=json.dumps([["payload", "like", f"{prefix}%"]])), [bill])
        self.assertEqual(http("frappe.client.get_count", http_method="GET", doctype="Ozturk Print Job",
                              filters=json.dumps([["payload", "like", f"{prefix}%"]]),), 1)
        self.assertEqual(listing(filters=json.dumps({"name": report})), [])

        # Oddiy chek topshirig'ining `payload` maydoni ham ro'yxat javobiga tushmaydi.
        rows = http("frappe.client.get_list", http_method="GET", doctype="Ozturk Print Job",
                    fields=json.dumps(["name", "payload"]), filters=json.dumps({"name": bill}))
        self.assertTrue(all(not row.get("payload") for row in rows))
        doc = http("frappe.client.get", http_method="GET", doctype="Ozturk Print Job", name=bill)
        self.assertFalse(doc.get("payload"))

        # Menejer hisobot topshirig'ini ko'radi.
        frappe.set_user(self.MANAGER)
        self.assertIn(report, [row["name"] for row in frappe.get_list(
            "Ozturk Print Job", filters={"name": report}, fields=["name"])])

    def _printer(self):
        return frappe.get_doc(
            {"doctype": "Ozturk Printer", "printer_name": "_RM Printer", "branch": self.scope.branch,
             "role": "Kassa", "ip_address": "192.0.2.90", "port": 9100}
        ).insert(ignore_permissions=True).name


# ═══════════════════════════════════════════════════════════════════
#  To'lov kirishlari
# ═══════════════════════════════════════════════════════════════════

class TestPaymentInputs(MoneyCase):
    def _payments(self, doc, rows, **kwargs):
        self._as_cashier()
        return billing.submit_payment(doc.name, json.dumps(rows), **kwargs)

    def test_rows_that_are_not_objects_get_a_clean_error(self):
        doc = self._invoice()
        for rows in ([5], ["Нахт"], [None], [[1, 2]]):
            with self.subTest(rows=rows):
                with self.assertRaises(frappe.ValidationError):
                    self._payments(doc, rows)

    def test_non_finite_and_absurd_amounts_are_rejected_before_the_database(self):
        doc = self._invoice()
        for amount in ("nan", "inf", "-inf", 1e30, 10 ** 15):
            with self.subTest(amount=amount):
                frappe.db.savepoint(SAVEPOINT_INNER)
                try:
                    with self.assertRaises(frappe.ValidationError):
                        self._payments(doc, [{"mode_of_payment": self.cash, "amount": amount}])
                finally:
                    frappe.db.rollback(save_point=SAVEPOINT_INNER)

    def test_amounts_are_rounded_to_the_currency_precision(self):
        """3 xonali summa bazaga `paid_amount = 67200.004` bo'lib yozilmasin."""
        doc = self._invoice()
        payable = self._payable(doc.name)

        result = self._payments(doc, [{"mode_of_payment": self.cash, "amount": payable + 0.004}])

        self.assertEqual(flt(result["paid_amount"]), payable)
        self.assertEqual(flt(result["change_amount"]), 0)
        self.assertEqual(result["payments"], [{"mode_of_payment": self.cash, "amount": payable}])

    def test_float_sums_do_not_break_the_exact_payment_check(self):
        """0.1 + 0.2 kabi float yig'indisi 'yetarli emas' xatosini bermasligi kerak."""
        self._feature(split_payment=True)
        doc = self._invoice()
        payable = self._payable(doc.name)
        parts = [payable * 0.1, payable * 0.2, payable * 0.3, payable * 0.4]

        result = self._payments(
            doc, [{"mode_of_payment": self.cash, "amount": amount} for amount in parts]
        )

        self.assertEqual(result["docstatus"], 1)
        self.assertEqual(flt(result["change_amount"]), 0)

    def test_duplicate_mode_rows_are_summed_and_change_comes_from_cash_only(self):
        self._feature(split_payment=True)
        doc = self._invoice()
        payable = self._payable(doc.name)

        result = self._payments(doc, [
            {"mode_of_payment": self.CARD, "amount": payable - 30000},
            {"mode_of_payment": self.cash, "amount": 20000},
            {"mode_of_payment": self.cash, "amount": 20000},
        ])

        self.assertEqual(flt(result["change_amount"]), 10000)

    def test_non_cash_overpay_cannot_be_hidden_in_several_rows(self):
        self._feature(split_payment=True)
        doc = self._invoice()
        payable = self._payable(doc.name)
        half = payable / 2 + 1000
        with self.assertRaises(frappe.ValidationError) as ctx:
            self._payments(doc, [
                {"mode_of_payment": self.CARD, "amount": half},
                {"mode_of_payment": self.CARD, "amount": half},
            ])
        self.assertIn("Naqd bo'lmagan", str(ctx.exception))

    def test_invoices_of_another_branch_cannot_be_paid(self):
        doc = self._invoice()
        frappe.db.set_value("POS Invoice", doc.name, "branch", "Boshqa filial", update_modified=False)
        with self.assertRaises(frappe.PermissionError):
            self._payments(doc, [{"mode_of_payment": self.cash, "amount": self._payable(doc.name)}])
        self.assertEqual(frappe.db.get_value("POS Invoice", doc.name, "docstatus"), 0)

    def test_unicode_and_padded_mode_names_are_not_accepted(self):
        doc = self._invoice()
        payable = self._payable(doc.name)
        for mode in (self.cash + " ", self.cash.upper() + "​", self.cash.lower() + "x", ""):
            with self.subTest(mode=mode):
                with self.assertRaises(frappe.ValidationError):
                    self._payments(doc, [{"mode_of_payment": mode, "amount": payable}])

    def test_payments_may_arrive_as_a_list_or_as_json(self):
        doc = self._invoice()
        payable = self._payable(doc.name)
        self._as_cashier()
        rows = [{"mode_of_payment": self.cash, "amount": payable}]

        self.assertEqual(billing.submit_payment(doc.name, rows)["docstatus"], 1)

    def test_a_draft_return_invoice_cannot_be_paid_as_a_sale(self):
        paid, _result = self._paid()
        draft = self._draft_return(paid)
        frappe.set_user("Administrator")
        saved = frappe.get_doc(draft)
        with cashier_billing.trusted_billing():
            saved.insert()
        self._as_cashier()

        with self.assertRaises(frappe.ValidationError) as ctx:
            billing.submit_payment(
                saved.name, json.dumps([{"mode_of_payment": self.cash, "amount": 1000}])
            )
        self.assertIn("Qaytarish", str(ctx.exception))

    def test_non_finite_tips_are_rejected(self):
        self._feature(tips=True)
        doc = self._invoice()
        payable = self._payable(doc.name)
        for tip in ("nan", "inf"):
            with self.subTest(tip=tip):
                with self.assertRaises(frappe.ValidationError):
                    self._payments(doc, [{"mode_of_payment": self.cash, "amount": payable}], tip=tip)

    def test_tip_never_carries_service_charge_after_later_recalculation(self):
        """Choychaqa qatori keyingi `save()` da xizmat haqi/QQS bilan qayta hisoblanmaydi."""
        self._feature(tips=True)
        doc = self._invoice()
        service_account = self.service["account"]
        cashier_billing.set_tip(doc, 5000)
        doc.run_method("calculate_taxes_and_totals")
        doc.save()

        doc = frappe.get_doc("POS Invoice", doc.name)
        doc.append("items", {"item_code": self.items[0], "qty": 2, "rate": 10000,
                             "warehouse": self.scope.warehouse})
        doc.save()
        doc = frappe.get_doc("POS Invoice", doc.name)

        self.assertEqual(cashier_billing.get_tip(doc), 5000)
        service = self._tax(doc, service_account)
        self.assertAlmostEqual(flt(service.tax_amount), flt(doc.net_total) * flt(self.service["rate"]) / 100, places=2)
        self.assertAlmostEqual(
            flt(doc.grand_total), flt(doc.net_total) + flt(service.tax_amount) + 5000, places=2
        )


# ═══════════════════════════════════════════════════════════════════
#  Chegirma kirishlari
# ═══════════════════════════════════════════════════════════════════

class TestDiscountInputs(MoneyCase):
    def setUp(self):
        super().setUp()
        self._feature(discount=True)
        self._max_discount(100)          # chegara tasdiqsiz o'tsin — kirish tekshiruvi sinaladi

    def test_non_finite_values_are_rejected_cleanly(self):
        doc = self._invoice()
        self._as_cashier()
        for kwargs in ({"percent": "nan"}, {"amount": "nan"}, {"percent": "inf"}, {"amount": "-inf"}):
            with self.subTest(kwargs=kwargs):
                frappe.db.savepoint(SAVEPOINT_INNER)
                try:
                    with self.assertRaises(frappe.ValidationError):
                        billing.apply_discount(doc.name, reason="sabab", **kwargs)
                finally:
                    frappe.db.rollback(save_point=SAVEPOINT_INNER)

    def test_amount_to_percent_to_amount_never_drifts(self):
        """Summa bilan qo'yilgan chegirma AYNAN shu summaga qaytadi (foizga aylantirishda yo'qolmaydi)."""
        rng = random.Random(7)
        for lines in (((3, 10000), (3, 10000)), ((7, 12345), (3, 4999)), ((1, 33333), (1, 33334))):
            doc = self._invoice(lines=lines)
            total = flt(doc.total)
            for _ in range(15):
                amount = float(rng.randint(1, int(total) - 1))
                frappe.db.savepoint(SAVEPOINT_INNER)
                try:
                    bill = billing.apply_discount(doc.name, amount=amount, reason="sabab")
                    self.assertEqual(flt(bill["discount"]), amount, f"{lines} {amount}")
                finally:
                    frappe.db.rollback(save_point=SAVEPOINT_INNER)

    def test_a_discount_that_exceeds_the_total_is_rejected_by_any_margin(self):
        doc = self._invoice()
        total = flt(doc.total)
        for amount in (total, total + 0.01, total + 1, total * 2):
            with self.subTest(amount=amount):
                with self.assertRaises(frappe.ValidationError):
                    billing.apply_discount(doc.name, amount=amount, reason="sabab")

    def test_the_last_unit_below_the_total_is_accepted(self):
        doc = self._invoice()
        bill = billing.apply_discount(doc.name, amount=flt(doc.total) - 1, reason="sabab")
        self.assertEqual(flt(bill["discount"]), flt(doc.total) - 1)

    def test_a_draft_return_cannot_be_discounted(self):
        paid, _result = self._paid()
        draft = self._draft_return(paid)
        saved = frappe.get_doc(draft)
        with cashier_billing.trusted_billing():
            saved.insert()
        self._as_cashier()

        with self.assertRaises(frappe.ValidationError) as ctx:
            billing.apply_discount(saved.name, percent=5, reason="sabab")
        self.assertIn("Qaytarish", str(ctx.exception))

    def test_new_discount_replaces_instead_of_stacking(self):
        doc = self._invoice()
        billing.apply_discount(doc.name, percent=10, reason="birinchi")
        bill = billing.apply_discount(doc.name, percent=10, reason="ikkinchi")

        self.assertEqual(flt(bill["discount"]), flt(doc.total) * 0.10)

    def test_percent_discount_follows_the_items_but_stays_within_the_approved_percent(self):
        """Summa bilan qo'yilgan chegirma foiz sifatida saqlanadi: yangi mahsulot qo'shilsa u
        mutanosib o'sadi (foiz o'zgarmaydi). Bu xulq hujjatlashtirilgan — jim o'zgarish EMAS."""
        doc = self._invoice()
        bill = billing.apply_discount(doc.name, amount=6000, reason="sabab")
        percent = flt(bill["discount_percent"])

        draft = frappe.get_doc("POS Invoice", doc.name)
        draft.append("items", {"item_code": self.items[0], "qty": 3, "rate": 10000,
                               "warehouse": self.scope.warehouse})
        draft.save()
        bill = cashier_billing.build_bill(frappe.get_doc("POS Invoice", doc.name), self.scope)

        self.assertEqual(flt(bill["discount_percent"]), percent)
        self.assertGreater(flt(bill["discount"]), 6000)


# ═══════════════════════════════════════════════════════════════════
#  Menejer tasdig'i
# ═══════════════════════════════════════════════════════════════════

class TestApprovalHardening(MoneyCase):
    def setUp(self):
        super().setUp()
        self._feature(discount=True)
        self._max_discount(0)
        self.doc = self._invoice()
        self._as_cashier()

    def _try(self, approval):
        return billing.apply_discount(self.doc.name, percent=20, reason="sabab", approval=approval)

    def test_malformed_approval_values_ask_for_approval_instead_of_crashing(self):
        for approval in (
            "not json", "[]", "5", 5, [], [1, 2], {},
            {"user": 5, "pin": 1234}, {"user": ["a"], "pin": {"x": 1}}, {"user": None, "pin": None},
            json.dumps({"user": self.MANAGER}), json.dumps({"pin": self.PIN}),
        ):
            with self.subTest(approval=approval):
                with self.assertRaises(ApprovalRequired):
                    self._try(approval)

    def test_pin_must_match_exactly(self):
        for pin in (self.PIN + " x", " ", "٤٣٢١", "4321.0", "04321", "4321\u0000"):
            with self.subTest(pin=pin):
                with self.assertRaises(ApprovalRequired):
                    self._try({"user": self.MANAGER, "pin": pin})
        manager_approval.reset_attempts(self.MANAGER)
        manager_approval.reset_requester(self.CASHIER)
        self.assertEqual(self._try({"user": self.MANAGER, "pin": f"  {self.PIN}  "})["discount_approved_by"], self.MANAGER)

    def test_cashier_cannot_approve_with_own_or_a_cashiers_pin(self):
        frappe.set_user("Administrator")
        frappe.db.set_value("User", self.OTHER_CASHIER, "custom_pos_pin", None)
        from frappe.utils.password import set_encrypted_password
        set_encrypted_password("User", self.OTHER_CASHIER, "5555", manager_approval.PIN_FIELD)
        self._as_cashier()

        for user in (self.CASHIER, self.OTHER_CASHIER, self.NOBODY, "Administrator", "Guest"):
            with self.subTest(user=user):
                with self.assertRaises(ApprovalRequired):
                    self._try({"user": user, "pin": "5555"})

    def test_a_disabled_or_demoted_manager_can_no_longer_approve(self):
        frappe.set_user("Administrator")
        frappe.db.set_value("User", self.MANAGER2, "enabled", 0)
        frappe.db.set_value("User", self.MANAGER3, "custom_pos_pin", "2222")
        frappe.db.sql("delete from `tabHas Role` where parent=%s", self.MANAGER3)
        frappe.clear_cache(user=self.MANAGER3)
        frappe.clear_cache(user=self.MANAGER2)
        self._as_cashier()

        with self.assertRaises(ApprovalRequired):
            self._try({"user": self.MANAGER2, "pin": "1111"})
        with self.assertRaises(ApprovalRequired):
            self._try({"user": self.MANAGER3, "pin": "2222"})

    def test_guessing_across_managers_is_capped_per_requester(self):
        """Har menejerga 5 urinishdan 3 menejer = 15 taxmin bo'lmaydi: kassirga umumiy chegara."""
        managers = (self.MANAGER, self.MANAGER2, self.MANAGER3)
        attempts = 0
        for _ in range(manager_approval.MAX_ATTEMPTS):
            for manager in managers:
                attempts += 1
                with self.assertRaises(ApprovalRequired):
                    self._try({"user": manager, "pin": "0000"})

        self.assertEqual(attempts, 15)
        total = sum(manager_approval.failed_attempts(m) for m in managers)
        self.assertLessEqual(total, manager_approval.REQUESTER_MAX_ATTEMPTS)

        # Endi TO'G'RI PIN ham o'tmaydi: kassir bloklangan.
        with self.assertRaises(ApprovalRequired) as ctx:
            self._try({"user": self.MANAGER3, "pin": "2222"})
        self.assertIn("daqiqa", str(ctx.exception))
        self.assertTrue(self._stays_unapproved())

    def test_requester_lock_does_not_touch_other_cashiers(self):
        for _ in range(manager_approval.REQUESTER_MAX_ATTEMPTS + 1):
            with self.assertRaises(ApprovalRequired):
                self._try({"user": self.MANAGER, "pin": "0000"})
        manager_approval.reset_attempts(self.MANAGER)          # menejerning o'z hisobchisi alohida

        frappe.set_user(self.OTHER_CASHIER)
        self._flush_caches()
        bill = billing.apply_discount(
            self.doc.name, percent=20, reason="sabab", approval={"user": self.MANAGER, "pin": self.PIN}
        )

        self.assertEqual(bill["discount_approved_by"], self.MANAGER)

    def test_unknown_approver_names_leave_no_counter_behind(self):
        """Cheksiz turli nom bilan kesh kalitlari to'ldirilmasin."""
        for name in ("no-such-user-1", "no-such-user-2", "x" * 200):
            with self.assertRaises(ApprovalRequired):
                self._try({"user": name, "pin": "1234"})
            self.assertEqual(manager_approval.failed_attempts(name), 0)

    def test_pin_format_accepts_only_ascii_digits(self):
        frappe.set_user("Administrator")
        for value in ("١٢٣٤", "12٣٤", "12 34", "123", "123456789", "abcd"):
            with self.subTest(value=value):
                doc = frappe._dict({manager_approval.PIN_FIELD: value})
                with self.assertRaises(frappe.ValidationError):
                    manager_approval.validate_pin_format(doc)
        manager_approval.validate_pin_format(frappe._dict({manager_approval.PIN_FIELD: "12345678"}))

    def _stays_unapproved(self) -> bool:
        return not frappe.db.get_value("POS Invoice", self.doc.name, "custom_discount_approved_by")


# ═══════════════════════════════════════════════════════════════════
#  Chop etish: endpointlar, g'aladon, ESC/POS
# ═══════════════════════════════════════════════════════════════════

class TestPrintingHardening(MoneyCase):
    def setUp(self):
        super().setUp()
        self._feature(cash_drawer=True)
        self.printer = frappe.get_doc(
            {"doctype": "Ozturk Printer", "printer_name": "_RM Printer", "branch": self.scope.branch,
             "role": "Kassa", "ip_address": "192.0.2.91", "port": 9100}
        ).insert(ignore_permissions=True)
        row = {"name": self.printer.name, "header_line_1": None, "header_line_2": None,
               "footer_text": None, "paper_width": "80", "codepage": "cp866",
               "codepage_number": 17, "cut_paper": 1}
        patcher = mock.patch.object(print_queue, "cashier_printer", return_value=row)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.job = print_queue.enqueue(
            "Bill", {"name": self.printer.name}, b"chek", self.scope.branch, title="Chek 1"
        )

    def test_diagnostic_endpoints_need_a_cashier_role(self):
        frappe.set_user(self.NOBODY)
        self._flush_caches()
        for call in (
            lambda: printing.reprint(self.job),
            lambda: printing.get_status(),
            lambda: printing.recent_jobs(),
            lambda: printing.test_print(self.printer.name),
        ):
            with self.assertRaises(frappe.PermissionError):
                call()

    def test_other_branches_are_not_readable_by_a_cashier(self):
        self._as_cashier()
        with self.assertRaises(frappe.PermissionError):
            printing.get_status(branch="Boshqa filial")
        with self.assertRaises(frappe.PermissionError):
            printing.recent_jobs(branch="Boshqa filial")

    def test_recent_jobs_survives_garbage_limits(self):
        self._as_cashier()
        for limit in ("abc", None, "-5", "99999", 0):
            with self.subTest(limit=limit):
                self.assertIsInstance(printing.recent_jobs(limit=limit), list)

    def test_cashier_can_still_reprint_a_bill_of_her_branch(self):
        self._as_cashier()
        self.assertTrue(printing.reprint(self.job)["queued"])

    def test_drawer_and_report_jobs_cannot_be_reprinted(self):
        drawer = print_queue.kick_drawer(self.scope, reason="sinov")
        report = print_queue.enqueue(
            print_queue.JOB_SHIFT_REPORT, {"name": self.printer.name}, b"x", self.scope.branch
        )
        self._as_cashier()
        for job in (drawer, report):
            with self.assertRaises(frappe.ValidationError):
                printing.reprint(job)

    def test_manual_drawer_openings_are_rate_limited(self):
        self._as_cashier()
        allowed = printing.DRAWER_MAX_PER_MINUTE
        for _ in range(allowed):
            self.assertTrue(printing.open_drawer("qaytim")["queued"])
        with self.assertRaises(frappe.ValidationError) as ctx:
            printing.open_drawer("yana")
        self.assertIn("ko'p", str(ctx.exception))
        self.assertEqual(
            frappe.db.count("Ozturk Print Job", {"job_type": "Drawer", "owner": self.CASHIER}), allowed
        )

    def test_the_drawer_limit_is_per_user_and_ignores_payment_openings(self):
        self._as_cashier()
        for _ in range(printing.DRAWER_MAX_PER_MINUTE):
            printing.open_drawer("qaytim")
        # Naqd to'lovdan keyingi ochilish (chekka bog'liq) limitga kirmaydi.
        self.assertTrue(print_queue.kick_drawer(self.scope, reason="payment", invoice=self._invoice().name))
        frappe.set_user(self.OTHER_CASHIER)
        self._flush_caches()
        self.assertTrue(printing.open_drawer("boshqa kassir")["queued"])

    def test_drawer_jobs_expire_instead_of_firing_late(self):
        job = print_queue.kick_drawer(self.scope, reason="eski")
        frappe.db.set_value(
            "Ozturk Print Job", job, "creation", add_to_date(now_datetime(), seconds=-print_queue.DRAWER_TTL - 5)
        )

        pulled = [row["job"] for row in print_queue.pull_jobs(self.scope.branch, "agent-1")]
        self.assertNotIn(job, pulled)
        self.assertEqual(frappe.db.get_value("Ozturk Print Job", job, "status"), "Failed")

    def test_drawer_audit_rows_cannot_be_erased_or_edited_by_a_cashier(self):
        """G'aladon izi (kim, qachon, nima uchun) kassir tomonidan o'chirilmaydi va o'zgartirilmaydi."""
        self._as_cashier()
        job = printing.open_drawer("qaytim berish")["queued"]

        self.assertEqual(frappe.db.get_value("Ozturk Print Job", job, ["owner", "reason"]),
                         (self.CASHIER, "qaytim berish"))
        self._rejected("frappe.client.delete", doctype="Ozturk Print Job", name=job)
        self._rejected("frappe.client.set_value", doctype="Ozturk Print Job", name=job,
                       fieldname="reason", value="hech narsa")
        report_rows = shift_report_module._drawer_openings(
            self.scope.branch, add_to_date(now_datetime(), minutes=-5), add_to_date(now_datetime(), minutes=5)
        )
        self.assertIn(job, [row["job"] for row in report_rows])

    def test_control_characters_in_text_cannot_fire_the_drawer(self):
        """Matndagi `ESC p 0 25 250` (g'aladon impulsi) qog'ozga BAYT sifatida o'tmasin."""
        pulse = b"\x1bp\x00\x19\xfa"
        evil = "Ali\x1bp\x00\x19\xfa\x1d\x56\x00\x1b@ oshxona"
        bill = {
            "invoice": "INV-1", "items": [{"item_name": evil, "qty": 1, "amount": 1000, "comment": evil}],
            "total": 1000, "grand_total": 1000, "discount": 100, "discount_percent": 10,
            "discount_reason": evil, "delivery": {"phone": evil, "address": evil},
            "waiter_name": evil, "cashier_name": evil, "taxes": [], "payments": [],
        }
        printer = {"paper_width": "80", "codepage": "cp866", "codepage_number": 17, "cut_paper": 1}

        payload = escpos.build_bill(bill, printer)

        self.assertNotIn(pulse, payload)
        # Faqat generator qo'ygan boshqaruv buyruqlari qoladi: init, kod jadvali, tekislash, qalin, o'lcham, kesish.
        control = [payload[i + 1:i + 2] for i in range(len(payload) - 1) if payload[i:i + 1] == b"\x1b"]
        self.assertTrue(set(control) <= {b"@", b"t", b"a", b"E"}, control)
        self.assertEqual(payload.count(b"\x1b@"), 1)
        self.assertEqual(payload.count(b"\x1dV"), 1)          # faqat generator qo'ygan oxirgi kesish

    def test_huge_texts_do_not_flood_the_printer(self):
        """Yuz minglab belgili izoh chekni cheksiz uzun qilib qog'ozni tugatmasin."""
        printer = {"paper_width": "80", "codepage": "cp866", "codepage_number": 17, "cut_paper": 1}
        bill = {"invoice": "INV-1", "items": [{"item_name": "Choy", "qty": 1, "amount": 1000,
                                               "comment": "x" * 300000}],
                "total": 1000, "grand_total": 1000, "taxes": [], "payments": []}

        payload = escpos.build_bill(bill, printer)

        self.assertLess(len(payload), 4000)

    def test_control_characters_are_stripped_from_shift_reports_and_kots_too(self):
        evil = "x\x1bp\x00\x19\xfa"
        printer = {"paper_width": "80", "codepage": "cp866", "codepage_number": 17, "cut_paper": 1}
        report = {
            "kind": "X", "restricted": True, "cashier": {"full_name": evil}, "printed_by": evil,
            "counts": {}, "sales": dict.fromkeys(("gross_sales",)), "payments": [],
            "cash": {"opening": 1, "counted": None, "expected": None, "difference": None},
            "cash_movements": {"count": 1, "total_in": 0, "total_out": 5, "items": [
                {"kind": "Out", "category": evil, "amount": 5, "reason": evil, "user_name": evil,
                 "approved_by_name": evil, "posting_datetime": "2026-01-01 10:00:00"}]},
            "drawer_openings": [{"time": "2026-01-01 10:00:00", "user_name": evil, "reason": evil}],
            "period_start": "2026-01-01 09:00:00", "period_end": "2026-01-01 10:00:00",
            "generated_at": "2026-01-01 10:00:00", "currency": "UZS",
        }
        kot = {"kot": "K", "station": evil, "table": evil, "waiter": evil, "items": [
            {"item_name": evil, "qty": 1, "comment": evil}], "comments": evil}

        for payload in (escpos.build_shift_report(report, printer), escpos.build_kot(kot, printer)):
            self.assertNotIn(b"\x1bp", payload)


# ═══════════════════════════════════════════════════════════════════
#  Ko'r sanoq
# ═══════════════════════════════════════════════════════════════════

class TestBlindCountRoutes(MoneyCase):
    def setUp(self):
        super().setUp()
        self.shift = cashier_permissions.open_shift_name(self.scope)

    def test_shift_closing_data_hides_the_totals_from_a_cashier(self):
        self._as_cashier()
        data = cashier_api.get_shift_closing_data()
        flat = json.dumps(data)
        for key in ("grand_total", "expected", "net_total", "difference", "total_sales"):
            self.assertNotIn(key, flat)

    def test_context_and_features_carry_no_cash_figures(self):
        self._as_cashier()
        flat = json.dumps(cashier_api.get_cashier_context(), default=str)
        for key in ("expected_amount", "expected_cash", "cash_diff", "grand_total", "total_sales"):
            self.assertNotIn(key, flat)

    def test_print_job_payload_of_a_managers_report_stays_permlevel_one(self):
        field = frappe.get_meta("Ozturk Print Job").get_field("payload")
        self.assertEqual(field.permlevel, 1)
        readers = {
            row.role for row in frappe.get_meta("Ozturk Print Job").permissions
            if row.permlevel == 1 and row.read
        }
        self.assertNotIn("URY Cashier", readers)

    @unittest.expectedFailure
    def test_desktop_pos_closing_data_hides_expected_cash_from_a_cashier(self):
        """ISBOTLANGAN TESHIK (integratsiya so'rovi): `desktop_pos.getPosClosingData` istalgan
        kassirga kutilgan naqd va jami savdoni bergan — ko'r sanoq buziladi. Fayl
        (`api/desktop_pos.py`) bu sharhchining ro'yxatida emas; tuzatilganda bu test
        «kutilmagan muvaffaqiyat» beradi va `expectedFailure` belgisini olib tashlash kerak."""
        self._as_cashier()
        for cmd in ("ozturkapp.ozturkapp.api.desktop_pos.getPosClosingData",
                    "ury.ury_pos.api.getPosClosingData"):
            data = http(cmd, http_method="GET", pos_opening_entry=self.shift)
            self.assertNotIn("grand_total", data)
            self.assertTrue(all("expected_amount" not in row for row in data["reconciliation"]))

    def test_closing_response_strips_expected_and_difference_for_a_cashier(self):
        result = {
            "name": "POS-CLO-1", "status": "closed",
            "z_report_data": {
                "total_sales": 10, "expected_cash": 9, "cash_diff": -1, "actual_cash": 8,
                "payments": [{"mode_of_payment": "Нахт", "expected_amount": 9, "closing_amount": 8,
                              "difference": -1, "opening_amount": 1}],
            },
        }
        cashier_api._withhold_cash_figures(result)
        report = result["z_report_data"]
        self.assertNotIn("expected_cash", report)
        self.assertNotIn("cash_diff", report)
        self.assertNotIn("total_sales", report)
        self.assertEqual(report["payments"], [{"mode_of_payment": "Нахт", "closing_amount": 8}])

    def test_underpayment_through_the_generic_submit_is_a_known_config_risk(self):
        """Hujjatlangan qoldiq xavf: profilda `allow_partial_payment` yoqilgan bo'lsa generic submit
        kam to'lovni qabul qiladi; bizning API esa bayroqdan qat'i nazar to'liq to'lov talab qiladi."""
        frappe.db.set_value("POS Profile", self.profile, "allow_partial_payment", 1)
        doc = self._invoice()
        self._as_cashier()
        with self.assertRaises(frappe.ValidationError):
            billing.submit_payment(doc.name, json.dumps([{"mode_of_payment": self.cash, "amount": 100}]))
        self.assertEqual(frappe.db.get_value("POS Invoice", doc.name, "docstatus"), 0)

    def test_non_finite_counted_cash_is_rejected(self):
        self._as_cashier()
        for value in ("nan", "inf", 1e30):
            with self.subTest(value=value):
                with self.assertRaises(frappe.ValidationError):
                    cashier_api._parse_counted_cash({self.cash: value}, self.profile)


# ═══════════════════════════════════════════════════════════════════
#  Qaytarish va konsolidatsiya (ERPNext manbasi bo'yicha, commit'siz)
# ═══════════════════════════════════════════════════════════════════

class TestRefundConsolidation(MoneyCase):
    """`POS Invoice Merge Log` qo'lda yaratiladi (ERPNext'ning `consolidate_pos_invoices`i
    commit qiladi, shuning uchun uni chaqirib bo'lmaydi) — natijaviy Sales Invoice/Credit Note
    haqiqiy `on_submit` yo'lidan o'tadi."""

    def setUp(self):
        super().setUp()
        self._feature(refunds=True)
        self._allow_in_returns(self.cash, True)

    def _sale(self):
        doc = self._invoice(lines=((3, 10000), (3, 10000)))
        frappe.db.set_value("POS Invoice", doc.name, "update_stock", 1)
        self._pay(doc.name, amount=self._payable(doc.name) + 5000)
        return frappe.get_doc("POS Invoice", doc.name)

    def _refund_first_row(self, paid, qty=1):
        result = billing.refund_invoice(
            paid.name, json.dumps([{"name": paid.items[0].name, "qty": qty}]), "sabab"
        )
        return frappe.get_doc("POS Invoice", result["invoice"])

    def _merge(self, *invoices):
        rows = [
            {"pos_invoice": inv.name, "customer": inv.customer, "posting_date": inv.posting_date,
             "grand_total": inv.grand_total, "is_return": inv.is_return, "return_against": inv.return_against}
            for inv in invoices
        ]
        log = frappe.get_doc({
            "doctype": "POS Invoice Merge Log", "posting_date": nowdate(), "posting_time": nowtime(),
            "customer": invoices[0].customer, "company": invoices[0].company,
            "merge_invoices_based_on": "Customer", "pos_invoices": rows,
        })
        log.insert(ignore_permissions=True)
        log.submit()
        log.reload()
        return log

    def test_the_return_is_created_without_stock_movement(self):
        paid = self._sale()
        ret = self._refund_first_row(paid)

        self.assertEqual(ret.update_stock, 0)
        self.assertFalse(frappe.get_all("Stock Ledger Entry", filters={"voucher_no": ret.name}))

    def test_credit_note_of_a_partial_refund_consolidates_without_touching_stock(self):
        paid = self._sale()
        ret = self._refund_first_row(paid)

        log = self._merge(paid, ret)

        credit_note = frappe.get_doc("Sales Invoice", log.consolidated_credit_note)
        sale = frappe.get_doc("Sales Invoice", log.consolidated_invoice)
        self.assertEqual(credit_note.update_stock, 0)
        self.assertEqual(credit_note.return_against, sale.name)
        self.assertFalse(frappe.get_all("Stock Ledger Entry", filters={"voucher_no": credit_note.name}))
        self.assertEqual(flt(credit_note.grand_total), flt(ret.grand_total))
        cash_account = frappe.db.get_value(
            "Mode of Payment Account", {"parent": self.cash, "company": paid.company}, "default_account"
        )
        cash_credit = sum(
            flt(row.credit) - flt(row.debit)
            for row in frappe.get_all(
                "GL Entry", filters={"voucher_no": credit_note.name, "is_cancelled": 0, "account": cash_account},
                fields=["debit", "credit"],
            )
        )
        self.assertEqual(cash_credit, abs(flt(ret.grand_total)))
        self.assertEqual(frappe.db.get_value("POS Invoice", ret.name, "status"), "Consolidated")

    def test_refund_after_the_sale_was_consolidated_becomes_a_credit_note_against_it(self):
        paid = self._sale()
        first = self._merge(paid)
        paid.reload()
        self.assertEqual(paid.status, "Consolidated")

        ret = self._refund_first_row(paid)
        second = self._merge(ret)

        credit_note = frappe.get_doc("Sales Invoice", second.consolidated_credit_note)
        self.assertEqual(credit_note.return_against, first.consolidated_invoice)
        self.assertEqual(credit_note.update_stock, 0)
        self.assertFalse(frappe.get_all("Stock Ledger Entry", filters={"voucher_no": credit_note.name}))

    def test_consolidation_runs_under_the_cashier_session_with_the_guards_on(self):
        """Smena yopilishi kassir nomidan ishlaydi: hujjat qo'riqchilari konsolidatsiyani buzmasligi kerak."""
        paid = self._sale()
        ret = self._refund_first_row(paid)
        frappe.set_user(self.CASHIER)
        self._flush_caches()

        log = self._merge(paid, ret)

        self.assertTrue(log.consolidated_invoice and log.consolidated_credit_note)
        self.assertEqual(frappe.db.get_value("POS Invoice", ret.name, "status"), "Consolidated")

    def test_refund_of_a_partially_paid_mix_never_exceeds_what_was_paid(self):
        """Ikki usul + qaytim: usul bo'yicha qaytarilgan summa to'langandan oshmaydi."""
        self._feature(split_payment=True)
        doc = self._invoice()
        payable = self._payable(doc.name)
        billing.submit_payment(doc.name, json.dumps([
            {"mode_of_payment": self.CARD, "amount": payable - 20000},
            {"mode_of_payment": self.cash, "amount": 30000},
        ]))
        paid = frappe.get_doc("POS Invoice", doc.name)
        refunded = {}
        for row in paid.items:
            ret = billing.refund_invoice(
                paid.name, json.dumps([{"name": row.name, "qty": row.qty}]), "sabab"
            )
            for payment in ret["payments"]:
                refunded[payment["mode_of_payment"]] = (
                    refunded.get(payment["mode_of_payment"], 0) + abs(payment["amount"])
                )

        self.assertAlmostEqual(refunded[self.CARD], payable - 20000, places=2)
        self.assertAlmostEqual(refunded[self.cash], 20000, places=2)

    def test_concurrent_double_refund_is_serialised_by_the_row_lock(self):
        """Ikkinchi qaytarish birinchisining submit'ini ko'radi va qolgan miqdorni tekshiradi."""
        paid = self._sale()
        self._refund_first_row(paid, qty=3)
        with self.assertRaises(frappe.ValidationError):
            self._refund_first_row(paid, qty=1)

    def test_refund_of_a_return_and_of_a_draft_are_rejected(self):
        paid = self._sale()
        ret = self._refund_first_row(paid)
        with self.assertRaises(frappe.ValidationError):
            billing.refund_invoice(ret.name, json.dumps([{"name": ret.items[0].name, "qty": 1}]), "sabab")
        draft = self._invoice()
        with self.assertRaises(frappe.ValidationError):
            billing.refund_invoice(draft.name, json.dumps([{"name": draft.items[0].name, "qty": 1}]), "sabab")

    def test_exhausted_payment_capacity_gives_a_clean_error(self):
        """Yaxlitlashdan keyin naqd sig'im tugagan bo'lsa ZeroDivisionError emas, tushunarli xato."""
        from decimal import Decimal
        from ozturkapp.ozturkapp.utils import refunds
        for capacity, total in (([("Нахт", 0.0), ("Karta", 0.0)], 10.0), ([("Нахт", 0.004)], 0.01)):
            with self.subTest(capacity=capacity):
                with self.assertRaises(frappe.ValidationError):
                    refunds.allocate_refund(capacity, total, Decimal("0.01"))


# ═══════════════════════════════════════════════════════════════════
#  Kassa harakati
# ═══════════════════════════════════════════════════════════════════

class TestCashMovementEdges(MoneyCase):
    def setUp(self):
        super().setUp()
        self._feature(cash_movements=True)
        frappe.db.set_value("POS Profile", self.profile,
                            cashier_features.SETTINGS["cash_payout_approval_limit"]["fieldname"], 50000)
        if not frappe.db.get_value("Company", self.scope.company, "custom_cash_movement_account"):
            from ozturkapp.ozturkapp.setup import cashier_shift_setup
            account = cashier_shift_setup.ensure_clearing_account(self.scope.company)
            frappe.db.set_value("Company", self.scope.company, "custom_cash_movement_account", account)

    def _create(self, kind="Out", amount=1000, category="Xarajat", reason="muz olindi", **kwargs):
        self._as_cashier()
        return cash_movements.create_cash_movement(kind, amount, category, reason, **kwargs)

    def test_bad_amounts_are_rejected(self):
        for amount in (0, -5, "nan", "inf", "abc", None, 0.004, 1e30):
            with self.subTest(amount=amount):
                frappe.db.savepoint(SAVEPOINT_INNER)
                try:
                    with self.assertRaises(frappe.ValidationError):
                        self._create(amount=amount)
                finally:
                    frappe.db.rollback(save_point=SAVEPOINT_INNER)

    def test_amounts_are_stored_at_the_currency_precision_and_the_journal_balances(self):
        result = self._create(amount=1234.5678)
        movement = frappe.get_doc("Ozturk Cash Movement", result["name"])
        journal = frappe.get_doc("Journal Entry", movement.journal_entry)

        self.assertEqual(flt(movement.amount), flt(1234.5678, movement.precision("amount")))
        self.assertEqual(journal.docstatus, 1)
        self.assertEqual(flt(journal.total_debit), flt(journal.total_credit))
        self.assertEqual(flt(journal.total_debit), flt(movement.amount))
        accounts = {row.account: (flt(row.debit), flt(row.credit)) for row in journal.accounts}
        self.assertEqual(accounts[movement.cash_account][1], flt(movement.amount))     # chiqim: kassa kredit
        self.assertEqual(accounts[movement.counter_account][0], flt(movement.amount))

    def test_cash_in_always_needs_approval_and_out_only_above_the_limit(self):
        with self.assertRaises(ApprovalRequired):
            self._create(kind="In", amount=1, category="Kassaga qo'shish")
        self._create(kind="Out", amount=50000)                        # limitning o'zi — tasdiqsiz
        with self.assertRaises(ApprovalRequired):
            self._create(kind="Out", amount=50000.01, reason="limitdan yuqori")

    def test_kind_spelling_is_normalised_but_categories_stay_strict(self):
        self._create(kind="out", amount=1000)
        with self.assertRaises(frappe.ValidationError):
            self._create(kind="Out", amount=1000, category="Kassaga qo'shish", reason="noto'g'ri tur")
        with self.assertRaises(frappe.ValidationError):
            self._create(kind="Sideways", amount=1000, reason="noto'g'ri yo'nalish")

    def test_a_closed_shift_takes_no_more_movements_and_cannot_lose_them(self):
        result = self._create(amount=2000)
        shift = cashier_permissions.open_shift_name(self.scope)
        frappe.set_user("Administrator")
        frappe.db.set_value("POS Opening Entry", shift, "status", "Closed")
        self._flush_caches()

        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc("Ozturk Cash Movement", result["name"]).cancel()
        frappe.set_user(self.CASHIER)
        with self.assertRaises(frappe.ValidationError):
            cash_movements.create_cash_movement("Out", 1000, "Xarajat", "yopilgandan keyin")

    def test_movement_in_an_open_shift_can_be_cancelled_by_a_manager_and_leaves_the_expected_cash(self):
        from ozturkapp.ozturkapp.utils import pos_closing
        result = self._create(amount=7000)
        shift = cashier_permissions.open_shift_name(self.scope)
        before = [dict(row) for row in pos_closing.get_cash_movements(shift)]
        self.assertEqual(len(before), 1)

        frappe.set_user("Administrator")
        frappe.get_doc("Ozturk Cash Movement", result["name"]).cancel()

        self.assertEqual(pos_closing.get_cash_movements(shift), [])
        self.assertEqual(frappe.db.get_value("Journal Entry", result["journal_entry"], "docstatus"), 2)

    def test_zero_limit_means_every_payout_needs_approval(self):
        frappe.db.set_value("POS Profile", self.profile,
                            cashier_features.SETTINGS["cash_payout_approval_limit"]["fieldname"], 0)
        with self.assertRaises(ApprovalRequired):
            self._create(kind="Out", amount=1)

    def test_a_cashier_sees_an_empty_ledger_once_the_shift_is_closed(self):
        self._create(amount=2000)
        shift = cashier_permissions.open_shift_name(self.scope)
        frappe.set_user("Administrator")
        frappe.db.set_value("POS Opening Entry", shift, "status", "Closed")
        self._as_cashier()

        data = cash_movements.get_cash_movements()

        self.assertIsNone(data["pos_opening_entry"])
        self.assertEqual((data["count"], data["items"], data["total_in"], data["total_out"]), (0, [], 0.0, 0.0))

    def test_identical_double_click_is_absorbed(self):
        self._create(amount=3000, reason="bir xil sabab")
        with self.assertRaises(frappe.ValidationError):
            cash_movements.create_cash_movement("Out", 3000, "Xarajat", "bir xil sabab")


# ═══════════════════════════════════════════════════════════════════
#  Endpoint gigienasi (introspeksiya)
# ═══════════════════════════════════════════════════════════════════

API_DIR = os.path.join(os.path.dirname(__file__), "..", "api")

#: endpoint -> funksiya kaliti: server `assert_enabled(scope.pos_profile, "<kalit>")` chaqirishi shart.
FEATURE_GATES = {
    ("billing", "apply_discount"): "discount",
    ("billing", "remove_discount"): "discount",
    ("billing", "get_even_split"): "split_payment",
    ("billing", "get_refundable"): "refunds",
    ("billing", "refund_invoice"): "refunds",
    ("cash_movements", "get_cash_movements"): "cash_movements",
    ("cash_movements", "create_cash_movement"): "cash_movements",
    ("printing", "open_drawer"): "cash_drawer",
    ("printing", "print_shift_report"): "shift_reports",
    ("cashier", "get_shift_report"): "shift_reports",
}

#: Kassa yozuvi o'zgartiradigan endpointlar: HTTP POST'gina.
GUARDED_MODULES = ("billing", "cashier", "cash_movements", "approval", "printing")


def _whitelisted_functions(module: str) -> dict:
    path = os.path.join(API_DIR, f"{module}.py")
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    found = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            for deco in node.decorator_list:
                text = ast.unparse(deco)
                if "whitelist" in text:
                    found[node.name] = (node, text)
    return found


class TestEndpointHygiene(unittest.TestCase):
    def test_every_whitelisted_endpoint_checks_the_cashier_role_first(self):
        missing = []
        for module in GUARDED_MODULES:
            for name, (node, _deco) in _whitelisted_functions(module).items():
                calls = [ast.unparse(n) for n in ast.walk(node) if isinstance(n, ast.Call)]
                if not any("require_cashier" in call or "_diagnostic_branch" in call for call in calls):
                    missing.append(f"{module}.{name}")
        self.assertEqual(missing, [], "require_cashier() yo'q endpointlar")

    def test_every_endpoint_that_acts_on_a_branch_resolves_the_scope(self):
        missing = []
        for module in GUARDED_MODULES:
            for name, (node, _deco) in _whitelisted_functions(module).items():
                source = ast.unparse(node)
                if not any(token in source for token in ("resolve_scope", "resolve_branch", "_diagnostic_branch")):
                    missing.append(f"{module}.{name}")
        self.assertEqual(missing, [], "ko'lami (scope) aniqlanmagan endpointlar")

    def test_feature_gated_endpoints_call_assert_enabled_with_their_key(self):
        wrong = []
        for (module, name), key in FEATURE_GATES.items():
            node = _whitelisted_functions(module)[name][0]
            source = ast.unparse(node)
            if f"assert_enabled(scope.pos_profile, '{key}')" not in source:
                wrong.append(f"{module}.{name} -> {key}")
        self.assertEqual(wrong, [])

    def test_state_changing_endpoints_are_post_only_or_read_only(self):
        """Yozadigan endpointlar `methods=["POST"]` yoki ichida DB'ga yozmaydi."""
        writers = {
            ("printing", "open_drawer"), ("printing", "print_shift_report"),
            ("cash_movements", "create_cash_movement"),
        }
        for module, name in writers:
            _node, deco = _whitelisted_functions(module)[name]
            self.assertIn("POST", deco, f"{module}.{name}")

    def test_commits_and_permission_bypasses_are_confined_to_known_places(self):
        """`frappe.db.commit` faqat setup/scheduler'da; `ignore_permissions` faqat izohlangan joylarda."""
        base = os.path.join(os.path.dirname(__file__), "..")
        commits, bypasses = [], []
        files = [
            "api/billing.py", "api/cashier.py", "api/cash_movements.py", "api/printing.py", "api/approval.py",
            "utils/discounts.py", "utils/refunds.py", "utils/cashier_billing.py", "utils/pos_closing.py",
            "utils/shift_report.py", "utils/manager_approval.py", "utils/print_queue.py",
            "doctype/ozturk_cash_movement/ozturk_cash_movement.py",
            "doctype/ozturk_print_job/ozturk_print_job.py",
        ]
        for rel in files:
            with open(os.path.join(base, rel), encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
                for node in ast.walk(func):
                    text = ast.unparse(node) if isinstance(node, (ast.Call, ast.keyword)) else ""
                    if text.startswith("frappe.db.commit"):
                        commits.append(f"{rel}:{func.name}")
                    if "ignore_permissions" in text and isinstance(node, (ast.Call, ast.keyword)) and (
                        text.startswith("ignore_permissions") or ".insert(ignore_permissions" in text
                        or ".save(ignore_permissions" in text
                    ):
                        bypasses.append(f"{rel}:{func.name}")

        self.assertEqual(
            sorted(set(commits)),
            ["utils/manager_approval.py:setup", "utils/print_queue.py:recover_stale"],
        )
        # Har biri sabablangan: audit izi, tasdiq izi, navbat topshirig'i, kassa harakati, ERPNext ichki yozuvi.
        self.assertLessEqual(
            sorted(set(bypasses)),
            sorted([
                "api/billing.py:_ensure_service_charge",
                "api/cash_movements.py:create_cash_movement",
                "utils/manager_approval.py:_audit",
                "utils/print_queue.py:enqueue",
                "utils/print_queue.py:requeue",
            ]),
        )

    def test_no_raw_sql_interpolates_values(self):
        """Yangi modullardagi `frappe.db.sql` chaqiruvlari qiymatni f-string/format bilan qo'shmaydi."""
        bad = []
        base = os.path.join(os.path.dirname(__file__), "..")
        for rel in ("api/billing.py", "api/cash_movements.py", "utils/discounts.py", "utils/refunds.py",
                    "utils/shift_report.py", "utils/pos_closing.py", "utils/manager_approval.py"):
            with open(os.path.join(base, rel), encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("db.sql") and node.args:
                    first = node.args[0]
                    if isinstance(first, (ast.JoinedStr, ast.BinOp)) or (
                        isinstance(first, ast.Call) and ast.unparse(first.func).endswith("format")
                    ):
                        bad.append(f"{rel}:{node.lineno}")
        self.assertEqual(bad, [])


# ═══════════════════════════════════════════════════════════════════
#  Kutilgan naqd — formula va yopilish yo'llari
# ═══════════════════════════════════════════════════════════════════

class TestClosingArithmetic(MoneyCase):
    """Kutilgan naqd = ochilish + naqd sotuv (qaytimsiz) + kirim - chiqim - naqd qaytarishlar.

    Smena egasi (`kassa@...`) haqiqiy dev smenasi: unga tegishli boshqa cheklar bo'lishi mumkin,
    shuning uchun natija BOSHLANG'ICH qiymatga nisbatan FARQ bilan tekshiriladi."""

    def setUp(self):
        super().setUp()
        self._feature(cash_movements=True, refunds=True, split_payment=True)
        self._allow_in_returns(self.cash, True)
        frappe.db.set_value("POS Profile", self.profile,
                            cashier_features.SETTINGS["cash_payout_approval_limit"]["fieldname"], 1000000)
        if not frappe.db.get_value("Company", self.scope.company, "custom_cash_movement_account"):
            from ozturkapp.ozturkapp.setup import cashier_shift_setup
            frappe.db.set_value("Company", self.scope.company, "custom_cash_movement_account",
                                cashier_shift_setup.ensure_clearing_account(self.scope.company))
        self.shift = cashier_permissions.open_shift_name(self.scope)
        self.shift_user = frappe.db.get_value("POS Opening Entry", self.shift, "user")
        self.baseline = self._expected()

    def _expected(self) -> dict:
        from ozturkapp.ozturkapp.utils import pos_closing
        closing = pos_closing.make_closing_entry_from_opening(
            frappe.get_doc("POS Opening Entry", self.shift)
        )
        return {row.mode_of_payment: flt(row.expected_amount) for row in closing.payment_reconciliation}

    def _delta(self, mode) -> float:
        return round(self._expected().get(mode, 0) - self.baseline.get(mode, 0), 2)

    def _sale(self, rows, lines=((3, 10000), (3, 10000))):
        """Smena egasi nomiga yozilgan to'langan chek (ERPNext hisobotni `owner` bo'yicha yig'adi)."""
        doc = self._invoice(lines=lines)
        billing.submit_payment(doc.name, json.dumps([
            {"mode_of_payment": mode, "amount": amount} for mode, amount in rows(self._payable(doc.name))
        ]))
        frappe.db.set_value("POS Invoice", doc.name, "owner", self.shift_user, update_modified=False)
        return frappe.get_doc("POS Invoice", doc.name)

    def test_change_is_not_counted_as_cash_in_the_till(self):
        paid = self._sale(lambda p: [(self.cash, p + 5000)])
        self.assertEqual(self._delta(self.cash), self._payable(paid.name))

    def test_full_formula_with_change_movements_refund_and_a_card(self):
        paid = self._sale(lambda p: [(self.CARD, 20000), (self.cash, p - 20000 + 3000)])
        payable = self._payable(paid.name)
        self.assertEqual(self._delta(self.cash), payable - 20000)
        self.assertEqual(self._delta(self.CARD), 20000)

        self._as_cashier()
        cash_movements.create_cash_movement("Out", 3000, "Xarajat", "muz olindi")
        cash_movements.create_cash_movement(
            "In", 7000, "Kassaga qo'shish", "mayda pul", approval={"user": self.MANAGER, "pin": self.PIN}
        )
        frappe.set_user("Administrator")
        self._flush_caches()
        result = billing.refund_invoice(
            paid.name, json.dumps([{"name": paid.items[0].name, "qty": 1}]), "sabab"
        )
        refunded = {row["mode_of_payment"]: abs(row["amount"]) for row in result["payments"]}

        self.assertEqual(
            self._delta(self.cash),
            round(payable - 20000 - 3000 + 7000 - refunded.get(self.cash, 0), 2),
        )
        self.assertEqual(self._delta(self.CARD), round(20000 - refunded.get(self.CARD, 0), 2))

    def test_a_cancelled_movement_leaves_the_expected_cash(self):
        self._as_cashier()
        result = cash_movements.create_cash_movement("Out", 4000, "Xarajat", "bekor qilinadi")
        self.assertEqual(self._delta(self.cash), -4000)
        frappe.set_user("Administrator")
        frappe.get_doc("Ozturk Cash Movement", result["name"]).cancel()
        self.assertEqual(self._delta(self.cash), 0)

    def test_draft_movements_do_not_count(self):
        """Faqat submit qilingan (`docstatus = 1`) harakat kutilgan naqdga kiradi."""
        frappe.get_doc({
            "doctype": "Ozturk Cash Movement", "pos_opening_entry": self.shift, "branch": self.scope.branch,
            "kind": "Out", "category": "Xarajat", "amount": 9000, "mode_of_payment": self.cash,
            "reason": "qoralama", "user": "Administrator", "posting_datetime": now_datetime(),
        }).insert(ignore_permissions=True)
        self.assertEqual(self._delta(self.cash), 0)

    def test_refund_capacity_follows_the_closing_change_attribution(self):
        """Ikki naqd usul + qaytim: qaytarish sig'imi yopilishdagi usul bo'yicha summa bilan bir xil."""
        from ozturkapp.ozturkapp.utils import pos_closing, refunds
        second = "RM Naqd 2"
        if not frappe.db.exists("Mode of Payment", second):
            frappe.get_doc({
                "doctype": "Mode of Payment", "mode_of_payment": second, "type": "Cash", "enabled": 1,
                "accounts": [{"company": self.scope.company, "default_account": frappe.db.get_value(
                    "Company", self.scope.company, "default_cash_account")}],
            }).insert()
        frappe.get_doc({
            "doctype": "POS Payment Method", "parent": self.profile, "parenttype": "POS Profile",
            "parentfield": "payments", "mode_of_payment": second, "default": 0, "allow_in_returns": 1, "idx": 98,
        }).insert(ignore_permissions=True)
        self._flush_caches()

        paid = self._sale(lambda p: [(self.cash, 20000), (second, p - 20000 + 12800)])
        self.assertEqual(flt(paid.change_amount), 12800)

        closing = dict(pos_closing.net_payments(paid))
        capacity = refunds.net_paid_by_mode(paid)
        self.assertEqual({k: round(v, 2) for k, v in capacity.items()},
                         {k: round(v, 2) for k, v in closing.items()})
        self.assertAlmostEqual(sum(capacity.values()), self._payable(paid.name), places=2)


class TestClosingGuards(MoneyCase):
    """POS Closing Entry: kassir generic REST orqali yopilishni soxtalashtira olmaydi."""

    def setUp(self):
        super().setUp()
        self.shift = cashier_permissions.open_shift_name(self.scope)
        self.opening = frappe.get_doc("POS Opening Entry", self.shift)
        # Yopilishda kamida bitta chek bo'lishi uchun: smena egasi nomidagi to'langan chek.
        doc = self._invoice()
        billing.submit_payment(doc.name, json.dumps(
            [{"mode_of_payment": self.cash, "amount": self._payable(doc.name)}]))
        frappe.db.set_value("POS Invoice", doc.name, "owner", self.opening.user, update_modified=False)

    def _closing_json(self) -> dict:
        from ozturkapp.ozturkapp.utils import pos_closing
        closing = pos_closing.make_closing_entry_from_opening(self.opening)
        closing.posting_date = nowdate()
        for row in closing.payment_reconciliation:
            row.closing_amount = row.expected_amount
            row.difference = 0
        return json_of(closing)

    def test_an_honest_closing_entry_passes_the_guard(self):
        """Bizning yo'llar quradigan yopilish (`make_closing_entry_from_opening`) rad etilmaydi."""
        data = self._closing_json()
        self.assertTrue(data["pos_transactions"])
        self._as_cashier()

        saved = http("frappe.client.insert", doc=json.dumps(data))

        self.assertEqual(frappe.db.get_value("POS Closing Entry", saved["name"], "docstatus"), 0)

    def test_a_faked_expected_amount_is_rejected(self):
        """Kutilgan summani sanagan summaga tenglab, kamomadni yashirish."""
        data = self._closing_json()
        for row in data["payment_reconciliation"]:
            row["expected_amount"] = row["closing_amount"] = 1
            row["difference"] = 0
        self._as_cashier()
        with self.assertRaises(frappe.ValidationError) as ctx:
            http("frappe.client.insert", doc=json.dumps(data))
        self.assertIn("mos emas", str(ctx.exception))

    def test_dropping_an_invoice_from_the_closing_is_rejected(self):
        """Chekni ro'yxatdan tashlasa u konsolidatsiya qilinmaydi — buxgalteriyaga tushmaydi."""
        data = self._closing_json()
        data["pos_transactions"] = data["pos_transactions"][:-1]
        self._as_cashier()
        with self.assertRaises(frappe.ValidationError):
            http("frappe.client.insert", doc=json.dumps(data))

    def test_managers_are_not_bound_by_the_closing_guard(self):
        data = self._closing_json()
        for row in data["payment_reconciliation"]:
            row["expected_amount"] = row["closing_amount"] = 1
        frappe.set_user(self.MANAGER)
        self._flush_caches()

        saved = http("frappe.client.insert", doc=json.dumps(data))

        self.assertTrue(saved["name"])

    def test_a_cashier_cannot_cancel_a_closing_to_recount_after_seeing_the_difference(self):
        from ozturkapp.ozturkapp.utils import pos_closing
        hooks = frappe.get_hooks("doc_events")["POS Closing Entry"]
        self.assertIn("ozturkapp.ozturkapp.utils.pos_closing.guard_closing_cancel", hooks["before_cancel"])
        self.assertIn("ozturkapp.ozturkapp.utils.pos_closing.guard_closing_entry", hooks["validate"])

        self._as_cashier()
        with self.assertRaises(frappe.PermissionError):
            pos_closing.guard_closing_cancel(frappe._dict(doctype="POS Closing Entry"))
        frappe.set_user(self.MANAGER)
        self._flush_caches()
        pos_closing.guard_closing_cancel(frappe._dict(doctype="POS Closing Entry"))


class TestClosingHardening(MoneyCase):
    def test_closing_takes_the_shift_lock_before_it_computes_anything(self):
        """Ikki bir vaqtdagi `close_shift` ikkita yopilish/konsolidatsiya yaratmasligi uchun qulf."""
        import inspect
        source = inspect.getsource(cashier_api.close_shift)
        lock = source.index("_lock_shift_for_closing(shift[\"name\"])")
        self.assertLess(lock, source.index("counted = _parse_counted_cash("))
        self.assertLess(lock, source.index("result = createPosClosing("))

    def test_a_shift_that_already_has_a_closing_entry_is_refused_under_the_lock(self):
        with mock.patch.object(frappe.db, "sql", side_effect=[[], [("POS-CLO-1",)]]):
            with self.assertRaises(frappe.ValidationError) as ctx:
                cashier_api._lock_shift_for_closing("POS-OPE-X")
        self.assertIn("yopilgan", str(ctx.exception))
        with mock.patch.object(frappe.db, "sql", side_effect=[[], []]):
            cashier_api._lock_shift_for_closing("POS-OPE-X")


class TestSubClosingPath(MoneyCase):
    """Ko'p kassirli rejimdagi `Sub POS Closing` yo'li (`desktop_pos._create_sub_closing`)."""

    @unittest.expectedFailure
    def test_sub_closing_cash_takings_are_net_of_change(self):
        """ISBOTLANGAN NOMUVOFIQLIK (integratsiya so'rovi): sub-yopilish kutilgan naqdni `Sales
        Invoice Payment.amount` xom yig'indisidan oladi — qaytim ayirilmaydi (va kassa harakati
        qo'shilmaydi), asosiy yopilish esa qaytimni ayiradi. Har smenada qaytim yig'indisicha
        soxta kamomad chiqadi. Fayl (`api/desktop_pos.py`) bu sharhchining ro'yxatida emas;
        tuzatilganda bu test «kutilmagan muvaffaqiyat» beradi."""
        from ozturkapp.ozturkapp.api import desktop_pos
        from ozturkapp.ozturkapp.utils import pos_closing

        shift = cashier_permissions.open_shift_name(self.scope)
        opening = frappe.get_doc("POS Opening Entry", shift)
        opening_cash = sum(flt(row.opening_amount) for row in opening.balance_details
                           if row.mode_of_payment == self.cash)
        # URY `SubPOSClosing.validate` kassirning qoralama cheklari bo'lsa to'xtaydi — smena
        # egasining haqiqiy qoralamalari shu test tranzaksiyasida boshqa kassirga yoziladi.
        frappe.db.sql(
            "update `tabPOS Invoice` set cashier = 'rm-nobody' where docstatus = 0 and cashier = %s",
            opening.user,
        )
        doc = self._invoice()
        billing.submit_payment(doc.name, json.dumps(
            [{"mode_of_payment": self.cash, "amount": self._payable(doc.name) + 3000}]))
        frappe.db.set_value("POS Invoice", doc.name, "owner", opening.user, update_modified=False)

        main = {row.mode_of_payment: flt(row.expected_amount)
                for row in pos_closing.make_closing_entry_from_opening(opening).payment_reconciliation}
        frappe.set_user(opening.user)
        sub = desktop_pos._create_sub_closing(opening, {})
        frappe.set_user("Administrator")
        got = {row.mode_of_payment: flt(row.expected_amount) for row in sub.payment_reconciliation}

        # Sub-yopilish ochilish summasini qo'shmaydi; naqd tushum ikkalasida bir xil bo'lishi kerak.
        self.assertEqual(got[self.cash], main[self.cash] - opening_cash)
