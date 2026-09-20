# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""3-to'lqin: buyurtma/stol yo'nalishini ADVERSARIAL tekshirish.

    cd /home/sherzod/frappe-bench && flock /tmp/ozturk_tests.lock \
        bench --site ozturk.local run-tests \
        --module ozturkapp.ozturkapp.tests.test_cashier_review_orders

Har bir sinf bitta hujum yo'nalishi: ruxsat/izolyatsiya, ofitsantga pul
oqishi, buyurtma kiritish, taom olib tashlash, stollar, bron, ishlash.
Testlar o'z savepoint'ida ishlaydi va orqaga qaytadi — dev bazaga tegilmaydi
(fikstura `test_cashier_orders.OrdersTestCase` dan).
"""

import ast
import inspect
import textwrap
from unittest import mock

import frappe
from frappe.utils import add_days, flt, nowdate

from ozturkapp.ozturkapp.api import cashier_orders as co
from ozturkapp.ozturkapp.api import kitchen as kitchen_api
from ozturkapp.ozturkapp.api import order as order_api
from ozturkapp.ozturkapp.api import table as table_api
from ozturkapp.ozturkapp.api import waiter
from ozturkapp.ozturkapp.tests.test_cashier_orders import OrdersTestCase
from ozturkapp.ozturkapp.utils import (
    cashier_permissions,
    order_items,
    order_transfer,
    table_status,
)

FOREIGN_BRANCH = "ZZ-Review-Boshqa-Filial"


def _new_user(email, roles, branch=None):
    """Faqat berilgan rollarga ega vaqtdagi foydalanuvchi (test oxirida rollback).

    `branch` berilsa foydalanuvchi shu filialga `URY User` orqali biriktiriladi."""
    user = frappe.get_doc(
        {
            "doctype": "User",
            "email": email,
            "first_name": "Review",
            "send_welcome_email": 0,
            "roles": [{"role": role} for role in roles],
        }
    )
    user.insert(ignore_permissions=True)
    if branch:
        frappe.get_doc(
            {
                "doctype": "URY User",
                "parent": branch,
                "parenttype": "Branch",
                "parentfield": "user",
                "user": user.name,
            }
        ).insert(ignore_permissions=True)
    return user.name


class ReviewCase(OrdersTestCase):
    """Fikstura + boshqa filial yordamchilari."""

    def _foreign_branch(self):
        if not frappe.db.exists("Branch", FOREIGN_BRANCH):
            with self._as_admin():
                frappe.get_doc(
                    {
                        "doctype": "Branch",
                        "branch": FOREIGN_BRANCH,
                        "user": [{"user": "Guest"}],
                    }
                ).insert()
        return FOREIGN_BRANCH

    def _foreign_table(self, name="F1"):
        table = self._table(name)
        frappe.db.set_value("URY Table", table, "branch", self._foreign_branch())
        return table

    def _foreign_invoice(self, name="F2"):
        """Boshqa filialga tegishli haqiqiy ochiq chek + uning stoli."""
        table = self._table(name)
        invoice = self._dine_in(table)["invoice"]
        frappe.db.set_value("POS Invoice", invoice, "branch", self._foreign_branch())
        frappe.db.set_value("URY Table", table, "branch", self._foreign_branch())
        return invoice, table

    def _foreign_reservation(self, table):
        with self._as_admin():
            reservation = frappe.get_doc(
                {
                    "doctype": "URY Table Reservation",
                    "table": table,
                    "restaurant": frappe.db.get_value("URY Table", table, "restaurant"),
                    "branch": self.branch,
                    "customer_name": "Begona",
                    "pax": 2,
                    "reservation_date": nowdate(),
                    "from_time": "20:00:00",
                    "to_time": "21:00:00",
                    "status": "Confirmed",
                }
            ).insert()
        frappe.db.set_value(
            "URY Table Reservation", reservation.name, "branch", self._foreign_branch()
        )
        return reservation.name

    def _as(self, user):
        frappe.set_user(user)
        frappe.local._ozturk_scope_cache = {}


# ═══════════════════════════════════════════════════════════════════
#  1. Ruxsat va izolyatsiya
# ═══════════════════════════════════════════════════════════════════

GUARD_NAMES = (
    "require_cashier", "require_waiter", "require_kitchen", "_begin",
    "_prepare_table_operation", "get_active_orders",
)


def _whitelisted_of(*modules):
    names = {module.__name__ for module in modules}
    return sorted(
        (fn for fn in frappe.whitelisted if getattr(fn, "__module__", None) in names),
        key=lambda fn: (fn.__module__, fn.__name__),
    )


class TestEveryEndpointIsGuarded(ReviewCase):
    def test_first_statement_of_every_whitelisted_function_is_a_role_guard(self):
        """Kassa/ofitsant/oshxona API'ning HAR BIR metodi rol tekshiruvi bilan boshlanadi."""
        found = _whitelisted_of(order_api, table_api, co, waiter, kitchen_api)
        self.assertGreater(len(found), 30, "introspeksiya bo'sh — sinov ma'nosiz")

        bad = []
        for fn in found:
            tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
            body = tree.body[0].body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(getattr(body[0], "value", None), ast.Constant)
            ):
                body = body[1:]
            head = ast.unparse(body[0]) if body else ""
            if not any(name in head for name in GUARD_NAMES):
                bad.append(f"{fn.__module__}.{fn.__name__}: {head[:60]}")

        self.assertEqual(bad, [], "himoyasiz endpointlar")

    def test_no_endpoint_allows_guests(self):
        for fn in _whitelisted_of(order_api, table_api, co, waiter, kitchen_api):
            with self.subTest(fn=fn.__name__):
                self.assertNotIn(fn, frappe.guest_methods)


class TestCrossBranchIsolation(ReviewCase):
    """Boshqa filialning stoli / cheki / broni bilan HECH QANDAY endpoint ishlamasin."""

    def test_cashier_endpoints_refuse_foreign_objects(self):
        foreign_invoice, foreign_table_of_invoice = self._foreign_invoice("FI")
        foreign_table = self._foreign_table("FT")
        foreign_reservation = self._foreign_reservation(foreign_table)
        own_table = self._table("OWN")
        own_invoice = self._dine_in(own_table)["invoice"]

        calls = {
            "get_table_status": lambda: table_api.get_table_status(foreign_table),
            "get_table_detail": lambda: table_api.get_table_detail(foreign_table),
            "update_table_layout": lambda: table_api.update_table_layout(foreign_table, 1, 1),
            "reserve_table": lambda: table_api.reserve_table(foreign_table, "Mehmon"),
            "cancel_reservation/table": lambda: table_api.cancel_reservation(table=foreign_table),
            "cancel_reservation/id": lambda: table_api.cancel_reservation(
                reservation=foreign_reservation
            ),
            "transfer_table/foreign_invoice": lambda: table_api.transfer_table(
                foreign_invoice, self._table("FREE1")
            ),
            "transfer_table/foreign_target": lambda: table_api.transfer_table(
                own_invoice, foreign_table
            ),
            "merge_tables/foreign_invoice": lambda: table_api.merge_tables(
                foreign_invoice, [self._table("FREE2")]
            ),
            "merge_tables/foreign_target": lambda: table_api.merge_tables(
                own_invoice, [foreign_table]
            ),
            "unmerge_table/foreign_invoice": lambda: table_api.unmerge_table(
                foreign_invoice, foreign_table_of_invoice
            ),
            "get_table_order": lambda: order_api.get_table_order(foreign_table_of_invoice),
            "get_order_bill_preview": lambda: order_api.get_order_bill_preview(foreign_invoice),
            "cancel_order": lambda: order_api.cancel_order(foreign_invoice, "sabab bor"),
            "add_items": lambda: co.add_items(
                foreign_invoice, self._lines(), str(frappe.db.get_value(
                    "POS Invoice", foreign_invoice, "modified"))
            ),
            "remove_item": lambda: co.remove_item(foreign_invoice, "x"),
            "set_customer": lambda: co.set_customer(foreign_invoice, self.scope.default_customer),
            "create_order/foreign_table": lambda: co.create_order(
                "Dine In", self._lines(), table=foreign_table
            ),
        }

        for name, call in calls.items():
            with self.subTest(endpoint=name):
                with self.assertRaises(frappe.PermissionError):
                    call()

        # Hech biri boshqa filialning chekini o'zgartirmagan bo'lishi kerak.
        self.assertEqual(
            frappe.db.get_value("POS Invoice", foreign_invoice, "restaurant_table"),
            foreign_table_of_invoice,
        )

    def test_release_table_refuses_a_foreign_table(self):
        foreign_table = self._foreign_table("FR")
        manager = _new_user("rev-mgr-release@example.com", ["URY Manager"], self.branch)
        self._as(manager)
        with self.assertRaises(frappe.PermissionError):
            table_api.release_table(foreign_table, "sabab")

    def test_waiter_endpoints_refuse_foreign_objects(self):
        foreign_invoice, foreign_table_of_invoice = self._foreign_invoice("WI")
        foreign_table = self._foreign_table("WT")
        captain = _new_user("rev-captain-x@example.com", [waiter.WAITER_ROLE], self.branch)
        self._as(captain)

        calls = {
            "get_order/invoice": lambda: waiter.get_order(invoice=foreign_invoice),
            "get_order/table": lambda: waiter.get_order(table=foreign_table_of_invoice),
            "submit_order": lambda: waiter.submit_order(foreign_table, self._lines()),
            "request_bill": lambda: waiter.request_bill(foreign_invoice),
            "cancel_order": lambda: waiter.cancel_order(foreign_invoice, "sabab"),
        }
        for name, call in calls.items():
            with self.subTest(endpoint=name):
                with self.assertRaises(frappe.PermissionError):
                    call()

    def test_client_ref_of_a_foreign_invoice_is_not_a_backdoor(self):
        """Boshqa filial chekining `client_ref` i bilan `create_order` uni qaytarmasin."""
        foreign_invoice, _ = self._foreign_invoice("CR")
        frappe.db.set_value("POS Invoice", foreign_invoice, "custom_client_ref", "REV-CR-1")

        with self.assertRaises(frappe.PermissionError):
            co.create_order("Take Away", self._lines(), client_ref="REV-CR-1")

    def test_invoice_without_a_branch_is_not_open_to_everyone(self):
        """`branch` bo'sh chek ("hech kimniki emas") har qanday filial kassiriga ochiq bo'lmasin."""
        table = self._table("NB")
        invoice = self._dine_in(table)["invoice"]
        frappe.db.set_value("POS Invoice", invoice, "branch", None)
        frappe.db.set_value("POS Invoice", invoice, "pos_profile", "ZZ-Boshqa-Profil")

        with self.assertRaises(frappe.PermissionError):
            order_api.get_order_bill_preview(invoice)


class TestRolesAndScopes(ReviewCase):
    def test_a_waiter_cannot_call_any_cashier_endpoint(self):
        captain = _new_user("rev-captain-y@example.com", [waiter.WAITER_ROLE])
        self._as(captain)

        calls = [
            lambda: table_api.get_floor_plan(),
            lambda: table_api.get_table_status("x"),
            lambda: table_api.get_table_detail("x"),
            lambda: table_api.update_table_layout("x", 1, 1),
            lambda: table_api.reserve_table("x", "m"),
            lambda: table_api.get_reservations(),
            lambda: table_api.cancel_reservation(table="x"),
            lambda: table_api.release_table("x", "s"),
            lambda: table_api.transfer_table("x", "y"),
            lambda: table_api.merge_tables("x", ["y"]),
            lambda: table_api.unmerge_table("x", "y"),
            lambda: order_api.get_active_orders(),
            lambda: order_api.get_paid_orders(),
            lambda: order_api.get_paid_order_filter_options(),
            lambda: order_api.get_order_counts(),
            lambda: order_api.get_table_order("x"),
            lambda: order_api.get_order_bill_preview("x"),
            lambda: order_api.cancel_order("x", "s"),
            lambda: co.get_menu(),
            lambda: co.create_order("Take Away", []),
            lambda: co.add_items("x", [], "t"),
            lambda: co.remove_item("x", "y"),
            lambda: co.search_customers("a"),
            lambda: co.create_customer("a"),
            lambda: co.set_customer("x", "y"),
        ]
        for index, call in enumerate(calls):
            with self.subTest(call=index):
                with self.assertRaises(cashier_permissions.CashierPermissionError):
                    call()

    def test_a_cashier_cannot_call_waiter_or_kitchen_endpoints(self):
        cashier = _new_user("rev-cashier-only@example.com", ["URY Cashier"])
        self._as(cashier)

        for name, call in {
            "get_context": lambda: waiter.get_context(),
            "get_bootstrap": lambda: waiter.get_bootstrap(),
            "get_tables": lambda: waiter.get_tables(),
            "get_order": lambda: waiter.get_order(invoice="x"),
            "submit_order": lambda: waiter.submit_order("x", []),
            "request_bill": lambda: waiter.request_bill("x"),
            "cancel_order": lambda: waiter.cancel_order("x", "s"),
            "mark_delivered": lambda: waiter.mark_delivered("x"),
            "kitchen.get_active_kots": lambda: kitchen_api.get_active_kots(),
            "kitchen.update_kot_item_status": lambda: kitchen_api.update_kot_item_status("x", "Preparing"),
        }.items():
            with self.subTest(endpoint=name):
                with self.assertRaises(frappe.PermissionError):
                    call()

    def test_guest_is_rejected_everywhere(self):
        frappe.set_user("Guest")
        frappe.local._ozturk_scope_cache = {}
        for name, call in {
            "floor": lambda: table_api.get_floor_plan(),
            "orders": lambda: order_api.get_active_orders(),
            "menu": lambda: co.get_menu(),
            "waiter": lambda: waiter.get_tables(),
            "kitchen": lambda: kitchen_api.get_active_kots(),
        }.items():
            with self.subTest(endpoint=name):
                with self.assertRaises(frappe.PermissionError):
                    call()

    def test_cashier_without_a_branch_mapping_is_not_guessed_onto_a_branch(self):
        """Ikkinchi filial paydo bo'lganda xaritasiz kassir AVTOMATIK birinchisiga tushmasin."""
        self._foreign_branch()
        loner = _new_user("rev-loner@example.com", ["URY Cashier"])
        self._as(loner)

        with self.assertRaises(frappe.ValidationError):
            table_api.get_floor_plan()


# ═══════════════════════════════════════════════════════════════════
#  2. Ofitsantga pul oqishi
# ═══════════════════════════════════════════════════════════════════

#: Ofitsantning HECH QAYSI javobida (ichma-ich ham) bo'lmasligi kerak kalitlar.
#: `rate`/`amount` faqat `items[]` ichida ruxsat: ofitsant taom narxini ko'radi.
MONEY_KEYS = {
    "total", "net_total", "grand_total", "rounded_total", "payable", "tip",
    "discount", "discount_amount", "discount_percent", "discount_reason",
    "discount_approved_by", "discount_approved_by_name",
    "taxes", "total_taxes", "service_charge", "service_charge_rate",
    "service_charge_amount", "paid_amount", "change_amount", "payments",
}


def _keys(payload, inside_items=False):
    """Ichma-ich barcha kalitlar; `items[]` qatoridagi `rate`/`amount` — istisno."""
    found = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.add(key)
            found |= _keys(value, inside_items or key == "items")
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            found |= _keys(value, inside_items)
    return found


def _numbers(payload) -> set:
    """Xabardagi barcha SON qiymatlar (satrlar hisobga olinmaydi)."""
    found = set()
    if isinstance(payload, dict):
        for value in payload.values():
            found |= _numbers(value)
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            found |= _numbers(value)
    elif isinstance(payload, (int, float)) and not isinstance(payload, bool):
        found.add(flt(payload))
    return found


class TestWaiterMoneyLeak(ReviewCase):
    def _discounted_order_with_service_charge(self):
        """Chegirmali va xizmat haqi (soliq) qatorli stol buyurtmasi."""
        from ozturkapp.ozturkapp.utils import discounts

        table = self._table("WM")
        invoice = self._dine_in(table, kitchen=2, bar=1)["invoice"]
        with self._as_admin():
            discounts.apply_discount(invoice, self.scope, percent=10, reason="sinov")
        doc = frappe.get_doc("POS Invoice", invoice)
        if not flt(doc.total_taxes_and_charges):
            row = frappe.get_all(
                "Sales Taxes and Charges",
                filters={"parenttype": "POS Invoice", "charge_type": ["!=", "Actual"]},
                fields=["account_head", "rate", "charge_type", "description"],
                limit=1,
            )
            if not row:
                self.skipTest("Dev bazada xizmat haqi shabloni yo'q")
            doc.append("taxes", dict(row[0], cost_center=self.scope.cost_center))
            with self._as_admin():
                doc.save()
        return table, invoice

    def _captain(self):
        user = _new_user("rev-captain-money@example.com", [waiter.WAITER_ROLE], self.branch)
        self._as(user)

    def test_get_order_has_no_money_keys_at_any_depth(self):
        table, invoice = self._discounted_order_with_service_charge()
        self._captain()

        for payload in (waiter.get_order(invoice=invoice), waiter.get_order(table=table)):
            self.assertFalse(_keys(payload) & MONEY_KEYS, _keys(payload) & MONEY_KEYS)

    def test_subtotal_does_not_reveal_the_discount(self):
        """`subtotal` = chegirmadan keyingi `net_total` bo'lsa, ofitsant chegirmani
        `taomlar summasi` bilan solishtirib topadi."""
        table, invoice = self._discounted_order_with_service_charge()
        self._captain()

        payload = waiter.get_order(invoice=invoice)

        items_sum = sum(flt(row["amount"]) for row in payload["items"])
        self.assertEqual(flt(payload["subtotal"]), items_sum)
        self.assertEqual(flt(payload["items_total"]), items_sum)

    def test_tables_payload_does_not_carry_the_grand_total(self):
        """Stol kartasidagi `amount` xizmat haqi va chegirma bilan hisoblangan
        JAMI bo'lmasin: ofitsant faqat taomlar summasini ko'radi."""
        table, invoice = self._discounted_order_with_service_charge()
        self._captain()

        tile = next(t for t in waiter.get_tables()["tables"] if t["name"] == table)

        order = waiter.get_order(invoice=invoice)
        grand = flt(frappe.db.get_value("POS Invoice", invoice, "rounded_total"))
        self.assertNotEqual(flt(tile["amount"]), grand, "stol kartasi jami summani ko'rsatyapti")
        self.assertEqual(flt(tile["amount"]), flt(order["items_total"]))

    def test_bootstrap_and_submit_order_carry_no_money(self):
        table, invoice = self._discounted_order_with_service_charge()
        self._captain()

        boot = waiter.get_bootstrap()
        tile = next(t for t in boot["tables"]["tables"] if t["name"] == table)
        self.assertFalse(_keys(boot) & MONEY_KEYS, _keys(boot) & MONEY_KEYS)
        self.assertEqual(
            flt(tile["amount"]),
            flt(waiter.get_order(invoice=invoice)["items_total"]),
        )

        # `submit_order` javobi = `get_order` — bir xil filtr.
        modified = str(frappe.db.get_value("POS Invoice", invoice, "modified"))
        result = waiter.submit_order(
            table, [{"item": self.bar_item, "qty": 1}], last_modified_time=modified
        )
        self.assertFalse(_keys(result) & MONEY_KEYS, _keys(result) & MONEY_KEYS)

    def test_whitelist_removed_only_money_keys_the_app_never_reads(self):
        """`WAITER_BILL_KEYS` ga o'tishdan oldin ofitsantga ko'ringan (qora ro'yxat
        bo'yicha qolgan) kalitlar hisobi: yo'qolganlarning hammasi pul kalitlari."""
        from ozturkapp.ozturkapp.utils import cashier_billing

        table, invoice = self._discounted_order_with_service_charge()
        bill = cashier_billing.build_bill(frappe.get_doc("POS Invoice", invoice), self.scope)
        old_blacklist = {
            "service_charge", "service_charge_rate", "taxes", "total_taxes",
            "grand_total", "rounded_total",
        }
        was_visible = set(bill) - old_blacklist
        now_visible = set(waiter._strip_financials(bill)) - {"items_total"}

        hidden_now = was_visible - now_visible
        # Flutter ilovasi (`ozturk-waiter/lib/models/models.dart`) o'qiydigan kalitlar:
        app_reads = {
            "invoice", "items", "subtotal", "items_total", "currency", "table",
            "waiter_name", "customer_name", "pax", "opened_at", "bill_requested",
            "can_edit", "last_modified_time", "order_number", "cancellation",
        }
        self.assertFalse(hidden_now & app_reads, hidden_now & app_reads)
        self.assertLessEqual(
            hidden_now - MONEY_KEYS,
            {"is_return", "return_against", "reprint_needed"},
            f"ofitsantdan pul bo'lmagan kalit yashirildi: {hidden_now - MONEY_KEYS}",
        )

    def test_realtime_payloads_carry_no_money(self):
        """Barcha yozuv amallarining realtime xabarlari faqat identifikatorlar tashiydi."""
        events = []

        def record(event, message=None, **kwargs):
            events.append((event, message, kwargs))

        with mock.patch.object(frappe, "publish_realtime", side_effect=record):
            table, invoice = self._discounted_order_with_service_charge()
            a, b = self._table("RT1"), self._table("RT2")
            second = self._dine_in(a)
            co.add_items(second["invoice"], [{"item": self.bar_item, "qty": 1}], second["last_modified_time"])
            table_api.merge_tables(second["invoice"], [b])
            table_api.unmerge_table(second["invoice"], b)
            table_api.transfer_table(second["invoice"], self._table("RT3"))
            co.set_customer(second["invoice"], co.create_customer("Realtime Sinov")["name"])

        self.assertTrue(events, "hech qanday realtime xabar tutilmadi")
        totals = {
            flt(frappe.db.get_value("POS Invoice", invoice, field))
            for field in ("grand_total", "rounded_total", "net_total")
        } - {0.0}
        for event, message, _ in events:
            with self.subTest(event=event):
                self.assertFalse(_keys(message) & MONEY_KEYS, _keys(message) & MONEY_KEYS)
                numbers = _numbers(message)
                self.assertFalse(numbers & totals, "xabarda chek summasi bor")


# ═══════════════════════════════════════════════════════════════════
#  3. Buyurtma kiritish
# ═══════════════════════════════════════════════════════════════════

class TestOrderEntryAttacks(ReviewCase):
    def _invoice_rows(self, invoice):
        return frappe.get_all(
            "POS Invoice Item",
            filters={"parent": invoice},
            fields=["item_code", "qty", "rate", "amount", "comment"],
            order_by="idx asc",
        )

    def test_client_prices_are_ignored(self):
        item_price = frappe.db.get_value(
            "Item Price", {"item_code": self.kitchen_item}, "price_list_rate"
        )
        order = co.create_order(
            "Take Away",
            [{"item": self.kitchen_item, "qty": 1, "rate": 1, "amount": 1,
              "price_list_rate": 1, "discount_percentage": 100}],
        )
        row = self._invoice_rows(order["invoice"])[0]
        self.assertEqual(flt(row.rate), flt(item_price))
        self.assertEqual(flt(row.amount), flt(item_price))

    def test_quantities_that_are_not_whole_positive_numbers_are_refused(self):
        """0 — "savatdan olib tashlangan" qator (jimgina tashlanadi), qolgan
        yaroqsiz qiymatlar (manfiy, kasr, matn, NaN, ulkan) XATO berishi kerak:
        aks holda taom jimgina yo'qoladi yoki qty o'zgaradi."""
        for bad in (-1, 2.5, "abc", "nan", float("nan"), float("inf"), 10 ** 9, True, [1], {"a": 1}):
            with self.subTest(qty=repr(bad)):
                with self.assertRaises(frappe.ValidationError):
                    co.create_order("Take Away", [{"item": self.kitchen_item, "qty": bad}])

    def test_a_zero_quantity_row_is_dropped_but_not_the_order(self):
        order = co.create_order(
            "Take Away",
            [{"item": self.kitchen_item, "qty": 2}, {"item": self.bar_item, "qty": 0}],
        )
        self.assertEqual([(r.item_code, r.qty) for r in self._invoice_rows(order["invoice"])],
                         [(self.kitchen_item, 2)])

    def test_numeric_strings_and_integral_floats_are_accepted(self):
        order = co.create_order(
            "Take Away",
            [{"item": self.kitchen_item, "qty": "2"}, {"item": self.bar_item, "qty": 3.0}],
        )
        self.assertEqual(sorted(r.qty for r in self._invoice_rows(order["invoice"])), [2, 3])

    def test_malformed_item_rows_give_a_validation_error_not_a_crash(self):
        for bad in (["BURGER"], [None], [5], "[1,2]", {"item": self.kitchen_item, "qty": 1}):
            with self.subTest(items=repr(bad)):
                with self.assertRaises(frappe.ValidationError):
                    co.create_order("Take Away", bad)

    def test_an_order_cannot_carry_thousands_of_lines(self):
        rows = [{"item": self.kitchen_item, "qty": 1}] * 500
        with self.assertRaises(frappe.ValidationError):
            co.create_order("Take Away", rows)

    def test_duplicate_lines_reach_the_kitchen_with_the_right_total(self):
        order = co.create_order(
            "Take Away",
            [{"item": self.kitchen_item, "qty": 1}, {"item": self.kitchen_item, "qty": 2}],
        )
        on_invoice = sum(r.qty for r in self._invoice_rows(order["invoice"]))
        self.assertEqual(on_invoice, 3)
        self.assertEqual(self._live_kitchen_qty(order["invoice"], self.kitchen_item), 3)

    def test_a_second_portion_with_another_comment_does_not_rewrite_the_first_ones_note(self):
        order = co.create_order(
            "Take Away", [{"item": self.kitchen_item, "qty": 1, "comment": "medium"}]
        )
        co.add_items(
            order["invoice"],
            [{"item": self.kitchen_item, "qty": 1, "comment": "well done"}],
            order["last_modified_time"],
        )
        notes = sorted(
            (row.comments or "") for row in frappe.get_all(
                "URY KOT Items",
                filters={"parent": ["in", [k.name for k in self._kots(order["invoice"])]]},
                fields=["comments"],
            )
        )
        self.assertIn("medium", notes)
        self.assertIn("well done", notes)

    def test_control_characters_never_reach_the_invoice_or_the_printer(self):
        """ESC/POS baytlari (masalan `ESC p` = g'aladon impulsi) izoh orqali
        chek/oshxona chiptasiga o'tib ketmasin."""
        nasty = "Piyozsiz\x1bp\x00\x19\xfa\x1d\x56\x00\x07 \u202eLIFO"
        order = co.create_order(
            "Take Away",
            [{"item": self.kitchen_item, "qty": 1, "comment": nasty}],
            comments=nasty,
        )
        stored = frappe.db.get_value("POS Invoice", order["invoice"], "custom_comments")
        line = self._invoice_rows(order["invoice"])[0].comment
        for value in (stored, line):
            self.assertFalse(any(ord(ch) < 32 and ch not in "\n\t" for ch in value or ""), repr(value))
            self.assertNotIn("\x7f", value or "")

    def test_absurdly_long_comments_get_a_friendly_error(self):
        with self.assertRaises(frappe.ValidationError) as ctx:
            co.create_order(
                "Take Away", [{"item": self.kitchen_item, "qty": 1, "comment": "x" * 400}]
            )
        self.assertNotIn("Error while updating order", str(ctx.exception))

    def test_html_in_comments_is_stored_inert_and_never_executes_in_the_kitchen_screen(self):
        payload = '<img src=x onerror="alert(1)">'
        order = co.create_order(
            "Take Away", [{"item": self.kitchen_item, "qty": 1, "comment": payload}]
        )
        js = frappe.get_app_path("ozturkapp", "ozturkapp", "page", "restaurant_kitchen",
                                 "restaurant_kitchen.js")
        source = open(js, encoding="utf-8").read()
        # Oshxona ekrani foydalanuvchi kiritgan har bir qiymatni `esc()` bilan chizadi.
        self.assertIn("esc(it.comments)", source)
        self.assertIn("esc(item.comments)", source)
        self.assertIn("esc(kot.comments)", source)
        self.assertTrue(order["invoice"])

    def test_client_ref_replay_with_another_payload_returns_the_first_order_untouched(self):
        first = co.create_order(
            "Take Away", [{"item": self.kitchen_item, "qty": 1}], client_ref="REV-REPLAY-1"
        )
        second = co.create_order(
            "Take Away", [{"item": self.bar_item, "qty": 9}], client_ref="REV-REPLAY-1"
        )
        self.assertEqual(second["invoice"], first["invoice"])
        self.assertEqual([r.qty for r in self._invoice_rows(first["invoice"])], [1])
        self.assertEqual(
            frappe.db.count("POS Invoice", {"custom_client_ref": "REV-REPLAY-1"}), 1
        )

    def test_absurd_client_ref_is_refused_cleanly(self):
        with self.assertRaises(frappe.ValidationError):
            co.create_order(
                "Take Away", [{"item": self.kitchen_item, "qty": 1}], client_ref="x" * 500
            )

    def test_take_away_with_a_table_and_dine_in_without_one_are_refused(self):
        table = self._table("TA")
        with self.assertRaises(frappe.ValidationError):
            co.create_order("Take Away", self._lines(), table=table)
        with self.assertRaises(frappe.ValidationError):
            co.create_order("Dine In", self._lines())
        self.assertFalse(frappe.db.exists("POS Invoice", {"restaurant_table": table, "docstatus": 0}))

    def test_delivery_phone_must_look_like_a_phone(self):
        for phone in ("-----", "()()()", "+", "1", "9" * 40):
            with self.subTest(phone=phone):
                with self.assertRaises(frappe.ValidationError):
                    co.create_order(
                        "Delivery", self._lines(),
                        delivery={"phone": phone, "address": "Ko'cha 1"},
                    )

    def test_delivery_address_is_bounded_and_clean(self):
        with self.assertRaises(frappe.ValidationError):
            co.create_order(
                "Delivery", self._lines(),
                delivery={"phone": "+998901234567", "address": "x" * 5000},
            )
        order = co.create_order(
            "Delivery", self._lines(),
            delivery={"phone": "+998901234567", "address": "Ko'cha\x1bp\x00\x19\xfa 1"},
        )
        stored = frappe.db.get_value("POS Invoice", order["invoice"], "custom_delivery_address")
        self.assertFalse(any(ord(ch) < 32 and ch not in "\n\t" for ch in stored), repr(stored))

    def test_a_dish_that_is_not_on_the_menu_cannot_be_ordered(self):
        """Menyuda yo'q (yoki menyuda o'chirilgan) taom — narx ro'yxatida bo'lsa ham
        buyurtma qilinmasin (stop-list chetlab o'tilmasin)."""
        menu = frappe.db.get_value("URY Restaurant", self.scope.restaurant, "active_menu")
        frappe.db.set_value(
            "URY Menu Item", {"parent": menu, "item": self.kitchen_item}, "disabled", 1
        )
        with self.assertRaises(frappe.ValidationError):
            co.create_order("Take Away", [{"item": self.kitchen_item, "qty": 1}])

    def test_an_item_missing_from_the_menu_is_refused_for_the_waiter_too(self):
        table = self._table("WN")
        menu = frappe.db.get_value("URY Restaurant", self.scope.restaurant, "active_menu")
        frappe.db.delete("URY Menu Item", {"parent": menu, "item": self.kitchen_item})
        captain = _new_user("rev-captain-menu@example.com", [waiter.WAITER_ROLE], self.branch)
        self._as(captain)
        with self.assertRaises(frappe.ValidationError):
            waiter.submit_order(table, [{"item": self.kitchen_item, "qty": 1}])


class TestCustomerAttacks(ReviewCase):
    def test_double_tap_does_not_create_two_identical_customers(self):
        first = co.create_customer("Ikki Marta Bosildi", "+998901110000")
        second = co.create_customer("Ikki Marta Bosildi", "+998901110000")
        self.assertEqual(first["name"], second["name"])
        self.assertEqual(frappe.db.count("Customer", {"customer_name": "Ikki Marta Bosildi"}), 1)

    def test_customer_fields_are_sanitised_and_bounded(self):
        for name, phone, address in (
            ("<script>alert(1)</script>", None, None),
            ("A" * 500, None, None),
            ("Yaxshi", "+++---", None),
            ("Yaxshi", "9" * 40, None),
            ("Yaxshi", None, "x" * 5000),
        ):
            with self.subTest(name=name[:12], phone=phone):
                with self.assertRaises(frappe.ValidationError):
                    co.create_customer(name, phone, address)

    def test_control_characters_in_a_customer_name_are_removed(self):
        created = co.create_customer("Ali\x1bp\x00\x19\xfa Vali")
        self.assertFalse(
            any(ord(ch) < 32 for ch in frappe.db.get_value("Customer", created["name"], "customer_name"))
        )

    def test_customer_flooding_is_rate_limited(self):
        """Soatiga 60 tadan ortiq mijoz yaratish so'rovi rad etiladi (`ignore_permissions`)."""
        key = frappe.cache.make_key("rl:review-flood:10.9.8.7")
        frappe.cache.delete(key)
        self.addCleanup(frappe.cache.delete, key)
        frappe.local.request = frappe._dict(method="POST")
        frappe.local.request_ip = "10.9.8.7"
        frappe.form_dict.cmd = "review-flood"
        self.addCleanup(lambda: setattr(frappe.local, "request", None))

        with mock.patch.object(order_items, "create_customer", return_value={}):
            for _ in range(60):
                co.create_customer("Bolalar")
            with self.assertRaises(frappe.RateLimitExceededError):
                co.create_customer("Bolalar")

    def test_set_customer_on_a_billed_order_keeps_the_discount_and_taxes(self):
        from ozturkapp.ozturkapp.utils import discounts

        order = self._take_away(kitchen=2)
        with self._as_admin():
            discounts.apply_discount(order["invoice"], self.scope, percent=10, reason="sinov")
        before = frappe.db.get_value(
            "POS Invoice", order["invoice"],
            ["additional_discount_percentage", "grand_total", "net_total"], as_dict=True,
        )
        co.set_customer(order["invoice"], co.create_customer("Chegirmali Mijoz")["name"])
        after = frappe.db.get_value(
            "POS Invoice", order["invoice"],
            ["additional_discount_percentage", "grand_total", "net_total"], as_dict=True,
        )
        self.assertEqual(before, after)


class TestRemoveAndEditAgainstDiscounts(ReviewCase):
    def _discounted(self, kitchen=3):
        from ozturkapp.ozturkapp.utils import discounts

        table = self._table("DR")
        order = self._dine_in(table, kitchen=kitchen)
        with self._as_admin():
            discounts.apply_discount(order["invoice"], self.scope, percent=10, reason="sinov")
        return table, order["invoice"]

    def _state(self, invoice):
        return frappe.db.get_value(
            "POS Invoice", invoice,
            ["additional_discount_percentage", "discount_amount", "total", "net_total",
             "grand_total", "custom_discount_reason"],
            as_dict=True,
        )

    def test_removing_a_dish_reprices_the_discount_instead_of_wiping_it(self):
        _, invoice = self._discounted()
        row = frappe.db.get_value("POS Invoice Item", {"parent": invoice}, "name")

        co.remove_item(invoice, row, qty=1)

        state = self._state(invoice)
        self.assertEqual(flt(state.additional_discount_percentage), 10)
        self.assertEqual(state.custom_discount_reason, "sinov")
        self.assertAlmostEqual(flt(state.discount_amount), flt(state.total) * 0.10, places=2)
        self.assertAlmostEqual(flt(state.net_total), flt(state.total) * 0.90, places=2)

    def test_adding_a_dish_reprices_the_discount_instead_of_wiping_it(self):
        _, invoice = self._discounted(kitchen=1)
        modified = str(frappe.db.get_value("POS Invoice", invoice, "modified"))

        co.add_items(invoice, [{"item": self.bar_item, "qty": 2}], modified)

        state = self._state(invoice)
        self.assertEqual(flt(state.additional_discount_percentage), 10)
        self.assertAlmostEqual(flt(state.net_total), flt(state.total) * 0.90, places=2)

    def test_a_tip_row_survives_an_edit_or_is_dropped_consistently(self):
        """Choychaqa mutlaq summa: buyurtma o'zgarsa chek NOMUVOFIQ qolmasin."""
        from ozturkapp.ozturkapp.utils import cashier_billing

        _, invoice = self._discounted(kitchen=2)
        doc = frappe.get_doc("POS Invoice", invoice)
        if not cashier_billing.tips_account(doc.company):
            self.skipTest("Choychaqa hisobi sozlanmagan")
        cashier_billing.set_tip(doc, 5000)
        doc.save()

        modified = str(frappe.db.get_value("POS Invoice", invoice, "modified"))
        co.add_items(invoice, [{"item": self.bar_item, "qty": 1}], modified)

        after = frappe.get_doc("POS Invoice", invoice)
        tip = cashier_billing.get_tip(after)
        self.assertIn(tip, (0, 5000))
        self.assertAlmostEqual(
            flt(after.grand_total),
            flt(after.net_total) + flt(after.total_taxes_and_charges),
            places=2,
        )

    def test_invalid_removal_quantities_do_not_touch_the_order(self):
        table = self._table("RQ")
        order = self._dine_in(table, kitchen=3)
        invoice = order["invoice"]
        row = frappe.db.get_value("POS Invoice Item", {"parent": invoice}, "name")
        for bad in (0, -1, 4, "abc", "nan", 2.5, 10 ** 9, [1]):
            with self.subTest(qty=repr(bad)):
                with self.assertRaises(frappe.ValidationError):
                    co.remove_item(invoice, row, qty=bad)
        self.assertEqual(frappe.db.get_value("POS Invoice Item", row, "qty"), 3)

    def test_removal_leaves_the_kitchen_ticket_consistent_with_the_invoice(self):
        table = self._table("RK")
        order = self._dine_in(table, kitchen=3, bar=1)
        invoice = order["invoice"]
        row = frappe.db.get_value(
            "POS Invoice Item", {"parent": invoice, "item_code": self.kitchen_item}, "name"
        )

        co.remove_item(invoice, row, qty=2)

        self.assertEqual(self._live_kitchen_qty(invoice, self.kitchen_item), 1)
        self.assertEqual(self._live_kitchen_qty(invoice, self.bar_item), 1)


# ═══════════════════════════════════════════════════════════════════
#  4. Poyga: qulfdan keyingi o'qish eski suratni qaytarmasligi kerak
# ═══════════════════════════════════════════════════════════════════

class TestLockedReadsSeeTheLatestCommit(ReviewCase):
    """MariaDB REPEATABLE READ: `SELECT ... FOR UPDATE` dan keyingi ODDIY `SELECT`
    so'rov boshidagi ESKI suratni qaytaradi (scratch-jadvalda isbotlangan:
    plain -> 0, locking -> 1). Bu yerda shu xatti-harakat modellashtiriladi:
    qulflovchi bo'lmagan o'qish "to'lanmagan" ko'rsatadi, haqiqat esa
    "to'langan" (`docstatus = 1`, boshqa kassir commit qilib ulgurgan)."""

    def _stale_docstatus(self, invoice):
        real = frappe.db.get_value

        def view(doctype, filters=None, fieldname="name", *args, **kwargs):
            result = real(doctype, filters, fieldname, *args, **kwargs)
            if (
                doctype == "POS Invoice"
                and filters == invoice
                and not kwargs.get("for_update")
                and isinstance(result, dict)
                and "docstatus" in result
            ):
                result = frappe._dict(result, docstatus=0)
            return result

        patcher = mock.patch.object(frappe.db, "get_value", side_effect=view)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _paid_meanwhile(self, invoice):
        frappe.db.set_value("POS Invoice", invoice, "docstatus", 1)
        self._stale_docstatus(invoice)

    def test_transfer_refuses_an_invoice_paid_a_moment_ago(self):
        table = self._table("SN1")
        invoice = self._dine_in(table)["invoice"]
        target = self._table("SN2")
        self._paid_meanwhile(invoice)

        with self.assertRaises(frappe.ValidationError):
            order_transfer.transfer(invoice, target, self.scope)

        self.assertEqual(frappe.db.get_value("POS Invoice", invoice, "restaurant_table"), table)
        self.assertEqual(self._flags(target).occupied, 0)

    def test_merge_and_unmerge_refuse_a_paid_invoice(self):
        table = self._table("SM1")
        invoice = self._dine_in(table)["invoice"]
        other = self._table("SM2")
        self._paid_meanwhile(invoice)

        with self.assertRaises(frappe.ValidationError):
            order_transfer.merge(invoice, [other], self.scope)
        with self.assertRaises(frappe.ValidationError):
            order_transfer.unmerge(invoice, other, self.scope)

    def test_cancel_refuses_an_invoice_paid_a_moment_ago(self):
        table = self._table("SC1")
        invoice = self._dine_in(table)["invoice"]
        row = cashier_permissions.assert_invoice_in_scope(invoice, self.scope)
        self._paid_meanwhile(invoice)

        with self.assertRaises(frappe.ValidationError):
            order_api.order_cancel.cancel_invoice(row, "xato zakaz", self.scope)

        self.assertFalse(frappe.db.get_value("POS Invoice", invoice, "custom_cancelled"))

    def test_add_items_and_remove_item_refuse_a_paid_invoice(self):
        table = self._table("SA1")
        order = self._dine_in(table, kitchen=2)
        invoice = order["invoice"]
        row = frappe.db.get_value("POS Invoice Item", {"parent": invoice}, "name")
        self._paid_meanwhile(invoice)

        with self.assertRaises(frappe.ValidationError):
            co.add_items(invoice, [{"item": self.bar_item, "qty": 1}], order["last_modified_time"])
        with self.assertRaises(frappe.ValidationError):
            co.remove_item(invoice, row, qty=1)


# ═══════════════════════════════════════════════════════════════════
#  5. Stollar, bron
# ═══════════════════════════════════════════════════════════════════

class TestTableAttacks(ReviewCase):
    def _mismatches(self, *tables):
        state = table_status.build_floor_state(self.branch)
        rows = {t["name"]: t for t in state["tables"]}
        return [name for name in tables if rows[name]["flag_mismatch"]]

    def test_transfer_and_merge_reject_empty_and_malformed_targets_cleanly(self):
        table = self._table("BT1")
        invoice = self._dine_in(table)["invoice"]
        for call in (
            lambda: table_api.transfer_table(invoice, None),
            lambda: table_api.transfer_table(invoice, ""),
            lambda: table_api.transfer_table(invoice, "   "),
            lambda: table_api.merge_tables(invoice, None),
            lambda: table_api.merge_tables(invoice, "[]"),
            lambda: table_api.merge_tables(invoice, [None, ""]),
            lambda: table_api.merge_tables(invoice, [{"a": 1}]),
            lambda: table_api.merge_tables(invoice, 5),
            lambda: table_api.unmerge_table(invoice, None),
        ):
            with self.assertRaises(frappe.ValidationError):
                call()
        self.assertEqual(frappe.db.get_value("POS Invoice", invoice, "restaurant_table"), table)

    def test_merge_cannot_lock_hundreds_of_tables_in_one_call(self):
        table = self._table("BT2")
        invoice = self._dine_in(table)["invoice"]
        with self.assertRaisesRegex(frappe.ValidationError, "tadan ortiq"):
            table_api.merge_tables(invoice, [f"ZZ-NOPE-{i}" for i in range(200)])

    def test_flags_and_clusters_stay_consistent_through_a_full_life_cycle(self):
        """Buyurtma -> birlashtirish -> ajratish -> ko'chirish -> bekor qilish:
        har qadamdan keyin `occupied` bayrog'i hisoblangan holatga mos."""
        a, b, c, d = (self._table(n) for n in ("LC1", "LC2", "LC3", "LC4"))
        everything = (a, b, c, d)
        invoice = self._dine_in(a)["invoice"]
        self.assertEqual(self._mismatches(*everything), [])

        table_api.merge_tables(invoice, [b, c])
        self.assertEqual(self._mismatches(*everything), [])
        self.assertEqual({self._flags(t).occupied for t in (a, b, c)}, {1})

        table_api.unmerge_table(invoice, c)
        self.assertEqual(self._mismatches(*everything), [])
        self.assertEqual(self._flags(c).occupied, 0)
        self.assertIsNone(self._flags(c).merged_with)

        table_api.unmerge_table(invoice, b)
        self.assertEqual(self._mismatches(*everything), [])
        self.assertIsNone(self._flags(a).merged_with)
        self.assertFalse(frappe.db.get_value("POS Invoice", invoice, "custom_merged_tables"))

        table_api.transfer_table(invoice, d)
        self.assertEqual(self._mismatches(*everything), [])
        self.assertEqual((self._flags(a).occupied, self._flags(d).occupied), (0, 1))

        order_api.cancel_order(invoice, "xato zakaz")
        self.assertEqual(self._mismatches(*everything), [])
        self.assertEqual(self._flags(d).occupied, 0)

    def test_paying_a_merged_order_leaves_no_flag_mismatch(self):
        a, b = self._table("PY1"), self._table("PY2")
        invoice = self._dine_in(a)["invoice"]
        table_api.merge_tables(invoice, [b])

        doc = frappe.get_doc("POS Invoice", invoice)
        with self._as_admin():
            from ozturkapp.ozturkapp.overrides import pos_invoice as override

            frappe.db.set_value("POS Invoice", invoice, "docstatus", 1)
            override._reconcile_tables(frappe.get_doc("POS Invoice", invoice))

        self.assertEqual(self._mismatches(a, b), [])
        self.assertEqual((self._flags(a).occupied, self._flags(b).occupied), (0, 0))
        self.assertIsNone(self._flags(a).merged_with)
        self.assertIsNone(self._flags(b).merged_with)
        self.assertTrue(doc.name)


class TestReservationAttacks(ReviewCase):
    def _reserve(self, table, **kwargs):
        return table_api.reserve_table(table, kwargs.pop("name", "Mehmon"), **kwargs)

    def _stored(self, result):
        return frappe.db.get_value(
            "URY Table Reservation", result["reservation"],
            ["from_time", "to_time", "pax", "customer_name", "notes", "phone"], as_dict=True,
        )

    def test_time_strings_in_the_shapes_clients_send_are_accepted(self):
        for index, raw in enumerate(("9:00:00", "09:00", "21:30:15", "13:05:00")):
            with self.subTest(from_time=raw):
                table = self._table(f"RT{index}")
                stored = self._stored(self._reserve(table, from_time=raw, reservation_date=add_days(nowdate(), 1)))
                self.assertTrue(stored.from_time)
                self.assertGreater(stored.to_time, stored.from_time)

    def test_garbage_times_and_dates_give_a_validation_error(self):
        table = self._table("RG")
        for kwargs in (
            {"from_time": "25:99"},
            {"from_time": "abc"},
            {"from_time": "10:00", "to_time": "xx"},
            {"from_time": "12:00", "to_time": "11:00"},
            {"from_time": "23:00", "to_time": "01:00"},
            {"reservation_date": "2026-02-30", "from_time": "10:00"},
            {"reservation_date": "garbage", "from_time": "10:00"},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(frappe.ValidationError):
                    self._reserve(table, **kwargs)

    def test_a_booking_at_the_very_end_of_the_day_gets_a_valid_window_or_a_clear_error(self):
        table = self._table("RE")
        tomorrow = add_days(nowdate(), 1)

        late = self._stored(self._reserve(table, from_time="23:30", reservation_date=tomorrow))
        self.assertEqual(str(late.to_time), "23:59:59")

        # Yarim tundan o'tmaydi: boshlanishdan keyingi vaqt yo'q -> aniq xato.
        with self.assertRaisesRegex(frappe.ValidationError, "kun oxirida"):
            self._reserve(self._table("RE2"), from_time="23:59:59", reservation_date=tomorrow)

    def test_guest_count_must_be_sane(self):
        for guests in (-5, 10 ** 9, "abc", 0.5):
            with self.subTest(guests=guests):
                table = self._table(f"RP{abs(hash(str(guests))) % 999}")
                try:
                    stored = self._stored(
                        self._reserve(
                            table, guests=guests, from_time="10:00",
                            reservation_date=add_days(nowdate(), 2),
                        )
                    )
                except frappe.ValidationError:
                    continue
                self.assertGreater(stored.pax, 0)
                self.assertLess(stored.pax, 500)

    def test_guest_name_phone_and_notes_are_cleaned(self):
        table = self._table("RN")
        stored = self._stored(
            self._reserve(
                table, name="Ali\x1bp\x00\x19\xfa", phone="+998901234567",
                notes="Deraza\x1d\x56\x00 yonida", from_time="10:00",
                reservation_date=add_days(nowdate(), 2),
            )
        )
        for value in (stored.customer_name, stored.notes):
            self.assertFalse(any(ord(ch) < 32 for ch in value or ""), repr(value))

        with self.assertRaises(frappe.ValidationError):
            self._reserve(self._table("RN2"), phone="-----", from_time="10:00",
                          reservation_date=add_days(nowdate(), 2))
        with self.assertRaises(frappe.ValidationError):
            self._reserve(self._table("RN3"), name="x" * 400, from_time="10:00",
                          reservation_date=add_days(nowdate(), 2))

    def test_a_second_non_overlapping_booking_for_today_is_allowed(self):
        """Ikki bron bir kunga, LEKIN vaqti to'qnashmasa — mumkin (DocType overlap
        tekshiruvi to'qnashuvni o'zi ushlaydi)."""
        table = self._table("RS")
        self._reserve(table, from_time="23:00", to_time="23:30")
        second = self._reserve(table, name="Boshqa", from_time="23:40", to_time="23:55")
        self.assertTrue(second["reservation"])

    def test_overlapping_bookings_are_refused(self):
        table = self._table("RO")
        day = add_days(nowdate(), 3)
        self._reserve(table, from_time="19:00", to_time="21:00", reservation_date=day)
        with self.assertRaises(frappe.ValidationError):
            self._reserve(table, name="Boshqa", from_time="20:00", to_time="22:00", reservation_date=day)

    def test_only_an_open_booking_can_be_cancelled(self):
        table = self._table("RC")
        reservation = self._reserve(table, from_time="23:00", to_time="23:30")["reservation"]
        frappe.db.set_value("URY Table Reservation", reservation, "status", "Seated")

        with self.assertRaises(frappe.ValidationError):
            table_api.cancel_reservation(reservation=reservation)
        self.assertEqual(
            frappe.db.get_value("URY Table Reservation", reservation, "status"), "Seated"
        )

    def test_get_reservations_date_handling(self):
        table = self._table("RD")
        day = add_days(nowdate(), 5)
        made = self._reserve(table, from_time="10:00", reservation_date=day)["reservation"]

        self.assertIn(made, [r["name"] for r in table_api.get_reservations(day)])
        self.assertIn(made, [r["name"] for r in table_api.get_reservations(f"{day} 23:59:59")])
        self.assertNotIn(made, [r["name"] for r in table_api.get_reservations()])
        for bad in ("garbage", "2026-13-45"):
            with self.assertRaises(frappe.ValidationError):
                table_api.get_reservations(bad)


class TestStaleClusters(ReviewCase):
    def _leftover_cluster(self, a, b):
        """Eski birlashtirishdan qolgan iz: ochiq chek yo'q, `merged_with` qolgan."""
        frappe.db.set_value("URY Table", a, "merged_with", b)
        frappe.db.set_value("URY Table", b, "merged_with", a)

    def test_a_new_order_does_not_drag_in_a_leftover_partner_table(self):
        a, b = self._table("SC1"), self._table("SC2")
        self._leftover_cluster(a, b)

        invoice = self._dine_in(a)["invoice"]

        self.assertFalse(
            frappe.db.get_value("POS Invoice", invoice, "custom_merged_tables"),
            "yangi buyurtma eski sherik stolni o'ziga tortdi",
        )
        self.assertEqual(self._flags(b).occupied, 0)
        self.assertIsNone(self._flags(b).merged_with)

    def test_a_leftover_cluster_does_not_block_a_walk_in_at_the_partner_table(self):
        a, b = self._table("SC3"), self._table("SC4")
        self._leftover_cluster(a, b)

        first = self._dine_in(b)["invoice"]
        second = self._dine_in(a)["invoice"]

        self.assertNotEqual(first, second)
        self.assertFalse(frappe.db.get_value("POS Invoice", first, "custom_merged_tables"))
        self.assertFalse(frappe.db.get_value("POS Invoice", second, "custom_merged_tables"))

    def test_the_live_cluster_of_a_running_order_is_never_dissolved_by_a_new_order(self):
        a, b, c = self._table("SC5"), self._table("SC6"), self._table("SC7")
        invoice = self._dine_in(a)["invoice"]
        table_api.merge_tables(invoice, [b])

        with self.assertRaises(frappe.ValidationError):
            self._dine_in(b)

        self.assertEqual(self._flags(a).merged_with, b)
        self.assertEqual(frappe.db.get_value("POS Invoice", invoice, "custom_merged_tables"), b)
        self.assertTrue(c)


# ═══════════════════════════════════════════════════════════════════
#  6. Ishlash: o'qish yo'llari va chegarasiz parametrlar
# ═══════════════════════════════════════════════════════════════════

class TestReadPathsScale(ReviewCase):
    def _queries(self, call):
        counter = {"n": 0}
        real = frappe.db.sql

        def counted(*args, **kwargs):
            counter["n"] += 1
            return real(*args, **kwargs)

        frappe.db.sql = counted
        try:
            call()
        finally:
            frappe.db.sql = real
        return counter["n"]

    def test_read_endpoints_cost_the_same_with_more_tables_and_orders(self):
        captain = _new_user("rev-captain-scale@example.com", [waiter.WAITER_ROLE], self.branch)
        calls = {
            "get_floor_plan": lambda: table_api.get_floor_plan(),
            "get_active_orders": lambda: order_api.get_active_orders(),
            "get_order_counts": lambda: order_api.get_order_counts(),
            "get_reservations": lambda: table_api.get_reservations(),
            "get_paid_orders": lambda: order_api.get_paid_orders(),
            "get_paid_order_filter_options": lambda: order_api.get_paid_order_filter_options(),
        }
        waiter_calls = {
            "waiter.get_tables": lambda: waiter.get_tables(),
        }

        def measure():
            frappe.local._ozturk_scope_cache = {}
            frappe.set_user(self.cashier)
            costs = {name: self._queries(call) for name, call in calls.items()}
            self._as(captain)
            costs.update({name: self._queries(call) for name, call in waiter_calls.items()})
            frappe.set_user(self.cashier)
            return costs

        self._dine_in(self._table("SCL-base"), kitchen=1, bar=1)
        measure()  # kesh isisin
        before = measure()
        frappe.set_user(self.cashier)
        for index in range(20):
            name = self._table(f"SCL{index}")
            if index % 2 == 0:
                self._dine_in(name, kitchen=1, bar=1)
        after = measure()

        for name in before:
            with self.subTest(endpoint=name):
                self.assertEqual(before[name], after[name])

    def test_paid_orders_limit_is_capped(self):
        seen = {}
        real = frappe.db.sql

        def spy(*args, **kwargs):
            # Argumentlar o'zgarishsiz uzatiladi: `values=None` ni aniq berish pymysql'da
            # "not all arguments converted" xatosini beradi (bugungi cheklar bo'lganda ishga tushadi).
            query = args[0] if args else kwargs.get("query")
            values = args[1] if len(args) > 1 else kwargs.get("values")
            if isinstance(values, dict) and "limit" in values and "tabPOS Invoice" in str(query):
                seen["limit"] = values["limit"]
            return real(*args, **kwargs)

        with mock.patch.object(frappe.db, "sql", side_effect=spy):
            order_api.get_paid_orders(limit=10 ** 9)
            self.assertLessEqual(seen["limit"], order_api.MAX_PAID_ROWS)
            order_api.get_paid_orders(limit=-5)
            self.assertGreater(seen["limit"], 0)

    def test_search_parameters_cannot_break_out_of_the_query(self):
        for nasty in ("'; DROP TABLE `tabPOS Invoice`; --", '" OR 1=1 --', "%", "_" * 50):
            with self.subTest(search=nasty[:14]):
                self.assertIsInstance(order_api.get_paid_orders(search=nasty), list)
                self.assertIsInstance(co.search_customers(nasty), list)
        self.assertTrue(frappe.db.count("POS Invoice") >= 0)
        with self.assertRaises(frappe.ValidationError):
            order_api.get_paid_orders(search="x" * 500)
        with self.assertRaises(frappe.ValidationError):
            order_api.get_paid_orders(date_from="garbage")

    def test_customer_search_limit_is_capped(self):
        seen = {}
        real = frappe.get_all

        def spy(doctype, *args, **kwargs):
            if doctype == "Customer":
                seen["limit"] = kwargs.get("limit_page_length")
            return real(doctype, *args, **kwargs)

        with mock.patch.object(frappe, "get_all", side_effect=spy):
            co.search_customers("a", limit=10 ** 9)
        self.assertLessEqual(seen["limit"], order_items.MAX_SEARCH_ROWS)


class TestWaiterStaleCluster(ReviewCase):
    def test_a_waiter_order_does_not_drag_in_a_leftover_partner_table(self):
        a, b = self._table("WS1"), self._table("WS2")
        frappe.db.set_value("URY Table", a, "merged_with", b)
        frappe.db.set_value("URY Table", b, "merged_with", a)
        captain = _new_user("rev-captain-stale@example.com", [waiter.WAITER_ROLE], self.branch)
        self._as(captain)

        result = waiter.submit_order(a, [{"item": self.kitchen_item, "qty": 1}])

        invoice = result["invoice"]
        self.assertFalse(frappe.db.get_value("POS Invoice", invoice, "custom_merged_tables"))
        self.assertEqual(self._flags(b).occupied, 0)


class TestWaiterStaleScreen(ReviewCase):
    """Ofitsantning eski ekrani: buyurtma ko'chirilgan / bekor qilingan bo'lsa
    yangi (dublikat) chek ochilmasin."""

    def _captain_with_order(self):
        table = self._table("SS1")
        order = self._dine_in(table, kitchen=1)
        captain = _new_user("rev-captain-stale-screen@example.com", [waiter.WAITER_ROLE], self.branch)
        self._as(captain)
        seen = waiter.get_order(invoice=order["invoice"])
        self._as(self.cashier)
        return table, order["invoice"], seen["last_modified_time"]

    def _resubmit(self, table, lmt):
        captain = frappe.db.get_value("User", {"email": "rev-captain-stale-screen@example.com"})
        self._as(captain)
        return waiter.submit_order(
            table, [{"item": self.kitchen_item, "qty": 1}, {"item": self.bar_item, "qty": 1}],
            last_modified_time=lmt,
        )

    def test_after_a_transfer_the_old_screen_cannot_open_a_duplicate_order(self):
        table, invoice, lmt = self._captain_with_order()
        table_api.transfer_table(invoice, self._table("SS2"))

        with self.assertRaises(frappe.ValidationError):
            self._resubmit(table, lmt)

        self.assertEqual(frappe.db.count("POS Invoice", {"restaurant_table": table, "docstatus": 0}), 0)

    def test_after_a_cancellation_the_old_screen_cannot_resurrect_the_order(self):
        table, invoice, lmt = self._captain_with_order()
        order_api.cancel_order(invoice, "xato zakaz")

        with self.assertRaises(frappe.ValidationError):
            self._resubmit(table, lmt)

        self.assertEqual(frappe.db.count("POS Invoice", {"restaurant_table": table, "docstatus": 0,
                                                          "custom_cancelled": 0}), 0)

    def test_a_first_order_without_a_lock_token_still_works(self):
        table = self._table("SS3")
        captain = _new_user("rev-captain-first@example.com", [waiter.WAITER_ROLE], self.branch)
        self._as(captain)

        result = waiter.submit_order(table, [{"item": self.kitchen_item, "qty": 1}])

        self.assertTrue(result["invoice"])


class TestCustomerEligibility(ReviewCase):
    def _customer(self, name, **flags):
        created = co.create_customer(name)["name"]
        for field, value in flags.items():
            frappe.db.set_value("Customer", created, field, value)
        return created

    def test_frozen_and_internal_customers_cannot_be_attached(self):
        order = self._take_away()
        for flag in ("is_frozen", "is_internal_customer", "disabled"):
            with self.subTest(flag=flag):
                customer = self._customer(f"Cheklangan {flag}", **{flag: 1})
                with self.assertRaises(frappe.ValidationError):
                    co.set_customer(order["invoice"], customer)
                with self.assertRaises(frappe.ValidationError):
                    co.create_order("Take Away", self._lines(), customer=customer)

    def test_waiter_cannot_attach_a_disabled_customer_either(self):
        table = self._table("CE1")
        customer = self._customer("Waiter Rad", disabled=1)
        captain = _new_user("rev-captain-cust@example.com", [waiter.WAITER_ROLE], self.branch)
        self._as(captain)
        with self.assertRaises(frappe.ValidationError):
            waiter.submit_order(table, self._lines(), customer=customer)


class TestTableNamesThatArePrefixesOfEachOther(ReviewCase):
    """URY `get_order_invoice()` chekni `custom_merged_tables LIKE %stol%` bilan
    qidiradi: "Table-1" nomi "Table-10" ni ham topadi. `sync_order` esa topilgan
    chekning taomlarini YANGI ro'yxat bilan ALMASHTIRADI (`invoice.items = []`)."""

    def _setup(self, billing_user=True):
        one, five, ten = self._table("C1"), self._table("C5"), self._table("C10")
        merged = self._dine_in(five, kitchen=2)["invoice"]
        table_api.merge_tables(merged, [ten])
        if billing_user:
            # Ko'p restoranlarda `role_allowed_for_billing` to'ldirilgan: bu holda
            # URY `sync_order` "stol band" tekshiruvini O'TKAZIB YUBORADI.
            with self._as_admin():
                profile = frappe.get_doc("POS Profile", self.profile)
                profile.append("role_allowed_for_billing", {"role": "URY Cashier"})
                profile.save()
            frappe.local._ozturk_scope_cache = {}
        return one, five, ten, merged

    def _lines_of(self, invoice):
        return [
            (r.item_code, r.qty)
            for r in frappe.get_all(
                "POS Invoice Item", filters={"parent": invoice}, fields=["item_code", "qty"]
            )
        ]

    def test_a_cashier_order_at_a_prefix_named_table_never_overwrites_a_merged_order(self):
        one, five, ten, merged = self._setup()
        before = self._lines_of(merged)

        with self.assertRaises(frappe.ValidationError):
            self._dine_in(one, kitchen=1, bar=1)

        self.assertEqual(before, self._lines_of(merged), "birlashtirilgan buyurtma ustiga yozildi")
        self.assertFalse(frappe.db.exists("POS Invoice", {"restaurant_table": one, "docstatus": 0}))

    def test_a_waiter_order_at_a_prefix_named_table_never_overwrites_a_merged_order(self):
        one, five, ten, merged = self._setup()
        before = self._lines_of(merged)
        captain = _new_user("rev-captain-prefix@example.com", [waiter.WAITER_ROLE], self.branch)
        self._as(captain)

        with self.assertRaises(frappe.ValidationError):
            waiter.submit_order(one, [{"item": self.bar_item, "qty": 1}])

        self.assertEqual(before, self._lines_of(merged))

    def test_the_real_owner_of_a_table_is_still_found_when_another_order_matches_by_prefix(self):
        """"Table-1" ning O'Z cheki bor: yangi taom o'shanga qo'shiladi, "Table-10" ning
        birlashtirilgan chekiga EMAS."""
        one, five, ten, merged = self._setup(billing_user=False)
        # `one` da chek YO'Q edi: avval ochamiz (begona moslik bo'lgani uchun rad etiladi),
        # shuning uchun birlashtirishni vaqtincha ajratib, o'z chekini ochamiz.
        table_api.unmerge_table(merged, ten)
        own = self._dine_in(one, kitchen=1)
        table_api.merge_tables(merged, [ten])
        merged_before = self._lines_of(merged)

        captain = _new_user("rev-captain-owner@example.com", [waiter.WAITER_ROLE], self.branch)
        self._as(captain)
        result = waiter.submit_order(
            one,
            [{"item": self.kitchen_item, "qty": 1}, {"item": self.bar_item, "qty": 2}],
            last_modified_time=own["last_modified_time"],
        )

        self.assertEqual(result["invoice"], own["invoice"])
        self.assertEqual(self._lines_of(merged), merged_before)

    def test_paying_or_cancelling_an_order_is_not_blocked_by_a_prefix_named_merge(self):
        """`pos_invoice._open_invoices_for` ham LIKE bilan qidiradi: "Table-1" to'langanda
        "Table-10" birlashtirilgan chek "ochiq chek qoldi" deb hisoblanmasin."""
        from ozturkapp.ozturkapp.overrides import pos_invoice as override

        one, five, ten, merged = self._setup(billing_user=False)
        self.assertEqual(override._open_invoices_for([one]), [])


class TestSmallLeaksAndStorms(ReviewCase):
    def test_merging_many_tables_emits_a_constant_number_of_events(self):
        events = []
        primary = self._table("EV0")
        invoice = self._dine_in(primary)["invoice"]
        others = [self._table(f"EV{i}") for i in range(1, 6)]

        with mock.patch.object(frappe, "publish_realtime", side_effect=lambda *a, **k: events.append(a)):
            table_api.merge_tables(invoice, others)

        self.assertLessEqual(len(events), 2, "birlashtirish har stol uchun alohida xabar chiqarmasligi kerak")

    def test_a_transfer_emits_floor_order_and_one_event_per_moved_ticket_only(self):
        events = []
        table, target = self._table("EV6"), self._table("EV7")
        invoice = self._dine_in(table, kitchen=1, bar=1)["invoice"]

        with mock.patch.object(frappe, "publish_realtime", side_effect=lambda *a, **k: events.append(a)):
            result = table_api.transfer_table(invoice, target)

        # Zal + buyurtma xabari, har bir ko'chirilgan chipta uchun ikkita (o'zimizniki + Mosaic KDS).
        self.assertEqual(len(events), 2 + 2 * result["kots_updated"])

    def test_the_waiter_does_not_get_a_guests_phone_number_from_the_floor_plan(self):
        table = self._table("PH")
        table_api.reserve_table(table, "Mehmon", phone="+998901234567", from_time="23:00", to_time="23:30")
        captain = _new_user("rev-captain-phone@example.com", [waiter.WAITER_ROLE], self.branch)
        self._as(captain)

        tile = next(t for t in waiter.get_tables()["tables"] if t["name"] == table)

        self.assertEqual(tile["status"], table_status.RESERVED)
        self.assertNotIn("phone", tile["reservation"])
        self.assertTrue(tile["reservation"]["from_time"])

    def test_a_bill_cannot_be_requested_for_a_cancelled_order(self):
        table = self._table("BR")
        invoice = self._dine_in(table)["invoice"]
        order_api.cancel_order(invoice, "xato zakaz")
        captain = _new_user("rev-captain-bill@example.com", [waiter.WAITER_ROLE], self.branch)
        self._as(captain)

        with self.assertRaises(frappe.ValidationError):
            waiter.request_bill(invoice)
        self.assertFalse(frappe.db.get_value("POS Invoice", invoice, "custom_bill_requested"))


class TestArgumentTypeConfusion(ReviewCase):
    """Whitelisted argumentlar HTTP orqali ro'yxat/lug'at bo'lib kelishi mumkin.
    `frappe.db.get_value(doctype, <lug'at>)` esa lug'atni FILTR deb qabul qiladi:
    `{"name": ["like", "%"]}` "birinchi mos qator" ni beradi."""

    def test_table_and_invoice_arguments_must_be_plain_names(self):
        table = self._table("TY")
        invoice = self._dine_in(table)["invoice"]
        sneaky_table = {"name": ["like", "ZZ-TY%"]}
        sneaky_invoice = {"restaurant_table": table}

        for name, call in {
            "get_table_status": lambda: table_api.get_table_status(sneaky_table),
            "get_table_detail": lambda: table_api.get_table_detail(sneaky_table),
            "get_table_order": lambda: order_api.get_table_order(sneaky_table),
            "reserve_table": lambda: table_api.reserve_table(sneaky_table, "Mehmon"),
            "get_order_bill_preview": lambda: order_api.get_order_bill_preview(sneaky_invoice),
            "cancel_order": lambda: order_api.cancel_order(sneaky_invoice, "xato zakaz"),
            "transfer_table/invoice": lambda: table_api.transfer_table(sneaky_invoice, table),
            "transfer_table/table": lambda: table_api.transfer_table(invoice, sneaky_table),
            "add_items": lambda: co.add_items(sneaky_invoice, self._lines(), "x"),
            "set_customer": lambda: co.set_customer(sneaky_invoice, self.scope.default_customer),
            "create_order/table": lambda: co.create_order("Dine In", self._lines(), table=sneaky_table),
            "create_order/customer": lambda: co.create_order(
                "Take Away", self._lines(), customer=["x"]),
        }.items():
            with self.subTest(endpoint=name):
                with self.assertRaises(frappe.ValidationError):
                    call()

        self.assertFalse(frappe.db.get_value("POS Invoice", invoice, "custom_cancelled"))

    def test_guest_counts_must_be_sane_on_every_order_path(self):
        for bad in (-3, 10 ** 6, "abc", 1.5):
            with self.subTest(pax=repr(bad)):
                with self.assertRaises(frappe.ValidationError):
                    co.create_order("Take Away", self._lines(), pax=bad)
        order = co.create_order("Take Away", self._lines(), pax="4")
        self.assertEqual(int(frappe.db.get_value("POS Invoice", order["invoice"], "no_of_pax")), 4)

        table = self._table("TP")
        captain = _new_user("rev-captain-pax@example.com", [waiter.WAITER_ROLE], self.branch)
        self._as(captain)
        with self.assertRaises(frappe.ValidationError):
            waiter.submit_order(table, self._lines(), pax=-2)


class TestTableTakenMeanwhile(ReviewCase):
    """Boshqa kassir stolni hozirgina band qilib commit qilgan: ochiq cheklar ro'yxati
    (oddiy o'qish, eski surat) uni ko'rmaydi, lekin qulflangan stol qatorining
    `occupied` bayrog'i (qulflovchi o'qish) ko'radi."""

    def _hide(self, *invoices):
        real = table_status.get_open_orders

        def stale(branch, tables=None):
            return [o for o in real(branch, tables) if o.name not in invoices]

        patcher = mock.patch.object(table_status, "get_open_orders", side_effect=stale)
        patcher.start()
        self.addCleanup(patcher.stop)
        hidden = mock.patch.object(order_items, "active_invoice_for_table", return_value=None)
        hidden.start()
        self.addCleanup(hidden.stop)

    def test_transfer_onto_a_table_taken_meanwhile_is_refused(self):
        a, b = self._table("TM1"), self._table("TM2")
        mine = self._dine_in(a)["invoice"]
        theirs = self._dine_in(b)["invoice"]
        self._hide(theirs)

        with self.assertRaises(frappe.ValidationError):
            table_api.transfer_table(mine, b)

        self.assertEqual(frappe.db.get_value("POS Invoice", mine, "restaurant_table"), a)
        self.assertEqual(frappe.db.get_value("POS Invoice", theirs, "restaurant_table"), b)

    def test_merge_of_a_table_taken_meanwhile_is_refused(self):
        a, b = self._table("TM3"), self._table("TM4")
        mine = self._dine_in(a)["invoice"]
        theirs = self._dine_in(b)["invoice"]
        self._hide(theirs)

        with self.assertRaises(frappe.ValidationError):
            table_api.merge_tables(mine, [b])

        self.assertFalse(frappe.db.get_value("POS Invoice", mine, "custom_merged_tables"))

    def test_a_new_order_on_a_table_taken_meanwhile_is_refused_for_cashier_and_waiter(self):
        table = self._table("TM5")
        theirs = self._dine_in(table)["invoice"]
        self._hide(theirs)

        with self.assertRaises(frappe.ValidationError):
            self._dine_in(table)

        captain = _new_user("rev-captain-taken@example.com", [waiter.WAITER_ROLE], self.branch)
        self._as(captain)
        with self.assertRaises(frappe.ValidationError):
            waiter.submit_order(table, self._lines())

        self.assertEqual(frappe.db.count("POS Invoice", {"restaurant_table": table, "docstatus": 0}), 1)

    def test_an_orphan_occupied_flag_does_not_block_a_free_table(self):
        """Bayroq band, lekin chek YO'Q (eskirgan bayroq): stol baribir bo'sh."""
        a, b = self._table("TM6"), self._table("TM7")
        frappe.db.set_value("URY Table", b, "occupied", 1)
        mine = self._dine_in(a)["invoice"]

        result = table_api.transfer_table(mine, b)

        self.assertEqual(result["to_table"], b)
        self.assertEqual(self._flags(b).occupied, 1)
        self.assertEqual(self._flags(a).occupied, 0)


class TestManagerReleaseAndCancelNotes(ReviewCase):
    def test_releasing_a_table_also_dissolves_its_leftover_cluster(self):
        a, b = self._table("MR1"), self._table("MR2")
        for name, partner in ((a, b), (b, a)):
            frappe.db.set_value("URY Table", name, {"occupied": 1, "merged_with": partner})
        manager = _new_user("rev-mgr-release2@example.com", ["URY Manager"], self.branch)
        self._as(manager)

        table_api.release_table(a, "eski birlashtirish qoldig'i")

        for name in (a, b):
            self.assertEqual(self._flags(name).occupied, 0)
            self.assertIsNone(self._flags(name).merged_with)

    def test_cancelling_a_booking_keeps_the_original_notes(self):
        table = self._table("MR3")
        reservation = table_api.reserve_table(
            table, "Mehmon", notes="Tug'ilgan kun tortini tayyorlang", from_time="23:00", to_time="23:30"
        )["reservation"]

        table_api.cancel_reservation(reservation=reservation, reason="mehmon qo'ng'iroq qildi")

        notes = frappe.db.get_value("URY Table Reservation", reservation, "notes")
        self.assertIn("Tug'ilgan kun tortini tayyorlang", notes)
        self.assertIn("mehmon qo'ng'iroq qildi", notes)
