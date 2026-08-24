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
from frappe.utils import cint, flt

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


class TestProductionUnitSetup(FrappeTestCase):
    """`URY Production Unit` bo'lmasa KOT UMUMAN yaratilmaydi.

    URY xatoni `frappe.log_error()` bilan yutib yuboradi
    (`ury_order.py:950`) — buyurtma yaratilgandek ko'rinadi, lekin
    oshxonaga tushmaydi. Shuning uchun sozlash alohida tekshiriladi.
    """

    def test_drink_groups_go_to_bar(self):
        from ozturkapp.ozturkapp.setup import kitchen_setup as kset

        for group in ("Напитки", "Drinks", "Ichimliklar", "Бар", "bar"):
            with self.subTest(group=group):
                self.assertTrue(kset._is_drink(group))

    def test_food_groups_stay_in_kitchen(self):
        from ozturkapp.ozturkapp.setup import kitchen_setup as kset

        # "Barbecue" ichida "bar" bor — qisqa so'z FAQAT to'liq moslikda
        # tekshirilishi kerak, aks holda taom barga ketardi.
        for group in ("Barbecue", "Супы", "Хлеб", "Кебаб и Мясные блюда"):
            with self.subTest(group=group):
                self.assertFalse(kset._is_drink(group))

    def test_setup_is_idempotent(self):
        """Qayta ishga tushirish dublikat nuqta yaratmaydi."""
        from ozturkapp.ozturkapp.setup import kitchen_setup as kset

        profiles = frappe.get_all("POS Profile", pluck="name")
        if len(profiles) != 1:
            self.skipTest("Saytda bitta POS Profile bo'lishi kerak")

        before = frappe.db.count("URY Production Unit", {"pos_profile": profiles[0]})
        kset.create_production_units(profiles[0])
        after = frappe.db.count("URY Production Unit", {"pos_profile": profiles[0]})
        self.assertEqual(before, after)


class TestSelfServiceStation(FrappeTestCase):
    """Bar (o'zi olib boriladi) — soddalashtirilgan ikki bosqichli oqim.

    Ichimlikni oshxona tayyorlamaydi: ofitsant barga borib oladi va
    mijozga eltadi. "Tayyorlanmoqda -> Tayyor" bosqichlari real hayotda
    hech qachon bosilmaydi, shuning uchun mahsulot abadiy "Kutilmoqda" da
    osilib qolardi.
    """

    def test_two_step_flow(self):
        from ozturkapp.ozturkapp.utils import kitchen_status as k

        k.assert_transition(k.PENDING, k.SERVED, self_service=True)  # ruxsat
        with self.assertRaises(k.InvalidTransition):
            k.assert_transition(k.PENDING, k.PREPARING, self_service=True)

    def test_kitchen_flow_is_unchanged(self):
        """Oshxona oqimiga TEGILMAGAN — u hamon to'rt bosqichli."""
        from ozturkapp.ozturkapp.utils import kitchen_status as k

        k.assert_transition(k.PENDING, k.PREPARING)
        with self.assertRaises(k.InvalidTransition):
            k.assert_transition(k.PENDING, k.SERVED)

    def test_waiter_cannot_deliver_kitchen_item(self):
        """Ofitsant oshxona taomini «berildi» deb belgilay olmaydi."""
        import inspect

        from ozturkapp.ozturkapp.api import waiter

        source = inspect.getsource(waiter.mark_delivered)
        self.assertIn("self_service_stations", source)
        self.assertIn("oshxona tayyorlaydi", source)

    def test_self_service_station_hidden_from_kitchen(self):
        """Bar KOT'lari oshxona ekranida ko'rinmaydi — stansiya tanlanmasa ham."""
        import inspect

        from ozturkapp.ozturkapp.api import kitchen

        self.assertIn("self_service_stations", inspect.getsource(kitchen.get_kitchen_context))
        self.assertIn("self_service_stations", inspect.getsource(kitchen.get_active_kots))

    def test_missing_column_is_tolerated(self):
        """Maydon yaratilmagan saytda ham yiqilmasligi kerak."""
        from ozturkapp.ozturkapp.utils import kitchen_status as k

        self.assertIsInstance(k.self_service_stations(), set)


class TestKitchenStationRestriction(FrappeTestCase):
    """Har oshpaz FAQAT o'ziga biriktirilgan stansiyani ko'rsin.

    MUAMMO: stansiya avval faqat brauzer sozlamasi edi (localStorage),
    standart holat "barcha stansiyalar" — bitta oshpazga (masalan faqat
    non pishiruvchi) boshqa stansiya taomlari ham birdek kelib turardi.
    `User.custom_kitchen_station` endi SERVERDA majburlanadi.
    """

    def setUp(self):
        from ozturkapp.ozturkapp.utils import cashier_permissions

        self.branch = cashier_permissions.resolve_scope().branch
        self.invoice = frappe.generate_hash(length=10)
        self.kots = []
        self.users = []

        frappe.db.sql(
            """
            insert into `tabPOS Invoice`
                (name, creation, modified, owner, modified_by, docstatus,
                 branch, invoice_printed, custom_cancelled)
            values (%s, now(), now(), 'Administrator', 'Administrator', 0, %s, 0, 0)
            """,
            (self.invoice, self.branch),
        )

    def tearDown(self):
        frappe.set_user("Administrator")
        for kot in self.kots:
            frappe.db.delete("URY KOT Items", {"parent": kot})
            frappe.db.delete("URY KOT", {"name": kot})
        frappe.db.delete("POS Invoice", {"name": self.invoice})
        for user in self.users:
            frappe.delete_doc("User", user, force=True, ignore_permissions=True)

    # ── Fikstura ──────────────────────────────────────────────────────

    def _kot(self, production, items):
        """`items` = [(item, status)]."""
        kot = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabURY KOT`
                (name, creation, modified, owner, modified_by, docstatus,
                 invoice, branch, type, order_status, production, verified)
            values (%s, now(), now(), 'Administrator', 'Administrator', 1,
                 %s, %s, 'New Order', 'Ready For Prepare', %s, 0)
            """,
            (kot, self.invoice, self.branch, production),
        )
        self.kots.append(kot)

        for idx, (item, status) in enumerate(items, start=1):
            frappe.db.sql(
                """
                insert into `tabURY KOT Items`
                    (name, creation, modified, owner, modified_by, docstatus,
                     parent, parenttype, parentfield, idx, item, item_name,
                     quantity, cancelled_qty, custom_kitchen_status)
                values (%s, now(), now(), 'Administrator', 'Administrator', 1,
                     %s, 'URY KOT', 'kot_items', %s, %s, %s, 1, 0, %s)
                """,
                (frappe.generate_hash(length=10), kot, idx, item, item, status),
            )
        return kot

    def _cook(self, station):
        from ozturkapp.ozturkapp.setup.kitchen_setup import KITCHEN_ROLE

        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": f"kds-station-{frappe.generate_hash(length=6)}@example.com",
                "first_name": "Oshpaz",
                "send_welcome_email": 0,
            }
        ).insert(ignore_permissions=True)
        user.add_roles(KITCHEN_ROLE)
        if station is not None:
            frappe.db.set_value("User", user.name, "custom_kitchen_station", station)
        self.users.append(user.name)
        return user.name

    # ── Testlar ───────────────────────────────────────────────────────

    def test_cook_only_sees_own_station(self):
        from ozturkapp.ozturkapp.api import kitchen

        bread = self._kot("Non", [("NON1", "Pending")])
        grill = self._kot("Grill", [("KEBAB1", "Pending")])

        cook = self._cook("Non")
        frappe.set_user(cook)
        try:
            visible = {row["kot"] for row in kitchen.get_active_kots()}
        finally:
            frappe.set_user("Administrator")

        self.assertIn(bread, visible)
        self.assertNotIn(grill, visible)

    def test_station_param_from_client_is_ignored(self):
        """Mijoz `station="Grill"` yuborsa ham serverda e'tiborsiz qoladi."""
        from ozturkapp.ozturkapp.api import kitchen

        grill = self._kot("Grill", [("KEBAB1", "Pending")])
        cook = self._cook("Non")
        frappe.set_user(cook)
        try:
            visible = {row["kot"] for row in kitchen.get_active_kots(station="Grill")}
        finally:
            frappe.set_user("Administrator")

        self.assertNotIn(grill, visible)

    def test_unassigned_cook_sees_nothing(self):
        """Stansiya biriktirilmagan xodimga BO'SH ro'yxat qaytadi, hammasi emas."""
        from ozturkapp.ozturkapp.api import kitchen

        self._kot("Non", [("NON1", "Pending")])
        cook = self._cook(None)
        frappe.set_user(cook)
        try:
            visible = kitchen.get_active_kots()
        finally:
            frappe.set_user("Administrator")

        self.assertEqual(visible, [])

    def test_manager_sees_all_stations(self):
        from ozturkapp.ozturkapp.api import kitchen

        bread = self._kot("Non", [("NON1", "Pending")])
        grill = self._kot("Grill", [("KEBAB1", "Pending")])

        frappe.set_user("Administrator")
        visible = {row["kot"] for row in kitchen.get_active_kots()}
        self.assertIn(bread, visible)
        self.assertIn(grill, visible)

    def test_cook_cannot_change_status_of_other_station_item(self):
        from ozturkapp.ozturkapp.api import kitchen

        grill = self._kot("Grill", [("KEBAB1", "Pending")])
        item = frappe.get_all("URY KOT Items", filters={"parent": grill}, pluck="name")[0]

        cook = self._cook("Non")
        frappe.set_user(cook)
        try:
            with self.assertRaises(frappe.PermissionError):
                kitchen.update_kot_item_status(item, "Preparing")
        finally:
            frappe.set_user("Administrator")

    def test_custom_field_exists_on_user(self):
        row = frappe.db.get_value(
            "Custom Field",
            {"dt": "User", "fieldname": "custom_kitchen_station"},
            ["fieldname", "options"],
            as_dict=True,
        )
        self.assertIsNotNone(row)
        self.assertEqual(row.options, "URY Production Unit")

    def test_missing_column_is_tolerated(self):
        from ozturkapp.ozturkapp.utils import kitchen_status as k

        self.assertIsInstance(k.user_station("Administrator"), str)


class TestPrintOnReadyTicket(FrappeTestCase):
    """Stansiya "Tayyor bo'lganda chek chiqarilsin"ni yoqsa — javobda
    `print_ticket` keladi; o'chirilgan yoki boshqa holatlarda `None`
    (frontend chop etish oynasini shunda ochmaydi).
    """

    def setUp(self):
        from ozturkapp.ozturkapp.utils import cashier_permissions

        self.branch = cashier_permissions.resolve_scope().branch
        self.invoice = frappe.generate_hash(length=10)
        self.kots = []
        self.units = []

        frappe.db.sql(
            """
            insert into `tabPOS Invoice`
                (name, creation, modified, owner, modified_by, docstatus,
                 branch, invoice_printed, custom_cancelled)
            values (%s, now(), now(), 'Administrator', 'Administrator', 0, %s, 0, 0)
            """,
            (self.invoice, self.branch),
        )

    def tearDown(self):
        for kot in self.kots:
            frappe.db.delete("URY KOT Items", {"parent": kot})
            frappe.db.delete("URY KOT", {"name": kot})
        frappe.db.delete("POS Invoice", {"name": self.invoice})
        for unit in self.units:
            frappe.delete_doc(
                "URY Production Unit", unit, force=True, ignore_permissions=True
            )

    def _unit(self, name, print_on_ready):
        frappe.get_doc(
            {
                "doctype": "URY Production Unit",
                "production": name,
                "custom_print_on_ready": 1 if print_on_ready else 0,
            }
        ).insert(ignore_permissions=True)
        self.units.append(name)
        return name

    def _kot_item(self, production):
        kot = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabURY KOT`
                (name, creation, modified, owner, modified_by, docstatus,
                 invoice, branch, type, order_status, production, verified)
            values (%s, now(), now(), 'Administrator', 'Administrator', 1,
                 %s, %s, 'New Order', 'Ready For Prepare', %s, 0)
            """,
            (kot, self.invoice, self.branch, production),
        )
        self.kots.append(kot)

        item = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabURY KOT Items`
                (name, creation, modified, owner, modified_by, docstatus,
                 parent, parenttype, parentfield, idx, item, item_name,
                 quantity, cancelled_qty, custom_kitchen_status)
            values (%s, now(), now(), 'Administrator', 'Administrator', 1,
                 %s, 'URY KOT', 'kot_items', 1, 'NON1', 'Non', 2, 0, 'Pending')
            """,
            (item, kot),
        )
        return item

    def test_ticket_included_when_station_enabled(self):
        from ozturkapp.ozturkapp.api import kitchen

        unit = self._unit("Non Test Station", print_on_ready=True)
        item = self._kot_item(unit)

        kitchen.update_kot_item_status(item, "Preparing")
        result = kitchen.update_kot_item_status(item, "Ready")

        self.assertIsNotNone(result["print_ticket"])
        self.assertEqual(result["print_ticket"]["item_name"], "Non")
        self.assertEqual(flt(result["print_ticket"]["quantity"]), 2)

    def test_ticket_absent_when_station_disabled(self):
        from ozturkapp.ozturkapp.api import kitchen

        unit = self._unit("Grill Test Station", print_on_ready=False)
        item = self._kot_item(unit)

        kitchen.update_kot_item_status(item, "Preparing")
        result = kitchen.update_kot_item_status(item, "Ready")

        self.assertIsNone(result["print_ticket"])

    def test_ticket_not_triggered_for_other_transitions(self):
        """Faqat "Ready"ga o'tganda — "Preparing"da chek chiqmasligi kerak."""
        from ozturkapp.ozturkapp.api import kitchen

        unit = self._unit("Non Test Station 2", print_on_ready=True)
        item = self._kot_item(unit)

        result = kitchen.update_kot_item_status(item, "Preparing")
        self.assertIsNone(result["print_ticket"])

    def test_custom_field_exists_on_production_unit(self):
        row = frappe.db.get_value(
            "Custom Field",
            {"dt": "URY Production Unit", "fieldname": "custom_print_on_ready"},
            ["fieldname", "fieldtype"],
            as_dict=True,
        )
        self.assertIsNotNone(row)
        self.assertEqual(row.fieldtype, "Check")


class TestRealtimeCarriesInvoice(FrappeTestCase):
    """Mahsulot holati xabari CHEK NOMINI tashishi shart.

    Ofitsant ilovasi ochiq buyurtmani aynan chek nomi bo'yicha yangilaydi.
    Maydon bo'lmasa xabar yetib boradi, lekin ekran yangilanmaydi —
    oshpaz holatni o'zgartirsa ham ofitsant eski holatni ko'rib turadi.
    """

    def test_emit_item_change_has_invoice(self):
        import inspect

        from ozturkapp.ozturkapp.utils import kitchen_realtime

        sig = inspect.signature(kitchen_realtime.emit_item_change)
        self.assertIn("invoice", sig.parameters)
        self.assertIn('"invoice": invoice', inspect.getsource(kitchen_realtime.emit_item_change))

    def test_all_callers_pass_invoice(self):
        import inspect

        from ozturkapp.ozturkapp.api import kitchen, waiter

        for fn in (kitchen.update_kot_item_status, waiter.mark_delivered):
            with self.subTest(fn=fn.__name__):
                source = inspect.getsource(fn)
                emit = source[source.index("emit_item_change") :]
                self.assertIn("row.invoice", emit[:200])


class TestStaffNotifications(FrappeTestCase):
    """Uchta bildirishnoma oqimi.

        Ofitsant hisob so'radi      -> KASSIR
        Oshpaz taomni tayyor qildi  -> OFITSANT
        Buyurtma oshxonaga tushdi   -> OSHPAZ
    """

    def test_ready_only_notifies_waiter(self):
        """Faqat `Ready` xabar beradi.

        `Preparing`/`Served` ofitsantdan hech narsa talab qilmaydi —
        ularga ham xabar bersak, xabar shovqinga aylanadi.
        """
        import inspect

        from ozturkapp.ozturkapp.api import kitchen

        source = inspect.getsource(kitchen.update_kot_item_status)
        self.assertIn("if target == kitchen_status.READY:", source)
        self.assertIn("notifications.item_ready", source)

    def test_new_order_notifies_from_kot_not_waiter_api(self):
        """Xabar KOT yaratilganda chiqadi — buyurtma manbasidan qat'i nazar.

        KOT'ni Desktop POS ham, kassir ham, ofitsant ilovasi ham yaratadi.
        Har biriga alohida xabar yozilsa bittasi unutilardi.
        """
        import inspect

        from ozturkapp.ozturkapp.utils import kitchen_realtime

        self.assertIn(
            "notifications.order_placed", inspect.getsource(kitchen_realtime.on_kot_submit)
        )

    def test_bill_request_notifies_cashier(self):
        import inspect

        from ozturkapp.ozturkapp.api import waiter

        self.assertIn(
            "notifications.bill_requested", inspect.getsource(waiter.request_bill)
        )

    def test_payload_carries_no_money(self):
        """Xabar sayt xonasiga ketadi — summa/mijoz YUBORILMAYDI."""
        import inspect

        from ozturkapp.ozturkapp.utils import notifications

        source = inspect.getsource(notifications)
        for leak in ("grand_total", "amount", "customer", "paid_amount"):
            with self.subTest(leak=leak):
                self.assertNotIn(f'"{leak}"', source)


class TestRemovedItemLeavesTheKitchen(FrappeTestCase):
    """Zakazdan olib tashlangan taom oshxona ekranidan yo'qoladi.

    MUAMMO QANDAY BO'LGAN
    =====================
    URY taom olib tashlanganda faqat YANGI «Partially cancelled» KOT
    yaratadi, asl chiptaga TEGMAYDI. Natijada oshpaz bitta taomni ikki
    marta ko'rardi: asl kartada hamon «Kutilmoqda» va «Tayyorlashni
    boshlash» tugmasi bilan, ustiga qizil «Qisman bekor qilindi»
    kartasi. Ya'ni bekor qilingan taom bemalol pishirilardi.

    KUTILAYOTGAN XULQ
    =================
        oshxona boshlamagan  -> taom ham, «to'xtat» kartasi ham yo'qoladi
        oshxona boshlagan    -> ofitsant olib tashlay olmaydi; menejer
                                majburan bekor qilsa karta QOLADI
        4 taomdan 1 tasi olinsa -> ekranda 3 ta qoladi
    """

    def setUp(self):
        from ozturkapp.ozturkapp.utils import cashier_permissions

        self.branch = cashier_permissions.resolve_scope().branch
        self.invoice = frappe.generate_hash(length=10)
        self.kots = []

        frappe.db.sql(
            """
            insert into `tabPOS Invoice`
                (name, creation, modified, owner, modified_by, docstatus,
                 branch, invoice_printed, custom_cancelled)
            values (%s, now(), now(), 'Administrator', 'Administrator', 0, %s, 0, 0)
            """,
            (self.invoice, self.branch),
        )

    def tearDown(self):
        for kot in self.kots:
            frappe.db.delete("URY KOT Items", {"parent": kot})
            frappe.db.delete("URY KOT", {"name": kot})
        frappe.db.delete("POS Invoice", {"name": self.invoice})

    # ── Fikstura ──────────────────────────────────────────────────────

    def _kot(self, kot_type, items, production="Oshxona", verified=0):
        """`items` = [(item, qty, status, cancelled_qty)]."""
        kot = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabURY KOT`
                (name, creation, modified, owner, modified_by, docstatus,
                 invoice, branch, type, order_status, production, verified)
            values (%s, now(), now(), 'Administrator', 'Administrator', 1,
                 %s, %s, %s, 'Ready For Prepare', %s, %s)
            """,
            (kot, self.invoice, self.branch, kot_type, production, verified),
        )
        self.kots.append(kot)

        for idx, (item, qty, status, cancelled) in enumerate(items, start=1):
            frappe.db.sql(
                """
                insert into `tabURY KOT Items`
                    (name, creation, modified, owner, modified_by, docstatus,
                     parent, parenttype, parentfield, idx, item, item_name,
                     quantity, cancelled_qty, custom_kitchen_status)
                values (%s, now(), now(), 'Administrator', 'Administrator', 1,
                     %s, 'URY KOT', 'kot_items', %s, %s, %s, %s, %s, %s)
                """,
                (
                    frappe.generate_hash(length=10), kot, idx, item, item,
                    qty, cancelled, status,
                ),
            )
        return kot

    def _cancel_kot(self, items, production="Oshxona"):
        """Bekor-KOT yaratib, `on_submit` hook'ini AYNAN URY kabi ishga tushiradi."""
        from ozturkapp.ozturkapp.utils.kitchen_realtime import on_kot_submit

        kot = self._kot("Partially cancelled", items, production=production)
        on_kot_submit(frappe.get_doc("URY KOT", kot))
        return kot

    def _statuses(self, kot):
        return frappe.get_all(
            "URY KOT Items",
            filters={"parent": kot, "parenttype": "URY KOT"},
            fields=["item", "quantity", "custom_kitchen_status"],
            order_by="idx asc",
        )

    def _screen(self):
        from ozturkapp.ozturkapp.api import kitchen

        return {row["kot"]: row for row in kitchen.get_active_kots()}

    # ── Oshxona hali boshlamagan ──────────────────────────────────────

    def test_removed_pending_item_disappears_from_the_original_ticket(self):
        bar = self._kot("New Order", [("CHOY", 1, ks.PENDING, 0),
                                      ("AYRAN", 1, ks.PENDING, 0)], production="Bar")
        oshxona = self._kot("New Order", [("PIDE", 1, ks.PENDING, 0),
                                          ("SALAD", 1, ks.PENDING, 0)])

        cancel = self._cancel_kot([("PIDE", 1, ks.PENDING, 1)])

        # Asl qator yopildi.
        rows = {r.item: r.custom_kitchen_status for r in self._statuses(oshxona)}
        self.assertEqual(rows["PIDE"], ks.CANCELLED)
        self.assertEqual(rows["SALAD"], ks.PENDING)

        # «To'xtat» kartasi yopildi — to'xtatadigan ish yo'q edi.
        self.assertEqual(frappe.db.get_value("URY KOT", cancel, "verified"), 1)
        self.assertEqual(
            frappe.db.get_value("URY KOT", cancel, "order_status"), "Cancelled"
        )
        self.assertTrue(bar)  # bar chiptasiga tegilmagan

    def test_kitchen_screen_shows_three_items_out_of_four(self):
        self._kot("New Order", [("CHOY", 1, ks.PENDING, 0),
                                ("AYRAN", 1, ks.PENDING, 0)], production="Bar")
        self._kot("New Order", [("PIDE", 1, ks.PENDING, 0),
                                ("SALAD", 1, ks.PENDING, 0)])

        self._cancel_kot([("PIDE", 1, ks.PENDING, 1)])

        screen = self._screen()
        shown = [
            item["item"]
            for card in screen.values()
            if card["invoice"] == self.invoice
            for item in card["items"]
        ]

        self.assertEqual(sorted(shown), ["AYRAN", "CHOY", "SALAD"])
        self.assertNotIn("PIDE", shown, "bekor qilingan taom ko'rinmasligi kerak")

    def test_no_stop_card_when_the_kitchen_had_not_started(self):
        self._kot("New Order", [("PIDE", 1, ks.PENDING, 0),
                                ("SALAD", 1, ks.PENDING, 0)])
        cancel = self._cancel_kot([("PIDE", 1, ks.PENDING, 1)])

        self.assertNotIn(cancel, self._screen(), "«to'xtat» kartasi keraksiz")

    def test_emptied_ticket_leaves_the_screen(self):
        """Chiptadagi hamma taom olinsa karta ham yo'qoladi."""
        oshxona = self._kot("New Order", [("PIDE", 1, ks.PENDING, 0)])
        self._cancel_kot([("PIDE", 1, ks.PENDING, 1)])

        self.assertNotIn(oshxona, self._screen())
        self.assertEqual(
            frappe.db.get_value("URY KOT", oshxona, "order_status"), "Cancelled"
        )

    # ── Oshxona boshlab yuborgan ──────────────────────────────────────

    def test_started_item_keeps_the_stop_card(self):
        """Menejer majburan bekor qilgan — oshpaz «to'xtat» ni KO'RISHI kerak."""
        oshxona = self._kot("New Order", [("PIDE", 1, ks.PREPARING, 0)])
        cancel = self._cancel_kot([("PIDE", 1, ks.PREPARING, 1)])

        # Pishayotgan qatorga TEGILMAYDI.
        rows = {r.item: r.custom_kitchen_status for r in self._statuses(oshxona)}
        self.assertEqual(rows["PIDE"], ks.PREPARING)

        self.assertEqual(frappe.db.get_value("URY KOT", cancel, "verified"), 0)
        self.assertIn(cancel, self._screen(), "«to'xtat» kartasi ko'rinishi kerak")

    # ── Qisman kamaytirish ────────────────────────────────────────────

    def test_partial_reduction_lowers_the_quantity(self):
        """2 tadan 1 tasi olinsa oshxona 1 ta pishirishi kerak.

        URY Mosaic KDS per-item holatni bilmaydi va aynan `quantity` ni
        ko'rsatadi — shuning uchun sonning O'ZI kamayishi shart.
        """
        oshxona = self._kot("New Order", [("PIDE", 2, ks.PENDING, 0)])
        self._cancel_kot([("PIDE", 2, ks.PENDING, 1)])

        row = self._statuses(oshxona)[0]
        # `quantity` — URY'da Data maydoni, ya'ni satr qaytadi.
        self.assertEqual(cint(row.quantity), 1)
        self.assertEqual(row.custom_kitchen_status, ks.PENDING)

    def test_newest_round_is_cancelled_first(self):
        """1 dona pishmoqda, 1 dona navbatda -> navbatdagisi ketadi."""
        old = self._kot("New Order", [("PIDE", 1, ks.PREPARING, 0)])
        new = self._kot("New Order", [("PIDE", 1, ks.PENDING, 0)])

        self._cancel_kot([("PIDE", 1, ks.PENDING, 1)])

        self.assertEqual(self._statuses(old)[0].custom_kitchen_status, ks.PREPARING)
        self.assertEqual(self._statuses(new)[0].custom_kitchen_status, ks.CANCELLED)

    def test_cancellation_kot_does_not_announce_a_new_order(self):
        """Ilgari olib tashlash oshxonaga «Yangi buyurtma» deb ketardi."""
        import inspect

        from ozturkapp.ozturkapp.utils import kitchen_realtime

        source = inspect.getsource(kitchen_realtime.on_kot_submit)
        self.assertIn("CANCELLATION_KOT_TYPES", source)
        self.assertIn("_on_cancellation_kot", source)


class TestRepairOfAlreadyBrokenTickets(FrappeTestCase):
    """Tuzatishdan OLDIN yaratilgan buzuq chiptalarni tozalash.

    Ekrandagi holat (mijoz skrinshoti):

        KOT-00012   CLOSED PIDE   TAYYORLANMOQDA  [Tayyor]   <-- 1-nusxa
        CNCL-KOT-1  QISMAN BEKOR QILINDI: CLOSED PIDE x1      <-- 2-nusxa

    Ofitsant taomni HALI NAVBATDA turganda bekor qilgan edi, lekin u asl
    chiptada qolib ketgani uchun oshpaz uni ko'rgan va «Tayyorlashni
    boshlash» ni bosgan. Kod tuzatilgani bilan bu YOZUVLAR o'zi
    tuzalmaydi — shuning uchun bir martalik tozalash kerak.
    """

    def setUp(self):
        from ozturkapp.ozturkapp.utils import cashier_permissions

        self.branch = cashier_permissions.resolve_scope().branch
        self.invoice = frappe.generate_hash(length=10)
        self.kots = []

        frappe.db.sql(
            """
            insert into `tabPOS Invoice`
                (name, creation, modified, owner, modified_by, docstatus,
                 branch, invoice_printed, custom_cancelled)
            values (%s, now(), now(), 'Administrator', 'Administrator', 0, %s, 0, 0)
            """,
            (self.invoice, self.branch),
        )

    def tearDown(self):
        for kot in self.kots:
            frappe.db.delete("URY KOT Items", {"parent": kot})
            frappe.db.delete("URY KOT", {"name": kot})
        frappe.db.delete("POS Invoice", {"name": self.invoice})

    def _kot(self, kot_type, items, verified=0):
        kot = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabURY KOT`
                (name, creation, modified, owner, modified_by, docstatus,
                 invoice, branch, type, order_status, production, verified)
            values (%s, now(), now(), 'Administrator', 'Administrator', 1,
                 %s, %s, %s, 'Ready For Prepare', 'Oshxona', %s)
            """,
            (kot, self.invoice, self.branch, kot_type, verified),
        )
        self.kots.append(kot)
        for idx, (item, qty, status, cancelled) in enumerate(items, start=1):
            frappe.db.sql(
                """
                insert into `tabURY KOT Items`
                    (name, creation, modified, owner, modified_by, docstatus,
                     parent, parenttype, parentfield, idx, item, item_name,
                     quantity, cancelled_qty, custom_kitchen_status)
                values (%s, now(), now(), 'Administrator', 'Administrator', 1,
                     %s, 'URY KOT', 'kot_items', %s, %s, %s, %s, %s, %s)
                """,
                (frappe.generate_hash(length=10), kot, idx, item, item,
                 qty, cancelled, status),
            )
        return kot

    def _screen(self):
        from ozturkapp.ozturkapp.api import kitchen

        return {row["kot"]: row for row in kitchen.get_active_kots()}

    def _shown_items(self):
        return [
            item["item"]
            for card in self._screen().values()
            if card["invoice"] == self.invoice
            for item in card["items"]
        ]

    # ── Aynan skrinshotdagi holat ─────────────────────────────────────

    def test_repairs_the_screenshot_scenario(self):
        """Oshpaz boshlab yuborgan bekor qilingan taom ham yo'qoladi."""
        from ozturkapp.ozturkapp.setup.cancelled_orders import reconcile_cancel_kots

        cooking = self._kot("New Order", [
            ("CLOSED PIDE", 1, ks.PREPARING, 0),   # xato tufayli boshlangan
            ("ÇOBAN SALAD", 1, ks.PREPARING, 0),   # haqiqiy zakaz
        ])
        cancel = self._kot("Partially cancelled", [("CLOSED PIDE", 1, ks.PENDING, 1)])

        self.assertIn("CLOSED PIDE", self._shown_items(), "boshlang'ich holat noto'g'ri")

        reconcile_cancel_kots()

        shown = self._shown_items()
        self.assertNotIn("CLOSED PIDE", shown, "bekor qilingan taom qolib ketdi")
        self.assertIn("ÇOBAN SALAD", shown, "haqiqiy zakazga tegilmasligi kerak")
        self.assertNotIn(cancel, self._screen(), "«to'xtat» kartasi ham yo'qolishi kerak")
        self.assertTrue(cooking)

    def test_served_items_are_never_touched(self):
        """Berilgan taom jismonan chiqib bo'lgan — uni bekor deb yozib bo'lmaydi."""
        from ozturkapp.ozturkapp.setup.cancelled_orders import reconcile_cancel_kots

        cooking = self._kot("New Order", [("CLOSED PIDE", 1, ks.SERVED, 0)])
        self._kot("Partially cancelled", [("CLOSED PIDE", 1, ks.PENDING, 1)])

        reconcile_cancel_kots()

        status = frappe.get_all(
            "URY KOT Items", filters={"parent": cooking}, pluck="custom_kitchen_status"
        )
        self.assertEqual(status, [ks.SERVED])

    def test_repair_is_idempotent(self):
        from ozturkapp.ozturkapp.setup.cancelled_orders import reconcile_cancel_kots

        cooking = self._kot("New Order", [("CLOSED PIDE", 2, ks.PENDING, 0)])
        self._kot("Partially cancelled", [("CLOSED PIDE", 2, ks.PENDING, 1)])

        reconcile_cancel_kots()
        after_first = frappe.get_all(
            "URY KOT Items", filters={"parent": cooking},
            fields=["quantity", "custom_kitchen_status"],
        )

        reconcile_cancel_kots()  # ikkinchi marta
        after_second = frappe.get_all(
            "URY KOT Items", filters={"parent": cooking},
            fields=["quantity", "custom_kitchen_status"],
        )

        self.assertEqual(after_first, after_second, "takroriy yurgizish qo'shimcha kamaytirdi")

    def test_already_verified_tickets_are_skipped(self):
        """Yopilgan chipta qayta ishlanmasligi kerak."""
        from ozturkapp.ozturkapp.setup.cancelled_orders import reconcile_cancel_kots

        cooking = self._kot("New Order", [("CLOSED PIDE", 1, ks.PENDING, 0)])
        self._kot("Partially cancelled", [("CLOSED PIDE", 1, ks.PENDING, 1)], verified=1)

        reconcile_cancel_kots()

        status = frappe.get_all(
            "URY KOT Items", filters={"parent": cooking}, pluck="custom_kitchen_status"
        )
        self.assertEqual(status, [ks.PENDING], "yopilgan chipta tegilmasligi kerak")
