# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Smena, g'aladon, X/Z hisobot va kassa harakati testlari.

Ishga tushirish::

    bench --site ozturk.local run-tests \
        --module ozturkapp.ozturkapp.tests.test_cashier_shift

FIKSTURALAR
===========
Testlar dev saytdagi haqiqiy smenaga TEGMAYDI: har bir test o'z `POS Opening
Entry`'sini (sinov kassiri nomiga) yaratadi va `desktop_pos._open_opening_entry`
ni shunga yo'naltiradi. Cheklar xom SQL bilan yoziladi (POS Invoice yaratish
oqimi menyu/ombor talab qiladi) — `TestBulkClosingMatchesErpnext` ham shunday.
Har test savepoint ichida ishlaydi va oxirida qaytariladi.
"""

import base64
import json
import re
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import frappe
from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import POSClosingEntry
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime, nowdate

from ozturkapp.ozturkapp.api import cash_movements as cash_api
from ozturkapp.ozturkapp.api import cashier as cashier_api
from ozturkapp.ozturkapp.api import desktop_pos, printing
from ozturkapp.ozturkapp.setup import cashier_features, cashier_shift_setup
from ozturkapp.ozturkapp.utils import (
    cashier_billing,
    cashier_permissions,
    escpos,
    manager_approval,
    pos_closing,
    print_queue,
    shift_report,
)
from ozturkapp.ozturkapp.utils.manager_approval import ApprovalRequired

CASHIER = "shift-cashier@example.com"
OTHER_CASHIER = "shift-cashier2@example.com"
MANAGER = "shift-manager@example.com"
PIN = "4321"

SAVEPOINT = "shift_test"


def _plain(raw: bytes) -> str:
    """ESC/POS buyruqlarini olib tashlab, chek matnini qaytaradi."""
    raw = re.sub(rb"\x1b@|\x1b[taE].|\x1d!.|\x1dV\x42\x00", b"", raw, flags=re.S)
    return raw.decode("cp866", errors="replace")


def _numbers(value) -> set:
    """Javob ichidagi barcha sonlar (kalitlar emas) — sizib chiqishni tekshirish uchun."""
    found = set()
    if isinstance(value, dict):
        for item in value.values():
            found |= _numbers(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found |= _numbers(item)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        found.add(float(value))
    return found


class ShiftTestCase(FrappeTestCase):
    """Umumiy fikstura: foydalanuvchilar, smena, cheklar, printer."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.profile = frappe.db.get_value("POS Profile", {}, "name")
        cls.company = frappe.db.get_value("POS Profile", cls.profile, "company")
        cls.branch = frappe.db.get_value("POS Profile", cls.profile, "branch")
        cls.cash_mode = cashier_api._cash_modes(cls.profile)[0]
        cls.cash_account = frappe.db.get_value(
            "Mode of Payment Account", {"parent": cls.cash_mode, "company": cls.company}, "default_account"
        )
        cls._make_user(CASHIER, ["URY Cashier"])
        cls._make_user(OTHER_CASHIER, ["URY Cashier"])
        cls._make_user(MANAGER, ["URY Manager"], pin=PIN)

        # Qarshi hisob sozlanmagan bo'lsa (setup ishlamagan) shu tranzaksiya ichida qo'yamiz.
        if not frappe.db.get_value("Company", cls.company, "custom_cash_movement_account"):
            account = cashier_shift_setup.ensure_clearing_account(cls.company)
            frappe.db.set_value("Company", cls.company, "custom_cash_movement_account", account)

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
        frappe.local._ozturk_scope_cache = {}
        frappe.local._ozturk_payment_methods = {}
        manager_approval.reset_attempts(MANAGER)
        for user in (CASHIER, OTHER_CASHIER):
            manager_approval.reset_requester(user)

        self._isolate_payment_methods()
        self.scope = cashier_permissions.resolve_scope("Administrator")
        self.invoices = []
        self.set_features(cash_drawer=1, cash_movements=1, shift_reports=1)
        self.set_payout_limit(100000)

    def _isolate_payment_methods(self):
        """Profilda FAQAT bitta (sinov) naqd usul qoladi.

        Testlar "yagona naqd usul" deb yozilgan, kassa esa vaqt o'tib boshqa usullar
        (click, Payme, ...) qo'shadi — ular sanoq/yopish arifmetikasini buzmasin.
        O'chirish savepoint ichida, tearDown'da qaytadi.
        """
        frappe.db.delete(
            "POS Payment Method",
            {"parent": self.profile, "mode_of_payment": ["!=", self.cash_mode]},
        )
        frappe.local._ozturk_payment_methods = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point=SAVEPOINT)
        frappe.local._ozturk_scope_cache = {}
        manager_approval.reset_attempts(MANAGER)
        for user in (CASHIER, OTHER_CASHIER):
            manager_approval.reset_requester(user)

    # ── Sozlamalar ───────────────────────────────────────────────

    def set_features(self, **flags):
        for key, value in flags.items():
            frappe.db.set_value(
                "POS Profile", self.profile, cashier_features.FEATURES[key]["fieldname"], value
            )

    def set_payout_limit(self, limit):
        frappe.db.set_value(
            "POS Profile", self.profile,
            cashier_features.SETTINGS["cash_payout_approval_limit"]["fieldname"], limit,
        )

    @contextmanager
    def as_user(self, user):
        frappe.set_user(user)
        frappe.local._ozturk_scope_cache = {}
        try:
            yield
        finally:
            frappe.set_user("Administrator")
            frappe.local._ozturk_scope_cache = {}

    # ── Smena ────────────────────────────────────────────────────

    def open_shift(self, user=CASHIER, opening_cash=100000):
        """Sinov kassiri nomiga OCHIQ smena; joriy smena sifatida shu ko'rsatiladi."""
        with patch.object(cashier_permissions, "pos_profile_users", return_value=[]):
            doc = frappe.get_doc(
                {
                    "doctype": "POS Opening Entry",
                    "period_start_date": add_to_date(now_datetime(), minutes=-60),
                    "posting_date": nowdate(),
                    "user": user,
                    "pos_profile": self.profile,
                    "company": self.company,
                    "branch": self.branch,
                    "balance_details": [
                        {"mode_of_payment": self.cash_mode, "opening_amount": opening_cash}
                    ],
                }
            )
            doc.insert(ignore_permissions=True)
            doc.submit()

        self.shift = doc.name
        patcher = patch.object(desktop_pos, "_open_opening_entry", return_value=doc.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        return doc

    # ── Cheklar (xom SQL) ────────────────────────────────────────

    def add_invoice(self, total, grand_total, taxes=(), payments=(), discount=0, change=0,
                    is_return=0, user=CASHIER):
        name = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabPOS Invoice`
                (name, creation, modified, owner, modified_by, docstatus,
                 posting_date, posting_time, pos_profile, customer, branch,
                 total, discount_amount, grand_total, rounded_total, net_total, total_qty,
                 change_amount, is_return, consolidated_invoice)
            values (%s, now(), now(), %s, %s, 1,
                 curdate(), curtime(), %s, 'TEST-MIJOZ', %s,
                 %s, %s, %s, %s, %s, 1, %s, %s, '')
            """,
            (name, user, user, self.profile, self.branch, total, discount, grand_total,
             grand_total, total - discount, change, is_return),
        )
        self.invoices.append(name)

        for idx, (description, account, amount) in enumerate(taxes, start=1):
            frappe.db.sql(
                """
                insert into `tabSales Taxes and Charges`
                    (name, creation, modified, owner, modified_by, docstatus,
                     parent, parenttype, parentfield, idx,
                     charge_type, account_head, description, rate, tax_amount)
                values (%s, now(), now(), %s, %s, 1, %s, 'POS Invoice', 'taxes', %s,
                     'Actual', %s, %s, 0, %s)
                """,
                (frappe.generate_hash(length=10), user, user, name, idx, account, description, amount),
            )

        for idx, (mode, kind, amount) in enumerate(payments, start=1):
            frappe.db.sql(
                """
                insert into `tabSales Invoice Payment`
                    (name, creation, modified, owner, modified_by, docstatus,
                     parent, parenttype, parentfield, idx, mode_of_payment, type, amount)
                values (%s, now(), now(), %s, %s, 1, %s, 'POS Invoice', 'payments',
                     %s, %s, %s, %s)
                """,
                (frappe.generate_hash(length=10), user, user, name, idx, mode, kind, amount),
            )
        return name

    def add_known_sales(self):
        """Yig'indisi ma'lum uchta chek: sotuv (qaytim bilan), karta va qaytarish.

        Naqd: 110 000 berildi - 2 000 qaytim = 108 000; qaytarishda -20 000 => sof 88 000.
        """
        service = cashier_billing.get_service_charge_config(self.scope.restaurant)["account"]
        self.sale_cash = self.add_invoice(
            total=100000, discount=10000, grand_total=108000, change=2000,
            taxes=[("Xizmat haqi", service, 12000),
                   (cashier_billing.TIPS_DESCRIPTION, self.tips_account(), 5000),
                   ("Boshqa soliq", "Other - TEST", 1000)],
            payments=[(self.cash_mode, "Cash", 110000)],
        )
        self.sale_card = self.add_invoice(
            total=50000, grand_total=56000,
            taxes=[("Xizmat haqi", service, 6000)],
            payments=[("Credit Card", "Bank", 56000)],
        )
        self.refund = self.add_invoice(
            total=-20000, grand_total=-20000, is_return=1,
            payments=[(self.cash_mode, "Cash", -20000)],
        )

    def tips_account(self):
        """Choychaqa hisobi; billing setup ishlamagan saytda shu tranzaksiya ichida yaratiladi."""
        account = cashier_billing.tips_account(self.company)
        if account:
            return account
        parent = frappe.db.get_value(
            "Account", {"company": self.company, "account_name": "Current Liabilities", "is_group": 1}, "name"
        )
        return frappe.get_doc(
            {"doctype": "Account", "account_name": cashier_billing.TIPS_ACCOUNT_NAME,
             "company": self.company, "parent_account": parent, "is_group": 0}
        ).insert(ignore_permissions=True).name

    def add_known_movements(self):
        """Chiqim 30 000 (limitdan past — tasdiqsiz) va kirim 7 000 (menejer tasdig'i bilan)."""
        with self.as_user(CASHIER):
            cash_api.create_cash_movement("Out", 30000, "Xarajat", "Muz sotib olindi")
            cash_api.create_cash_movement(
                "In", 7000, "Kassaga qo'shish", "Mayda pul keltirildi",
                approval={"user": MANAGER, "pin": PIN},
            )

    # Ma'lum fikstura bo'yicha kutilgan raqamlar.
    EXPECTED_CASH = 100000 + 88000 + 7000 - 30000   # ochilish + sotuv + kirim - chiqim

    # ── Printer ──────────────────────────────────────────────────

    def add_printer(self):
        printer = frappe.get_doc(
            {
                "doctype": "Ozturk Printer", "printer_name": "_Test Shift Printer",
                "branch": self.branch, "role": "Kassa", "ip_address": "192.0.2.77", "port": 9100,
            }
        ).insert(ignore_permissions=True)
        row = {"name": printer.name, "header_line_1": "TEST", "header_line_2": None,
               "footer_text": None, "paper_width": "80", "codepage": "cp866",
               "codepage_number": 17, "cut_paper": 1}
        patcher = patch.object(print_queue, "cashier_printer", return_value=row)
        patcher.start()
        self.addCleanup(patcher.stop)
        return row

    def jobs(self, job_type):
        return frappe.get_all(
            "Ozturk Print Job",
            filters={"branch": self.branch, "job_type": job_type},
            fields=["name", "owner", "reason", "ref_name", "payload", "status", "title"],
            order_by="creation asc",
        )


# ═══════════════════════════════════════════════════════════════════
#  G'aladon
# ═══════════════════════════════════════════════════════════════════

class TestCashDrawer(ShiftTestCase):
    def test_pulse_bytes_are_esc_p(self):
        """`ESC p 0 25 250` = DK-1 pini, 50 ms yoqilgan, 500 ms tanaffus."""
        self.assertEqual(escpos.build_drawer_pulse(), b"\x1bp\x00\x19\xfa")
        self.assertEqual(escpos.build_drawer_pulse(pin=1, on_ms=100, off_ms=200), b"\x1bp\x01\x32\x64")

    def test_pulse_parameters_are_clamped(self):
        self.assertEqual(escpos.build_drawer_pulse(pin=7, on_ms=0, off_ms=100000), b"\x1bp\x00\x01\xff")

    def test_open_drawer_is_rejected_when_feature_is_off(self):
        self.open_shift()
        self.add_printer()
        self.set_features(cash_drawer=0)
        with self.as_user(CASHIER), self.assertRaises(frappe.PermissionError):
            printing.open_drawer()
        self.assertEqual(self.jobs("Drawer"), [])

    def test_open_drawer_requires_a_cashier_role(self):
        self.open_shift()
        self.add_printer()
        frappe.set_user("Guest")
        try:
            with self.assertRaises(frappe.PermissionError):
                printing.open_drawer()
        finally:
            frappe.set_user("Administrator")

    def test_open_drawer_requires_an_open_shift(self):
        self.add_printer()
        with patch.object(desktop_pos, "_open_opening_entry", return_value=""):
            with self.as_user(CASHIER), self.assertRaises(frappe.ValidationError):
                printing.open_drawer()
        self.assertEqual(self.jobs("Drawer"), [])

    def test_manual_open_queues_the_pulse_and_is_traceable(self):
        self.open_shift()
        printer = self.add_printer()
        with self.as_user(CASHIER):
            result = printing.open_drawer()
            printing.open_drawer(reason="Mijozga qaytim")

        jobs = self.jobs("Drawer")
        self.assertEqual(len(jobs), 2)
        first = jobs[0]
        self.assertEqual(result["queued"], first.name)
        self.assertEqual(base64.b64decode(first.payload), b"\x1bp\x00\x19\xfa")
        self.assertEqual(first.reason, "manual")           # `reason` bermasa — «manual»
        self.assertEqual(jobs[1].reason, "Mijozga qaytim")
        self.assertEqual(first.owner, CASHIER)              # kim ochdi
        self.assertFalse(first.ref_name)                    # savdosiz
        self.assertEqual(frappe.db.get_value("Ozturk Print Job", first.name, "printer"), printer["name"])

    def test_longest_reason_still_opens_the_drawer(self):
        """Sabab 140 belgi + sarlavha `title` (140) ga sig'masa g'aladon ochilmay qolardi."""
        self.open_shift()
        self.add_printer()
        reason = "x" * 200
        with self.as_user(CASHIER):
            job = printing.open_drawer(reason=reason)["queued"]
        self.assertTrue(job)
        row = self.jobs("Drawer")[0]
        self.assertEqual(len(row.reason), 140)
        self.assertLessEqual(len(row.title), 140)

    def test_blank_reason_falls_back_to_manual(self):
        self.open_shift()
        self.add_printer()
        with self.as_user(CASHIER):
            printing.open_drawer(reason="   ")
        self.assertEqual(self.jobs("Drawer")[0].reason, "manual")

    def test_open_drawer_without_printer_says_so(self):
        self.open_shift()
        with patch.object(print_queue, "cashier_printer", return_value=None):
            with self.as_user(CASHIER):
                self.assertEqual(printing.open_drawer(), {"queued": None, "reason": "no_printer"})

    def test_pulse_job_is_what_the_agent_receives(self):
        """Agent baytlarni o'zgartirmay uzatadi — yangi turda ham (agentni yangilash shart emas)."""
        self.open_shift()
        self.add_printer()
        with self.as_user(CASHIER):
            job = printing.open_drawer()["queued"]

        pulled = print_queue.pull_jobs(self.branch, "agent-test")
        row = next(item for item in pulled if item["job"] == job)
        self.assertEqual(row["job_type"], "Drawer")
        self.assertEqual(base64.b64decode(row["payload"]), b"\x1bp\x00\x19\xfa")

    def test_stale_pulse_expires_instead_of_firing_later(self):
        """Agent oflayn bo'lib, 10 daqiqadan keyin g'aladon o'zi ochilmasin."""
        self.open_shift()
        self.add_printer()
        with self.as_user(CASHIER):
            job = printing.open_drawer()["queued"]
        frappe.db.sql(
            "UPDATE `tabOzturk Print Job` SET creation = %s WHERE name = %s",
            (add_to_date(now_datetime(), seconds=-(print_queue.DRAWER_TTL + 30)), job),
        )

        pulled = print_queue.pull_jobs(self.branch, "agent-test")

        self.assertNotIn(job, [item["job"] for item in pulled])
        self.assertEqual(frappe.db.get_value("Ozturk Print Job", job, "status"), "Failed")

    def test_drawer_job_cannot_be_reprinted(self):
        """`reprint` g'aladonni funksiya bayrog'i va sabab yozuvisiz ochib yubormasin."""
        self.open_shift()
        self.add_printer()
        with self.as_user(CASHIER):
            job = printing.open_drawer()["queued"]
        with self.assertRaises(frappe.ValidationError):
            print_queue.requeue(job)

    # ── kick_drawer: hech qachon xato bermaydi ───────────────────

    def test_kick_drawer_returns_job_with_invoice_and_reason(self):
        self.add_printer()
        sale = self.add_invoice(total=1000, grand_total=1000, payments=[(self.cash_mode, "Cash", 1000)])
        job = print_queue.kick_drawer(self.scope, reason="naqd to'lov", invoice=sale)

        row = self.jobs("Drawer")[0]
        self.assertEqual(row.name, job)
        self.assertEqual(row.ref_name, sale)
        self.assertEqual(row.reason, "naqd to'lov")
        self.assertEqual(base64.b64decode(row.payload), escpos.build_drawer_pulse())

    def test_kick_drawer_without_printer_returns_none(self):
        with patch.object(print_queue, "cashier_printer", return_value=None):
            self.assertIsNone(print_queue.kick_drawer(self.scope, reason="x"))

    def test_kick_drawer_never_raises(self):
        self.add_printer()
        with patch.object(print_queue, "enqueue", side_effect=RuntimeError("baza yiqildi")):
            self.assertIsNone(print_queue.kick_drawer(self.scope, reason="x"))
        # scope buzuq bo'lsa ham.
        self.assertIsNone(print_queue.kick_drawer(object(), reason="x"))
        self.assertIsNone(print_queue.kick_drawer(None))

    def test_failing_kick_does_not_undo_the_callers_writes(self):
        """G'aladon yiqilsa to'lov (chaqiruvchining yozuvlari) bekor bo'lmasligi shart."""
        self.add_printer()
        frappe.db.set_value("POS Profile", self.profile, "custom_receipt_footer", "SAVDO-MARKER")
        with patch.object(print_queue, "enqueue", side_effect=RuntimeError("baza yiqildi")):
            self.assertIsNone(print_queue.kick_drawer(self.scope, reason="x"))
        self.assertEqual(
            frappe.db.get_value("POS Profile", self.profile, "custom_receipt_footer"), "SAVDO-MARKER"
        )

    def test_payload_is_hidden_from_cashier_but_not_from_manager(self):
        """Menejer hisoboti (kutilgan summa) Print Job payload'ida — kassir o'qiy olmasin."""
        self.open_shift()
        self.add_printer()
        self.add_known_sales()
        with self.as_user(MANAGER):
            job = printing.print_shift_report("X")["queued"]

        with self.as_user(CASHIER):
            doc = frappe.get_doc("Ozturk Print Job", job)
            doc.apply_fieldlevel_read_permissions()
            self.assertFalse(doc.get("payload"))
        with self.as_user(MANAGER):
            doc = frappe.get_doc("Ozturk Print Job", job)
            doc.apply_fieldlevel_read_permissions()
            self.assertTrue(doc.get("payload"))

    def test_shift_report_job_cannot_be_reprinted(self):
        """Tayyor baytlarni nusxalash menejer hisobotini kassirga chiqarib yuborardi."""
        self.open_shift()
        self.add_printer()
        with self.as_user(MANAGER):
            job = printing.print_shift_report("X")["queued"]
        with self.assertRaises(frappe.ValidationError):
            print_queue.requeue(job)


# ═══════════════════════════════════════════════════════════════════
#  Kassa cheki mazmuni
# ═══════════════════════════════════════════════════════════════════

class TestReceiptContent(FrappeTestCase):
    PRINTER = {"paper_width": "80", "codepage": "cp866", "codepage_number": 17, "cut_paper": 1}

    def bill(self, **extra):
        bill = {
            "items": [{"item_name": "Lag'mon", "qty": 2, "rate": 35000, "amount": 70000}],
            "total": 70000, "taxes": [], "discount": 0, "grand_total": 70000,
            "rounded_total": 70000, "paid": False, "payments": [],
        }
        bill.update(extra)
        return bill

    def text(self, bill, printer=None):
        return _plain(escpos.build_bill(bill, printer or self.PRINTER, {"line1": "TEST"}))

    def test_tip_label_matches_the_billing_tax_row(self):
        self.assertEqual(escpos.TIP_LABEL, cashier_billing.TIPS_DESCRIPTION)

    def test_bill_without_optional_keys_prints_as_before(self):
        text = self.text(self.bill())
        self.assertIn("JAMI TO'LOV:", text)
        for absent in ("Chegirma", "Choychaqa", "QAYTARISH", "Yetkazib"):
            self.assertNotIn(absent, text)

    def test_discount_line_shows_percent_and_reason(self):
        text = self.text(self.bill(discount=7000, discount_percent=10, discount_reason="Doimiy mijoz",
                                   grand_total=63000, rounded_total=63000))
        self.assertIn("Chegirma 10%:", text)
        self.assertIn("-7 000", text)
        self.assertIn("Doimiy mijoz", text)

    def test_discount_without_percent_has_plain_label(self):
        text = self.text(self.bill(discount=7000))
        self.assertIn("Chegirma:", text)
        self.assertNotIn("%", text.split("Chegirma:")[1].splitlines()[0])

    def test_tip_is_printed_once_even_if_it_is_also_a_tax_row(self):
        tip_row = {"description": "Choychaqa", "amount": 5000, "is_service_charge": False}
        for extra in ({"tip": 5000, "taxes": [tip_row]}, {"tip": 5000}, {"taxes": [tip_row]}):
            text = self.text(self.bill(**extra))
            self.assertEqual(text.count("Choychaqa:"), 1, extra)
            self.assertIn("5 000", text)

    def test_tip_row_flagged_by_billing_is_printed_once_under_any_name(self):
        row = {"description": "Xizmat uchun", "amount": 5000, "is_tip": True, "is_service_charge": False}
        text = self.text(self.bill(tip=5000, taxes=[row]))
        self.assertEqual(text.count("Choychaqa:"), 1)
        self.assertNotIn("Xizmat uchun", text)

    def test_delivery_phone_and_address(self):
        text = self.text(self.bill(order_type="Delivery",
                                   delivery={"phone": "+998901234567", "address": "Maksim Gorkiy 5"}))
        self.assertIn("Telefon: +998901234567", text)
        self.assertIn("Manzil: Maksim Gorkiy 5", text)

    def test_delivery_none_or_empty_is_ignored(self):
        for delivery in (None, {}, {"phone": None, "address": None}, "noto'g'ri"):
            self.assertNotIn("Yetkazib", self.text(self.bill(delivery=delivery)))

    def test_return_header_and_label(self):
        text = self.text(self.bill(is_return=1, return_against="INV-00099", total=-70000,
                                   grand_total=-70000, rounded_total=-70000,
                                   items=[{"item_name": "Lag'mon", "qty": -2, "rate": 35000,
                                           "amount": -70000}]))
        self.assertIn("QAYTARISH", text)
        self.assertIn("Asl chek: INV-00099", text)
        self.assertIn("QAYTARILADI:", text)
        self.assertNotIn("hisob o'zgargan", text.lower())

    def test_58mm_never_exceeds_32_columns_with_new_lines(self):
        printer = dict(self.PRINTER, paper_width="58")
        text = self.text(
            self.bill(discount=7000, discount_percent=10, discount_reason="Doimiy mijoz, uzoq izoh matni",
                      tip=5000, delivery={"phone": "+998901234567",
                                          "address": "Maksim Gorkiy ko'chasi 5-uy 12-xonadon"},
                      is_return=1, return_against="INV-00099"),
            printer,
        )
        for line in text.splitlines():
            self.assertLessEqual(len(line), 32, line)


# ═══════════════════════════════════════════════════════════════════
#  Smena hisoboti (X / Z) va ko'r sanoq
# ═══════════════════════════════════════════════════════════════════

class TestShiftReport(ShiftTestCase):
    SENSITIVE = {
        150000.0: "yalpi savdo", 164000.0: "sotuv jami", 144000.0: "sof savdo",
        165000.0: "kutilgan naqd", 108000.0: "naqd sotuv", 88000.0: "sof naqd sotuv",
        18000.0: "xizmat haqi", 5000.0: "choychaqa", 20000.0: "qaytarishlar",
        10000.0: "chegirma",
    }

    def setUp(self):
        super().setUp()
        self.open_shift()
        self.add_known_sales()
        self.add_known_movements()

    def report(self, kind="X", user=None):
        if user:
            with self.as_user(user):
                return cashier_api.get_shift_report(kind)
        return cashier_api.get_shift_report(kind)

    def add_closing(self, counted=160000):
        """Yopilgan smenani ifodalovchi `POS Closing Entry` (xom yozuv, konsolidatsiyasiz)."""
        name = f"TEST-CLO-{frappe.generate_hash(length=6)}"
        frappe.db.sql(
            """
            insert into `tabPOS Closing Entry`
                (name, creation, modified, owner, modified_by, docstatus, pos_opening_entry,
                 pos_profile, company, user, period_start_date, period_end_date,
                 posting_date, status)
            values (%s, now(), now(), %s, %s, 1, %s, %s, %s, %s, %s, now(), curdate(), 'Submitted')
            """,
            (name, CASHIER, CASHIER, self.shift, self.profile, self.company, CASHIER,
             add_to_date(now_datetime(), minutes=-60)),
        )
        for idx, invoice in enumerate([self.sale_cash, self.sale_card, self.refund], start=1):
            frappe.db.sql(
                """
                insert into `tabPOS Invoice Reference`
                    (name, creation, modified, owner, modified_by, docstatus, parent,
                     parenttype, parentfield, idx, pos_invoice)
                values (%s, now(), now(), %s, %s, 1, %s, 'POS Closing Entry',
                     'pos_transactions', %s, %s)
                """,
                (frappe.generate_hash(length=10), CASHIER, CASHIER, name, idx, invoice),
            )
        frappe.db.sql(
            """
            insert into `tabPOS Closing Entry Detail`
                (name, creation, modified, owner, modified_by, docstatus, parent, parenttype,
                 parentfield, idx, mode_of_payment, opening_amount, expected_amount,
                 closing_amount, difference)
            values (%s, now(), now(), %s, %s, 1, %s, 'POS Closing Entry', 'payment_reconciliation',
                 1, %s, 100000, %s, %s, %s)
            """,
            (frappe.generate_hash(length=10), CASHIER, CASHIER, name, self.cash_mode,
             self.EXPECTED_CASH, counted, counted - self.EXPECTED_CASH),
        )
        return name

    # ── Jami va sonlar ───────────────────────────────────────────

    def test_totals_match_the_known_invoices(self):
        report = self.report("X")

        self.assertEqual(report["kind"], "X")
        self.assertFalse(report["restricted"])
        self.assertEqual(report["pos_opening_entry"], self.shift)
        self.assertEqual(report["counts"]["invoices"], 2)
        self.assertEqual(report["counts"]["returns"], 1)
        self.assertEqual(
            report["sales"],
            {
                "gross_sales": 150000.0, "discounts": 10000.0, "service_charge": 18000.0,
                "tips": 5000.0, "other_taxes": 1000.0, "rounding": 0.0,
                "sales_total": 164000.0, "returns_total": 20000.0, "net_total": 144000.0,
            },
        )
        # Sotuv ichki muvozanati: yalpi - chegirma + xizmat + choychaqa + soliq = jami.
        sales = report["sales"]
        self.assertEqual(
            sales["gross_sales"] - sales["discounts"] + sales["service_charge"] + sales["tips"]
            + sales["other_taxes"] + sales["rounding"],
            sales["sales_total"],
        )

    def test_payment_modes_have_counts_and_sums_net_of_change(self):
        by_mode = {row["mode_of_payment"]: row for row in self.report("X")["payments"]}

        cash = by_mode[self.cash_mode]
        self.assertTrue(cash["is_cash"])
        self.assertEqual(
            (cash["sales_count"], cash["sales_amount"], cash["refund_count"],
             cash["refund_amount"], cash["net_amount"]),
            (1, 108000.0, 1, 20000.0, 88000.0),        # 110 000 - 2 000 qaytim
        )
        card = by_mode["Credit Card"]
        self.assertFalse(card["is_cash"])
        self.assertEqual((card["sales_count"], card["sales_amount"], card["net_amount"]),
                         (1, 56000.0, 56000.0))

    def test_cash_block_and_movements(self):
        report = self.report("X")

        self.assertEqual(report["cash"]["opening"], 100000.0)
        self.assertEqual(report["cash"]["expected"], float(self.EXPECTED_CASH))
        self.assertIsNone(report["cash"]["counted"])       # X da sanoq yo'q
        self.assertEqual(report["cash_movements"]["count"], 2)
        self.assertEqual(report["cash_movements"]["total_in"], 7000.0)
        self.assertEqual(report["cash_movements"]["total_out"], 30000.0)

    def test_cancelled_orders_and_drawer_openings_are_counted(self):
        frappe.db.sql(
            """
            insert into `tabPOS Invoice`
                (name, creation, modified, owner, modified_by, docstatus, branch, custom_cancelled)
            values (%s, now(), now(), %s, %s, 0, %s, 1)
            """,
            (frappe.generate_hash(length=10), CASHIER, CASHIER, self.branch),
        )
        self.add_printer()
        with self.as_user(CASHIER):
            printing.open_drawer(reason="Mijozga qaytim")
        # Naqd to'lovdan keyingi ochilish (chek bilan) «savdosiz» sanalmaydi.
        print_queue.kick_drawer(self.scope, reason="naqd to'lov", invoice=self.sale_cash)

        report = self.report("X")

        self.assertEqual(report["counts"]["cancelled_orders"], 1)
        self.assertEqual(report["counts"]["drawer_no_sale"], 1)
        self.assertEqual(report["drawer_openings"][0]["reason"], "Mijozga qaytim")
        self.assertEqual(report["drawer_openings"][0]["user"], CASHIER)

    def test_x_report_needs_an_open_shift_and_valid_kind(self):
        with patch.object(desktop_pos, "_open_opening_entry", return_value=""):
            with self.assertRaises(frappe.ValidationError):
                self.report("X")
        with self.assertRaises(frappe.ValidationError):
            self.report("Y")

    def test_report_gate(self):
        self.set_features(shift_reports=0)
        with self.assertRaises(frappe.PermissionError):
            self.report("X")
        self.add_printer()
        with self.assertRaises(frappe.PermissionError):
            printing.print_shift_report("X")

    def test_report_needs_a_cashier_role(self):
        frappe.set_user("Guest")
        try:
            with self.assertRaises(frappe.PermissionError):
                cashier_api.get_shift_report("X")
        finally:
            frappe.set_user("Administrator")

    # ── KO'R SANOQ ───────────────────────────────────────────────

    def test_manager_sees_everything_in_x(self):
        report = self.report("X", user=MANAGER)
        self.assertFalse(report["restricted"])
        self.assertEqual(report["cash"]["expected"], float(self.EXPECTED_CASH))
        self.assertEqual(report["sales"]["net_total"], 144000.0)

    def test_cashier_x_report_hides_every_figure_that_reveals_expected_cash(self):
        report = self.report("X", user=CASHIER)

        self.assertTrue(report["restricted"])
        self.assertIsNone(report["cash"]["expected"])
        self.assertIsNone(report["cash"]["difference"])
        self.assertTrue(all(value is None for value in report["sales"].values()))
        # Naqd usul bo'yicha summalar yashirin, sonlar qoladi.
        cash = next(row for row in report["payments"] if row["is_cash"])
        self.assertIsNone(cash["sales_amount"])
        self.assertIsNone(cash["refund_amount"])
        self.assertIsNone(cash["net_amount"])
        self.assertEqual(cash["sales_count"], 1)
        # Kassirga qoladigan raqamlar.
        self.assertEqual(report["cash"]["opening"], 100000.0)
        self.assertEqual(report["counts"]["invoices"], 2)
        card = next(row for row in report["payments"] if not row["is_cash"])
        self.assertEqual(card["net_amount"], 56000.0)

        leaked = set(self.SENSITIVE) & _numbers(report)
        self.assertFalse(leaked, f"kassirga sizib chiqdi: {[self.SENSITIVE[v] for v in leaked]}")

    def test_cashier_cannot_derive_expected_cash_from_any_endpoint_payload(self):
        """Kassirga ketadigan har qanday smena javobida kutilgan summa yo'q."""
        with self.as_user(CASHIER):
            closing_data = cashier_api.get_shift_closing_data()
            movements = cash_api.get_cash_movements()
            report = cashier_api.get_shift_report("X")
        for payload in (closing_data, movements, report):
            self.assertNotIn(float(self.EXPECTED_CASH), _numbers(payload))
            self.assertNotIn("expected_amount", json.dumps(payload))

    def test_supervisor_who_opened_the_shift_is_blind_until_it_is_closed(self):
        """Smenani o'zi ochgan menejerga ham yopilishdan OLDIN kutilgan summa yo'q."""
        self.open_shift(user=MANAGER)
        report = self.report("X", user=MANAGER)

        self.assertTrue(report["restricted"])
        self.assertIsNone(report["cash"]["expected"])
        # Boshqa menejer/administrator esa ko'ra oladi.
        self.assertFalse(self.report("X")["restricted"])

    def test_may_see_cash_figures_rules(self):
        with self.as_user(CASHIER):
            self.assertFalse(shift_report.may_see_cash_figures(OTHER_CASHIER, True))
            self.assertFalse(shift_report.may_see_cash_figures(CASHIER, True))
        with self.as_user(MANAGER):
            self.assertTrue(shift_report.may_see_cash_figures(CASHIER, False))
            self.assertFalse(shift_report.may_see_cash_figures(MANAGER, False))
            self.assertTrue(shift_report.may_see_cash_figures(MANAGER, True))

    def test_z_report_full_for_manager(self):
        self.add_closing(counted=160000)
        report = self.report("Z", user=MANAGER)

        self.assertEqual(report["kind"], "Z")
        self.assertFalse(report["restricted"])
        self.assertTrue(report["pos_closing_entry"].startswith("TEST-CLO-"))
        self.assertEqual(report["cash"]["counted"], 160000.0)
        self.assertEqual(report["cash"]["expected"], float(self.EXPECTED_CASH))
        self.assertEqual(report["cash"]["difference"], -5000.0)
        self.assertEqual(report["sales"]["net_total"], 144000.0)
        self.assertEqual(report["counts"]["invoices"], 2)

    def test_z_report_for_the_closing_cashier_hides_expected_and_difference(self):
        self.add_closing(counted=160000)
        report = self.report("Z", user=CASHIER)

        self.assertTrue(report["restricted"])
        self.assertEqual(report["cash"]["counted"], 160000.0)   # o'zi sanagan
        self.assertIsNone(report["cash"]["expected"])
        self.assertIsNone(report["cash"]["difference"])
        leaked = set(self.SENSITIVE) & _numbers(report)
        self.assertFalse(leaked, f"kassirga sizib chiqdi: {[self.SENSITIVE[v] for v in leaked]}")
        self.assertNotIn(-5000.0, _numbers(report))

    def test_cashier_cannot_read_another_cashiers_z_report(self):
        self.add_closing()
        with self.as_user(OTHER_CASHIER), self.assertRaises(frappe.ValidationError):
            cashier_api.get_shift_report("Z")

    # ── Bosma ────────────────────────────────────────────────────

    def test_printed_report_follows_the_same_rule(self):
        self.add_printer()
        with self.as_user(CASHIER):
            cashier_job = printing.print_shift_report("X")["queued"]
        with self.as_user(MANAGER):
            manager_job = printing.print_shift_report("X")["queued"]

        payloads = {row.name: _plain(base64.b64decode(row.payload)) for row in self.jobs("Shift Report")}
        cashier_text, manager_text = payloads[cashier_job], payloads[manager_job]

        self.assertIn("X-HISOBOT", cashier_text)
        self.assertNotIn("Kutilgan", cashier_text)
        self.assertNotIn("SOF SAVDO", cashier_text)
        self.assertNotIn("165 000", cashier_text)
        self.assertIn("menejer hisobotida", cashier_text)
        self.assertIn("Kutilgan:", manager_text)
        self.assertIn("165 000", manager_text)
        self.assertIn("SOF SAVDO:", manager_text)
        self.assertIn("144 000", manager_text)

    def test_printed_z_report_shows_counted_and_difference_to_manager_only(self):
        self.add_printer()
        self.add_closing(counted=160000)
        with self.as_user(CASHIER):
            cashier_job = printing.print_shift_report("Z")["queued"]
        with self.as_user(MANAGER):
            manager_job = printing.print_shift_report("Z")["queued"]

        payloads = {row.name: _plain(base64.b64decode(row.payload)) for row in self.jobs("Shift Report")}
        self.assertIn("Sanalgan:", payloads[cashier_job])
        self.assertNotIn("Farq:", payloads[cashier_job])
        self.assertIn("Farq:", payloads[manager_job])
        self.assertIn("-5 000", payloads[manager_job])

    def test_print_shift_report_without_printer(self):
        with patch.object(print_queue, "cashier_printer", return_value=None):
            result = printing.print_shift_report("X")
        self.assertEqual((result["queued"], result["reason"]), (None, "no_printer"))

    def test_report_prints_on_58mm_paper_within_32_columns(self):
        report = self.report("X", user=MANAGER)
        report["cash_movements"]["items"][0]["reason"] = "Juda uzun sabab matni " * 4
        printer = {"paper_width": "58", "codepage": "cp866", "codepage_number": 17, "cut_paper": 1}
        for line in _plain(escpos.build_shift_report(report, printer, {"line1": "TEST"})).splitlines():
            self.assertLessEqual(len(line), 32, line)

    def test_report_builder_tolerates_a_bare_report(self):
        """Yetishmayotgan kalitlar chop etishni yiqitmasin."""
        printer = {"paper_width": "80", "codepage": "cp866", "codepage_number": 17, "cut_paper": 1}
        self.assertIn("Z-HISOBOT", _plain(escpos.build_shift_report({"kind": "Z"}, printer)))


# ═══════════════════════════════════════════════════════════════════
#  Smenani yopish: Z-hisobot navbati va mavjud kafolatlar
# ═══════════════════════════════════════════════════════════════════

class TestCloseShift(ShiftTestCase):
    Z_DATA = {
        "pos_opening_entry": "X", "pos_closing_entry": "POS-CLO-TEST", "total_sales": 144000.0,
        "total_invoices": 3, "expected_cash": 165000.0, "actual_cash": 160000.0, "cash_diff": -5000.0,
        "payments": [{"mode_of_payment": "Нахт", "opening_amount": 100000.0,
                      "expected_amount": 165000.0, "closing_amount": 160000.0, "difference": -5000.0}],
    }

    def setUp(self):
        super().setUp()
        self.open_shift()
        self.add_printer()
        self.order = MagicMock()

        def fake_closing(**kwargs):
            self.order.closing(**kwargs)
            return {"name": "POS-CLO-TEST", "status": "closed",
                    "z_report_data": json.loads(json.dumps(self.Z_DATA))}

        self.closing_mock = MagicMock(side_effect=fake_closing)
        # `Error Log` jadvali MyISAM — tranzaksiya qaytarilganda ham QOLADI. Xato yo'lini
        # sinaganda dev bazani ifloslamaslik uchun yozuv soxtalashtiriladi.
        self.log_error = MagicMock()
        for patcher in (
            patch("frappe.log_error", self.log_error),
            patch.object(desktop_pos, "createPosClosing", self.closing_mock),
            patch.object(cashier_permissions, "pos_profile_users", return_value=[]),
            patch("ozturkapp.ozturkapp.setup.item_costs.missing_costs", return_value=[]),
            patch.object(cashier_api.table_status, "get_open_orders", return_value=[]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def close(self, user=CASHIER):
        with self.as_user(user):
            return cashier_api.close_shift(json.dumps({self.cash_mode: 160000}))

    def patch_z_report(self, **kwargs):
        def record_build(*args, **kw):
            self.order.build(*args, **kw)
            return {"kind": "Z"}

        def record_enqueue(*args, **kw):
            self.order.enqueue(*args, **kw)
            return "PJ-TEST"

        build = patch.object(shift_report, "build_report", side_effect=kwargs.get("build", record_build))
        enqueue = patch.object(print_queue, "enqueue_shift_report",
                               side_effect=kwargs.get("enqueue", record_enqueue))
        build.start()
        enqueue.start()
        self.addCleanup(build.stop)
        self.addCleanup(enqueue.stop)

    def test_z_report_is_queued_after_a_successful_closing(self):
        self.patch_z_report()

        result = self.close()

        self.assertIs(result["z_report_queued"], True)
        names = [call[0] for call in self.order.mock_calls]
        self.assertEqual(names, ["closing", "build", "enqueue"])     # AVVAL yopish, KEYIN hisobot
        self.assertEqual(self.order.build.call_args[0][0], "Z")
        self.assertEqual(self.order.build.call_args[1]["closing"], "POS-CLO-TEST")

    def test_z_report_is_not_queued_when_closing_fails(self):
        self.patch_z_report()
        self.closing_mock.side_effect = frappe.ValidationError("yopilmadi")

        with self.assertRaises(frappe.ValidationError):
            self.close()

        self.order.build.assert_not_called()
        self.order.enqueue.assert_not_called()

    def test_printing_failure_never_undoes_or_fails_the_closing(self):
        frappe.db.set_value("POS Profile", self.profile, "custom_receipt_footer", "YOPISH-MARKER")

        def boom(*args, **kwargs):
            raise RuntimeError("printer yiqildi")

        self.patch_z_report(enqueue=boom)

        result = self.close()

        self.assertIs(result["z_report_queued"], False)
        self.assertEqual(result["name"], "POS-CLO-TEST")              # yopilish natijasi saqlangan
        self.assertEqual(
            frappe.db.get_value("POS Profile", self.profile, "custom_receipt_footer"), "YOPISH-MARKER"
        )
        self.log_error.assert_called_once()                            # sabab Error Log'ga yozildi

    def test_report_building_failure_never_fails_the_closing(self):
        def boom(*args, **kwargs):
            raise RuntimeError("hisobot tuzilmadi")

        self.patch_z_report(build=boom)
        self.assertIs(self.close()["z_report_queued"], False)
        self.log_error.assert_called_once()

    def test_z_report_is_skipped_when_feature_is_off(self):
        self.patch_z_report()
        self.set_features(shift_reports=0)

        result = self.close()

        self.assertIs(result["z_report_queued"], False)
        self.order.build.assert_not_called()
        self.closing_mock.assert_called_once()                        # smena baribir yopiladi

    def test_z_report_is_skipped_for_a_sub_closing(self):
        """Yordamchi kassir yopishi (Sub POS Closing) yakuniy Z emas."""
        self.patch_z_report()
        self.closing_mock.side_effect = lambda **kw: {"name": "SUB-1", "type": "sub",
                                                      "status": "sub_closed", "z_report_data": {}}
        self.assertIs(self.close()["z_report_queued"], False)
        self.order.build.assert_not_called()

    def test_real_z_report_lands_in_the_queue(self):
        """Soxta qismlarsiz: yopilgan smena uchun haqiqiy `Shift Report` topshirig'i."""
        self.add_known_sales()
        with patch.object(shift_report, "build_report", wraps=shift_report.build_report) as build:
            with patch.object(shift_report, "_closed_shift") as closed:
                closed.return_value = frappe._dict(
                    opening=frappe.get_doc("POS Opening Entry", self.shift), closing="POS-CLO-TEST",
                    period_end=now_datetime(), invoices=self.invoices,
                    reconciliation=[frappe._dict(mode_of_payment=self.cash_mode, opening_amount=100000.0,
                                                 expected_amount=188000.0, closing_amount=160000.0,
                                                 difference=-28000.0)],
                )
                result = self.close()

        self.assertIs(result["z_report_queued"], True)
        self.assertEqual(build.call_args[0][0], "Z")
        job = self.jobs("Shift Report")[0]
        self.assertIn("Z-HISOBOT", _plain(base64.b64decode(job.payload)))

    def test_cashier_response_hides_expected_cash_and_difference(self):
        result = self.close(CASHIER)
        report = result["z_report_data"]

        for hidden in ("expected_cash", "cash_diff", "total_sales"):
            self.assertNotIn(hidden, report)
        self.assertEqual(report["payments"], [{"mode_of_payment": "Нахт", "closing_amount": 160000.0}])
        self.assertEqual(report["total_invoices"], 3)
        self.assertNotIn(165000.0, _numbers(result))
        self.assertNotIn(-5000.0, _numbers(result))

    def test_manager_response_keeps_the_full_report(self):
        report = self.close(MANAGER)["z_report_data"]
        self.assertEqual(report["expected_cash"], 165000.0)
        self.assertEqual(report["cash_diff"], -5000.0)

    # ── Mavjud kafolatlar saqlangan ──────────────────────────────

    def test_closing_is_still_refused_with_unpaid_orders(self):
        self.patch_z_report()
        with patch.object(cashier_api.table_status, "get_open_orders", return_value=[{"name": "INV-1"}]):
            with self.assertRaises(frappe.ValidationError) as caught:
                self.close()
        self.assertIn("to'lanmagan buyurtma", str(caught.exception))
        self.closing_mock.assert_not_called()
        self.order.enqueue.assert_not_called()

    def test_closing_is_still_refused_without_valuation_rate(self):
        self.patch_z_report()
        gaps = [{"item": "ITEM-1", "item_name": "Lag'mon"}]
        with patch("ozturkapp.ozturkapp.setup.item_costs.missing_costs", return_value=gaps):
            with self.assertRaises(frappe.ValidationError) as caught:
                self.close()
        self.assertIn("tannarx", str(caught.exception))
        self.closing_mock.assert_not_called()

    def test_blind_count_input_is_still_required(self):
        with self.as_user(CASHIER), self.assertRaises(frappe.ValidationError):
            cashier_api.close_shift(json.dumps({}))
        self.closing_mock.assert_not_called()

    def test_closing_uses_expected_cash_including_movements(self):
        """`close_shift` ERPNext'ga yuboradigan kutilgan naqd = ochilish + sotuv + kirim - chiqim."""
        self.add_known_sales()
        self.add_known_movements()

        self.close()

        sent = json.loads(json.dumps(self.order.closing.call_args[1]["payment_reconciliation"],
                                     default=str))
        cash = next(row for row in sent if row["mode_of_payment"] == self.cash_mode)
        self.assertEqual(cash["expected_amount"], float(self.EXPECTED_CASH))
        self.assertEqual(cash["closing_amount"], 160000.0)          # kassir sanagani
        card = next(row for row in sent if row["mode_of_payment"] == "Credit Card")
        self.assertEqual(card["closing_amount"], card["expected_amount"])   # terminal yozuvi


# ═══════════════════════════════════════════════════════════════════
#  Kassa harakati
# ═══════════════════════════════════════════════════════════════════

class TestCashMovements(ShiftTestCase):
    def setUp(self):
        super().setUp()
        self.open_shift()

    def create(self, kind="Out", amount=30000, category="Xarajat", reason="Muz sotib olindi",
               user=CASHIER, **kwargs):
        with self.as_user(user):
            return cash_api.create_cash_movement(kind, amount, category, reason, **kwargs)

    def count(self):
        return frappe.db.count("Ozturk Cash Movement", {"pos_opening_entry": self.shift})

    def approval(self, pin=PIN):
        return {"user": MANAGER, "pin": pin}

    # ── Yoqish/o'chirish va ruxsat ───────────────────────────────

    def test_endpoints_are_gated_by_the_feature(self):
        self.set_features(cash_movements=0)
        with self.assertRaises(frappe.PermissionError):
            self.create()
        with self.as_user(CASHIER), self.assertRaises(frappe.PermissionError):
            cash_api.get_cash_movements()
        self.assertEqual(self.count(), 0)

    def test_guest_is_rejected(self):
        frappe.set_user("Guest")
        try:
            with self.assertRaises(frappe.PermissionError):
                cash_api.create_cash_movement("Out", 1000, "Xarajat", "sabab")
            with self.assertRaises(frappe.PermissionError):
                cash_api.get_cash_movements()
        finally:
            frappe.set_user("Administrator")

    def test_shift_must_be_open(self):
        with patch.object(desktop_pos, "_open_opening_entry", return_value=""):
            with self.assertRaises(frappe.ValidationError):
                self.create()
        self.assertEqual(self.count(), 0)

    def test_doctype_desk_access_is_for_managers_only(self):
        meta = frappe.get_meta("Ozturk Cash Movement")
        self.assertTrue(meta.is_submittable)
        roles = {row.role: row for row in meta.permissions}
        self.assertEqual(set(roles), {"System Manager", "URY Manager"})
        for role in roles.values():
            self.assertTrue(role.read and role.print and role.report and role.export)
        self.assertFalse(frappe.has_permission("Ozturk Cash Movement", "read", user=CASHIER))
        self.assertTrue(frappe.has_permission("Ozturk Cash Movement", "read", user=MANAGER))

    # ── Journal Entry ────────────────────────────────────────────

    def journal(self, name):
        doc = frappe.get_doc("Journal Entry", frappe.db.get_value("Ozturk Cash Movement", name, "journal_entry"))
        return doc, {row.account: row for row in doc.accounts}

    def test_out_movement_posts_a_balanced_journal_entry(self):
        result = self.create("Out", 30000)

        movement = frappe.get_doc("Ozturk Cash Movement", result["name"])
        journal, rows = self.journal(result["name"])
        counter = frappe.db.get_value("Company", self.company, "custom_cash_movement_account")

        self.assertEqual(movement.docstatus, 1)
        self.assertEqual(movement.user, CASHIER)
        self.assertEqual(journal.docstatus, 1)
        self.assertEqual(journal.voucher_type, "Cash Entry")
        self.assertEqual(journal.total_debit, journal.total_credit)
        self.assertEqual(journal.total_debit, 30000)
        self.assertEqual(rows[self.cash_account].credit, 30000)       # naqd hisob kreditlanadi
        self.assertEqual(rows[counter].debit, 30000)
        self.assertEqual(movement.cash_account, self.cash_account)
        self.assertEqual(movement.counter_account, counter)
        self.assertIn(result["name"], journal.user_remark)
        self.assertEqual(result["journal_entry"], journal.name)

        gl = frappe.get_all("GL Entry", filters={"voucher_no": journal.name, "is_cancelled": 0},
                            fields=["account", "debit", "credit"])
        self.assertEqual(sum(row.debit for row in gl), sum(row.credit for row in gl))
        self.assertEqual(len(gl), 2)

    def test_in_movement_reverses_the_entry(self):
        result = self.create("In", 7000, "Kassaga qo'shish", "Mayda pul", approval=self.approval())

        journal, rows = self.journal(result["name"])
        counter = frappe.db.get_value("Company", self.company, "custom_cash_movement_account")

        self.assertEqual(rows[self.cash_account].debit, 7000)
        self.assertEqual(rows[counter].credit, 7000)
        self.assertEqual(journal.total_debit, journal.total_credit)

    def test_cancelling_reverses_the_journal_entry_and_the_expected_cash(self):
        result = self.create("Out", 30000)
        self.assertEqual(self.expected_cash(), 100000 - 30000)

        movement = frappe.get_doc("Ozturk Cash Movement", result["name"])
        journal_name = movement.journal_entry
        with self.as_user(MANAGER):
            movement.cancel()

        self.assertEqual(frappe.db.get_value("Journal Entry", journal_name, "docstatus"), 2)
        self.assertEqual(self.expected_cash(), 100000)               # bekor qilingan hisobga olinmaydi

    def test_closed_shift_movement_cannot_be_cancelled(self):
        result = self.create("Out", 30000)
        frappe.db.set_value("POS Opening Entry", self.shift, "status", "Closed")
        movement = frappe.get_doc("Ozturk Cash Movement", result["name"])
        with self.assertRaises(frappe.ValidationError):
            movement.cancel()

    # ── Tasdiq chegarasi ─────────────────────────────────────────

    def test_out_up_to_the_limit_needs_no_approval(self):
        self.set_payout_limit(100000)
        result = self.create("Out", 100000)                          # aynan chegarada
        self.assertFalse(result["approved_by"])

    def test_out_above_the_limit_requires_approval(self):
        self.set_payout_limit(100000)
        with self.assertRaises(ApprovalRequired):
            self.create("Out", 100001)
        self.assertEqual(self.count(), 0)                            # hech narsa yozilmadi

    def test_zero_limit_means_every_payout_needs_approval(self):
        self.set_payout_limit(0)
        with self.assertRaises(ApprovalRequired):
            self.create("Out", 1)
        result = self.create("Out", 1, approval=self.approval())
        self.assertEqual(result["approved_by"], MANAGER)

    def test_wrong_pin_is_rejected(self):
        self.set_payout_limit(0)
        with self.assertRaises(ApprovalRequired):
            self.create("Out", 500, approval=self.approval(pin="0000"))
        self.assertEqual(self.count(), 0)

    def test_valid_approval_is_recorded(self):
        result = self.create("Out", 250000, approval=self.approval())
        self.assertEqual(result["approved_by"], MANAGER)
        self.assertEqual(frappe.db.get_value("Ozturk Cash Movement", result["name"], "approved_by"), MANAGER)
        self.assertTrue(frappe.db.exists("Comment", {
            "reference_doctype": "POS Opening Entry", "reference_name": self.shift,
            "content": ["like", "%Kassadan chiqarish%"],
        }))

    def test_cash_in_always_needs_approval_from_a_cashier(self):
        """Sohta kirim kamomadni yopa olmasin — kirim har doim tasdiqlanadi."""
        self.set_payout_limit(10 ** 9)
        with self.assertRaises(ApprovalRequired):
            self.create("In", 1000, "Kassaga qo'shish", "Mayda pul")
        self.assertEqual(self.count(), 0)

    def test_manager_at_the_till_needs_no_pin(self):
        self.set_payout_limit(0)
        result = self.create("Out", 500000, user=MANAGER)
        self.assertEqual(result["approved_by"], MANAGER)

    def test_no_balance_check_and_no_balance_in_errors(self):
        """Chiqim g'aladondagi puldan oshsa ham rad etilmaydi: balansni ochib qo'ymaymiz."""
        self.set_payout_limit(10 ** 9)
        result = self.create("Out", 9000000)                         # ochilish 100 000
        self.assertEqual(result["total_out"], 9000000.0)

    # ── Tekshiruvlar ─────────────────────────────────────────────

    def test_invalid_input_is_rejected_before_asking_for_a_pin(self):
        self.set_payout_limit(0)                                    # tasdiq har doim kerak bo'lardi
        bad = [
            ("Out", 0, "Xarajat", "sabab"),
            ("Out", -5, "Xarajat", "sabab"),
            ("Out", "nan", "Xarajat", "sabab"),
            ("Out", "inf", "Xarajat", "sabab"),
            ("Out", 1000, "Kassaga qo'shish", "sabab"),            # kirim turi chiqimga mos emas
            ("In", 1000, "Xarajat", "sabab"),
            ("Out", 1000, "Xarajat", "  "),
            ("Out", 1000, "Xarajat", "ab"),
            ("Sideways", 1000, "Xarajat", "sabab"),
        ]
        for kind, amount, category, reason in bad:
            with self.assertRaises(frappe.ValidationError, msg=(kind, amount, category, reason)):
                self.create(kind, amount, category, reason)        # ApprovalRequired EMAS
        self.assertEqual(self.count(), 0)

    def test_non_cash_mode_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self.create(mode_of_payment="Credit Card")

    def test_duplicate_within_the_window_is_rejected(self):
        self.create("Out", 30000)
        with self.assertRaises(frappe.ValidationError):
            self.create("Out", 30000)
        self.create("Out", 30000, reason="Boshqa sabab")             # boshqa sabab — yangi harakat
        self.assertEqual(self.count(), 2)

    # ── Yopish arifmetikasi ──────────────────────────────────────

    def opening_doc(self):
        return frappe.get_doc("POS Opening Entry", self.shift)

    def expected_cash(self):
        closing = pos_closing.make_closing_entry_from_opening(self.opening_doc())
        return next(row.expected_amount for row in closing.payment_reconciliation
                    if row.mode_of_payment == self.cash_mode)

    def test_expected_cash_is_opening_plus_sales_plus_in_minus_out(self):
        self.assertEqual(self.expected_cash(), 100000)               # faqat ochilish
        self.add_known_sales()
        self.assertEqual(self.expected_cash(), 100000 + 88000)       # + naqd sotuv (qaytim va qaytarish bilan)
        self.add_known_movements()
        self.assertEqual(self.expected_cash(), self.EXPECTED_CASH)

    def test_close_shift_and_desktop_pos_see_the_same_expected_cash(self):
        self.add_known_sales()
        self.add_known_movements()

        desktop = desktop_pos.getPosClosingData(self.shift)
        cash = next(row for row in desktop["reconciliation"] if row["mode_of_payment"] == self.cash_mode)

        self.assertEqual(cash["expected_amount"], float(self.EXPECTED_CASH))
        self.assertEqual(self.expected_cash(), cash["expected_amount"])

    def test_movements_are_included_even_if_the_feature_was_switched_off_later(self):
        self.create("Out", 30000)
        self.set_features(cash_movements=0)
        self.assertEqual(self.expected_cash(), 100000 - 30000)

    def test_missing_movement_table_does_not_break_closing(self):
        with patch.object(frappe.db, "table_exists", return_value=False):
            self.assertEqual(pos_closing.get_cash_movements(self.shift), [])
            self.assertEqual(self.expected_cash(), 100000)

    def test_cash_movement_list_for_the_shift(self):
        self.create("Out", 30000)
        with self.as_user(CASHIER):
            data = cash_api.get_cash_movements()

        self.assertEqual(data["pos_opening_entry"], self.shift)
        self.assertEqual((data["count"], data["total_in"], data["total_out"]), (1, 0.0, 30000.0))
        self.assertEqual(data["items"][0]["category"], "Xarajat")
        self.assertEqual(data["categories"]["In"], ["Kassaga qo'shish", "Boshqa"])
        self.assertEqual(data["cash_modes"], [self.cash_mode])

    def test_list_is_empty_when_the_shift_is_closed(self):
        with patch.object(desktop_pos, "_open_opening_entry", return_value=""):
            with self.as_user(CASHIER):
                data = cash_api.get_cash_movements()
        self.assertIsNone(data["pos_opening_entry"])
        self.assertEqual(data["items"], [])


class TestNetOfChange(FrappeTestCase):
    """`pos_closing.net_payments` — qaytim naqd qatordan ayiriladi."""

    @staticmethod
    def invoice(change, *rows):
        return frappe._dict(
            change_amount=change,
            payments=[frappe._dict(mode_of_payment=mode, type=kind, amount=amount)
                      for mode, kind, amount in rows],
        )

    def test_change_is_taken_off_the_cash_row_only(self):
        invoice = self.invoice(2000, ("Cash", "Cash", 390000), ("Card", "Bank", 50000))
        self.assertEqual(pos_closing.net_payments(invoice), [("Cash", 388000), ("Card", 50000)])

    def test_no_change_leaves_rows_untouched(self):
        invoice = self.invoice(0, ("Cash", "Cash", 100000))
        self.assertEqual(pos_closing.net_payments(invoice), [("Cash", 100000)])

    def test_change_never_exceeds_a_row_and_carries_to_the_next_cash_row(self):
        invoice = self.invoice(7000, ("Cash", "Cash", 5000), ("Cash 2", "Cash", 10000))
        self.assertEqual(pos_closing.net_payments(invoice), [("Cash", 0), ("Cash 2", 8000)])

    def test_refund_rows_are_not_reduced(self):
        invoice = self.invoice(0, ("Cash", "Cash", -20000))
        self.assertEqual(pos_closing.net_payments(invoice), [("Cash", -20000)])


class TestSetupIsIdempotent(ShiftTestCase):
    def test_clearing_account_is_reused_not_duplicated(self):
        first = cashier_shift_setup.ensure_clearing_account(self.company)
        second = cashier_shift_setup.ensure_clearing_account(self.company)
        self.assertEqual(first, second)
        self.assertEqual(frappe.db.count("Account", {"account_name": "Kassa harakati", "company": self.company}), 1)
        self.assertEqual(frappe.db.get_value("Account", first, "root_type"), "Asset")
        self.assertFalse(frappe.db.get_value("Account", first, "is_group"))

    def test_missing_counter_account_gives_a_clear_error(self):
        frappe.db.set_value("Company", self.company, "custom_cash_movement_account", None)
        self.open_shift()
        with self.assertRaises(frappe.ValidationError) as caught:
            with self.as_user(CASHIER):
                cash_api.create_cash_movement("Out", 1000, "Xarajat", "sabab")
        self.assertIn("Kassa harakati hisobi", str(caught.exception))


# ═══════════════════════════════════════════════════════════════════
#  Yopish yo'llari bir xil raqam ko'radi (qaytim, qaytarish, kassa harakati)
# ═══════════════════════════════════════════════════════════════════

class TestClosingPathsAgree(ShiftTestCase):
    """`close_shift` (kassa sahifasi) va `createPosClosing` (Desktop POS) bir xil kutilgan summa.

    `POS Closing Entry` yozuvi (insert/submit) o'chirilgan: u xom fikstura cheklarni
    konsolidatsiya qila olmaydi. Kutilgan summani hisoblovchi kod esa HAQIQIY.
    """

    def setUp(self):
        super().setUp()
        self.open_shift()
        self.add_known_sales()          # naqd chek: 110 000 berildi, 2 000 qaytim; karta; qaytarish
        self.add_known_movements()      # chiqim 30 000, kirim 7 000
        for patcher in (
            patch.object(POSClosingEntry, "insert", autospec=True, side_effect=lambda doc, *a, **k: doc),
            patch.object(POSClosingEntry, "submit", autospec=True, side_effect=lambda doc, *a, **k: doc),
            patch.object(cashier_permissions, "pos_profile_users", return_value=[]),
            patch("ozturkapp.ozturkapp.setup.item_costs.missing_costs", return_value=[]),
            patch.object(cashier_api.table_status, "get_open_orders", return_value=[]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def counted(self, amount):
        return [{"mode_of_payment": self.cash_mode, "closing_amount": amount}]

    def test_desktop_pos_path(self):
        with self.as_user(CASHIER):
            result = desktop_pos.createPosClosing(self.shift, json.dumps(self.counted(160000)))

        report = result["z_report_data"]
        self.assertEqual(report["expected_cash"], float(self.EXPECTED_CASH))
        self.assertEqual(report["actual_cash"], 160000.0)
        self.assertEqual(report["cash_diff"], 160000.0 - self.EXPECTED_CASH)
        card = next(row for row in report["payments"] if row["mode_of_payment"] == "Credit Card")
        self.assertEqual(card["expected_amount"], 56000.0)           # naqd bo'lmagan qatorga tegilmagan

    def test_web_cashier_path(self):
        with self.as_user(MANAGER):
            result = cashier_api.close_shift(json.dumps({self.cash_mode: 160000}))

        report = result["z_report_data"]
        self.assertEqual(report["expected_cash"], float(self.EXPECTED_CASH))
        self.assertEqual(report["cash_diff"], 160000.0 - self.EXPECTED_CASH)

    def test_exactly_counted_cash_gives_zero_difference(self):
        """Kassir to'g'ri sanasa (qaytim berilgan, qaytarish va harakatlar bilan) farq 0."""
        with self.as_user(MANAGER):
            report = cashier_api.close_shift(json.dumps({self.cash_mode: self.EXPECTED_CASH}))["z_report_data"]
        self.assertEqual(report["cash_diff"], 0.0)

    def test_both_paths_and_the_report_agree(self):
        desktop = desktop_pos.getPosClosingData(self.shift)
        expected = next(row["expected_amount"] for row in desktop["reconciliation"]
                        if row["mode_of_payment"] == self.cash_mode)
        report = self.expected_in_report()

        self.assertEqual(expected, float(self.EXPECTED_CASH))
        self.assertEqual(report, expected)

    def expected_in_report(self):
        return cashier_api.get_shift_report("X")["cash"]["expected"]

    def test_real_overpaid_invoice_nets_to_its_payable_amount(self):
        """Dev bazadagi HAQIQIY qaytimli chek: naqd qator - qaytim = to'langan summa."""
        row = frappe.db.sql(
            """
            select name, change_amount, rounded_total, grand_total from `tabPOS Invoice`
            where docstatus = 1 and is_return = 0 and change_amount > 0 limit 1
            """,
            as_dict=True,
        )
        if not row:
            self.skipTest("Dev bazada qaytimli chek yo'q")
        row = row[0]
        invoice = frappe._dict(
            change_amount=row.change_amount,
            payments=pos_closing.bulk_children(
                "Sales Invoice Payment", [row.name], ["mode_of_payment", "amount", "type"]
            )[row.name],
        )

        paid = sum(amount for _mode, amount in pos_closing.net_payments(invoice))

        self.assertEqual(paid, row.rounded_total or row.grand_total)


class TestBillJobHousekeeping(ShiftTestCase):
    """Hisob cheki chiqqach «qayta chop etish kerak» belgisi o'chadi; boshqa turlarda emas."""

    def setUp(self):
        super().setUp()
        self.invoice = frappe.db.get_value("POS Invoice", {"docstatus": ["in", [0, 1]]}, "name")
        if not self.invoice or not frappe.db.has_column("POS Invoice", "custom_reprint_needed"):
            self.skipTest("Chek yoki custom_reprint_needed maydoni yo'q")
        frappe.db.set_value("POS Invoice", self.invoice, "custom_reprint_needed", 1)

    def flag(self):
        return frappe.db.get_value("POS Invoice", self.invoice, "custom_reprint_needed")

    def test_bill_job_clears_the_flag(self):
        self.add_printer()
        self.assertTrue(print_queue.enqueue_bill(self.invoice, self.scope))
        self.assertEqual(self.flag(), 0)

    def test_no_printer_means_no_job_and_the_flag_stays(self):
        with patch.object(print_queue, "cashier_printer", return_value=None):
            self.assertIsNone(print_queue.enqueue_bill(self.invoice, self.scope))
        self.assertEqual(self.flag(), 1)

    def test_drawer_and_report_jobs_leave_the_flag_alone(self):
        self.open_shift()
        self.add_printer()
        print_queue.kick_drawer(self.scope, reason="x", invoice=self.invoice)
        print_queue.enqueue_shift_report(shift_report.build_report("X", self.scope), self.scope)
        self.assertEqual(self.flag(), 1)


class TestEndpointHygiene(FrappeTestCase):
    def test_mutating_endpoints_accept_post_only(self):
        """Pul/g'aladon/chop etishga tegadigan metodlarni GET (CSRF'siz) bilan chaqirib bo'lmasin."""
        for fn in (printing.open_drawer, printing.print_shift_report, cash_api.create_cash_movement):
            self.assertIn(fn, frappe.whitelisted)
            self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[fn], ["POST"])

    def test_read_endpoints_are_whitelisted(self):
        for fn in (cashier_api.get_shift_report, cash_api.get_cash_movements):
            self.assertIn(fn, frappe.whitelisted)


class TestCashModesSingleSource(ShiftTestCase):
    def test_cashier_api_delegates_to_billing(self):
        with patch.object(cashier_billing, "cash_modes", return_value=["Sinov"]):
            self.assertEqual(cashier_api._cash_modes(self.profile), ["Sinov"])
        self.assertEqual(cashier_api._cash_modes(self.profile), cashier_billing.cash_modes(self.profile))
