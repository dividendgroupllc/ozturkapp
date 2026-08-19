# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Oshxona KDS testlari (Phase 3).

Ishga tushirish::

    bench --site ozturk.local run-tests \
        --module ozturkapp.ozturkapp.tests.test_kitchen
"""

import re

import frappe
from frappe.tests.utils import FrappeTestCase

from ozturkapp.ozturkapp.utils import kitchen_status as ks


class TestKitchenStateMachine(FrappeTestCase):
    """TZ §5, §14, §23 — o'tish qoidalari."""

    def test_allowed_forward_transitions(self):
        ks.assert_transition(ks.PENDING, ks.PREPARING)
        ks.assert_transition(ks.PREPARING, ks.READY)
        ks.assert_transition(ks.READY, ks.SERVED)
        ks.assert_transition(ks.PENDING, ks.CANCELLED)

    def test_preparing_cannot_be_cancelled(self):
        """TZ §14 — tayyorlash boshlangach bekor qilish MUMKIN EMAS."""
        with self.assertRaises(ks.InvalidTransition):
            ks.assert_transition(ks.PREPARING, ks.CANCELLED)

    def test_ready_and_served_cannot_be_cancelled(self):
        for current in (ks.READY, ks.SERVED):
            with self.assertRaises(ks.InvalidTransition):
                ks.assert_transition(current, ks.CANCELLED)

    def test_skipping_states_is_rejected(self):
        with self.assertRaises(ks.InvalidTransition):
            ks.assert_transition(ks.PENDING, ks.READY)
        with self.assertRaises(ks.InvalidTransition):
            ks.assert_transition(ks.PENDING, ks.SERVED)
        with self.assertRaises(ks.InvalidTransition):
            ks.assert_transition(ks.PREPARING, ks.SERVED)

    def test_backward_transitions_are_rejected(self):
        with self.assertRaises(ks.InvalidTransition):
            ks.assert_transition(ks.READY, ks.PREPARING)
        with self.assertRaises(ks.InvalidTransition):
            ks.assert_transition(ks.PREPARING, ks.PENDING)

    def test_repeating_current_state_is_rejected(self):
        """Ikkinchi oshpaz bir vaqtda bosgan bo'lishi mumkin (TZ §24)."""
        with self.assertRaises(ks.InvalidTransition):
            ks.assert_transition(ks.PREPARING, ks.PREPARING)

    def test_unknown_status_is_rejected(self):
        with self.assertRaises(ks.InvalidTransition):
            ks.assert_transition(ks.PENDING, "Burned")

    def test_terminal_states_have_no_exit(self):
        self.assertEqual(ks.TRANSITIONS[ks.SERVED], ())
        self.assertEqual(ks.TRANSITIONS[ks.CANCELLED], ())

    def test_null_status_is_treated_as_pending(self):
        """Custom field qo'shilishidan oldingi qatorlar `NULL` bo'ladi."""
        self.assertEqual(ks.normalize(None), ks.PENDING)
        self.assertEqual(ks.normalize(""), ks.PENDING)
        self.assertEqual(ks.normalize("Nonsense"), ks.PENDING)


class TestWaiterCancellationRule(FrappeTestCase):
    """TZ §14 — kelajakdagi Ofitsant ilovasi uchun yagona qoida."""

    def test_only_pending_is_cancellable(self):
        self.assertTrue(ks.can_waiter_cancel(ks.PENDING))
        self.assertTrue(ks.can_waiter_cancel(None))  # -> Pending

        for status in (ks.PREPARING, ks.READY, ks.SERVED, ks.CANCELLED):
            self.assertFalse(
                ks.can_waiter_cancel(status),
                f"'{status}' holatida bekor qilish taqiqlanishi kerak",
            )

    def test_no_generic_cancel_bypass_exists(self):
        """TZ §14 — umumiy `item.cancelled = True` amali BO'LMASLIGI kerak."""
        import inspect

        from ozturkapp.ozturkapp.api import kitchen

        source = inspect.getsource(kitchen)
        # Har qanday holat yozuvi `assert_transition` dan o'tishi shart.
        self.assertIn("assert_transition", source)
        self.assertNotIn("cancelled = True", source)
        self.assertNotIn('"cancelled": 1', source)


class TestDerivedKotStatus(FrappeTestCase):
    """TZ §11 — KOT holati mahsulotlardan keltiriladi."""

    def test_all_pending(self):
        self.assertEqual(ks.derive_kot_status([ks.PENDING, ks.PENDING]), ks.PENDING)

    def test_any_preparing(self):
        self.assertEqual(
            ks.derive_kot_status([ks.PENDING, ks.PREPARING]), ks.PREPARING
        )

    def test_all_ready(self):
        self.assertEqual(ks.derive_kot_status([ks.READY, ks.READY]), ks.READY)

    def test_all_served(self):
        self.assertEqual(ks.derive_kot_status([ks.SERVED, ks.SERVED]), ks.SERVED)

    def test_mixed_ready_and_pending_counts_as_working(self):
        self.assertEqual(ks.derive_kot_status([ks.READY, ks.PENDING]), ks.PREPARING)

    def test_cancelled_items_are_ignored(self):
        self.assertEqual(
            ks.derive_kot_status([ks.CANCELLED, ks.SERVED, ks.SERVED]), ks.SERVED
        )

    def test_all_cancelled(self):
        self.assertEqual(ks.derive_kot_status([ks.CANCELLED]), ks.CANCELLED)

    def test_empty_kot(self):
        self.assertEqual(ks.derive_kot_status([]), ks.CANCELLED)


class TestKitchenSetup(FrappeTestCase):
    """TZ §21, §22 — custom fieldlar, dublikat DocType yo'q."""

    def test_item_status_fields_exist_and_allow_on_submit(self):
        """KOT submit qilingan — bu maydonlar `allow_on_submit` bo'lishi SHART."""
        for fieldname in (
            "custom_kitchen_status",
            "custom_started_at",
            "custom_ready_at",
            "custom_served_at",
            "custom_status_changed_by",
        ):
            row = frappe.db.get_value(
                "Custom Field",
                {"dt": "URY KOT Items", "fieldname": fieldname},
                ["fieldname", "allow_on_submit"],
                as_dict=True,
            )
            self.assertIsNotNone(row, f"'{fieldname}' maydoni yo'q")
            self.assertTrue(
                row.allow_on_submit,
                f"'{fieldname}' `allow_on_submit` bo'lishi kerak — "
                "aks holda submit qilingan KOT'da o'zgarmaydi",
            )

    def test_no_duplicate_kitchen_doctypes_created(self):
        """TZ §22 — Kitchen Order / Kitchen Item / KOT dublikatlari yo'q."""
        for name in (
            "Kitchen Order",
            "Kitchen Item",
            "Restaurant Kitchen Order",
            "Kitchen Status",
            "Custom KOT",
        ):
            self.assertFalse(
                frappe.db.exists("DocType", name),
                f"dublikat DocType yaratilgan: {name}",
            )

    def test_kitchen_role_exists(self):
        from ozturkapp.ozturkapp.setup.kitchen_setup import KITCHEN_ROLE

        self.assertTrue(frappe.db.exists("Role", KITCHEN_ROLE))

    def test_kitchen_role_has_no_financial_access(self):
        """TZ §15, §27 — oshxona narx/to'lov/buxgalteriyaga tegmaydi."""
        from ozturkapp.ozturkapp.setup.kitchen_setup import KITCHEN_ROLE

        for doctype in (
            "POS Invoice", "Sales Invoice", "Payment Entry",
            "Item Price", "POS Profile", "Customer",
        ):
            perm = frappe.db.get_value(
                "Custom DocPerm",
                {"parent": doctype, "role": KITCHEN_ROLE},
                "name",
            )
            self.assertIsNone(
                perm, f"oshxona roli '{doctype}' ga ruxsat olmasligi kerak"
            )

    def test_kitchen_role_cannot_submit_or_cancel_kot(self):
        from ozturkapp.ozturkapp.setup.kitchen_setup import KITCHEN_ROLE

        row = frappe.db.get_value(
            "Custom DocPerm",
            {"parent": "URY KOT", "role": KITCHEN_ROLE},
            ["read", "write", "submit", "cancel", "create", "delete"],
            as_dict=True,
        )
        self.assertIsNotNone(row)
        self.assertTrue(row.read)
        self.assertTrue(row.write)
        self.assertFalse(row.submit, "oshxona KOT submit qila olmasligi kerak")
        self.assertFalse(row.cancel, "oshxona KOT bekor qila olmasligi kerak")
        self.assertFalse(row.create, "oshxona KOT yarata olmasligi kerak")


class TestUryCompatibility(FrappeTestCase):
    """TZ §18/#18, #19 — mavjud URY KOT va Mosaic buzilmaydi."""

    def test_ury_order_status_values_are_preserved(self):
        """Mosaic `order_status == "Ready For Prepare"` ga tayanadi."""
        import inspect

        from ozturkapp.ozturkapp.api import kitchen

        source = inspect.getsource(kitchen._sync_kot_order_status)
        self.assertIn("Ready For Prepare", source)
        self.assertIn("Served", source)

    def test_kot_generation_module_is_untouched(self):
        """TZ §2 — URY'ning KOT yaratish mantig'iga tegilmagan."""
        import ury.ury.api.ury_kot_generate as gen

        self.assertTrue(hasattr(gen, "kot_execute"))
        self.assertTrue(hasattr(gen, "process_items_for_kot"))
        self.assertTrue(hasattr(gen, "create_kot_doc"))

    def test_mosaic_kds_api_still_available(self):
        import ury.ury.api.ury_kot_display as display

        for fn in ("kot_list", "served_kot_list", "serve_kot", "confirm_cancel_kot"):
            self.assertTrue(hasattr(display, fn), f"URY '{fn}' yo'qolgan")

    def test_printer_flow_untouched(self):
        from ury.ury.doctype.ury_kot.ury_kot import URYKOT

        self.assertTrue(hasattr(URYKOT, "multi_print_kot"))
        self.assertTrue(hasattr(URYKOT, "kotDisplayRealtime"))


class TestKitchenPageAssets(FrappeTestCase):
    """Sahifa resurslari — apostrof xatosi qaytmasligi uchun."""

    PAGE = "restaurant-kitchen"

    def _assets(self):
        page = frappe.get_doc("Page", self.PAGE)
        page.load_assets()
        return page

    def test_html_template_has_no_unescaped_apostrophe(self):
        page = self._assets()
        match = re.search(
            r"frappe\.templates\[\"restaurant_kitchen\"\] = '(.*)';", page.script
        )
        self.assertIsNotNone(match, "HTML shabloni skriptga qo'shilmagan")
        self.assertNotIn(
            "'",
            match.group(1),
            "HTML shablonida xom apostrof bor — `&#39;` ishlating",
        )

    def test_page_exposes_entry_points(self):
        script = self._assets().script
        self.assertRegex(script, r"frappe\.pages\[.restaurant-kitchen.\]\.on_page_load")
        self.assertIn("ozturk.kitchen.Screen", script)

    def test_page_style_is_loaded(self):
        style = self._assets().style or ""
        self.assertIn(".kds-root", style)
        self.assertIn(".kds-badge--Preparing", style)

    def test_page_has_no_order_or_payment_endpoints(self):
        """TZ §27 — oshxona buyurtma/to'lov bilan ishlamaydi."""
        script = self._assets().script

        for needle in ("sync_order", "submit_payment", "open_bill", "seat_table", "make_invoice"):
            self.assertNotIn(needle, script, f"oshxona sahifasida '{needle}' bo'lmasligi kerak")

    def test_next_status_is_not_computed_in_javascript(self):
        """Tugma server bergan `next` dan quriladi (TZ §23)."""
        script = self._assets().script
        self.assertIn("item.next", script)
        # JS o'zi o'tish jadvalini saqlamasligi kerak.
        self.assertNotIn("TRANSITIONS", script)


class TestKitchenPermissionGate(FrappeTestCase):
    """TZ §15 — rolsiz foydalanuvchi kira olmaydi."""

    def test_user_without_kitchen_role_is_rejected(self):
        from ozturkapp.ozturkapp.api.kitchen import require_kitchen

        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": "kds-test-nobody@example.com",
                "first_name": "Ruxsatsiz",
                "send_welcome_email": 0,
            }
        ).insert(ignore_permissions=True)

        frappe.set_user(user.name)
        try:
            with self.assertRaises(frappe.PermissionError):
                require_kitchen()
        finally:
            frappe.set_user("Administrator")

    def test_manager_is_accepted(self):
        from ozturkapp.ozturkapp.api.kitchen import require_kitchen

        frappe.set_user("Administrator")
        require_kitchen()  # xato bermasligi kerak
