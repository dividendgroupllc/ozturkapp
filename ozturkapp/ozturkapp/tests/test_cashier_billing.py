# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""To'lov, choychaqa, chegirma, teng bo'lish va qaytarish testlari.

Ishga tushirish (bir nechta agent bitta bazani ishlatadi — qulf bilan)::

    cd /home/sherzod/frappe-bench && flock /tmp/ozturk_tests.lock \
        bench --site ozturk.local run-tests \
        --module ozturkapp.ozturkapp.tests.test_cashier_billing

FIKSTURA
========
Cheklar ilova ishlatadigan yo'l bilan yaratiladi: haqiqiy `POS Invoice`
hujjati (mahsulotlar, xizmat haqi qatori), ERPNext `save/submit` orqali.
Saytdagi mavjud cheklarga TEGILMAYDI. Har test o'z SAVEPOINT'ida ishlaydi
va oxirida orqaga qaytariladi, `commit` yo'q.

Bu saytda yagona to'lov usuli — naqd «Нахт». Naqd BO'LMAGAN usul kerak
bo'lgan testlar uchun vaqtinchalik «Test Karta» (turi Bank) qo'shiladi.
Usul nomlari kodda qattiq yozilmaydi: naqd usul `cash_modes()` dan olinadi.
"""

import json
from decimal import Decimal
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, now_datetime

from ozturkapp.ozturkapp.api import billing
from ozturkapp.ozturkapp.api import cashier as cashier_api
from ozturkapp.ozturkapp.setup import cashier_features
from ozturkapp.ozturkapp.utils import (
    cashier_billing,
    cashier_permissions,
    manager_approval,
    print_queue,
    refunds,
)
from ozturkapp.ozturkapp.utils.manager_approval import ApprovalRequired

SAVEPOINT = "ozturk_billing_test"

#: Testlar yoqib/o'chiradigan, pulga tegadigan funksiyalar.
MONEY_FEATURES = ("split_payment", "discount", "refunds", "cash_drawer", "tips")


class BillingCase(FrappeTestCase):
    """Umumiy fikstura: foydalanuvchilar, smena, usullar va chek yaratish."""

    MANAGER = "bill-manager@example.com"
    CASHIER = "bill-cashier@example.com"
    NOBODY = "bill-nobody@example.com"
    PIN = "4321"
    CARD = "Test Karta"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._make_user(cls.MANAGER, ["URY Manager", "URY Cashier"], pin=cls.PIN)
        cls._make_user(cls.CASHIER, ["URY Cashier"])
        cls._make_user(cls.NOBODY, [])

    @staticmethod
    def _make_user(email, roles, pin=None):
        if frappe.db.exists("User", email):
            frappe.delete_doc("User", email, force=True, ignore_permissions=True)
        doc = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": email.split("@")[0],
                "send_welcome_email": 0,
                "enabled": 1,
                "roles": [{"role": role} for role in roles],
            }
        )
        if pin:
            doc.custom_pos_pin = pin
        doc.insert(ignore_permissions=True)

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint(SAVEPOINT)
        self._flush_caches()

        self.scope = cashier_permissions.resolve_scope()
        self.profile = self.scope.pos_profile
        self.cash = (cashier_billing.cash_modes(self.profile) or [None])[0]
        if not self.cash:
            self.skipTest("POS Profile'da naqd usul yo'q")
        if not cashier_permissions.open_shift_name(self.scope):
            self.skipTest("Ochiq kassa smenasi yo'q")

        self.items = frappe.get_all(
            "Item",
            # Xomashyo sotilmaydi. Ilgari `RM-` prefiksi bilan ajratilardi, lekin
            # kodlar `ITEM-####` bo'lgach prefiks yo'q — belgiga tayanamiz.
            filters={"disabled": 0, "is_sales_item": 1},
            pluck="name",
            limit=2,
        )
        if len(self.items) < 2:
            self.skipTest("Sinov uchun kamida 2 ta mahsulot kerak")

        self.service = cashier_billing.get_service_charge_config(self.scope.restaurant)
        for key in MONEY_FEATURES:
            self._feature(**{key: False})
        self._add_card_mode()
        manager_approval.reset_attempts(self.MANAGER)
        manager_approval.reset_requester(self.CASHIER)

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point=SAVEPOINT)
        self._flush_caches()

    # ── Fikstura yordamchilari ───────────────────────────────────────────

    @staticmethod
    def _flush_caches():
        frappe.local._ozturk_payment_methods = {}
        frappe.local._ozturk_scope_cache = {}

    def _feature(self, **flags):
        for key, enabled in flags.items():
            frappe.db.set_value(
                "POS Profile",
                self.profile,
                cashier_features.FEATURES[key]["fieldname"],
                1 if enabled else 0,
                update_modified=False,
            )

    def _max_discount(self, percent):
        frappe.db.set_value(
            "POS Profile",
            self.profile,
            cashier_features.SETTINGS["max_cashier_discount_percent"]["fieldname"],
            percent,
            update_modified=False,
        )

    def _add_card_mode(self):
        """Naqd bo'lmagan usul: aralash to'lov va ortiqcha to'lov qoidalari uchun."""
        if not frappe.db.exists("Mode of Payment", self.CARD):
            frappe.get_doc(
                {
                    "doctype": "Mode of Payment",
                    "mode_of_payment": self.CARD,
                    "type": "Bank",
                    "enabled": 1,
                    "accounts": [
                        {
                            "company": self.scope.company,
                            "default_account": frappe.db.get_value(
                                "Company", self.scope.company, "default_cash_account"
                            ),
                        }
                    ],
                }
            ).insert()
        frappe.get_doc(
            {
                "doctype": "POS Payment Method",
                "parent": self.profile,
                "parenttype": "POS Profile",
                "parentfield": "payments",
                "mode_of_payment": self.CARD,
                "default": 0,
                "allow_in_returns": 1,
                "idx": 99,
            }
        ).insert(ignore_permissions=True)
        self._flush_caches()

    def _allow_in_returns(self, mode, allowed=True):
        frappe.db.set_value(
            "POS Payment Method",
            {"parent": self.profile, "mode_of_payment": mode},
            "allow_in_returns",
            1 if allowed else 0,
        )
        self._flush_caches()

    def _invoice(
        self,
        lines=((3, 10000), (3, 10000)),
        table=None,
        printed=False,
        order_type="Dine In",
        via_template=False,
    ):
        """Xizmat haqi qatorli, to'lanmagan chek (`(miqdor, narx)` juftliklari).

        Xizmat haqi faqat `Dine In` da qoladi: `Take Away` va `Delivery` cheklaridan
        `remove_service_charge_for_takeaway` uni olib tashlaydi.

        `via_template=True` — qator qo'lda qo'shilmaydi, faqat shablon nomi qo'yiladi
        (URY `get_order_invoice` shunday qiladi; qatorni ERPNext o'zi qo'shadi).
        """
        doc = frappe.new_doc("POS Invoice")
        doc.update(
            {
                "customer": self.scope.default_customer,
                "pos_profile": self.profile,
                "company": self.scope.company,
                "branch": self.scope.branch,
                "restaurant": self.scope.restaurant,
                "order_type": order_type,
                "restaurant_table": table,
            }
        )
        for code, (qty, rate) in zip(self.items, lines):
            doc.append(
                "items",
                {"item_code": code, "qty": qty, "rate": rate, "warehouse": self.scope.warehouse},
            )
        doc.append("payments", {"mode_of_payment": self.cash, "amount": 0})
        if via_template:
            doc.taxes_and_charges = self.service["template"]
        elif self.service["enabled"]:
            doc.append(
                "taxes",
                {
                    "charge_type": "On Net Total",
                    "account_head": self.service["account"],
                    "description": self.service["description"],
                    "rate": self.service["rate"],
                },
            )
        doc.insert()
        if printed:
            frappe.db.set_value("POS Invoice", doc.name, "invoice_printed", 1)
        return frappe.get_doc("POS Invoice", doc.name)

    def _payable(self, invoice):
        doc = frappe.get_doc("POS Invoice", invoice)
        return flt(doc.rounded_total) or flt(doc.grand_total)

    def _pay(self, invoice, amount=None, mode=None, **kwargs):
        amount = self._payable(invoice) if amount is None else amount
        payments = [{"mode_of_payment": mode or self.cash, "amount": amount}]
        return billing.submit_payment(invoice, json.dumps(payments), **kwargs)

    def _paid(self, **invoice_kwargs):
        """To'langan (submit) chek va uning `submit_payment` javobi."""
        doc = self._invoice(**invoice_kwargs)
        result = self._pay(doc.name)
        return frappe.get_doc("POS Invoice", doc.name), result

    def _approval(self, pin=None):
        return {"user": self.MANAGER, "pin": pin or self.PIN}

    def _as_cashier(self):
        frappe.set_user(self.CASHIER)
        self._flush_caches()

    def _tax(self, doc, account_head):
        return next((row for row in doc.taxes if row.account_head == account_head), None)


# ═══════════════════════════════════════════════════════════════════
#  To'lov
# ═══════════════════════════════════════════════════════════════════

class TestPaymentHardening(BillingCase):
    def test_plain_single_mode_payment_works_as_before(self):
        """Regressiya: oddiy bitta usulli aniq to'lov avvalgidek ishlaydi."""
        doc = self._invoice()
        payable = self._payable(doc.name)

        result = self._pay(doc.name)

        self.assertEqual(result["docstatus"], 1)
        self.assertEqual(result["invoice"], doc.name)
        self.assertEqual(flt(result["paid_amount"]), payable)
        self.assertEqual(flt(result["change_amount"]), 0)
        self.assertEqual(flt(result["rounded_total"]), payable)
        self.assertEqual(result["tip"], 0)
        self.assertEqual(result["payments"], [{"mode_of_payment": self.cash, "amount": payable}])
        self.assertEqual(frappe.db.get_value("POS Invoice", doc.name, "docstatus"), 1)

    def test_cash_overpay_produces_change(self):
        doc = self._invoice()
        payable = self._payable(doc.name)

        result = self._pay(doc.name, amount=payable + 5000)

        self.assertEqual(flt(result["change_amount"]), 5000)
        self.assertEqual(flt(result["paid_amount"]), payable + 5000)

    def test_underpayment_is_rejected(self):
        doc = self._invoice()
        with self.assertRaises(frappe.ValidationError):
            self._pay(doc.name, amount=self._payable(doc.name) - 1)
        self.assertEqual(frappe.db.get_value("POS Invoice", doc.name, "docstatus"), 0)

    def test_unknown_mode_is_rejected(self):
        doc = self._invoice()
        with self.assertRaises(frappe.ValidationError):
            self._pay(doc.name, mode="Bitcoin")

    def test_multiple_rows_rejected_when_split_is_off(self):
        doc = self._invoice()
        payable = self._payable(doc.name)
        payments = [
            {"mode_of_payment": self.cash, "amount": payable - 1000},
            {"mode_of_payment": self.CARD, "amount": 1000},
        ]
        with self.assertRaises(frappe.ValidationError) as ctx:
            billing.submit_payment(doc.name, json.dumps(payments))
        self.assertIn("Aralash to'lov", str(ctx.exception))
        self.assertEqual(frappe.db.get_value("POS Invoice", doc.name, "docstatus"), 0)

    def test_split_payment_sums_to_payable_when_enabled(self):
        self._feature(split_payment=True)
        doc = self._invoice()
        payable = self._payable(doc.name)
        payments = [
            {"mode_of_payment": self.cash, "amount": 40000},
            {"mode_of_payment": self.CARD, "amount": payable - 40000},
        ]

        result = billing.submit_payment(doc.name, json.dumps(payments))

        self.assertEqual(flt(result["paid_amount"]), payable)
        self.assertEqual(flt(result["change_amount"]), 0)
        self.assertEqual(len(result["payments"]), 2)
        paid = frappe.get_doc("POS Invoice", doc.name)
        self.assertEqual(
            sorted((row.mode_of_payment, flt(row.amount)) for row in paid.payments),
            sorted((row["mode_of_payment"], row["amount"]) for row in payments),
        )

    def test_non_cash_overpay_is_rejected(self):
        self._feature(split_payment=True)
        for label, extra in (("yagona karta, ortiqcha", None), ("karta to'liq + ortiqcha naqd", 1000)):
            with self.subTest(case=label):
                doc = self._invoice()
                payable = self._payable(doc.name)
                if extra is None:
                    payments = [{"mode_of_payment": self.CARD, "amount": payable + 1000}]
                else:
                    payments = [
                        {"mode_of_payment": self.CARD, "amount": payable},
                        {"mode_of_payment": self.cash, "amount": extra},
                    ]
                with self.assertRaises(frappe.ValidationError):
                    billing.submit_payment(doc.name, json.dumps(payments))
                self.assertEqual(frappe.db.get_value("POS Invoice", doc.name, "docstatus"), 0)

    def test_card_plus_cash_overpay_gives_change_from_cash_only(self):
        self._feature(split_payment=True)
        doc = self._invoice()
        payable = self._payable(doc.name)
        payments = [
            {"mode_of_payment": self.CARD, "amount": 30000},
            {"mode_of_payment": self.cash, "amount": payable - 30000 + 2000},
        ]

        result = billing.submit_payment(doc.name, json.dumps(payments))

        self.assertEqual(flt(result["change_amount"]), 2000)

    def test_cash_definition_matches_shift_code(self):
        """Naqd ta'rifi smena kodi bilan bir xil: `Mode of Payment.type == Cash`."""
        self.assertEqual(
            cashier_billing.cash_modes(self.profile), cashier_api._cash_modes(self.profile)
        )
        self.assertIn(self.cash, cashier_billing.cash_modes(self.profile))
        self.assertNotIn(self.CARD, cashier_billing.cash_modes(self.profile))
        for method in cashier_billing.get_payment_methods(self.profile):
            self.assertIn("allow_in_returns", method)

    def test_second_payment_of_the_same_invoice_is_rejected(self):
        """Parallel to'lov qo'riqchisi: chek qulflanadi, ikkinchi urinish to'langanini ko'radi."""
        doc = self._invoice()
        self._pay(doc.name)

        with self.assertRaises(frappe.ValidationError) as ctx:
            self._pay(doc.name)
        self.assertIn("allaqachon to'langan", str(ctx.exception))

    def test_row_lock_is_taken_before_the_status_check(self):
        import inspect

        for fn in (billing.submit_payment, billing.refund_invoice):
            source = inspect.getsource(fn)
            with self.subTest(fn=fn.__name__):
                self.assertIn("for update", source)
                self.assertLess(
                    source.index("for update"), source.index("assert_invoice_in_scope")
                )

    def test_cancelled_invoice_is_rejected(self):
        doc = self._invoice()
        frappe.db.set_value("POS Invoice", doc.name, "custom_cancelled", 1)
        with self.assertRaises(frappe.ValidationError):
            self._pay(doc.name)

    def test_table_invoice_needs_the_bill_first(self):
        table = self._free_table()
        doc = self._invoice(table=table, printed=False)
        with self.assertRaises(frappe.ValidationError) as ctx:
            self._pay(doc.name)
        self.assertIn("hisobni oching", str(ctx.exception))

    def test_table_invoice_can_be_paid_once_billed(self):
        table = self._free_table()
        doc = self._invoice(table=table, printed=True)
        self.assertEqual(self._pay(doc.name)["docstatus"], 1)

    def test_payment_needs_an_open_shift(self):
        doc = self._invoice()
        with mock.patch.object(cashier_permissions, "open_shift_name", return_value=""):
            with self.assertRaises(frappe.ValidationError):
                self._pay(doc.name)

    def _free_table(self):
        busy = set(
            frappe.get_all(
                "POS Invoice", filters={"docstatus": 0}, pluck="restaurant_table"
            )
        )
        for name in frappe.get_all("URY Table", filters={"branch": self.scope.branch}, pluck="name"):
            if name not in busy:
                return name
        self.skipTest("Ochiq cheksiz stol yo'q")


# ═══════════════════════════════════════════════════════════════════
#  Naqd to'lovdan keyin g'aladon
# ═══════════════════════════════════════════════════════════════════

class TestCashDrawer(BillingCase):
    JOB = "ozturkapp.ozturkapp.utils.cashier_billing.kick_drawer_after_payment"

    def _kicks(self, enqueue):
        """`enqueue` chaqiruvlaridan faqat g'aladon topshiriqlari."""
        return [
            (call.args, call.kwargs)
            for call in enqueue.call_args_list
            if call.args and call.args[0] == self.JOB
        ]

    def test_cash_payment_queues_the_kick_after_commit(self):
        self._feature(cash_drawer=True)
        doc = self._invoice()

        with mock.patch("frappe.enqueue") as enqueue:
            self._pay(doc.name)

        kicks = self._kicks(enqueue)
        self.assertEqual(len(kicks), 1)
        args, kwargs = kicks[0]
        self.assertTrue(kwargs["enqueue_after_commit"])
        self.assertEqual(kwargs["invoice"], doc.name)
        self.assertEqual(kwargs["user"], frappe.session.user)

    def test_no_kick_when_the_feature_is_off(self):
        doc = self._invoice()
        with mock.patch("frappe.enqueue") as enqueue:
            self._pay(doc.name)
        self.assertEqual(self._kicks(enqueue), [])

    def test_no_kick_for_a_card_only_payment(self):
        self._feature(cash_drawer=True)
        doc = self._invoice()
        with mock.patch("frappe.enqueue") as enqueue:
            self._pay(doc.name, mode=self.CARD)
        self.assertEqual(self._kicks(enqueue), [])

    def test_mixed_payment_with_cash_row_kicks_the_drawer(self):
        self._feature(cash_drawer=True, split_payment=True)
        doc = self._invoice()
        payable = self._payable(doc.name)
        payments = [
            {"mode_of_payment": self.CARD, "amount": 10000},
            {"mode_of_payment": self.cash, "amount": payable - 10000},
        ]
        with mock.patch("frappe.enqueue") as enqueue:
            billing.submit_payment(doc.name, json.dumps(payments))
        self.assertEqual(len(self._kicks(enqueue)), 1)

    def test_queue_failure_never_fails_the_payment(self):
        self._feature(cash_drawer=True)
        doc = self._invoice()
        with mock.patch("frappe.enqueue", side_effect=RuntimeError("redis yo'q")):
            result = self._pay(doc.name)
        self.assertEqual(result["docstatus"], 1)

    def test_job_calls_kick_drawer_with_scope_reason_and_invoice(self):
        with mock.patch.object(print_queue, "kick_drawer", create=True) as kick:
            cashier_billing.kick_drawer_after_payment("INV-X", self.CASHIER)

        kick.assert_called_once()
        scope = kick.call_args.args[0]
        self.assertEqual(scope.pos_profile, self.profile)
        self.assertEqual(kick.call_args.kwargs, {"reason": "payment", "invoice": "INV-X"})

    def test_job_swallows_any_failure_including_a_missing_function(self):
        for error in (AttributeError("hali yozilmagan"), RuntimeError("printer")):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(print_queue, "kick_drawer", create=True, side_effect=error):
                    cashier_billing.kick_drawer_after_payment("INV-X", self.CASHIER)


# ═══════════════════════════════════════════════════════════════════
#  Choychaqa
# ═══════════════════════════════════════════════════════════════════

class TestTips(BillingCase):
    def _tip_account(self):
        return cashier_billing.tips_account(self.scope.company)

    def test_setup_created_the_liability_account(self):
        account = frappe.db.get_value(
            "Account", self._tip_account(), ["root_type", "is_group"], as_dict=True
        )
        self.assertEqual(account.root_type, "Liability")
        self.assertEqual(account.is_group, 0)

    def test_tip_is_rejected_when_the_feature_is_off(self):
        doc = self._invoice()
        with self.assertRaises(frappe.PermissionError):
            self._pay(doc.name, tip=5000)
        self.assertEqual(frappe.db.get_value("POS Invoice", doc.name, "docstatus"), 0)

    def test_tip_becomes_a_tax_row_and_joins_the_grand_total(self):
        self._feature(tips=True)
        doc = self._invoice()
        before = self._payable(doc.name)

        result = self._pay(doc.name, amount=before + 5000, tip=5000)

        paid = frappe.get_doc("POS Invoice", doc.name)
        tip_row = self._tax(paid, self._tip_account())
        self.assertIsNotNone(tip_row)
        self.assertEqual(tip_row.charge_type, "Actual")
        self.assertEqual(flt(tip_row.tax_amount), 5000)
        self.assertEqual(flt(paid.grand_total), before + 5000)
        self.assertEqual(flt(result["tip"]), 5000)
        self.assertEqual(flt(paid.paid_amount), flt(paid.rounded_total))
        # Choychaqa qatori eng oxirida va xizmat haqi undan bexabar.
        self.assertEqual(paid.taxes[-1].account_head, self._tip_account())

    def test_tip_is_not_subject_to_service_charge(self):
        self._feature(tips=True)
        doc = self._invoice()
        service_before = flt(self._tax(doc, self.service["account"]).tax_amount)

        self._pay(doc.name, amount=self._payable(doc.name) + 5000, tip=5000)

        paid = frappe.get_doc("POS Invoice", doc.name)
        self.assertEqual(flt(self._tax(paid, self.service["account"]).tax_amount), service_before)
        self.assertEqual(flt(paid.net_total), 60000)

    def test_payment_must_cover_the_tip(self):
        self._feature(tips=True)
        doc = self._invoice()
        with self.assertRaises(frappe.ValidationError):
            self._pay(doc.name, amount=self._payable(doc.name), tip=5000)

    def test_tip_replaces_an_existing_row_instead_of_stacking(self):
        self._feature(tips=True)
        doc = self._invoice()
        cashier_billing.set_tip(doc, 7000)
        doc.save()
        base = self._payable(doc.name) - 7000

        self._pay(doc.name, amount=base + 2000, tip=2000)

        paid = frappe.get_doc("POS Invoice", doc.name)
        self.assertEqual(flt(paid.grand_total), base + 2000)
        self.assertEqual(len([r for r in paid.taxes if r.account_head == self._tip_account()]), 1)

    def test_zero_tip_removes_a_stale_row(self):
        self._feature(tips=True)
        doc = self._invoice()
        base = self._payable(doc.name)
        cashier_billing.set_tip(doc, 7000)
        doc.save()

        self._pay(doc.name, amount=base, tip=0)

        paid = frappe.get_doc("POS Invoice", doc.name)
        self.assertIsNone(self._tax(paid, self._tip_account()))
        self.assertEqual(flt(paid.grand_total), base)

    def test_set_tip_is_idempotent(self):
        doc = self._invoice()
        for _ in range(3):
            cashier_billing.set_tip(doc, 5000)
        self.assertEqual(len([r for r in doc.taxes if cashier_billing.is_tip_row(r, self._tip_account())]), 1)
        self.assertEqual(cashier_billing.get_tip(doc), 5000)
        cashier_billing.set_tip(doc, 3000)
        self.assertEqual(cashier_billing.get_tip(doc), 3000)
        cashier_billing.set_tip(doc, 0)
        self.assertEqual(cashier_billing.get_tip(doc), 0)

    def test_negative_tip_is_rejected(self):
        self._feature(tips=True)
        doc = self._invoice()
        with self.assertRaises(frappe.ValidationError):
            self._pay(doc.name, tip=-1)

    def test_tip_above_the_net_total_is_rejected_and_the_limit_is_accepted(self):
        self._feature(tips=True)
        doc = self._invoice()
        with self.assertRaises(frappe.ValidationError):
            self._pay(doc.name, amount=10 ** 7, tip=60001)

        result = self._pay(doc.name, amount=self._payable(doc.name) + 60000, tip=60000)
        self.assertEqual(flt(result["tip"]), 60000)

    def test_tip_and_discount_do_not_interfere(self):
        """Chegirma choychaqaga tegmaydi: choychaqa qat'iy summa, chegirmadan tashqarida."""
        self._feature(tips=True, discount=True)
        doc = self._invoice()
        billing.apply_discount(doc.name, percent=10, reason="Doimiy mijoz")
        discounted = self._payable(doc.name)

        self._pay(doc.name, amount=discounted + 5000, tip=5000)

        paid = frappe.get_doc("POS Invoice", doc.name)
        self.assertEqual(flt(paid.grand_total), discounted + 5000)
        self.assertEqual(flt(self._tax(paid, self._tip_account()).tax_amount), 5000)

    def test_bill_reports_the_tip(self):
        doc = self._invoice()
        cashier_billing.set_tip(doc, 4000)
        doc.save()

        bill = cashier_billing.build_bill(frappe.get_doc("POS Invoice", doc.name), self.scope)

        self.assertEqual(bill["tip"], 4000)
        self.assertEqual(sum(1 for t in bill["taxes"] if t["is_tip"]), 1)
        self.assertEqual(bill["payable"], self._payable(doc.name))


# ═══════════════════════════════════════════════════════════════════
#  Chegirma
# ═══════════════════════════════════════════════════════════════════

class TestDiscount(BillingCase):
    def setUp(self):
        super().setUp()
        self._feature(discount=True)
        self._max_discount(10)

    def test_feature_off_is_rejected(self):
        self._feature(discount=False)
        doc = self._invoice()
        for call in (
            lambda: billing.apply_discount(doc.name, percent=5, reason="x"),
            lambda: billing.remove_discount(doc.name),
        ):
            with self.assertRaises(frappe.PermissionError):
                call()

    def test_percent_discount_shrinks_the_service_charge_too(self):
        doc = self._invoice()
        rate = self.service["rate"]

        bill = billing.apply_discount(doc.name, percent=10, reason="Doimiy mijoz")

        fresh = frappe.get_doc("POS Invoice", doc.name)
        self.assertEqual(flt(fresh.discount_amount), 6000)
        self.assertEqual(fresh.apply_discount_on, "Net Total")
        self.assertEqual(flt(fresh.net_total), 54000)
        self.assertEqual(flt(self._tax(fresh, self.service["account"]).tax_amount), flt(54000 * rate / 100, 2))
        self.assertEqual(flt(fresh.grand_total), flt(54000 * (1 + rate / 100), 2))
        self.assertEqual(bill["discount"], 6000)
        self.assertEqual(bill["discount_percent"], 10)
        self.assertEqual(bill["discount_reason"], "Doimiy mijoz")
        self.assertEqual(bill["discount_approved_by"], "")
        self.assertEqual(bill["payable"], flt(fresh.rounded_total))

    def test_amount_discount_is_exact_and_stored_as_percent(self):
        doc = self._invoice(lines=((3, 11111), (3, 11111)))
        self._max_discount(50)

        billing.apply_discount(doc.name, amount=7777, reason="Kechikish uchun")

        fresh = frappe.get_doc("POS Invoice", doc.name)
        self.assertEqual(flt(fresh.discount_amount), 7777)
        self.assertAlmostEqual(flt(fresh.additional_discount_percentage), 7777 / 66666 * 100, places=6)

    def test_reason_is_mandatory(self):
        doc = self._invoice()
        for reason in (None, "", "   "):
            with self.assertRaises(frappe.ValidationError):
                billing.apply_discount(doc.name, percent=5, reason=reason)

    def test_exactly_one_of_percent_and_amount(self):
        doc = self._invoice()
        with self.assertRaises(frappe.ValidationError):
            billing.apply_discount(doc.name, reason="x")
        with self.assertRaises(frappe.ValidationError):
            billing.apply_discount(doc.name, percent=5, amount=1000, reason="x")

    def test_out_of_range_values_are_rejected(self):
        doc = self._invoice()
        for kwargs in (
            {"percent": 0}, {"percent": -5}, {"percent": 100}, {"percent": 150},
            {"amount": 0}, {"amount": -1000}, {"amount": 60000}, {"amount": 70000},
        ):
            with self.subTest(**kwargs), self.assertRaises(frappe.ValidationError):
                billing.apply_discount(doc.name, reason="x", **kwargs)
        self.assertEqual(flt(frappe.get_doc("POS Invoice", doc.name).discount_amount), 0)

    def test_limit_itself_needs_no_approval_but_above_it_does(self):
        doc = self._invoice()
        self._as_cashier()

        billing.apply_discount(doc.name, percent=10, reason="Chegara")  # aynan chegara
        with self.assertRaises(ApprovalRequired):
            billing.apply_discount(doc.name, percent=10.5, reason="Chegaradan oshdi")
        self.assertEqual(flt(frappe.get_doc("POS Invoice", doc.name).additional_discount_percentage), 10)

    def test_amount_is_judged_by_its_effective_percent(self):
        doc = self._invoice()
        self._as_cashier()

        billing.apply_discount(doc.name, amount=6000, reason="10% ga teng")
        with self.assertRaises(ApprovalRequired):
            billing.apply_discount(doc.name, amount=6100, reason="10% dan oshadi")

    def test_zero_limit_means_every_discount_needs_approval(self):
        self._max_discount(0)
        doc = self._invoice()
        self._as_cashier()
        with self.assertRaises(ApprovalRequired):
            billing.apply_discount(doc.name, percent=1, reason="x")

        billing.apply_discount(doc.name, percent=1, reason="x", approval=self._approval())
        self.assertEqual(frappe.db.get_value("POS Invoice", doc.name, "custom_discount_approved_by"), self.MANAGER)

    def test_missing_wrong_and_correct_pin(self):
        doc = self._invoice()
        self._as_cashier()

        with self.assertRaises(ApprovalRequired):
            billing.apply_discount(doc.name, percent=30, reason="x")
        with self.assertRaises(ApprovalRequired):
            billing.apply_discount(doc.name, percent=30, reason="x", approval=self._approval("0000"))
        self.assertEqual(flt(frappe.get_doc("POS Invoice", doc.name).discount_amount), 0)

        bill = billing.apply_discount(
            doc.name, percent=30, reason="Menejer ruxsati", approval=json.dumps(self._approval())
        )

        self.assertEqual(bill["discount_percent"], 30)
        self.assertEqual(bill["discount_approved_by"], self.MANAGER)
        self.assertTrue(
            frappe.db.exists(
                "Comment",
                {
                    "reference_doctype": "POS Invoice",
                    "reference_name": doc.name,
                    "content": ["like", "%Chegirma 30%"],
                },
            )
        )

    def test_manager_at_the_till_needs_no_pin(self):
        doc = self._invoice()
        frappe.set_user(self.MANAGER)
        self._flush_caches()

        bill = billing.apply_discount(doc.name, percent=40, reason="Menejer")

        self.assertEqual(bill["discount_approved_by"], self.MANAGER)

    def test_new_discount_replaces_the_old_one(self):
        doc = self._invoice()
        billing.apply_discount(doc.name, percent=5, reason="a")
        billing.apply_discount(doc.name, percent=8, reason="b")

        fresh = frappe.get_doc("POS Invoice", doc.name)
        self.assertEqual(flt(fresh.additional_discount_percentage), 8)
        self.assertEqual(flt(fresh.discount_amount), 4800)
        self.assertEqual(fresh.custom_discount_reason, "b")

    def test_within_limit_discount_clears_a_previous_approver(self):
        doc = self._invoice()
        self._as_cashier()
        billing.apply_discount(doc.name, percent=30, reason="a", approval=self._approval())
        billing.apply_discount(doc.name, percent=5, reason="b")

        self.assertFalse(frappe.db.get_value("POS Invoice", doc.name, "custom_discount_approved_by"))

    def test_remove_discount_restores_the_totals_and_is_idempotent(self):
        doc = self._invoice()
        original = self._payable(doc.name)
        billing.apply_discount(doc.name, percent=10, reason="x")

        bill = billing.remove_discount(doc.name)
        again = billing.remove_discount(doc.name)

        self.assertEqual(bill["payable"], original)
        self.assertEqual(again["payable"], original)
        self.assertEqual(bill["discount"], 0)
        self.assertEqual(bill["discount_reason"], "")
        self.assertEqual(bill["discount_approved_by"], "")

    def test_only_unpaid_uncancelled_invoices(self):
        paid = self._invoice()
        self._pay(paid.name)
        cancelled = self._invoice()
        frappe.db.set_value("POS Invoice", cancelled.name, "custom_cancelled", 1)

        for name in (paid.name, cancelled.name):
            with self.subTest(invoice=name), self.assertRaises(frappe.ValidationError):
                billing.apply_discount(name, percent=5, reason="x")

    def test_discount_needs_an_open_shift(self):
        doc = self._invoice()
        with mock.patch.object(cashier_permissions, "open_shift_name", return_value=""):
            with self.assertRaises(frappe.ValidationError):
                billing.apply_discount(doc.name, percent=5, reason="x")

    def test_discount_after_the_bill_was_issued_flags_a_reprint(self):
        doc = self._invoice(printed=True)

        bill = billing.apply_discount(doc.name, percent=5, reason="x")
        self.assertTrue(bill["reprint_needed"])
        self.assertTrue(bill["billed"])

        cashier_billing.clear_reprint_needed(doc.name)
        self.assertFalse(cashier_billing.build_bill(frappe.get_doc("POS Invoice", doc.name), self.scope)["reprint_needed"])

        # Olib tashlash ham qog'ozdagi chekni eskirtiradi.
        self.assertTrue(billing.remove_discount(doc.name)["reprint_needed"])

    def test_discount_before_the_bill_is_issued_needs_no_reprint(self):
        doc = self._invoice(printed=False)
        self.assertFalse(billing.apply_discount(doc.name, percent=5, reason="x")["reprint_needed"])

    def test_open_bill_clears_the_reprint_flag(self):
        doc = self._invoice(printed=False)
        frappe.db.set_value("POS Invoice", doc.name, "custom_reprint_needed", 1)

        bill = billing.open_bill(doc.name)

        self.assertTrue(bill["billed"])
        self.assertFalse(bill["reprint_needed"])
        self.assertEqual(frappe.db.get_value("POS Invoice", doc.name, "custom_reprint_needed"), 0)

    def test_discount_drops_a_stale_tip(self):
        """Choychaqa mutlaq summa: chegirma o'zgarsa eskirgan choychaqa qolmasligi kerak."""
        doc = self._invoice()
        cashier_billing.set_tip(doc, 5000)
        doc.save()

        billing.apply_discount(doc.name, percent=10, reason="x")

        self.assertEqual(cashier_billing.get_tip(frappe.get_doc("POS Invoice", doc.name)), 0)

    def test_realtime_event_is_emitted(self):
        doc = self._invoice()
        with mock.patch.object(billing, "emit_order_change") as emit:
            billing.apply_discount(doc.name, percent=5, reason="x")
            billing.remove_discount(doc.name)
        self.assertEqual([c.args[2] for c in emit.call_args_list], ["DISCOUNT_APPLIED", "DISCOUNT_REMOVED"])


# ═══════════════════════════════════════════════════════════════════
#  Teng bo'lish
# ═══════════════════════════════════════════════════════════════════

class TestEvenSplit(BillingCase):
    @staticmethod
    def _fake(payable, currency="UZS"):
        return frappe._dict(
            name="X", rounded_total=payable, grand_total=payable, currency=currency,
            precision=lambda field: 2,
        )

    def _sum(self, amounts):
        return sum(Decimal(str(a)) for a in amounts)

    def test_remainder_goes_to_the_last_part_and_the_sum_is_exact(self):
        for payable, parts, expected in (
            (100000, 3, [33333.33, 33333.33, 33333.34]),
            (100.01, 3, [33.33, 33.33, 33.35]),
            (100, 1, [100]),
            (10, 4, [2.5, 2.5, 2.5, 2.5]),
            (0.05, 3, [0.01, 0.01, 0.03]),
        ):
            with self.subTest(payable=payable, parts=parts):
                result = cashier_billing.get_even_split(self._fake(payable), parts)
                self.assertEqual(result["amounts"], expected)
                self.assertEqual(self._sum(result["amounts"]), Decimal(str(payable)))
                self.assertEqual(result["parts"], parts)

    def test_many_uneven_totals_always_sum_exactly(self):
        for payable in (67200, 72200.5, 1234567.89, 99999.99):
            for parts in range(1, 13):
                with self.subTest(payable=payable, parts=parts):
                    result = cashier_billing.get_even_split(self._fake(payable), parts)
                    self.assertEqual(self._sum(result["amounts"]), Decimal(str(payable)))
                    self.assertEqual(len(result["amounts"]), parts)

    def test_invalid_parts_are_rejected(self):
        for parts in (0, -1, cashier_billing.MAX_SPLIT_PARTS + 1, "abc"):
            with self.subTest(parts=parts), self.assertRaises(frappe.ValidationError):
                cashier_billing.get_even_split(self._fake(1000), parts)

    def test_empty_invoice_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            cashier_billing.get_even_split(self._fake(0), 2)

    def test_api_requires_the_feature_and_does_not_mutate(self):
        doc = self._invoice()
        with self.assertRaises(frappe.PermissionError):
            billing.get_even_split(doc.name, 3)

        self._feature(split_payment=True)
        modified = frappe.db.get_value("POS Invoice", doc.name, "modified")
        result = billing.get_even_split(doc.name, 3)

        self.assertEqual(self._sum(result["amounts"]), Decimal(str(self._payable(doc.name))))
        self.assertEqual(frappe.db.get_value("POS Invoice", doc.name, "modified"), modified)

    def test_api_rejects_a_paid_invoice(self):
        self._feature(split_payment=True)
        doc = self._invoice()
        self._pay(doc.name)
        with self.assertRaises(frappe.ValidationError):
            billing.get_even_split(doc.name, 2)


# ═══════════════════════════════════════════════════════════════════
#  Qaytarish
# ═══════════════════════════════════════════════════════════════════

class TestRefunds(BillingCase):
    def setUp(self):
        super().setUp()
        self._feature(refunds=True)
        self._allow_in_returns(self.cash)

    def _rows(self, doc):
        return {row.item_code: row.name for row in doc.items}

    def _refund(self, doc, qtys, reason="Taom sovuq edi", **kwargs):
        """`qtys` — `{mahsulot: miqdor}`."""
        rows = self._rows(doc)
        items = [{"name": rows[code], "qty": qty} for code, qty in qtys.items()]
        return billing.refund_invoice(doc.name, json.dumps(items), reason, **kwargs)

    def _all(self, doc):
        return {row.item_code: row.qty for row in doc.items}

    def test_feature_off_is_rejected(self):
        self._feature(refunds=False)
        doc, _result = self._paid()
        with self.assertRaises(frappe.PermissionError):
            billing.get_refundable(doc.name)
        with self.assertRaises(frappe.PermissionError):
            self._refund(doc, self._all(doc))

    def test_full_refund_mirrors_the_sale(self):
        doc, _result = self._paid()
        payable = self._payable(doc.name)

        result = self._refund(doc, self._all(doc))

        ret = frappe.get_doc("POS Invoice", result["invoice"])
        self.assertEqual(ret.docstatus, 1)
        self.assertEqual(ret.is_return, 1)
        self.assertEqual(ret.return_against, doc.name)
        self.assertEqual(flt(ret.rounded_total), -payable)
        self.assertEqual(flt(ret.paid_amount), -payable)
        self.assertEqual([(p.mode_of_payment, flt(p.amount)) for p in ret.payments], [(self.cash, -payable)])
        self.assertEqual(ret.custom_return_reason, "Taom sovuq edi")
        self.assertTrue(result["final"])
        self.assertEqual(result["refunded"], payable)
        self.assertEqual(result["approved_by"], "Administrator")

    def test_return_is_safe_for_the_kitchen_and_the_floor(self):
        """Taom omborga qaytmaydi; stol/birlashtirish bog'lari ko'chmaydi."""
        table = self._free_table()
        doc = self._invoice(table=table, printed=True)
        self._pay(doc.name)
        frappe.db.set_value("POS Invoice", doc.name, "custom_merged_tables", "Table-X")

        ret = frappe.get_doc(
            "POS Invoice", self._refund(frappe.get_doc("POS Invoice", doc.name), {c: 3 for c in self.items})["invoice"]
        )

        self.assertEqual(ret.update_stock, 0)
        self.assertFalse(ret.restaurant_table)
        self.assertFalse(ret.custom_merged_tables)
        self.assertFalse(ret.custom_merged_pos_invoice)
        self.assertFalse(ret.invoice_printed)

    def test_return_attaches_to_the_open_shift_owner(self):
        """`owner` = smena egasi, aks holda smena hisobotiga (owner bo'yicha) tushmaydi."""
        doc, _result = self._paid()
        shift = cashier_permissions.open_shift_name(self.scope)
        shift_user = frappe.db.get_value("POS Opening Entry", shift, "user")

        result = self._refund(doc, self._all(doc))

        self.assertEqual(frappe.db.get_value("POS Invoice", result["invoice"], "owner"), shift_user)
        self.assertEqual(frappe.db.get_value("POS Invoice", result["invoice"], "pos_profile"), self.profile)

    def test_return_is_seen_by_the_shift_closing(self):
        """Smena yopilishi qaytarishni manfiy sotuv sifatida ko'radi."""
        from ozturkapp.ozturkapp.utils import pos_closing

        doc, _result = self._paid()
        payable = self._payable(doc.name)
        result = self._refund(doc, self._all(doc))
        shift = cashier_permissions.open_shift_name(self.scope)
        opening = frappe.get_doc("POS Opening Entry", shift)

        rows = pos_closing.get_pos_invoices(
            frappe.utils.add_to_date(now_datetime(), minutes=-5),
            frappe.utils.add_to_date(now_datetime(), minutes=5),
            self.profile,
            opening.user,
        )
        by_name = {row.name: row for row in rows}

        self.assertIn(result["invoice"], by_name)
        self.assertEqual(flt(by_name[result["invoice"]].grand_total), -payable)
        self.assertEqual(sum(flt(p.amount) for p in by_name[result["invoice"]].payments), -payable)

    def test_partial_refund_and_refundable_bookkeeping(self):
        doc, _result = self._paid()
        first = self.items[0]

        result = self._refund(doc, {first: 1})

        rate = self.service["rate"]
        self.assertEqual(result["refunded"], flt(10000 * (1 + rate / 100), 2))
        self.assertFalse(result["final"])

        info = billing.get_refundable(doc.name)
        by_code = {row["item_code"]: row for row in info["items"]}
        self.assertEqual(by_code[first]["sold_qty"], 3)
        self.assertEqual(by_code[first]["returned_qty"], 1)
        self.assertEqual(by_code[first]["refundable_qty"], 2)
        self.assertEqual(by_code[first]["rate"], 10000)
        self.assertEqual(by_code[self.items[1]]["refundable_qty"], 3)
        self.assertTrue(info["refundable"])
        self.assertEqual(info["paid"][0]["mode_of_payment"], self.cash)
        self.assertEqual(info["paid"][0]["paid"], self._payable(doc.name))
        self.assertEqual(info["paid"][0]["refunded"], result["refunded"])
        self.assertTrue(info["paid"][0]["allow_in_returns"])

    def test_over_quantity_and_double_refund_are_rejected(self):
        doc, _result = self._paid()

        with self.assertRaises(frappe.ValidationError):
            self._refund(doc, {self.items[0]: 4})

        self._refund(doc, self._all(doc))
        with self.assertRaises(frappe.ValidationError):
            self._refund(doc, {self.items[0]: 1})
        self.assertFalse(billing.get_refundable(doc.name)["refundable"])

    def test_previous_returns_are_counted_under_the_lock(self):
        doc, _result = self._paid()
        self._refund(doc, {self.items[0]: 2})

        with self.assertRaises(frappe.ValidationError):
            self._refund(doc, {self.items[0]: 2})  # 2 + 2 > 3
        self._refund(doc, {self.items[0]: 1})  # aynan qolgani

    def test_invalid_item_selection_is_rejected(self):
        doc, _result = self._paid()
        for items in ("[]", "not json", json.dumps([{"name": "NOPE", "qty": 1}]),
                      json.dumps([{"name": self._rows(doc)[self.items[0]], "qty": 0}])):
            with self.subTest(items=items), self.assertRaises(frappe.ValidationError):
                billing.refund_invoice(doc.name, items, "x")

    def test_reason_is_mandatory(self):
        doc, _result = self._paid()
        for reason in (None, "", "  "):
            with self.assertRaises(frappe.ValidationError):
                self._refund(doc, {self.items[0]: 1}, reason=reason)

    def test_manager_approval_is_always_required_for_a_cashier(self):
        doc, _result = self._paid()
        self._as_cashier()

        with self.assertRaises(ApprovalRequired):
            self._refund(doc, {self.items[0]: 1})
        with self.assertRaises(ApprovalRequired):
            self._refund(doc, {self.items[0]: 1}, approval=self._approval("0000"))
        self.assertFalse(frappe.db.exists("POS Invoice", {"return_against": doc.name}))

        result = self._refund(doc, {self.items[0]: 1}, approval=self._approval())
        self.assertEqual(result["approved_by"], self.MANAGER)

    def test_approval_is_audited_on_the_original_invoice(self):
        doc, _result = self._paid()
        self._as_cashier()
        self._refund(doc, {self.items[0]: 1}, reason="Sifatsiz", approval=self._approval())

        self.assertTrue(
            frappe.db.exists(
                "Comment",
                {
                    "reference_doctype": "POS Invoice",
                    "reference_name": doc.name,
                    "content": ["like", "%Chekni qaytarish%"],
                },
            )
        )

    def test_refund_needs_an_open_shift(self):
        doc, _result = self._paid()
        with mock.patch.object(cashier_permissions, "open_shift_name", return_value=""):
            with self.assertRaises(frappe.ValidationError):
                self._refund(doc, {self.items[0]: 1})

    def test_unpaid_cancelled_and_return_invoices_cannot_be_refunded(self):
        unpaid = self._invoice()
        with self.assertRaises(frappe.ValidationError):
            billing.refund_invoice(unpaid.name, json.dumps([{"name": unpaid.items[0].name, "qty": 1}]), "x")

        paid, _result = self._paid()
        frappe.db.set_value("POS Invoice", paid.name, "custom_cancelled", 1)
        with self.assertRaises(frappe.ValidationError):
            self._refund(paid, {self.items[0]: 1})
        frappe.db.set_value("POS Invoice", paid.name, "custom_cancelled", 0)

        ret_name = self._refund(paid, {self.items[0]: 1})["invoice"]
        ret = frappe.get_doc("POS Invoice", ret_name)
        with self.assertRaises(frappe.ValidationError):
            billing.refund_invoice(ret_name, json.dumps([{"name": ret.items[0].name, "qty": 1}]), "x")

    def test_mode_without_allow_in_returns_is_rejected(self):
        self._allow_in_returns(self.cash, allowed=False)
        doc, _result = self._paid()

        with self.assertRaises(frappe.ValidationError) as ctx:
            self._refund(doc, self._all(doc))

        self.assertIn(self.cash, str(ctx.exception))
        self.assertFalse(frappe.db.exists("POS Invoice", {"return_against": doc.name}))

    def test_refund_of_an_overpaid_sale_returns_net_of_change(self):
        doc = self._invoice()
        payable = self._payable(doc.name)
        self._pay(doc.name, amount=payable + 2800)  # qaytim 2800
        doc = frappe.get_doc("POS Invoice", doc.name)
        self.assertEqual(flt(doc.change_amount), 2800)

        result = self._refund(doc, self._all(doc))

        self.assertEqual(result["payments"], [{"mode_of_payment": self.cash, "amount": -payable}])
        self.assertEqual(refunds.net_paid_by_mode(doc), {self.cash: payable})

    def test_refund_is_split_over_the_original_modes_to_the_cent(self):
        self._feature(split_payment=True)
        doc = self._invoice()
        payable = self._payable(doc.name)
        payments = [
            {"mode_of_payment": self.cash, "amount": 40000},
            {"mode_of_payment": self.CARD, "amount": payable - 40000},
        ]
        billing.submit_payment(doc.name, json.dumps(payments))
        doc = frappe.get_doc("POS Invoice", doc.name)

        full = self._refund(doc, {c: 3 for c in self.items})
        self.assertEqual(
            {p["mode_of_payment"]: p["amount"] for p in full["payments"]},
            {self.cash: -40000, self.CARD: -(payable - 40000)},
        )

    def test_partial_refund_is_proportional_and_exact_over_modes(self):
        self._feature(split_payment=True)
        doc = self._invoice()
        payable = self._payable(doc.name)
        billing.submit_payment(
            doc.name,
            json.dumps(
                [
                    {"mode_of_payment": self.cash, "amount": 40000},
                    {"mode_of_payment": self.CARD, "amount": payable - 40000},
                ]
            ),
        )
        doc = frappe.get_doc("POS Invoice", doc.name)

        result = self._refund(doc, {self.items[0]: 1})

        total = Decimal(str(result["refunded"]))
        self.assertEqual(sum(Decimal(str(-p["amount"])) for p in result["payments"]), total)
        cash_share = next(-p["amount"] for p in result["payments"] if p["mode_of_payment"] == self.cash)
        self.assertAlmostEqual(cash_share, float(total) * 40000 / payable, delta=0.01)

    def test_tip_is_refunded_only_by_the_final_return(self):
        """Choychaqa taomga emas, xodimga tegishli: faqat butunlay qaytarishda qaytadi."""
        self._feature(tips=True)
        doc = self._invoice()
        payable = self._payable(doc.name)
        self._pay(doc.name, amount=payable + 5000, tip=5000)
        doc = frappe.get_doc("POS Invoice", doc.name)
        rate = self.service["rate"]

        partial = self._refund(doc, {self.items[0]: 1})
        partial_doc = frappe.get_doc("POS Invoice", partial["invoice"])
        self.assertIsNone(self._tax(partial_doc, cashier_billing.tips_account(self.scope.company)))
        self.assertEqual(partial["refunded"], flt(10000 * (1 + rate / 100), 2))

        final = self._refund(doc, {self.items[0]: 2, self.items[1]: 3})
        final_doc = frappe.get_doc("POS Invoice", final["invoice"])
        tip_row = self._tax(final_doc, cashier_billing.tips_account(self.scope.company))
        self.assertEqual(flt(tip_row.tax_amount), -5000)
        self.assertTrue(final["final"])
        # Ikkala qaytarish yig'indisi to'lovga ANIQ teng.
        self.assertAlmostEqual(partial["refunded"] + final["refunded"], payable + 5000, delta=0.01)

    def test_discounted_sale_is_refunded_proportionally(self):
        self._feature(discount=True)
        doc = self._invoice()
        billing.apply_discount(doc.name, percent=10, reason="Doimiy mijoz")
        self._pay(doc.name)
        doc = frappe.get_doc("POS Invoice", doc.name)
        payable = self._payable(doc.name)

        result = self._refund(doc, {self.items[0]: 1})

        ret = frappe.get_doc("POS Invoice", result["invoice"])
        self.assertEqual(flt(ret.additional_discount_percentage), 10)
        self.assertAlmostEqual(result["refunded"], payable / 6, delta=0.02)

    def test_amount_only_discount_is_refunded_proportionally(self):
        """URY Desktop POS summa bilan qo'ygan chegirma: to'liq summa emas, ulush qaytadi."""
        doc = self._invoice()
        frappe.db.set_value(
            "POS Invoice", doc.name,
            {"apply_discount_on": "Net Total", "discount_amount": 6000, "additional_discount_percentage": 0},
        )
        doc = frappe.get_doc("POS Invoice", doc.name)
        doc.save()
        self._pay(doc.name)
        doc = frappe.get_doc("POS Invoice", doc.name)
        self.assertEqual(flt(doc.discount_amount), 6000)

        result = self._refund(doc, {self.items[0]: 1})

        self.assertAlmostEqual(result["refunded"], self._payable(doc.name) / 6, delta=0.02)

    def test_realtime_events(self):
        doc, _result = self._paid()
        with mock.patch.object(refunds, "emit_order_change") as emit:
            result = self._refund(doc, {self.items[0]: 1})
        self.assertEqual(
            [(c.args[1], c.args[2]) for c in emit.call_args_list],
            [(result["invoice"], "REFUND_COMPLETED"), (doc.name, "REFUNDED")],
        )

    def test_bill_of_a_return_reports_its_origin(self):
        doc, _result = self._paid()
        result = self._refund(doc, {self.items[0]: 1})

        bill = cashier_billing.build_bill(frappe.get_doc("POS Invoice", result["invoice"]), self.scope)

        self.assertTrue(bill["is_return"])
        self.assertEqual(bill["return_against"], doc.name)
        self.assertFalse(cashier_billing.build_bill(doc, self.scope)["is_return"])

    def test_allocation_helper_is_exact(self):
        step = Decimal("0.01")
        for total, capacity in (
            (11200, [("a", 40000), ("b", 27200)]),
            (0.03, [("a", 1), ("b", 1), ("c", 1)]),
            (100, [("a", 0), ("b", 100)]),
        ):
            with self.subTest(total=total):
                shares = refunds.allocate_refund(capacity, total, step)
                self.assertEqual(sum(Decimal(str(amount)) for _mode, amount in shares), Decimal(str(total)))
        with self.assertRaises(frappe.ValidationError):
            refunds.allocate_refund([("a", 100)], 200, step)

    def _free_table(self):
        busy = set(frappe.get_all("POS Invoice", filters={"docstatus": 0}, pluck="restaurant_table"))
        for name in frappe.get_all("URY Table", filters={"branch": self.scope.branch}, pluck="name"):
            if name not in busy:
                return name
        self.skipTest("Ochiq cheksiz stol yo'q")


# ═══════════════════════════════════════════════════════════════════
#  build_bill kengaytmasi
# ═══════════════════════════════════════════════════════════════════

class TestBuildBillExtensions(BillingCase):
    KEYS = (
        "tip", "discount_percent", "discount_reason", "discount_approved_by", "payable",
        "bill_requested", "bill_requested_at", "delivery", "order_type", "is_return",
        "return_against", "reprint_needed",
    )

    def test_contract_keys_exist_with_plain_defaults(self):
        doc = self._invoice()

        bill = cashier_billing.build_bill(doc, self.scope)

        for key in self.KEYS:
            self.assertIn(key, bill)
        self.assertEqual(bill["tip"], 0)
        self.assertEqual(bill["discount_percent"], 0)
        self.assertEqual(bill["discount_reason"], "")
        self.assertIsNone(bill["delivery"])
        self.assertFalse(bill["is_return"])
        self.assertIsNone(bill["return_against"])
        self.assertFalse(bill["reprint_needed"])
        self.assertEqual(bill["order_type"], "Dine In")
        self.assertEqual(bill["payable"], self._payable(doc.name))

    def test_existing_keys_are_unchanged(self):
        bill = cashier_billing.build_bill(self._invoice(), self.scope)
        for key in ("invoice", "items", "subtotal", "total", "discount", "taxes", "service_charge",
                    "grand_total", "rounded_total", "paid_amount", "payments", "kitchen", "cancellation"):
            self.assertIn(key, bill)

    def test_bill_requested_only_until_the_bill_is_issued(self):
        doc = self._invoice()
        frappe.db.set_value(
            "POS Invoice", doc.name,
            {"custom_bill_requested": 1, "custom_bill_requested_at": now_datetime()},
        )
        bill = cashier_billing.build_bill(frappe.get_doc("POS Invoice", doc.name), self.scope)
        self.assertTrue(bill["bill_requested"])
        self.assertTrue(bill["bill_requested_at"])

        frappe.db.set_value("POS Invoice", doc.name, "invoice_printed", 1)
        bill = cashier_billing.build_bill(frappe.get_doc("POS Invoice", doc.name), self.scope)
        self.assertFalse(bill["bill_requested"])

    def test_delivery_is_read_from_the_orders_custom_fields(self):
        doc = self._invoice()
        self.assertIsNone(cashier_billing.build_bill(doc, self.scope)["delivery"])

        frappe.db.set_value(
            "POS Invoice", doc.name,
            {"custom_delivery_phone": "+998901234567", "custom_delivery_address": "Maksim Gorkiy 5"},
        )
        bill = cashier_billing.build_bill(frappe.get_doc("POS Invoice", doc.name), self.scope)
        self.assertEqual(bill["delivery"], {"phone": "+998901234567", "address": "Maksim Gorkiy 5"})

    def test_plain_dict_documents_still_work(self):
        """Mavjud `test_cashier` hujjat o'rniga oddiy lug'at beradi."""
        row = frappe._dict(name="R1", idx=1, item_code="X", item_name="X", qty=2, uom="Nos", rate=1000, amount=2000)
        doc = frappe._dict(
            doctype="POS Invoice", name="TEST", docstatus=0, creation=None, modified=None,
            customer="X", currency="UZS", net_total=2000, total=2000, grand_total=2000,
            rounded_total=2000, total_taxes_and_charges=0, items=[row], taxes=[],
            precision=lambda field: 2,
        )
        bill = cashier_billing.build_bill(doc, include_kitchen=False)
        self.assertEqual(bill["payable"], 2000)
        self.assertEqual(bill["tip"], 0)
        self.assertIsNone(bill["delivery"])
        self.assertFalse(bill["reprint_needed"])


# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
#  Xizmat haqi faqat zalda (Dine In)
# ═══════════════════════════════════════════════════════════════════

class TestServiceChargeScope(BillingCase):
    """Olib ketish va yetkazib berishda ofitsant yo'q — xizmat haqi olinmaydi."""

    def setUp(self):
        super().setUp()
        if not self.service["enabled"]:
            self.skipTest("Xizmat haqi sozlanmagan")

    def _service_row(self, doc):
        return self._tax(doc, self.service["account"])

    def test_dine_in_keeps_the_service_charge(self):
        for via_template in (False, True):
            doc = self._invoice(order_type="Dine In", via_template=via_template)

            row = self._service_row(doc)
            self.assertIsNotNone(row, f"via_template={via_template}")
            self.assertEqual(flt(row.tax_amount), flt(doc.net_total) * flt(self.service["rate"]) / 100)
            self.assertGreater(flt(doc.grand_total), flt(doc.net_total))

    def test_take_away_and_delivery_have_no_service_charge(self):
        for order_type in ("Take Away", "Delivery"):
            for via_template in (False, True):
                doc = self._invoice(order_type=order_type, via_template=via_template)

                label = f"{order_type}, via_template={via_template}"
                self.assertIsNone(self._service_row(doc), label)
                self.assertEqual(flt(doc.total_taxes_and_charges), 0, label)
                self.assertEqual(flt(doc.grand_total), flt(doc.net_total), label)

    def test_template_is_cleared_when_it_only_held_the_service_charge(self):
        # Aks holda bo'sh jadval keyingi saqlashda shablon qatorini qaytarib qo'shardi.
        doc = self._invoice(order_type="Take Away", via_template=True)

        self.assertFalse(doc.taxes_and_charges)

        doc.save()
        self.assertIsNone(self._service_row(frappe.get_doc("POS Invoice", doc.name)))

    def test_resaving_an_existing_dine_in_invoice_keeps_the_service_charge(self):
        doc = self._invoice(order_type="Dine In", via_template=True)
        expected = flt(doc.grand_total)

        doc.save()

        saved = frappe.get_doc("POS Invoice", doc.name)
        self.assertIsNotNone(self._service_row(saved))
        self.assertEqual(flt(saved.grand_total), expected)

    def test_an_open_invoice_that_already_had_the_row_loses_it_on_next_save(self):
        # Tuzatishdan OLDIN yaratilgan ochiq chek.
        doc = self._invoice(order_type="Dine In")
        self.assertIsNotNone(self._service_row(doc))
        frappe.db.set_value("POS Invoice", doc.name, "order_type", "Take Away")

        reopened = frappe.get_doc("POS Invoice", doc.name)
        reopened.save()

        saved = frappe.get_doc("POS Invoice", doc.name)
        self.assertIsNone(self._service_row(saved))
        self.assertEqual(flt(saved.grand_total), flt(saved.net_total))

    def test_other_tax_rows_survive(self):
        doc = self._invoice(order_type="Take Away")
        doc.append(
            "taxes",
            {
                "charge_type": "Actual",
                "account_head": cashier_billing.tips_account(self.scope.company),
                "description": "Boshqa qator",
                "tax_amount": 1000,
            },
        )
        doc.append(
            "taxes",
            {
                "charge_type": "On Net Total",
                "account_head": self.service["account"],
                "description": self.service["description"],
                "rate": self.service["rate"],
            },
        )

        doc.save()

        saved = frappe.get_doc("POS Invoice", doc.name)
        self.assertIsNone(self._service_row(saved))
        self.assertEqual([row.description for row in saved.taxes], ["Boshqa qator"])
        self.assertEqual(flt(saved.grand_total), flt(saved.net_total) + 1000)

    def test_a_take_away_invoice_is_paid_in_full_and_submitted(self):
        doc = self._invoice(order_type="Take Away", via_template=True)
        payable = self._payable(doc.name)
        self.assertEqual(payable, flt(doc.net_total))

        self._pay(doc.name)

        paid = frappe.get_doc("POS Invoice", doc.name)
        self.assertEqual(paid.docstatus, 1)
        self.assertEqual(flt(paid.paid_amount), payable)
        self.assertIsNone(self._service_row(paid))

    def test_nothing_is_removed_when_the_service_charge_is_not_configured(self):
        doc = self._invoice(order_type="Take Away", via_template=False)
        self.assertIsNone(self._service_row(doc))  # tayyorgarlik: odatda olib tashlanadi

        with mock.patch.object(
            cashier_billing, "get_service_charge_config", return_value={"enabled": False}
        ):
            doc.append(
                "taxes",
                {
                    "charge_type": "On Net Total",
                    "account_head": self.service["account"],
                    "description": self.service["description"],
                    "rate": self.service["rate"],
                },
            )
            doc.save()

        self.assertIsNotNone(self._service_row(frappe.get_doc("POS Invoice", doc.name)))


#  Ruxsat: har bir endpoint server tomonida himoyalangan
# ═══════════════════════════════════════════════════════════════════

class TestPermissions(BillingCase):
    def _calls(self, invoice):
        return {
            "submit_payment": lambda: billing.submit_payment(invoice, "[]"),
            "apply_discount": lambda: billing.apply_discount(invoice, percent=5, reason="x"),
            "remove_discount": lambda: billing.remove_discount(invoice),
            "get_even_split": lambda: billing.get_even_split(invoice, 2),
            "get_refundable": lambda: billing.get_refundable(invoice),
            "refund_invoice": lambda: billing.refund_invoice(invoice, "[]", "x"),
        }

    def test_users_without_a_cashier_role_are_rejected(self):
        doc = self._invoice()
        for user in ("Guest", self.NOBODY):
            frappe.set_user(user)
            self._flush_caches()
            for name, call in self._calls(doc.name).items():
                with self.subTest(user=user, endpoint=name), self.assertRaises(frappe.PermissionError):
                    call()

    def test_every_gated_endpoint_rejects_a_disabled_feature(self):
        doc = self._invoice()
        for name in ("apply_discount", "remove_discount", "get_even_split", "get_refundable", "refund_invoice"):
            with self.subTest(endpoint=name), self.assertRaises(frappe.PermissionError):
                self._calls(doc.name)[name]()

    def test_mutating_endpoints_require_the_billing_role_and_a_shift(self):
        """`assert_can_bill` va `assert_shift_open` har bir pul o'zgartiruvchi metodda."""
        import inspect

        for fn in (billing.submit_payment, billing.apply_discount, billing.remove_discount, billing.refund_invoice):
            source = inspect.getsource(fn)
            for guard in ("require_cashier()", "resolve_scope()", "assert_can_bill", "assert_shift_open"):
                with self.subTest(fn=fn.__name__, guard=guard):
                    self.assertIn(guard, source)
