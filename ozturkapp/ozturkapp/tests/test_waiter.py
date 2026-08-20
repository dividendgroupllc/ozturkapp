# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Ofitsant mobil ilovasi API testlari.

    bench --site ozturk.local run-tests \
        --module ozturkapp.ozturkapp.tests.test_waiter
"""

import inspect

import frappe
from frappe.tests.utils import FrappeTestCase

from ozturkapp.ozturkapp.api import waiter
from ozturkapp.ozturkapp.setup.waiter_setup import WAITER_ROLE
from ozturkapp.ozturkapp.utils import cashier_permissions, kitchen_status as ks


class TestWaiterUsesExistingUryFlow(FrappeTestCase):
    """Ikkinchi buyurtma tizimi YARATILMAGAN."""

    def test_submit_order_delegates_to_ury_sync_order(self):
        source = inspect.getsource(waiter.submit_order)
        self.assertIn("sync_order", source)
        # O'zi POS Invoice yaratmasligi kerak.
        self.assertNotIn('frappe.new_doc("POS Invoice")', source)

    def test_menu_comes_from_ury(self):
        source = inspect.getsource(waiter.get_menu)
        self.assertIn("getRestaurantMenu", source)

    def test_no_duplicate_order_doctypes(self):
        for name in ("Waiter Order", "Mobile Order", "Restaurant Order"):
            self.assertFalse(frappe.db.exists("DocType", name), f"dublikat: {name}")


class TestWaiterMoneyRules(FrappeTestCase):
    """Ilova ham, bu API ham pul HISOBLAMAYDI (TZ §8/#5)."""

    def test_api_never_computes_totals(self):
        source = inspect.getsource(waiter)
        for forbidden in ("* 1.12", "* 0.12", "1.12 *", "grand_total ="):
            self.assertNotIn(forbidden, source, f"moliyaviy hisob topildi: {forbidden}")

    def test_client_supplied_rate_is_ignored(self):
        """Mijoz narx yubora olmaydi — narx serverda olinadi (TZ §12)."""
        source = inspect.getsource(waiter._parse_items)
        self.assertNotIn('"rate"', source)
        self.assertNotIn("'rate'", source)


class TestWaiterCancellationRule(FrappeTestCase):
    """TZ §8 — tayyorlash boshlangach o'zgartirib bo'lmaydi."""

    def test_rule_delegates_to_single_source(self):
        source = inspect.getsource(waiter._assert_removals_allowed)
        self.assertIn("get_item_statuses_for_invoice", source)
        self.assertIn("can_waiter_cancel", source)

    def test_adding_items_is_always_allowed(self):
        """Qo'shish hech qachon bloklanmaydi — faqat kamaytirish/olib tashlash."""
        source = inspect.getsource(waiter._assert_removals_allowed)
        self.assertIn("if new_qty >= old_qty:", source)
        self.assertIn("continue", source)

    def test_kitchen_status_is_source_of_truth(self):
        self.assertTrue(ks.can_waiter_cancel(ks.PENDING))
        for status in (ks.PREPARING, ks.READY, ks.SERVED):
            self.assertFalse(ks.can_waiter_cancel(status))


class TestWaiterOfflineIdempotency(FrappeTestCase):
    """TZ §11 — oflayn qayta yuborish dublikat yaratmaydi."""

    def test_client_ref_short_circuits(self):
        source = inspect.getsource(waiter.submit_order)
        self.assertIn("custom_client_ref", source)
        self.assertIn("client_ref", source)

    def test_client_ref_field_is_unique(self):
        unique = frappe.db.get_value(
            "Custom Field",
            {"dt": "POS Invoice", "fieldname": "custom_client_ref"},
            "unique",
        )
        self.assertTrue(unique, "custom_client_ref unique bo'lishi kerak")


class TestWaiterPermissions(FrappeTestCase):
    """TZ §12 — ofitsant to'lov/narx/buxgalteriyaga tegmaydi."""

    def test_waiter_role_exists(self):
        self.assertTrue(frappe.db.exists("Role", WAITER_ROLE))

    def test_waiter_cannot_submit_pos_invoice(self):
        row = frappe.db.get_value(
            "Custom DocPerm",
            {"parent": "POS Invoice", "role": WAITER_ROLE},
            ["read", "write", "create", "submit", "cancel"],
            as_dict=True,
        )
        self.assertIsNotNone(row)
        self.assertTrue(row.read)
        self.assertTrue(row.create)
        self.assertFalse(row.submit, "ofitsant chekni submit qila olmasligi kerak")
        self.assertFalse(row.cancel, "ofitsant chekni bekor qila olmasligi kerak")

    def test_waiter_has_no_payment_or_accounting_access(self):
        for doctype in ("Payment Entry", "Journal Entry", "GL Entry", "Sales Invoice"):
            perm = frappe.db.get_value(
                "Custom DocPerm", {"parent": doctype, "role": WAITER_ROLE}, "name"
            )
            self.assertIsNone(perm, f"ofitsant '{doctype}' ga ruxsat olmasligi kerak")

    def test_item_price_is_read_only(self):
        row = frappe.db.get_value(
            "Custom DocPerm",
            {"parent": "Item Price", "role": WAITER_ROLE},
            ["read", "write"],
            as_dict=True,
        )
        if row:
            self.assertTrue(row.read)
            self.assertFalse(row.write, "ofitsant narx o'zgartira olmasligi kerak")

    def test_user_without_role_is_rejected(self):
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": "waiter-test-nobody@example.com",
                "first_name": "Ruxsatsiz",
                "send_welcome_email": 0,
            }
        ).insert(ignore_permissions=True)

        frappe.set_user(user.name)
        try:
            with self.assertRaises(frappe.PermissionError):
                waiter.require_waiter()
        finally:
            frappe.set_user("Administrator")


class TestWaiterBillRequest(FrappeTestCase):
    """TZ §7.8 — hisob so'rash to'lov EMAS."""

    def test_request_bill_does_not_submit(self):
        source = inspect.getsource(waiter.request_bill)
        self.assertNotIn(".submit(", source)
        self.assertNotIn("payments", source)
        self.assertIn("custom_bill_requested", source)

    def test_bill_fields_exist(self):
        for fieldname in (
            "custom_bill_requested",
            "custom_bill_requested_at",
            "custom_bill_requested_by",
        ):
            self.assertTrue(
                frappe.db.exists(
                    "Custom Field", {"dt": "POS Invoice", "fieldname": fieldname}
                ),
                f"'{fieldname}' maydoni yo'q",
            )


class TestWaiterShiftGate(FrappeTestCase):
    """Kassa yopiq bo'lsa ilova bloklovchi oyna ko'rsatadi.

    Oyna realtime bilan yo'qoladi — ofitsant ilovani qayta ochmaydi.
    """

    def test_context_reports_shift_state(self):
        source = inspect.getsource(waiter.get_context)
        self.assertIn('"shift"', source)
        self.assertIn("open_shift_name", source)

    def test_context_exposes_shift_and_menu_channels(self):
        source = inspect.getsource(waiter.get_context)
        self.assertIn('"shift": "ozturk_shift"', source)
        self.assertIn('"menu": "ozturk_menu"', source)

    def test_light_endpoint_exists(self):
        """Xabar kelganda butun kontekstni qayta yuklash shart emas."""
        self.assertTrue(hasattr(waiter, "get_shift_state"))
        self.assertIn("require_waiter", inspect.getsource(waiter.get_shift_state))

    def test_shift_signal_fires_from_the_document_itself(self):
        """Hook manbaga osilgan — kassa sahifasi ham, Desktop POS ham qamraladi."""
        events = frappe.get_hooks("doc_events")
        self.assertIn("on_submit", events.get("POS Opening Entry", {}))
        self.assertIn("on_submit", events.get("POS Closing Entry", {}))

    def test_shift_payload_carries_no_money(self):
        """Xabar sayt xonasiga ketadi — faqat identifikator tashiydi."""
        from ozturkapp.ozturkapp.utils import cashier_realtime

        source = inspect.getsource(cashier_realtime.emit_shift_change)
        for leak in ("amount", "grand_total", "expected", "user"):
            with self.subTest(leak=leak):
                self.assertNotIn(f'"{leak}"', source)


class TestMenuRealtime(FrappeTestCase):
    """Menyu o'zgarganda ilova xabardor bo'ladi."""

    def test_manual_menu_edit_is_hooked(self):
        self.assertIn("on_update", frappe.get_hooks("doc_events").get("URY Menu", {}))

    def test_dynamic_pricing_emits_too(self):
        """Narx `set_value(update_modified=False)` bilan yoziladi — hujjat
        hodisasi uyg'onmaydi, shuning uchun signal qo'lda yuboriladi."""
        from ozturkapp.ozturkapp.api import dynamic_pricing

        source = inspect.getsource(dynamic_pricing)
        self.assertGreaterEqual(source.count("_emit_menu_change(branch)"), 2)


class TestWaiterCancelsWholeOrder(FrappeTestCase):
    """Ofitsant xato olgan buyurtmani BUTUNLAY bekor qiladi (TZ §8).

    Qoida bitta taomni olib tashlash bilan bir xil manbadan olinadi
    (`utils/kitchen_status.py`): oshxona ishga kirishmaguncha mumkin,
    kirishgandan keyin yo'q.
    """

    def setUp(self):
        self.scope = cashier_permissions.resolve_scope()
        self.branch = self.scope.branch
        self.invoices, self.kots = [], []

        # Testlar Administrator nomidan ketadi — u menejer, ya'ni
        # «majburan bekor qilish» huquqiga ega. Ofitsantda bunday huquq
        # yo'q, shuning uchun rol tekshiruvini almashtiramiz.
        self._real_supervisor = cashier_permissions.has_supervisor_role
        cashier_permissions.has_supervisor_role = lambda user=None: False

    def tearDown(self):
        cashier_permissions.has_supervisor_role = self._real_supervisor
        for kot in self.kots:
            frappe.db.delete("URY KOT Items", {"parent": kot})
            frappe.db.delete("URY KOT", {"name": kot})
        for name in self.invoices:
            frappe.db.delete("POS Invoice", {"name": name})

    # ── Fikstura ──────────────────────────────────────────────────────

    def _draft(self, printed=0):
        name = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabPOS Invoice`
                (name, creation, modified, owner, modified_by, docstatus,
                 branch, invoice_printed, custom_cancelled, waiter)
            values (%s, now(), now(), 'Administrator', 'Administrator', 0,
                 %s, %s, 0, 'Administrator')
            """,
            (name, self.branch, printed),
        )
        self.invoices.append(name)
        return name

    def _kot(self, invoice, statuses):
        kot = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabURY KOT`
                (name, creation, modified, owner, modified_by, docstatus,
                 invoice, branch, type, order_status)
            values (%s, now(), now(), 'Administrator', 'Administrator', 1,
                 %s, %s, 'New Order', 'Ready For Prepare')
            """,
            (kot, invoice, self.branch),
        )
        self.kots.append(kot)

        for idx, status in enumerate(statuses, start=1):
            frappe.db.sql(
                """
                insert into `tabURY KOT Items`
                    (name, creation, modified, owner, modified_by, docstatus,
                     parent, parenttype, parentfield, idx, item, quantity,
                     custom_kitchen_status)
                values (%s, now(), now(), 'Administrator', 'Administrator', 1,
                     %s, 'URY KOT', 'kot_items', %s, %s, 1, %s)
                """,
                (frappe.generate_hash(length=10), kot, idx, f"TEST-TAOM-{idx}", status),
            )
        return kot

    # ── Qoida ─────────────────────────────────────────────────────────

    def test_endpoint_exists(self):
        """Ilova aynan shu metodni chaqiradi."""
        self.assertTrue(callable(getattr(waiter, "cancel_order", None)))

    def test_waiter_may_cancel_while_everything_is_pending(self):
        invoice = self._draft()
        kot = self._kot(invoice, [ks.PENDING, ks.PENDING])

        result = waiter.cancel_order(invoice, "Ofitsant xato stolga yozdi")

        self.assertEqual(result["cancelled_items"], 2)
        self.assertEqual(
            frappe.db.get_value("POS Invoice", invoice, "custom_cancelled"), 1
        )
        self.assertEqual(
            frappe.db.get_value("POS Invoice", invoice, "cancel_reason"),
            "Ofitsant xato stolga yozdi",
        )
        # Oshxona chiptasi ham yopilishi kerak — aks holda oshpaz bekor
        # qilingan buyurtmani tayyorlab yuboradi.
        self.assertNotEqual(
            frappe.db.get_value("URY KOT", kot, "order_status"), "Ready For Prepare"
        )

    def test_order_without_a_kot_can_still_be_cancelled(self):
        """KOT yaratilmagan bo'lsa ham (oshxona sozlanmagan) bekor bo'ladi."""
        invoice = self._draft()

        waiter.cancel_order(invoice, "xato zakaz")

        self.assertEqual(
            frappe.db.get_value("POS Invoice", invoice, "custom_cancelled"), 1
        )

    def test_waiter_may_not_cancel_once_the_kitchen_started(self):
        invoice = self._draft()
        self._kot(invoice, [ks.PENDING, ks.PREPARING])

        with self.assertRaises(frappe.ValidationError):
            waiter.cancel_order(invoice, "fikrimdan qaytdim")

        self.assertEqual(
            frappe.db.get_value("POS Invoice", invoice, "custom_cancelled"), 0
        )

    def test_billed_order_cannot_be_cancelled(self):
        """Hisob mijozga chiqarilgan bo'lsa — bu kassirning ishi."""
        invoice = self._draft(printed=1)
        self._kot(invoice, [ks.PENDING])

        with self.assertRaises(frappe.ValidationError):
            waiter.cancel_order(invoice, "kech qoldi")

        self.assertEqual(
            frappe.db.get_value("POS Invoice", invoice, "custom_cancelled"), 0
        )

    def test_reason_is_required(self):
        invoice = self._draft()

        for empty in ("", "   "):
            with self.assertRaises(frappe.ValidationError):
                waiter.cancel_order(invoice, empty)

        self.assertEqual(
            frappe.db.get_value("POS Invoice", invoice, "custom_cancelled"), 0
        )

    def test_the_same_rule_governs_removing_a_single_item(self):
        """Bitta taomni olib tashlash ham, butun buyurtma ham — bir manba."""
        removal = inspect.getsource(waiter._assert_removals_allowed)
        cancel = inspect.getsource(waiter.cancel_order)

        self.assertIn("can_waiter_cancel", removal)
        self.assertIn("order_cancel", cancel)


class TestWaiterAppKnowsWhenToShowTheButton(FrappeTestCase):
    """Ilova qoidani O'ZI HISOBLAMAYDI — server aytadi (TZ §8)."""

    def test_get_order_returns_the_cancellation_block(self):
        source = inspect.getsource(waiter.get_order)
        self.assertIn("build_bill", source)

        stripped = inspect.getsource(waiter._strip_financials)
        # Pul maydonlari olib tashlanadi, LEKIN qoidalar qolishi kerak —
        # aks holda ilova tugmani chizolmaydi.
        self.assertNotIn("cancellation", stripped)
        self.assertNotIn("kitchen", stripped)

    def test_item_level_flag_is_still_exposed(self):
        source = inspect.getsource(ks.get_item_statuses_for_invoice)
        self.assertIn("can_waiter_cancel", source)
