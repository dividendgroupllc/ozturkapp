# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa: buyurtma qabul qilish, stol ko'chirish/birlashtirish, mijoz, bronlar.

    cd /home/sherzod/frappe-bench && flock /tmp/ozturk_tests.lock \
        bench --site ozturk.local run-tests \
        --module ozturkapp.ozturkapp.tests.test_cashier_orders

FIKSTURA
========
Buyurtmalar ilovaning O'Z yo'li bilan yaratiladi (`cashier_orders.create_order`
-> URY `sync_order` -> POS Invoice + KOT), qo'lda SQL bilan emas. Har bir test
o'z savepoint'ida ishlaydi va oxirida orqaga qaytadi, sinf tugagach esa butun
tranzaksiya rollback qilinadi — dev bazadagi chek va stollarga tegilmaydi.
Test stollari (`ZZ-...`) ham shu savepoint ichida yaratiladi.
"""

import inspect
import unittest
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, cint, nowdate

from ozturkapp.ozturkapp.api import cashier_orders as co
from ozturkapp.ozturkapp.api import order as order_api
from ozturkapp.ozturkapp.api import table as table_api
from ozturkapp.ozturkapp.api import waiter
from ozturkapp.ozturkapp.overrides import pos_invoice as pos_invoice_override
from ozturkapp.ozturkapp.setup import cashier_features
from ozturkapp.ozturkapp.utils import (
    cashier_permissions,
    kitchen_status as ks,
    order_cancel,
    order_items,
    order_transfer,
    table_status,
)

FEATURE_FIELDS = {
    "cashier_orders": "custom_enable_cashier_orders",
    "customer_attach": "custom_enable_customer_attach",
    "table_transfer": "custom_enable_table_transfer",
}

SAVEPOINT = "orders_test"


class OrdersTestCase(FrappeTestCase):
    """Umumiy fikstura: smena, kassir, narxlar, funksiya bayroqlari, test stollari."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.scope = cashier_permissions.resolve_scope()
        cls.profile = cls.scope.pos_profile
        cls.branch = cls.scope.branch

        if not cashier_permissions.open_shift_name(cls.scope):
            raise unittest.SkipTest("Ochiq kassa smenasi yo'q")

        cls.cashier = order_items.current_shift_user(cls.scope)
        if not (cls.cashier and cashier_permissions.has_cashier_role(cls.cashier)):
            raise unittest.SkipTest("Smena egasi kassir roliga ega emas")

        cls.kitchen_item, cls.bar_item = cls._pick_items()
        cls._price(cls.kitchen_item)
        cls._price(cls.bar_item)

        cls.base_table = frappe.get_all(
            "URY Table", filters={"branch": cls.branch}, pluck="name", order_by="name asc"
        )[0]

    # ── Sinf darajasidagi fikstura ────────────────────────────────────

    @classmethod
    def _pick_items(cls):
        """Menyudan ikki taom: biri oshxona, biri bar (ikkita KOT chiqishi uchun)."""
        menu = frappe.db.get_value("URY Restaurant", cls.scope.restaurant, "active_menu")
        in_menu = set(
            frappe.get_all("URY Menu Item", filters={"parent": menu, "disabled": 0}, pluck="item")
        )

        by_unit = {}
        for unit in frappe.get_all("URY Production Unit", filters={"branch": cls.branch}, pluck="name"):
            groups = frappe.get_all(
                "URY Production Item Groups", filters={"parent": unit}, pluck="item_group"
            )
            items = frappe.get_all(
                "Item", filters={"item_group": ["in", groups], "name": ["in", list(in_menu)]}, pluck="name"
            )
            if items:
                by_unit[unit] = items

        if len(by_unit) < 2:
            raise unittest.SkipTest("Kamida ikkita ishlab chiqarish nuqtasida taom kerak")

        kitchen_unit, bar_unit = sorted(by_unit)[0], sorted(by_unit)[1]
        return by_unit[kitchen_unit][0], by_unit[bar_unit][0]

    @classmethod
    def _price(cls, item):
        """`sync_order` narxni `Item Price` dan oladi — dev bazada narxlar yo'q."""
        menu = frappe.db.get_value("URY Restaurant", cls.scope.restaurant, "active_menu")
        price_list = frappe.db.get_value("Price List", {"restaurant_menu": menu, "enabled": 1})
        if not frappe.db.exists("Item Price", {"item_code": item, "price_list": price_list}):
            frappe.get_doc(
                {
                    "doctype": "Item Price",
                    "item_code": item,
                    "price_list": price_list,
                    "price_list_rate": 10000,
                    "selling": 1,
                }
            ).insert()

    # ── Har bir test uchun ────────────────────────────────────────────

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint(SAVEPOINT)
        self.addCleanup(self._restore)

        for fieldname in FEATURE_FIELDS.values():
            frappe.db.set_value("POS Profile", self.profile, fieldname, 1)
        frappe.db.set_value("POS Profile", self.profile, "remove_items", 1)

        frappe.local._ozturk_scope_cache = {}
        frappe.set_user(self.cashier)

    def _restore(self):
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point=SAVEPOINT)
        frappe.local._ozturk_scope_cache = {}

    # ── Yordamchilar ──────────────────────────────────────────────────

    def _set_feature(self, key, on):
        frappe.db.set_value("POS Profile", self.profile, FEATURE_FIELDS[key], 1 if on else 0)

    def _as_admin(self):
        """`with` bloki uchun: Administrator sifatida (ma'lumotnoma yaratish)."""
        return _AsUser("Administrator", self.cashier)

    def _table(self, name, room=None):
        """Test stoli (`ZZ-...`) — savepoint bilan birga yo'qoladi."""
        base = frappe.db.get_value(
            "URY Table", self.base_table, ["restaurant", "restaurant_room"], as_dict=True
        )
        with self._as_admin():
            frappe.get_doc(
                {
                    "doctype": "URY Table",
                    "name": f"ZZ-{name}",
                    "restaurant": base.restaurant,
                    "restaurant_room": room or base.restaurant_room,
                    "no_of_seats": 4,
                }
            ).insert()
        return f"ZZ-{name}"

    def _other_room(self):
        room = frappe.db.get_value("URY Table", self.base_table, "restaurant_room")
        other = frappe.get_all(
            "URY Room", filters={"branch": self.branch, "name": ["!=", room]}, pluck="name"
        )
        if not other:
            self.skipTest("Ikkinchi zal yo'q")
        return other[0]

    def _lines(self, kitchen=1, bar=0):
        lines = [{"item": self.kitchen_item, "qty": kitchen}]
        if bar:
            lines.append({"item": self.bar_item, "qty": bar})
        return lines

    def _dine_in(self, table, kitchen=1, bar=0):
        return co.create_order("Dine In", self._lines(kitchen, bar), table=table)

    def _take_away(self, kitchen=1, bar=0, **kwargs):
        return co.create_order("Take Away", self._lines(kitchen, bar), **kwargs)

    def _flags(self, table):
        return frappe.db.get_value(
            "URY Table", table, ["occupied", "merged_with", "restaurant_room"], as_dict=True
        )

    def _kots(self, invoice):
        return frappe.get_all(
            "URY KOT",
            filters={"invoice": invoice},
            fields=["name", "type", "production", "restaurant_table", "order_status", "verified"],
            order_by="creation asc",
        )

    def _kitchen_rows(self, invoice, item=None):
        """Taom tayyorlash chiptalarining qatorlari (bekor-KOT ko'rsatmalari EMAS)."""
        filters = {"item": item} if item else {}
        rows = []
        for kot in self._kots(invoice):
            if kot.type not in ks.COOKING_KOT_TYPES:
                continue
            rows.extend(
                frappe.get_all(
                    "URY KOT Items",
                    filters={"parent": kot.name, **filters},
                    fields=["name", "item", "quantity", "cancelled_qty", "custom_kitchen_status"],
                )
            )
        return rows

    def _live_kitchen_qty(self, invoice, item):
        """Oshxona chiptalaridagi hali bekor qilinmagan jami miqdor."""
        return sum(
            cint(row.quantity)
            for row in self._kitchen_rows(invoice, item)
            if row.custom_kitchen_status != ks.CANCELLED
        )

    def _emitted(self):
        """`order_transfer` ichidagi realtime chaqiruvlarini ushlab turadi."""
        patchers = {
            name: mock.patch.object(order_transfer, name)
            for name in ("emit_floor_change", "emit_order_change", "emit_kot_change")
        }
        mocks = {name: patcher.start() for name, patcher in patchers.items()}
        for patcher in patchers.values():
            self.addCleanup(patcher.stop)
        return mocks


class _AsUser:
    """`with` bloki: vaqtincha boshqa foydalanuvchi, chiqishda qaytish."""

    def __init__(self, user, back):
        self.user, self.back = user, back

    def __enter__(self):
        frappe.set_user(self.user)

    def __exit__(self, *exc):
        frappe.set_user(self.back)


# ═══════════════════════════════════════════════════════════════════
#  Ruxsat, funksiya bayrog'i, smena — HAR BIR yozuv amali uchun
# ═══════════════════════════════════════════════════════════════════

def _gated_calls():
    """{funksiya bayrog'i: [(nom, chaqiruv)]}. Argumentlar soxta: ruxsat
    tekshiruvi ularga TEGMASDAN oldin to'xtashi kerak."""
    return {
        "cashier_orders": [
            ("get_menu", lambda: co.get_menu()),
            ("create_order", lambda: co.create_order("Take Away", [])),
            ("add_items", lambda: co.add_items("YO'Q", [], "2026-01-01 00:00:00")),
            ("remove_item", lambda: co.remove_item("YO'Q", "qator")),
        ],
        "customer_attach": [
            ("search_customers", lambda: co.search_customers("a")),
            ("create_customer", lambda: co.create_customer("Test")),
            ("set_customer", lambda: co.set_customer("YO'Q", "YO'Q")),
        ],
        "table_transfer": [
            ("transfer_table", lambda: table_api.transfer_table("YO'Q", "T")),
            ("merge_tables", lambda: table_api.merge_tables("YO'Q", ["T"])),
            ("unmerge_table", lambda: table_api.unmerge_table("YO'Q", "T")),
        ],
    }


class TestGatingAndPermissions(OrdersTestCase):
    def test_feature_off_rejects_every_gated_endpoint(self):
        for key, calls in _gated_calls().items():
            self._set_feature(key, False)
            for name, call in calls:
                with self.subTest(feature=key, endpoint=name):
                    with self.assertRaises(frappe.PermissionError) as ctx:
                        call()
                    # Bayroq tekshiruvi chekka TEGMASDAN oldin — "YO'Q" chek
                    # `DoesNotExistError` bermagan, demak zanjir to'g'ri tartibda.
                    self.assertNotIsInstance(ctx.exception, frappe.DoesNotExistError)

    def test_user_without_cashier_role_is_rejected_everywhere(self):
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": "orders-test-nobody@example.com",
                "first_name": "Ruxsatsiz",
                "send_welcome_email": 0,
            }
        )
        with self._as_admin():
            user.insert(ignore_permissions=True)

        frappe.set_user(user.name)
        everything = [call for calls in _gated_calls().values() for _, call in calls]
        everything.append(lambda: table_api.get_reservations())

        for index, call in enumerate(everything):
            with self.subTest(call=index):
                with self.assertRaises(cashier_permissions.CashierPermissionError):
                    call()

    def test_closed_shift_rejects_every_mutating_endpoint(self):
        mutating = {
            "create_order", "add_items", "remove_item", "set_customer",
            "transfer_table", "merge_tables", "unmerge_table",
        }
        with mock.patch.object(cashier_permissions, "open_shift_name", return_value=""):
            for calls in _gated_calls().values():
                for name, call in calls:
                    if name not in mutating:
                        continue
                    with self.subTest(endpoint=name):
                        with self.assertRaisesRegex(frappe.ValidationError, "Kassa smenasi ochilmagan"):
                            call()

    def test_reads_do_not_need_a_shift(self):
        with mock.patch.object(cashier_permissions, "open_shift_name", return_value=""):
            self.assertIn("items", co.get_menu())
            self.assertIsInstance(co.search_customers("a"), list)
            self.assertIsInstance(table_api.get_reservations(), list)

    def test_all_endpoints_are_whitelisted(self):
        for module, names in (
            (co, ("get_menu", "create_order", "add_items", "remove_item",
                  "search_customers", "create_customer", "set_customer")),
            (table_api, ("transfer_table", "merge_tables", "unmerge_table", "get_reservations")),
        ):
            for name in names:
                with self.subTest(endpoint=name):
                    self.assertIn(getattr(module, name), frappe.whitelisted)

    def test_feature_flags_default_to_off_for_a_fresh_profile(self):
        """Ilova yangilanganda hech narsa o'zgarmasligi kerak (default = 0)."""
        for key in FEATURE_FIELDS:
            self.assertEqual(cashier_features.FEATURES[key]["default"], 0)

    def test_orders_use_the_existing_ury_flow(self):
        """Ikkinchi buyurtma tizimi yo'q: hammasi `sync_order` orqali."""
        self.assertIn("run_sync_order", inspect.getsource(co.create_order))
        self.assertIn("run_sync_order", inspect.getsource(co._push))
        self.assertIn("sync_order", inspect.getsource(order_items.run_sync_order))
        self.assertNotIn('frappe.new_doc("POS Invoice")', inspect.getsource(co))
        self.assertNotIn("kot_execute(", inspect.getsource(co))

    def test_invoice_owner_rule_matches_the_waiter_flow(self):
        source = inspect.getsource(order_items.run_sync_order)
        self.assertIn('"cashier": shift_user', source)
        self.assertIn('"owner": shift_user', source)
        self.assertIn("waiter or frappe.session.user", source)


# ═══════════════════════════════════════════════════════════════════
#  Waiter API o'zgarmadi (umumiy yordamchilar)
# ═══════════════════════════════════════════════════════════════════

class TestSharedHelpersKeepWaiterBehaviour(FrappeTestCase):
    def test_waiter_uses_the_extracted_helpers(self):
        self.assertIs(waiter._parse_items, order_items.parse_items)
        self.assertIs(waiter._assert_removals_allowed, order_items.assert_removals_allowed)
        self.assertIs(waiter._current_shift_user, order_items.current_shift_user)
        self.assertIs(waiter._existing_pax, order_items.existing_pax)
        self.assertIs(waiter._default_mode_of_payment, order_items.default_mode_of_payment)
        self.assertIs(waiter._active_invoice_for_table, order_items.active_invoice_for_table)

    def test_waiter_and_cashier_menu_share_one_implementation(self):
        self.assertIn("order_items.build_menu", inspect.getsource(waiter.get_menu))
        self.assertIn("order_items.build_menu", inspect.getsource(co.get_menu))

    def test_waiter_customer_search_still_matches_names_only(self):
        source = inspect.getsource(waiter.search_customers)
        self.assertNotIn("include_phone", source)
        self.assertIn("require_waiter", source)

    def test_parse_items_drops_client_prices_and_bad_rows(self):
        code = frappe.get_all("Item", pluck="name", limit=1)[0]
        cleaned = order_items.parse_items(
            [
                {"item": code, "qty": 2, "rate": 1, "comment": "x"},
                {"item": code, "qty": 0},
                {"qty": 1},
            ]
        )
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(sorted(cleaned[0]), ["comment", "item", "item_name", "qty"])

    def test_parse_items_rejects_unknown_item(self):
        with self.assertRaises(frappe.ValidationError):
            order_items.parse_items([{"item": "YO'Q-TAOM", "qty": 1}])


# ═══════════════════════════════════════════════════════════════════
#  Ofitsant chekda pul KO'RMAYDI (kassir uchun yig'ilgan `build_bill` dan)
# ═══════════════════════════════════════════════════════════════════

#: `build_bill()` dagi, ofitsantga MUTLAQO ko'rinmasligi kerak kalitlar:
#: xizmat haqi/soliq/jami, chegirma, choychaqa, to'lov, qaytarish.
MONEY_HIDDEN_FROM_WAITER = (
    "service_charge", "service_charge_rate", "taxes", "total_taxes",
    "grand_total", "rounded_total", "payable", "tip",
    "discount", "discount_percent", "discount_reason",
    "discount_approved_by", "discount_approved_by_name",
    "total", "paid_amount", "change_amount", "payments",
    "is_return", "return_against", "reprint_needed",
)


class TestWaiterPayloadHasNoMoney(OrdersTestCase):
    def _bill(self):
        from ozturkapp.ozturkapp.utils import cashier_billing

        invoice = self._take_away(kitchen=2, bar=1)["invoice"]
        return invoice, cashier_billing.build_bill(frappe.get_doc("POS Invoice", invoice), self.scope)

    def test_the_cashier_bill_really_carries_these_keys(self):
        """Sinov mazmunli bo'lishi uchun: kassir chekida ular BOR."""
        _, bill = self._bill()
        for key in ("service_charge", "taxes", "grand_total", "payable", "tip",
                    "discount_percent", "payments"):
            self.assertIn(key, bill)

    def test_stripped_bill_has_none_of_them(self):
        _, bill = self._bill()

        visible = waiter._strip_financials(bill)

        for key in MONEY_HIDDEN_FROM_WAITER:
            with self.subTest(key=key):
                self.assertNotIn(key, visible)

    def test_a_future_money_key_does_not_leak(self):
        """Oq ro'yxat: `build_bill` ga qo'shilgan yangi kalit ofitsantga chiqmaydi."""
        _, bill = self._bill()
        bill["some_future_money_field"] = 12345

        self.assertNotIn("some_future_money_field", waiter._strip_financials(bill))

    def test_the_waiter_app_still_gets_everything_it_reads(self):
        _, bill = self._bill()

        visible = waiter._strip_financials(bill)

        for key in ("invoice", "items", "subtotal", "items_total", "currency", "table",
                    "waiter_name", "customer_name", "pax", "opened_at", "order_number",
                    "cancellation", "kitchen", "billed", "order_type"):
            with self.subTest(key=key):
                self.assertIn(key, visible)
        self.assertEqual(visible["items_total"], bill["subtotal"])
        self.assertEqual(
            sorted(visible["items"][0]),
            sorted(bill["items"][0]),
            "taom qatori (narx va oshxona holati bilan) o'zgarmasligi kerak",
        )

    def test_get_order_for_a_waiter_carries_no_money(self):
        invoice = self._take_away(kitchen=1)["invoice"]
        captains = frappe.get_all(
            "Has Role", filters={"role": waiter.WAITER_ROLE, "parenttype": "User"}, pluck="parent"
        )
        captains = [user for user in captains if user not in ("Administrator", "Guest")]
        if not captains:
            self.skipTest("URY Captain roli bor foydalanuvchi yo'q")

        frappe.set_user(captains[0])
        frappe.local._ozturk_scope_cache = {}
        payload = waiter.get_order(invoice=invoice)

        for key in MONEY_HIDDEN_FROM_WAITER:
            with self.subTest(key=key):
                self.assertNotIn(key, payload)
        for key in ("items_total", "can_edit", "last_modified_time", "bill_requested", "cancellation"):
            self.assertIn(key, payload)


# ═══════════════════════════════════════════════════════════════════
#  Ko'rinish maydonlari (doim yoqiq)
# ═══════════════════════════════════════════════════════════════════

VISIBILITY_KEYS = {
    "bill_requested", "bill_requested_at", "opened_at",
    "elapsed_minutes", "order_type", "delivery",
}


class TestVisibilityFields(OrdersTestCase):
    def test_active_orders_carry_the_new_fields(self):
        invoice = self._take_away()["invoice"]

        row = next(o for o in order_api.get_active_orders() if o["invoice"] == invoice)

        self.assertTrue(VISIBILITY_KEYS.issubset(row))
        self.assertEqual(row["order_type"], "Take Away")
        self.assertFalse(row["bill_requested"])
        self.assertIsNone(row["bill_requested_at"])
        self.assertIsNone(row["delivery"])
        self.assertIsInstance(row["elapsed_minutes"], int)
        self.assertTrue(row["opened_at"])

    def test_existing_active_order_keys_are_kept(self):
        invoice = self._take_away()["invoice"]
        row = next(o for o in order_api.get_active_orders() if o["invoice"] == invoice)

        for key in ("invoice", "table", "merged_tables", "room", "customer", "waiter",
                    "pax", "amount", "billed", "status", "status_label",
                    "order_number", "comments", "kitchen"):
            self.assertIn(key, row)

    def test_bill_request_is_visible_until_the_bill_is_issued(self):
        table = self._table("BR")
        invoice = self._dine_in(table)["invoice"]
        frappe.db.set_value(
            "POS Invoice", invoice,
            {"custom_bill_requested": 1, "custom_bill_requested_at": frappe.utils.now_datetime()},
        )

        row = next(o for o in order_api.get_active_orders() if o["invoice"] == invoice)
        self.assertTrue(row["bill_requested"])
        self.assertTrue(row["bill_requested_at"])

        floor = next(t for t in table_api.get_floor_plan()["tables"] if t["name"] == table)
        self.assertTrue(floor["bill_requested"])

        # Hisob chiqarilgach so'rov bajarilgan hisoblanadi.
        frappe.db.set_value("POS Invoice", invoice, "invoice_printed", 1)

        row = next(o for o in order_api.get_active_orders() if o["invoice"] == invoice)
        self.assertFalse(row["bill_requested"])
        self.assertIsNone(row["bill_requested_at"])

    def test_delivery_details_are_exposed(self):
        invoice = co.create_order(
            "Delivery", self._lines(),
            delivery={"phone": "+998901234567", "address": "Chilonzor 5"},
        )["invoice"]

        row = next(o for o in order_api.get_active_orders() if o["invoice"] == invoice)
        self.assertEqual(row["delivery"], {"phone": "+998901234567", "address": "Chilonzor 5"})
        self.assertEqual(row["order_type"], "Delivery")

    def test_floor_plan_tables_carry_the_new_fields(self):
        busy, free = self._table("FB"), self._table("FF")
        invoice = self._dine_in(busy)["invoice"]

        tables = {t["name"]: t for t in table_api.get_floor_plan()["tables"]}

        self.assertTrue(VISIBILITY_KEYS.issubset(tables[busy]))
        self.assertEqual(tables[busy]["order_type"], "Dine In")
        self.assertEqual(tables[busy]["order"]["name"], invoice)
        self.assertIsInstance(tables[busy]["elapsed_minutes"], int)
        self.assertTrue(tables[busy]["opened_at"])

        # Buyurtmasiz stol — bir xil shakldagi bo'sh qiymatlar.
        self.assertTrue(VISIBILITY_KEYS.issubset(tables[free]))
        self.assertFalse(tables[free]["bill_requested"])
        self.assertIsNone(tables[free]["order_type"])
        self.assertIsNone(tables[free]["delivery"])
        self.assertIsNone(tables[free]["elapsed_minutes"])

    def test_table_detail_carries_the_new_fields(self):
        table = self._table("TD")
        self._dine_in(table)

        detail = table_api.get_table_detail(table)
        self.assertTrue(VISIBILITY_KEYS.issubset(detail))
        self.assertEqual(detail["order_type"], "Dine In")

        empty = table_api.get_table_detail(self._table("TE"))
        self.assertTrue(VISIBILITY_KEYS.issubset(empty))
        self.assertIsNone(empty["order_type"])

    def test_floor_plan_query_count_does_not_grow_with_tables_or_orders(self):
        def count_queries():
            counter = {"n": 0}
            real = frappe.db.sql

            def counted(*args, **kwargs):
                counter["n"] += 1
                return real(*args, **kwargs)

            frappe.db.sql = counted
            try:
                table_status.build_floor_state(self.branch)
            finally:
                frappe.db.sql = real
            return counter["n"]

        count_queries()  # ustun kesh(lar)i isinsin
        before = count_queries()

        for index in range(12):
            name = self._table(f"Q{index}")
            if index % 3 == 0:
                self._dine_in(name)

        after = count_queries()
        self.assertEqual(before, after, "zal rejasi so'rovlar soni stollar soniga bog'liq bo'lmasligi kerak")


# ═══════════════════════════════════════════════════════════════════
#  Bronlar
# ═══════════════════════════════════════════════════════════════════

class TestReservations(OrdersTestCase):
    def test_list_defaults_to_today_sorted_by_time_without_cancelled(self):
        late, early, gone, tomorrow = (self._table(n) for n in ("RL", "RE", "RG", "RT"))

        table_api.reserve_table(late, "Kech mehmon", phone="+998901111111", guests=5,
                                from_time="20:00:00", to_time="22:00:00", notes="Tug'ilgan kun")
        table_api.reserve_table(early, "Erta mehmon", from_time="12:00:00")
        cancelled = table_api.reserve_table(gone, "Kelmaydi", from_time="15:00:00")["reservation"]
        table_api.cancel_reservation(reservation=cancelled, reason="fikridan qaytdi")
        table_api.reserve_table(tomorrow, "Ertaga", reservation_date=add_days(nowdate(), 1),
                                from_time="19:00:00")

        rows = [r for r in table_api.get_reservations() if r["table"].startswith("ZZ-R")]

        self.assertEqual([r["table"] for r in rows], [early, late])
        first, second = rows
        self.assertEqual(second["guest"], "Kech mehmon")
        self.assertEqual(second["phone"], "+998901111111")
        self.assertEqual(second["pax"], 5)
        self.assertEqual(second["notes"], "Tug'ilgan kun")
        self.assertEqual(second["status"], "Confirmed")
        self.assertTrue(second["from_time"].startswith("20:00"))
        self.assertTrue(second["to_time"].startswith("22:00"))
        self.assertEqual(second["date"], nowdate())
        for key in ("name", "table", "room", "guest", "phone", "pax", "date",
                    "from_time", "to_time", "status", "notes"):
            self.assertIn(key, first)

        later = [r for r in table_api.get_reservations(date=add_days(nowdate(), 1))
                 if r["table"].startswith("ZZ-R")]
        self.assertEqual([r["table"] for r in later], [tomorrow])

    def test_reserve_table_keeps_working_for_existing_callers(self):
        """Eski chaqiruvlar (`pax`, pozitsion argumentlar) buzilmaydi."""
        table = self._table("RC")

        result = table_api.reserve_table(table, "Eski mijoz", "+998902222222", 3)

        self.assertEqual(result["status"], table_status.RESERVED)
        self.assertEqual(frappe.db.get_value("URY Table Reservation", result["reservation"], "pax"), 3)
        state = table_api.get_table_status(table)
        self.assertEqual(state["status"], table_status.RESERVED)

    def test_guests_overrides_pax(self):
        table = self._table("RG2")
        result = table_api.reserve_table(table, "Mehmon", pax=2, guests=6)
        self.assertEqual(frappe.db.get_value("URY Table Reservation", result["reservation"], "pax"), 6)

    def test_future_reservation_does_not_block_or_need_a_free_table_today(self):
        table = self._table("RF")
        self._dine_in(table)  # bugun band

        result = table_api.reserve_table(
            table, "Ertangi mehmon", reservation_date=add_days(nowdate(), 2), from_time="19:30:00"
        )

        self.assertEqual(result["date"], add_days(nowdate(), 2))
        # Bugungi holat o'zgarmadi.
        self.assertEqual(result["status"], table_status.OCCUPIED)
        self.assertEqual(table_api.get_table_status(table)["status"], table_status.OCCUPIED)

    def test_future_reservation_needs_a_time(self):
        with self.assertRaises(frappe.ValidationError):
            table_api.reserve_table(
                self._table("RN"), "Mehmon", reservation_date=add_days(nowdate(), 1)
            )

    def test_past_date_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            table_api.reserve_table(
                self._table("RP"), "Mehmon",
                reservation_date=add_days(nowdate(), -1), from_time="10:00:00",
            )

    def test_todays_occupied_table_still_cannot_be_reserved(self):
        table = self._table("RO")
        self._dine_in(table)
        with self.assertRaises(frappe.ValidationError):
            table_api.reserve_table(table, "Mehmon")

    def test_cancel_flow_is_unchanged(self):
        table = self._table("RX")
        table_api.reserve_table(table, "Mehmon")
        result = table_api.cancel_reservation(table=table, reason="sabab")
        self.assertEqual(result["status"], table_status.AVAILABLE)


# ═══════════════════════════════════════════════════════════════════
#  Stol ko'chirish
# ═══════════════════════════════════════════════════════════════════

class TestTransferTable(OrdersTestCase):
    def test_transfer_moves_invoice_flags_room_and_open_kots(self):
        source, target = self._table("S1"), self._table("T1")
        order = self._dine_in(source, kitchen=1, bar=1)
        invoice = order["invoice"]
        kots = self._kots(invoice)
        self.assertEqual(len(kots), 2)

        # Bittasi allaqachon berilgan — tugallangan chipta eski stolda qoladi.
        served = kots[0].name
        frappe.db.set_value("URY KOT", served, "order_status", "Served")
        emitted = self._emitted()

        result = table_api.transfer_table(invoice, target)

        self.assertEqual(result["from_table"], source)
        self.assertEqual(result["to_table"], target)
        self.assertFalse(result["billed"])
        self.assertEqual(result["freed_tables"], [source])
        self.assertEqual(result["kots_updated"], 1)

        row = frappe.db.get_value(
            "POS Invoice", invoice,
            ["restaurant_table", "custom_restaurant_room", "docstatus"], as_dict=True,
        )
        self.assertEqual(row.restaurant_table, target)
        self.assertEqual(row.custom_restaurant_room, frappe.db.get_value("URY Table", target, "restaurant_room"))
        self.assertEqual(row.docstatus, 0)

        self.assertEqual(self._flags(target).occupied, 1)
        self.assertEqual(self._flags(source).occupied, 0)

        by_name = {k.name: k for k in self._kots(invoice)}
        open_kot = next(name for name in by_name if name != served)
        self.assertEqual(by_name[open_kot].restaurant_table, target)
        self.assertEqual(by_name[served].restaurant_table, source)

        # Kassa ekrani ham shu stolni ko'rsatadi.
        states = {t["name"]: t["status"] for t in table_api.get_floor_plan()["tables"]}
        self.assertEqual(states[target], table_status.OCCUPIED)
        self.assertEqual(states[source], table_status.AVAILABLE)

        touched = emitted["emit_floor_change"].call_args.args[1]
        self.assertEqual(set(touched), {source, target})
        emitted["emit_order_change"].assert_called_once()
        self.assertEqual(emitted["emit_kot_change"].call_count, 1)
        self.assertEqual(emitted["emit_kot_change"].call_args.args[1], open_kot)

    def test_transfer_moves_bar_and_kitchen_tickets(self):
        source, target = self._table("S2"), self._table("T2")
        invoice = self._dine_in(source, kitchen=1, bar=1)["invoice"]

        table_api.transfer_table(invoice, target)

        for kot in self._kots(invoice):
            self.assertEqual(kot.restaurant_table, target)

    def test_billed_invoice_can_be_transferred_and_ui_is_told(self):
        source, target = self._table("S3"), self._table("T3")
        invoice = self._dine_in(source)["invoice"]
        frappe.db.set_value("POS Invoice", invoice, "invoice_printed", 1)

        result = table_api.transfer_table(invoice, target)

        self.assertTrue(result["billed"])
        self.assertEqual(frappe.db.get_value("POS Invoice", invoice, "restaurant_table"), target)

    def test_transfer_to_an_occupied_table_is_refused_and_changes_nothing(self):
        source, busy = self._table("S4"), self._table("B4")
        invoice = self._dine_in(source)["invoice"]
        other = self._dine_in(busy)["invoice"]

        with self.assertRaises(frappe.ValidationError):
            table_api.transfer_table(invoice, busy)

        self.assertEqual(frappe.db.get_value("POS Invoice", invoice, "restaurant_table"), source)
        self.assertEqual(frappe.db.get_value("POS Invoice", other, "restaurant_table"), busy)
        self.assertEqual(self._flags(source).occupied, 1)

    def test_transfer_to_a_table_of_another_branch_is_refused(self):
        source, foreign = self._table("S5"), self._table("F5")
        frappe.db.set_value("URY Table", foreign, "branch", "Boshqa filial")
        invoice = self._dine_in(source)["invoice"]

        with self.assertRaises(cashier_permissions.CashierPermissionError):
            table_api.transfer_table(invoice, foreign)

        self.assertEqual(frappe.db.get_value("POS Invoice", invoice, "restaurant_table"), source)

    def test_transfer_to_an_unknown_table_is_refused(self):
        source = self._table("S6")
        invoice = self._dine_in(source)["invoice"]

        with self.assertRaises(frappe.DoesNotExistError):
            table_api.transfer_table(invoice, "YO'Q-STOL")

    def test_double_transfer_is_refused_and_moving_back_works(self):
        source, target = self._table("S7"), self._table("T7")
        invoice = self._dine_in(source)["invoice"]

        table_api.transfer_table(invoice, target)
        with self.assertRaises(frappe.ValidationError):
            table_api.transfer_table(invoice, target)

        # Eski stol bo'shagan edi — qaytarish mumkin.
        table_api.transfer_table(invoice, source)
        self.assertEqual(frappe.db.get_value("POS Invoice", invoice, "restaurant_table"), source)
        self.assertEqual(self._flags(target).occupied, 0)
        self.assertEqual(self._flags(source).occupied, 1)

    def test_take_away_order_has_no_table_to_transfer(self):
        target = self._table("T8")
        invoice = self._take_away()["invoice"]

        with self.assertRaises(frappe.ValidationError):
            table_api.transfer_table(invoice, target)

    def test_merged_tables_cannot_be_transferred(self):
        a, b, target = self._table("A9"), self._table("B9"), self._table("T9")
        invoice = self._dine_in(a)["invoice"]
        table_api.merge_tables(invoice, [b])

        with self.assertRaises(frappe.ValidationError):
            table_api.transfer_table(invoice, target)

    def test_transfer_into_a_merged_group_is_refused(self):
        a, b, c, source = (self._table(n) for n in ("A10", "B10", "C10", "S10"))
        merged = self._dine_in(a)["invoice"]
        table_api.merge_tables(merged, [b])
        invoice = self._dine_in(source)["invoice"]

        with self.assertRaises(frappe.ValidationError):
            table_api.transfer_table(invoice, b)

    def _stale_pair(self, first, second):
        """Eski to'lovdan qolgan iz: ikki stol bir-biriga bog'langan, buyurtma yo'q."""
        for table, partner in ((first, second), (second, first)):
            frappe.db.set_value("URY Table", table, "merged_with", partner, update_modified=False)

    def test_transfer_into_a_leftover_merged_table_clears_the_stale_link(self):
        source, x, y = self._table("S14"), self._table("X14"), self._table("Y14")
        self._stale_pair(x, y)
        invoice = self._dine_in(source)["invoice"]

        table_api.transfer_table(invoice, x)

        self.assertEqual(frappe.db.get_value("POS Invoice", invoice, "restaurant_table"), x)
        self.assertIsNone(self._flags(x).merged_with)
        self.assertIsNone(self._flags(y).merged_with)
        self.assertEqual(self._flags(y).occupied, 0)
        self.assertFalse(frappe.db.get_value("POS Invoice", invoice, "custom_merged_tables"))

    def test_merge_with_a_leftover_merged_table_clears_the_stale_link(self):
        a, x, y = self._table("A15"), self._table("X15"), self._table("Y15")
        self._stale_pair(x, y)
        invoice = self._dine_in(a)["invoice"]

        result = table_api.merge_tables(invoice, [x])

        self.assertEqual(result["cluster"], [a, x])
        self.assertIsNone(self._flags(y).merged_with)
        self.assertEqual(self._flags(x).merged_with, a)

    def test_cancelled_and_paid_invoices_cannot_be_transferred(self):
        source, target = self._table("S11"), self._table("T11")
        invoice = self._dine_in(source)["invoice"]

        frappe.db.set_value("POS Invoice", invoice, "custom_cancelled", 1)
        with self.assertRaises(frappe.ValidationError):
            table_api.transfer_table(invoice, target)
        frappe.db.set_value("POS Invoice", invoice, "custom_cancelled", 0)

        frappe.db.set_value("POS Invoice", invoice, "docstatus", 1)
        with self.assertRaises(frappe.ValidationError):
            table_api.transfer_table(invoice, target)

    def test_transfer_is_all_or_nothing(self):
        source, target = self._table("S12"), self._table("T12")
        invoice = self._dine_in(source, bar=1)["invoice"]
        emitted = self._emitted()

        with mock.patch.object(order_transfer, "_move_open_kots", side_effect=RuntimeError("uzildi")):
            with self.assertRaises(RuntimeError):
                table_api.transfer_table(invoice, target)

        self.assertEqual(frappe.db.get_value("POS Invoice", invoice, "restaurant_table"), source)
        self.assertEqual(self._flags(source).occupied, 1)
        self.assertEqual(self._flags(target).occupied, 0)
        for kot in self._kots(invoice):
            self.assertEqual(kot.restaurant_table, source)
        # Orqaga qaytgan amal haqida xabar chiqmaydi.
        emitted["emit_floor_change"].assert_not_called()
        emitted["emit_order_change"].assert_not_called()

    def test_split_bills_keep_the_old_table_busy_until_the_last_one_leaves(self):
        """Hisob bo'lingan: bitta stolda ikki chek. Birini ko'chirsak stol band qoladi."""
        source, target = self._table("S13"), self._table("T13")
        from ury.ury.doctype.ury_order.ury_order import split_bill

        order = self._dine_in(source, kitchen=1, bar=1)
        first = order["invoice"]
        moved = next(i["name"] for i in order["items"] if i["item_code"] == self.bar_item)
        sibling = split_bill(first, [{"name": moved, "qty": 1}])["new_invoice"]
        self.assertEqual(frappe.db.get_value("POS Invoice", sibling, "restaurant_table"), source)

        result = table_api.transfer_table(first, target)

        self.assertEqual(result["freed_tables"], [])
        self.assertEqual(self._flags(source).occupied, 1)
        self.assertEqual(self._flags(target).occupied, 1)


class TestTransferAndReservations(OrdersTestCase):
    def _reserve(self, table, **values):
        doc = {
            "doctype": "URY Table Reservation",
            "table": table,
            "customer_name": "Boshqa mehmon",
            "reservation_date": nowdate(),
            "from_time": "19:00:00",
            "to_time": "21:00:00",
            "pax": 2,
            "status": "Confirmed",
        }
        doc.update(values)
        with self._as_admin():
            return frappe.get_doc(doc).insert().name

    def test_table_reserved_for_another_guest_is_refused(self):
        source, target = self._table("RS1"), self._table("RT1")
        invoice = self._dine_in(source)["invoice"]
        self._reserve(target)

        with self.assertRaises(frappe.ValidationError):
            table_api.transfer_table(invoice, target)

        self.assertEqual(frappe.db.get_value("POS Invoice", invoice, "restaurant_table"), source)

    def test_table_reserved_for_this_guest_is_allowed_and_marked_seated(self):
        source, target = self._table("RS2"), self._table("RT2")
        order = self._dine_in(source)
        invoice, customer = order["invoice"], order["customer"]
        reservation = self._reserve(target, customer=customer, customer_name=None)

        result = table_api.transfer_table(invoice, target)

        self.assertEqual(result["reservation"], reservation)
        self.assertEqual(
            frappe.db.get_value("URY Table Reservation", reservation, ["status", "seated_invoice"], as_dict=True),
            {"status": "Seated", "seated_invoice": invoice},
        )
        # Bron yopildi — stol endi faqat OCCUPIED.
        self.assertEqual(table_api.get_table_status(target)["status"], table_status.OCCUPIED)

    def test_reservation_matches_by_guest_name_too(self):
        source, target = self._table("RS3"), self._table("RT3")
        order = self._dine_in(source)
        self._reserve(target, customer_name=order["customer_name"].upper())

        result = table_api.transfer_table(order["invoice"], target)

        self.assertTrue(result["reservation"])


# ═══════════════════════════════════════════════════════════════════
#  Birlashtirish / ajratish
# ═══════════════════════════════════════════════════════════════════

class TestMergeAndUnmerge(OrdersTestCase):
    def test_merge_keeps_ury_model_consistent(self):
        a, b, c = (self._table(n) for n in ("MA", "MB", "MC"))
        invoice = self._dine_in(a)["invoice"]
        emitted = self._emitted()

        result = table_api.merge_tables(invoice, [b, c])

        self.assertEqual(result["table"], a)
        self.assertEqual(result["merged_tables"], [b, c])
        self.assertEqual(result["cluster"], [a, b, c])
        self.assertFalse(result["billed"])

        self.assertEqual(frappe.db.get_value("POS Invoice", invoice, "custom_merged_tables"), f"{b},{c}")
        self.assertEqual(frappe.db.get_value("POS Invoice", invoice, "restaurant_table"), a)
        self.assertEqual(self._flags(a).merged_with, f"{b},{c}")
        self.assertEqual(self._flags(b).merged_with, f"{a},{c}")
        self.assertEqual(self._flags(c).merged_with, f"{a},{b}")
        for table in (a, b, c):
            self.assertEqual(self._flags(table).occupied, 1)

        states = {t["name"]: t for t in table_api.get_floor_plan()["tables"]}
        for table in (a, b, c):
            self.assertEqual(states[table]["status"], table_status.OCCUPIED)
            self.assertEqual(states[table]["order"]["name"], invoice)
            self.assertTrue(states[table]["is_merged"])

        self.assertEqual(set(emitted["emit_floor_change"].call_args.args[1]), {a, b, c})

    def test_merge_accepts_a_json_string(self):
        a, b = self._table("MJ1"), self._table("MJ2")
        invoice = self._dine_in(a)["invoice"]

        table_api.merge_tables(invoice, frappe.as_json([b]))

        self.assertEqual(frappe.db.get_value("POS Invoice", invoice, "custom_merged_tables"), b)

    def test_merge_refuses_other_room_occupied_merged_and_empty(self):
        a, same_room, busy = self._table("MR1"), self._table("MR2"), self._table("MR3")
        other_room = self._table("MR4", room=self._other_room())
        invoice = self._dine_in(a)["invoice"]
        self._dine_in(busy)

        with self.assertRaises(frappe.ValidationError):
            table_api.merge_tables(invoice, [other_room])
        with self.assertRaises(frappe.ValidationError):
            table_api.merge_tables(invoice, [busy])
        with self.assertRaises(frappe.ValidationError):
            table_api.merge_tables(invoice, [])
        with self.assertRaises(frappe.ValidationError):
            table_api.merge_tables(invoice, [a])  # o'zi bilan

        table_api.merge_tables(invoice, [same_room])
        with self.assertRaises(frappe.ValidationError):
            table_api.merge_tables(invoice, [same_room])  # allaqachon birlashtirilgan

        self.assertEqual(frappe.db.get_value("POS Invoice", invoice, "custom_merged_tables"), same_room)

    def test_merge_refuses_unknown_and_foreign_branch_tables(self):
        a, foreign = self._table("MF1"), self._table("MF2")
        frappe.db.set_value("URY Table", foreign, "branch", "Boshqa filial")
        invoice = self._dine_in(a)["invoice"]

        with self.assertRaises(frappe.DoesNotExistError):
            table_api.merge_tables(invoice, ["YO'Q-STOL"])
        with self.assertRaises(cashier_permissions.CashierPermissionError):
            table_api.merge_tables(invoice, [foreign])

        self.assertIsNone(self._flags(a).merged_with)

    def test_take_away_cannot_merge_tables(self):
        invoice = self._take_away()["invoice"]
        with self.assertRaises(frappe.ValidationError):
            table_api.merge_tables(invoice, [self._table("MT")])

    def test_unmerge_releases_only_the_detached_table(self):
        a, b, c = (self._table(n) for n in ("UA", "UB", "UC"))
        invoice = self._dine_in(a)["invoice"]
        table_api.merge_tables(invoice, [b, c])

        result = table_api.unmerge_table(invoice, c)

        self.assertEqual(result["released_table"], c)
        self.assertEqual(result["cluster"], [a, b])
        self.assertEqual(result["merged_tables"], [b])

        self.assertEqual(frappe.db.get_value("POS Invoice", invoice, "custom_merged_tables"), b)
        self.assertEqual(self._flags(a).merged_with, b)
        self.assertEqual(self._flags(b).merged_with, a)
        self.assertIsNone(self._flags(c).merged_with)
        self.assertEqual(self._flags(c).occupied, 0)
        self.assertEqual(self._flags(a).occupied, 1)
        self.assertEqual(self._flags(b).occupied, 1)

        states = {t["name"]: t["status"] for t in table_api.get_floor_plan()["tables"]}
        self.assertEqual(states[c], table_status.AVAILABLE)

    def test_unmerging_the_last_partner_dissolves_the_cluster(self):
        a, b = self._table("UD1"), self._table("UD2")
        invoice = self._dine_in(a)["invoice"]
        table_api.merge_tables(invoice, [b])

        table_api.unmerge_table(invoice, b)

        self.assertIsNone(frappe.db.get_value("POS Invoice", invoice, "custom_merged_tables"))
        self.assertIsNone(self._flags(a).merged_with)
        self.assertEqual(self._flags(a).occupied, 1)
        self.assertEqual(self._flags(b).occupied, 0)

    def test_unmerge_refuses_primary_and_non_members(self):
        a, b, stranger = self._table("UE1"), self._table("UE2"), self._table("UE3")
        invoice = self._dine_in(a)["invoice"]
        table_api.merge_tables(invoice, [b])

        with self.assertRaises(frappe.ValidationError):
            table_api.unmerge_table(invoice, a)
        with self.assertRaises(frappe.ValidationError):
            table_api.unmerge_table(invoice, stranger)

        self.assertEqual(frappe.db.get_value("POS Invoice", invoice, "custom_merged_tables"), b)

    def test_merge_then_transfer_needs_an_unmerge_first(self):
        a, b, target = self._table("UF1"), self._table("UF2"), self._table("UF3")
        invoice = self._dine_in(a)["invoice"]
        table_api.merge_tables(invoice, [b])
        table_api.unmerge_table(invoice, b)

        table_api.transfer_table(invoice, target)

        self.assertEqual(frappe.db.get_value("POS Invoice", invoice, "restaurant_table"), target)

    def test_merge_marks_a_reservation_for_this_guest_as_seated(self):
        a, b = self._table("UG1"), self._table("UG2")
        order = self._dine_in(a)
        with self._as_admin():
            reservation = frappe.get_doc(
                {
                    "doctype": "URY Table Reservation",
                    "table": b,
                    "customer": order["customer"],
                    "reservation_date": nowdate(),
                    "from_time": "19:00:00",
                    "to_time": "21:00:00",
                    "pax": 2,
                    "status": "Confirmed",
                }
            ).insert().name

        result = table_api.merge_tables(order["invoice"], [b])

        self.assertEqual(result["reservations"], [reservation])
        self.assertEqual(frappe.db.get_value("URY Table Reservation", reservation, "status"), "Seated")

    def test_paying_a_merged_order_frees_and_dissolves_the_whole_cluster(self):
        """To'lovdan keyin stollar birlashtirilgan QOLMASLIGI kerak.

        Aks holda keyingi buyurtma asosiy stolga o'tirganda sherik stolni ham
        o'ziga tortardi va sherik stolni ko'chirish/birlashtirishga
        ishlatib bo'lmasdi.
        """
        a, b, c, target = (self._table(n) for n in ("UP1", "UP2", "UP3", "UP4"))
        invoice = self._dine_in(a)["invoice"]
        table_api.merge_tables(invoice, [b, c])

        # To'landi. `on_submit` hook'i shu funksiyani chaqiradi
        # (`overrides/pos_invoice.py`).
        frappe.db.set_value("POS Invoice", invoice, "docstatus", 1)
        pos_invoice_override._reconcile_tables(frappe.get_doc("POS Invoice", invoice))

        for table in (a, b, c):
            self.assertEqual(self._flags(table).occupied, 0)
            self.assertIsNone(self._flags(table).merged_with)

        # Bo'shagan stollar qayta ishlatiladi.
        fresh = self._dine_in(b)["invoice"]
        table_api.transfer_table(fresh, target)
        self.assertEqual(frappe.db.get_value("POS Invoice", fresh, "restaurant_table"), target)

    def test_cancelling_a_merged_order_frees_and_dissolves_the_cluster(self):
        a, b = self._table("UQ1"), self._table("UQ2")
        invoice = self._dine_in(a)["invoice"]
        table_api.merge_tables(invoice, [b])

        result = order_api.cancel_order(invoice, "xato zakaz")

        self.assertEqual(sorted(result["freed_tables"]), sorted([a, b]))
        for table in (a, b):
            self.assertEqual(self._flags(table).occupied, 0)
            self.assertIsNone(self._flags(table).merged_with)

    def test_split_bills_keep_a_merged_cluster_together(self):
        """Ikkinchi ochiq chek qolgan bo'lsa birlashtirish tarqalmaydi."""
        from ury.ury.doctype.ury_order.ury_order import split_bill

        a, b = self._table("UR1"), self._table("UR2")
        order = self._dine_in(a, kitchen=1, bar=1)
        table_api.merge_tables(order["invoice"], [b])
        moved = next(i["name"] for i in order["items"] if i["item_code"] == self.bar_item)
        sibling = split_bill(order["invoice"], [{"name": moved, "qty": 1}])["new_invoice"]
        self.assertEqual(frappe.db.get_value("POS Invoice", sibling, "custom_merged_tables"), b)

        pos_invoice_override._reconcile_tables(frappe.get_doc("POS Invoice", order["invoice"]))

        for table in (a, b):
            self.assertEqual(self._flags(table).occupied, 1)
        self.assertEqual(self._flags(a).merged_with, b)

    def test_merge_is_all_or_nothing(self):
        a, b = self._table("UH1"), self._table("UH2")
        invoice = self._dine_in(a)["invoice"]

        with mock.patch.object(order_transfer, "_mark_seated", side_effect=RuntimeError("uzildi")):
            with mock.patch.object(order_transfer, "_assert_available", return_value=[mock.Mock()]):
                with self.assertRaises(RuntimeError):
                    table_api.merge_tables(invoice, [b])

        self.assertIsNone(self._flags(a).merged_with)
        self.assertIsNone(self._flags(b).merged_with)
        self.assertEqual(self._flags(b).occupied, 0)
        self.assertFalse(frappe.db.get_value("POS Invoice", invoice, "custom_merged_tables"))


# ═══════════════════════════════════════════════════════════════════
#  Kassadan buyurtma
# ═══════════════════════════════════════════════════════════════════

class TestCreateOrder(OrdersTestCase):
    def test_menu_has_the_waiter_shape(self):
        menu = co.get_menu()

        self.assertEqual(sorted(menu), ["courses", "currency", "items", "menu", "modified"])
        self.assertTrue(menu["items"])
        self.assertEqual(
            sorted(menu["items"][0]),
            ["course", "image", "item", "item_name", "rate", "special"],
        )

    def test_menu_rejects_unknown_order_type(self):
        with self.assertRaises(frappe.ValidationError):
            co.get_menu(order_type="Aggregators")

    def test_take_away_needs_no_table_and_reaches_kitchen_and_bar(self):
        tables_before = {
            t.name: t.occupied for t in frappe.get_all("URY Table", fields=["name", "occupied"])
        }

        order = self._take_away(kitchen=2, bar=1)
        invoice = order["invoice"]

        self.assertEqual(order["order_type"], "Take Away")
        self.assertIsNone(order["table"])
        self.assertFalse(order["billed"])
        self.assertTrue(order["can_edit"])
        self.assertTrue(order["last_modified_time"])
        self.assertIsNone(order["delivery"])
        self.assertEqual(order["items"][0]["rate"], 10000)  # narx serverdan

        row = frappe.db.get_value(
            "POS Invoice", invoice,
            ["docstatus", "restaurant_table", "branch", "order_type", "custom_client_ref"],
            as_dict=True,
        )
        self.assertEqual(row.docstatus, 0)
        self.assertFalse(row.restaurant_table)
        self.assertEqual(row.branch, self.branch)
        self.assertTrue(row.custom_client_ref)

        # Oshxona va bar chiptalari — ofitsant buyurtmasidagi kabi.
        kots = self._kots(invoice)
        self.assertEqual(len(kots), 2)
        self.assertEqual(len({k.production for k in kots}), 2)
        self.assertTrue(all(not k.restaurant_table for k in kots))

        # Hech bir stol band bo'lmadi.
        tables_after = {
            t.name: t.occupied for t in frappe.get_all("URY Table", fields=["name", "occupied"])
        }
        self.assertEqual(tables_before, tables_after)

        # Kassa ro'yxatida ko'rinadi.
        self.assertIn(invoice, [o["invoice"] for o in order_api.get_active_orders()])

    def test_invoice_owner_is_the_shift_cashier_and_session_user_is_the_waiter(self):
        invoice = self._take_away()["invoice"]

        row = frappe.db.get_value("POS Invoice", invoice, ["owner", "cashier", "waiter"], as_dict=True)

        self.assertEqual(row.owner, self.cashier)
        self.assertEqual(row.cashier, self.cashier)
        self.assertEqual(row.waiter, frappe.session.user)

    def test_take_away_with_a_table_is_refused(self):
        with self.assertRaises(frappe.ValidationError):
            self._take_away(table=self._table("CT"))

    def test_dine_in_needs_a_table(self):
        with self.assertRaises(frappe.ValidationError):
            co.create_order("Dine In", self._lines())

    def test_dine_in_occupies_the_table_and_sets_the_room(self):
        table = self._table("CD")

        order = self._dine_in(table)

        self.assertEqual(order["table"], table)
        self.assertEqual(order["order_type"], "Dine In")
        self.assertEqual(self._flags(table).occupied, 1)
        self.assertEqual(
            frappe.db.get_value("POS Invoice", order["invoice"], "custom_restaurant_room"),
            self._flags(table).restaurant_room,
        )

    def test_dine_in_on_a_busy_table_points_to_add_items(self):
        table = self._table("CB")
        self._dine_in(table)

        with self.assertRaisesRegex(frappe.ValidationError, "taom qo'shing"):
            self._dine_in(table)

    def test_dine_in_on_a_foreign_or_unknown_table_is_refused(self):
        foreign = self._table("CF")
        frappe.db.set_value("URY Table", foreign, "branch", "Boshqa filial")

        with self.assertRaises(cashier_permissions.CashierPermissionError):
            self._dine_in(foreign)
        with self.assertRaises(frappe.DoesNotExistError):
            self._dine_in("YO'Q-STOL")

    def test_delivery_requires_phone_and_address(self):
        for delivery in (
            None,
            {},
            {"phone": "+998901234567"},
            {"address": "Chilonzor 5"},
            {"phone": "  ", "address": "Chilonzor 5"},
            {"phone": "+998901234567", "address": ""},
        ):
            with self.subTest(delivery=delivery):
                with self.assertRaises(frappe.ValidationError):
                    co.create_order("Delivery", self._lines(), delivery=delivery)

        with self.assertRaises(frappe.ValidationError):
            co.create_order("Delivery", self._lines(), delivery={"phone": "telefon", "address": "x"})

        self.assertFalse(
            frappe.db.exists("POS Invoice", {"order_type": "Delivery", "docstatus": 0, "custom_cancelled": 0})
        )

    def test_delivery_is_stored_on_the_invoice(self):
        order = co.create_order(
            "Delivery", self._lines(),
            delivery=frappe.as_json({"phone": " +998 90 123-45-67 ", "address": " Chilonzor 5 "}),
        )

        self.assertEqual(order["order_type"], "Delivery")
        self.assertIsNone(order["table"])
        self.assertEqual(order["delivery"], {"phone": "+998 90 123-45-67", "address": "Chilonzor 5"})
        self.assertEqual(
            frappe.db.get_value(
                "POS Invoice", order["invoice"],
                ["custom_delivery_phone", "custom_delivery_address"], as_dict=True,
            ),
            {"custom_delivery_phone": "+998 90 123-45-67", "custom_delivery_address": "Chilonzor 5"},
        )

    def test_delivery_details_are_ignored_for_other_order_types(self):
        order = self._take_away(delivery={"phone": "+998901234567", "address": "Chilonzor 5"})
        self.assertIsNone(order["delivery"])

    def test_unknown_order_type_and_empty_items_are_refused(self):
        for order_type in ("Aggregators", "Phone In", "", None):
            with self.subTest(order_type=order_type):
                with self.assertRaises(frappe.ValidationError):
                    co.create_order(order_type, self._lines())

        with self.assertRaises(frappe.ValidationError):
            co.create_order("Take Away", [{"item": self.kitchen_item, "qty": 0}])
        with self.assertRaises(frappe.ValidationError):
            co.create_order("Take Away", [{"item": "YO'Q-TAOM", "qty": 1}])

    def test_client_ref_makes_creation_idempotent(self):
        first = self._take_away(client_ref="test-idem-1")
        kots = len(self._kots(first["invoice"]))

        second = self._take_away(kitchen=5, client_ref="test-idem-1")

        self.assertEqual(second["invoice"], first["invoice"])
        self.assertEqual(second["items"][0]["qty"], first["items"][0]["qty"])
        self.assertEqual(frappe.db.count("POS Invoice", {"custom_client_ref": "test-idem-1"}), 1)
        self.assertEqual(len(self._kots(first["invoice"])), kots)

    def test_other_customer_needs_the_customer_attach_feature(self):
        customer = co.create_customer("Test Mijoz")["name"]

        self._set_feature("customer_attach", False)
        with self.assertRaises(frappe.PermissionError):
            self._take_away(customer=customer)

        self._set_feature("customer_attach", True)
        order = self._take_away(customer=customer)
        self.assertEqual(order["customer"], customer)

    def test_pax_and_comments_are_kept(self):
        table = self._table("CP")

        order = co.create_order(
            "Dine In", self._lines(), table=table, pax=4, comments="Tezroq bo'lsin"
        )

        self.assertEqual(order["pax"], 4)
        self.assertEqual(order["comments"], "Tezroq bo'lsin")


# ═══════════════════════════════════════════════════════════════════
#  Taom qo'shish
# ═══════════════════════════════════════════════════════════════════

class TestAddItems(OrdersTestCase):
    def test_add_items_sends_only_the_new_dish_to_the_kitchen(self):
        order = self._take_away(kitchen=1)
        invoice = order["invoice"]
        self.assertEqual(len(self._kots(invoice)), 1)

        updated = co.add_items(
            invoice, [{"item": self.bar_item, "qty": 2}], order["last_modified_time"]
        )

        quantities = {i["item_code"]: i["qty"] for i in updated["items"]}
        self.assertEqual(quantities, {self.kitchen_item: 1, self.bar_item: 2})
        # Bar uchun yangi chipta, oshxonadagisi o'zgarmadi.
        self.assertEqual(len(self._kots(invoice)), 2)
        self.assertEqual(self._live_kitchen_qty(invoice, self.kitchen_item), 1)
        self.assertEqual(self._live_kitchen_qty(invoice, self.bar_item), 2)

    def test_adding_more_of_the_same_dish_merges_the_line(self):
        order = self._take_away(kitchen=2)

        updated = co.add_items(
            order["invoice"], [{"item": self.kitchen_item, "qty": 3}], order["last_modified_time"]
        )

        self.assertEqual([(i["item_code"], i["qty"]) for i in updated["items"]], [(self.kitchen_item, 5)])
        self.assertEqual(self._live_kitchen_qty(order["invoice"], self.kitchen_item), 5)

    def test_add_items_keeps_the_waiter_and_owner(self):
        order = self._take_away()
        frappe.db.set_value("POS Invoice", order["invoice"], "waiter", "ofitsiant@gmail.com")
        modified = str(frappe.db.get_value("POS Invoice", order["invoice"], "modified"))

        co.add_items(order["invoice"], [{"item": self.bar_item, "qty": 1}], modified)

        row = frappe.db.get_value("POS Invoice", order["invoice"], ["owner", "waiter"], as_dict=True)
        self.assertEqual(row.waiter, "ofitsiant@gmail.com")
        self.assertEqual(row.owner, self.cashier)

    def test_add_items_to_a_dine_in_order_keeps_table_and_room(self):
        table = self._table("AD")
        order = self._dine_in(table)

        updated = co.add_items(
            order["invoice"], [{"item": self.bar_item, "qty": 1}], order["last_modified_time"]
        )

        self.assertEqual(updated["table"], table)
        self.assertEqual(self._flags(table).occupied, 1)
        self.assertEqual({k.restaurant_table for k in self._kots(order["invoice"])}, {table})

    def test_stale_last_modified_time_is_a_conflict(self):
        order = self._take_away()

        with self.assertRaises(frappe.ValidationError):
            co.add_items(order["invoice"], [{"item": self.bar_item, "qty": 1}], "2020-01-01 00:00:00")
        with self.assertRaises(frappe.ValidationError):
            co.add_items(order["invoice"], [{"item": self.bar_item, "qty": 1}], "")

        self.assertEqual(len(frappe.get_doc("POS Invoice", order["invoice"]).items), 1)

    def test_retry_with_the_old_lock_does_not_add_twice(self):
        order = self._take_away()
        item = [{"item": self.bar_item, "qty": 1}]

        co.add_items(order["invoice"], item, order["last_modified_time"])
        with self.assertRaises(frappe.ValidationError):
            co.add_items(order["invoice"], item, order["last_modified_time"])

        self.assertEqual(self._live_kitchen_qty(order["invoice"], self.bar_item), 1)

    def test_billed_cancelled_and_paid_orders_cannot_be_extended(self):
        order = self._take_away()
        invoice = order["invoice"]
        item = [{"item": self.bar_item, "qty": 1}]

        frappe.db.set_value("POS Invoice", invoice, "invoice_printed", 1)
        with self.assertRaises(frappe.ValidationError):
            co.add_items(invoice, item, order["last_modified_time"])
        frappe.db.set_value("POS Invoice", invoice, "invoice_printed", 0)

        frappe.db.set_value("POS Invoice", invoice, "custom_cancelled", 1)
        with self.assertRaises(frappe.ValidationError):
            co.add_items(invoice, item, order["last_modified_time"])
        frappe.db.set_value("POS Invoice", invoice, "custom_cancelled", 0)

        frappe.db.set_value("POS Invoice", invoice, "docstatus", 1)
        with self.assertRaises(frappe.ValidationError):
            co.add_items(invoice, item, order["last_modified_time"])

    def test_empty_item_list_is_refused(self):
        order = self._take_away()
        with self.assertRaises(frappe.ValidationError):
            co.add_items(order["invoice"], [], order["last_modified_time"])

    def test_lines_of_the_same_dish_are_collapsed_only_for_the_dish_being_touched(self):
        lines = [
            {"item": "A", "qty": 1, "comment": "x"},
            {"item": "B", "qty": 2, "comment": ""},
            {"item": "A", "qty": 2, "comment": "y"},
        ]

        collapsed = co._collapse(lines, "A")

        self.assertEqual([(l["item"], l["qty"]) for l in collapsed], [("A", 3), ("B", 2)])
        self.assertEqual(collapsed[0]["comment"], "x")


# ═══════════════════════════════════════════════════════════════════
#  Taom olib tashlash
# ═══════════════════════════════════════════════════════════════════

class TestRemoveItem(OrdersTestCase):
    def _row(self, invoice, item):
        return frappe.db.get_value("POS Invoice Item", {"parent": invoice, "item_code": item}, "name")

    def test_flag_off_forbids_removal_even_for_pending_dishes(self):
        order = self._take_away(kitchen=2, bar=1)
        frappe.db.set_value("POS Profile", self.profile, "remove_items", 0)

        with self.assertRaises(cashier_permissions.CashierPermissionError):
            co.remove_item(order["invoice"], self._row(order["invoice"], self.kitchen_item))

        self.assertEqual(self._live_kitchen_qty(order["invoice"], self.kitchen_item), 2)

    def test_pending_dish_can_be_reduced_and_its_kitchen_ticket_shrinks(self):
        order = self._take_away(kitchen=3, bar=1)
        invoice = order["invoice"]

        updated = co.remove_item(invoice, self._row(invoice, self.kitchen_item), qty=2, reason="mijoz fikridan qaytdi")

        self.assertEqual(
            {i["item_code"]: i["qty"] for i in updated["items"]},
            {self.kitchen_item: 1, self.bar_item: 1},
        )
        # Oshxonada olib tashlangan porsiyalarning chiptasi QOLMADI.
        self.assertEqual(self._live_kitchen_qty(invoice, self.kitchen_item), 1)
        self.assertEqual(self._live_kitchen_qty(invoice, self.bar_item), 1)

    def test_pending_dish_can_be_removed_entirely_and_leaves_no_ticket(self):
        order = self._take_away(kitchen=2, bar=1)
        invoice = order["invoice"]

        updated = co.remove_item(invoice, self._row(invoice, self.kitchen_item))

        self.assertEqual([i["item_code"] for i in updated["items"]], [self.bar_item])
        live = [
            row for row in self._kitchen_rows(invoice, self.kitchen_item)
            if row.custom_kitchen_status != ks.CANCELLED
        ]
        self.assertEqual(live, [])

    def test_removal_is_written_to_the_invoice_history(self):
        order = self._take_away(kitchen=2, bar=1)
        invoice = order["invoice"]

        co.remove_item(invoice, self._row(invoice, self.kitchen_item), qty=1, reason="ortiqcha yozilgan")

        comments = frappe.get_all(
            "Comment",
            filters={"reference_doctype": "POS Invoice", "reference_name": invoice, "comment_type": "Comment"},
            pluck="content",
        )
        self.assertTrue(any("ortiqcha yozilgan" in text for text in comments))

    def test_started_dish_cannot_be_removed_by_a_cashier(self):
        order = self._take_away(kitchen=2, bar=1)
        invoice = order["invoice"]
        for row in self._kitchen_rows(invoice, self.kitchen_item):
            frappe.db.set_value("URY KOT Items", row.name, "custom_kitchen_status", ks.PREPARING)

        with mock.patch.object(cashier_permissions, "has_supervisor_role", return_value=False):
            with self.assertRaises(frappe.ValidationError):
                co.remove_item(invoice, self._row(invoice, self.kitchen_item), qty=1, reason="kech")

        self.assertEqual(frappe.get_doc("POS Invoice", invoice).items[0].qty, 2)
        self.assertEqual(self._live_kitchen_qty(invoice, self.kitchen_item), 2)

    def test_supervisor_can_force_a_started_dish_but_needs_a_reason_and_kitchen_is_told(self):
        order = self._take_away(kitchen=2, bar=1)
        invoice = order["invoice"]
        for row in self._kitchen_rows(invoice, self.kitchen_item):
            frappe.db.set_value("URY KOT Items", row.name, "custom_kitchen_status", ks.PREPARING)
        line = self._row(invoice, self.kitchen_item)

        with mock.patch.object(cashier_permissions, "has_supervisor_role", return_value=True):
            with self.assertRaises(frappe.ValidationError):
                co.remove_item(invoice, line, qty=1)
            self.assertEqual(frappe.get_doc("POS Invoice", invoice).items[0].qty, 2)

            updated = co.remove_item(invoice, line, qty=1, reason="mijoz ketib qoldi")

        self.assertEqual(
            {i["item_code"]: i["qty"] for i in updated["items"]}[self.kitchen_item], 1
        )
        # Pishayotgan porsiya chiptasi ekranda QOLADI va oshpazga «to'xtat»
        # kartasi boradi (bekor qilish KOT'i yopilmagan).
        stop_cards = [
            k for k in self._kots(invoice)
            if k.type in ks.CANCELLATION_KOT_TYPES and not k.verified
        ]
        self.assertTrue(stop_cards)
        self.assertNotEqual(stop_cards[0].order_status, "Cancelled")
        started = [
            r for r in self._kitchen_rows(invoice, self.kitchen_item)
            if r.custom_kitchen_status == ks.PREPARING
        ]
        self.assertTrue(started)

    def test_only_the_pending_portion_can_be_removed_from_a_partly_started_dish(self):
        order = self._take_away(kitchen=1)
        invoice = order["invoice"]
        co.add_items(invoice, [{"item": self.kitchen_item, "qty": 1}], order["last_modified_time"])

        first = next(r for r in self._kitchen_rows(invoice, self.kitchen_item) if r.quantity == "1"
                     and r.custom_kitchen_status == ks.PENDING)
        frappe.db.set_value("URY KOT Items", first.name, "custom_kitchen_status", ks.PREPARING)
        line = self._row(invoice, self.kitchen_item)

        with mock.patch.object(cashier_permissions, "has_supervisor_role", return_value=False):
            # 1 dona olib tashlash — mumkin (navbatdagisi), 2 dona — yo'q.
            with self.assertRaises(frappe.ValidationError):
                co.remove_item(invoice, line, qty=2)
            co.remove_item(invoice, line, qty=1)

        pending = [
            r for r in self._kitchen_rows(invoice, self.kitchen_item)
            if r.custom_kitchen_status == ks.PENDING
        ]
        self.assertEqual(pending, [])
        self.assertEqual(self._live_kitchen_qty(invoice, self.kitchen_item), 1)

    def test_invalid_quantities_unknown_rows_and_last_dish_are_refused(self):
        order = self._take_away(kitchen=2)
        invoice = order["invoice"]
        line = self._row(invoice, self.kitchen_item)

        for qty in (0, -1, 3):
            with self.subTest(qty=qty):
                with self.assertRaises(frappe.ValidationError):
                    co.remove_item(invoice, line, qty=qty)
        with self.assertRaises(frappe.DoesNotExistError):
            co.remove_item(invoice, "yo'q-qator")
        # Oxirgi taomni olib tashlash = butun buyurtmani bekor qilish.
        with self.assertRaises(frappe.ValidationError):
            co.remove_item(invoice, line)

        self.assertEqual(frappe.get_doc("POS Invoice", invoice).items[0].qty, 2)

    def test_billed_order_cannot_lose_dishes(self):
        order = self._take_away(kitchen=1, bar=1)
        frappe.db.set_value("POS Invoice", order["invoice"], "invoice_printed", 1)

        with self.assertRaises(frappe.ValidationError):
            co.remove_item(order["invoice"], self._row(order["invoice"], self.bar_item))


class TestSurplusKitchenRows(OrdersTestCase):
    """URY solishtirishi adashsa ham olib tashlangan taomning chiptasi qolmaydi."""

    def _invoice(self, qty):
        name = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabPOS Invoice`
                (name, creation, modified, owner, modified_by, docstatus,
                 branch, invoice_printed, custom_cancelled)
            values (%s, now(), now(), 'Administrator', 'Administrator', 0, %s, 0, 0)
            """,
            (name, self.branch),
        )
        frappe.db.sql(
            """
            insert into `tabPOS Invoice Item`
                (name, creation, modified, owner, modified_by, docstatus,
                 parent, parenttype, parentfield, idx, item_code, qty, rate, amount)
            values (%s, now(), now(), 'Administrator', 'Administrator', 0,
                 %s, 'POS Invoice', 'items', 1, 'TAOM-Z', %s, 1000, %s)
            """,
            (frappe.generate_hash(length=10), name, qty, qty * 1000),
        )
        return name

    def _kot(self, invoice, rows):
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
        for idx, (quantity, status) in enumerate(rows, start=1):
            frappe.db.sql(
                """
                insert into `tabURY KOT Items`
                    (name, creation, modified, owner, modified_by, docstatus,
                     parent, parenttype, parentfield, idx, item, quantity,
                     custom_kitchen_status)
                values (%s, now(), now(), 'Administrator', 'Administrator', 1,
                     %s, 'URY KOT', 'kot_items', %s, 'TAOM-Z', %s, %s)
                """,
                (frappe.generate_hash(length=10), kot, idx, quantity, status),
            )
        return kot

    def _statuses(self, invoice):
        return sorted((r.custom_kitchen_status, cint(r.quantity)) for r in self._kitchen_rows(invoice))

    def test_surplus_pending_rows_are_closed_newest_first(self):
        invoice = self._invoice(qty=1)
        self._kot(invoice, [(2, ks.PENDING)])
        self._kot(invoice, [(1, ks.PENDING)])

        closed = order_cancel.close_surplus_kitchen_rows(invoice, "TAOM-Z")

        self.assertEqual(closed, 2)
        live = [r for r in self._kitchen_rows(invoice) if r.custom_kitchen_status != ks.CANCELLED]
        self.assertEqual(sum(cint(r.quantity) for r in live), 1)

    def test_no_surplus_is_a_noop(self):
        invoice = self._invoice(qty=3)
        self._kot(invoice, [(2, ks.PENDING)])
        self._kot(invoice, [(1, ks.PENDING)])
        before = self._statuses(invoice)

        self.assertEqual(order_cancel.close_surplus_kitchen_rows(invoice, "TAOM-Z"), 0)
        self.assertEqual(self._statuses(invoice), before)

    def test_started_rows_are_never_touched(self):
        invoice = self._invoice(qty=1)
        self._kot(invoice, [(1, ks.PREPARING)])
        self._kot(invoice, [(1, ks.SERVED)])
        before = self._statuses(invoice)

        self.assertEqual(order_cancel.close_surplus_kitchen_rows(invoice, "TAOM-Z"), 0)
        self.assertEqual(self._statuses(invoice), before)


# ═══════════════════════════════════════════════════════════════════
#  Mijoz
# ═══════════════════════════════════════════════════════════════════

class TestCustomers(OrdersTestCase):
    def test_create_customer_needs_only_a_name(self):
        result = co.create_customer("Faqat Ism")

        self.assertEqual(result["customer_name"], "Faqat Ism")
        self.assertIsNone(result["mobile_number"])
        self.assertTrue(frappe.db.exists("Customer", result["name"]))

    def test_create_customer_with_phone_and_address(self):
        result = co.create_customer("Telefonli", "+998901234567", "Yunusobod 7")

        row = frappe.db.get_value(
            "Customer", result["name"], ["mobile_number", "customer_details"], as_dict=True
        )
        self.assertEqual(row.mobile_number, "+998901234567")
        self.assertEqual(row.customer_details, "Yunusobod 7")
        self.assertEqual(result["address"], "Yunusobod 7")

    def test_create_customer_is_not_committed_and_rejects_bad_input(self):
        with self.assertRaises(frappe.ValidationError):
            co.create_customer("   ")
        with self.assertRaises(frappe.ValidationError):
            co.create_customer("Telefon xato", "telefon")

    def test_search_finds_by_name_and_by_phone(self):
        created = co.create_customer("Qidiruv Sinovi", "+998907654321")["name"]

        by_name = [c["name"] for c in co.search_customers("Qidiruv Sin")]
        by_phone = [c["name"] for c in co.search_customers("907654321")]

        self.assertIn(created, by_name)
        self.assertIn(created, by_phone)
        self.assertEqual(sorted(co.search_customers("Qidiruv Sin")[0]), ["customer_name", "mobile_number", "name"])

    def test_search_respects_the_limit(self):
        for index in range(3):
            co.create_customer(f"Limit Sinovi {index}")
        self.assertEqual(len(co.search_customers("Limit Sinovi", limit=2)), 2)

    def test_set_customer_on_a_draft_invoice(self):
        order = self._take_away()
        customer = co.create_customer("Doimiy Mijoz", "+998901112233")["name"]

        updated = co.set_customer(order["invoice"], customer)

        self.assertEqual(updated["customer"], customer)
        self.assertEqual(updated["customer_name"], "Doimiy Mijoz")
        row = frappe.db.get_value(
            "POS Invoice", order["invoice"], ["customer", "customer_name", "mobile_number"], as_dict=True
        )
        self.assertEqual(row.customer, customer)
        self.assertEqual(row.customer_name, "Doimiy Mijoz")
        self.assertEqual(row.mobile_number, "+998901112233")

    def test_set_customer_is_idempotent(self):
        order = self._take_away()
        customer = co.create_customer("Ikki Marta")["name"]

        co.set_customer(order["invoice"], customer)
        modified = frappe.db.get_value("POS Invoice", order["invoice"], "modified")
        co.set_customer(order["invoice"], customer)

        self.assertEqual(frappe.db.get_value("POS Invoice", order["invoice"], "modified"), modified)

    def test_set_customer_on_a_billed_invoice_marks_it_for_reprint(self):
        if not frappe.db.has_column("POS Invoice", "custom_reprint_needed"):
            self.skipTest("`custom_reprint_needed` maydonini hisob moduli yaratadi")

        order = self._take_away()
        frappe.db.set_value("POS Invoice", order["invoice"], "invoice_printed", 1)
        customer = co.create_customer("Chekdan Keyin")["name"]

        co.set_customer(order["invoice"], customer)

        self.assertEqual(frappe.db.get_value("POS Invoice", order["invoice"], "custom_reprint_needed"), 1)

    def test_set_customer_on_an_unbilled_invoice_does_not_flag_reprint(self):
        if not frappe.db.has_column("POS Invoice", "custom_reprint_needed"):
            self.skipTest("`custom_reprint_needed` maydonini hisob moduli yaratadi")

        order = self._take_away()
        customer = co.create_customer("Chekdan Oldin")["name"]

        co.set_customer(order["invoice"], customer)

        self.assertFalse(frappe.db.get_value("POS Invoice", order["invoice"], "custom_reprint_needed"))

    def test_set_customer_refuses_paid_cancelled_and_unknown(self):
        order = self._take_away()
        invoice = order["invoice"]
        customer = co.create_customer("Rad Etiladi")["name"]

        with self.assertRaises(frappe.DoesNotExistError):
            co.set_customer(invoice, "YO'Q-MIJOZ")

        frappe.db.set_value("POS Invoice", invoice, "custom_cancelled", 1)
        with self.assertRaises(frappe.ValidationError):
            co.set_customer(invoice, customer)
        frappe.db.set_value("POS Invoice", invoice, "custom_cancelled", 0)

        frappe.db.set_value("POS Invoice", invoice, "docstatus", 1)
        with self.assertRaises(frappe.ValidationError):
            co.set_customer(invoice, customer)

    def test_disabled_customer_cannot_be_attached(self):
        order = self._take_away()
        customer = co.create_customer("O'chirilgan")["name"]
        frappe.db.set_value("Customer", customer, "disabled", 1)

        with self.assertRaises(frappe.ValidationError):
            co.set_customer(order["invoice"], customer)
