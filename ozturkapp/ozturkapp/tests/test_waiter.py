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
from ozturkapp.ozturkapp.utils import kitchen_status as ks


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
