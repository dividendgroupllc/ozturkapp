# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa funksiyalari reestri va menejer PIN-tasdig'i testlari.

Ishga tushirish::

    bench --site ozturk.local run-tests \
        --module ozturkapp.ozturkapp.tests.test_cashier_features
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from ozturkapp.ozturkapp.api import approval as approval_api
from ozturkapp.ozturkapp.api import cashier as cashier_api
from ozturkapp.ozturkapp.setup import cashier_features
from ozturkapp.ozturkapp.utils import cashier_permissions, manager_approval
from ozturkapp.ozturkapp.utils.manager_approval import ApprovalRequired


def _pos_profile():
    return frappe.db.get_value("POS Profile", {}, "name")


class TestFeatureRegistry(FrappeTestCase):
    def setUp(self):
        self.profile = _pos_profile()
        self.assertTrue(self.profile, "Testlar uchun POS Profile kerak")

    def test_every_feature_has_a_pos_profile_column(self):
        """Reestrdagi har bir bayroq/sozlama uchun maydon haqiqatan yaratilgan."""
        columns = set(frappe.db.get_table_columns("POS Profile"))
        for feature in cashier_features.FEATURES.values():
            self.assertIn(feature["fieldname"], columns)
        for setting in cashier_features.SETTINGS.values():
            self.assertIn(setting["fieldname"], columns)

    def test_get_features_returns_every_key(self):
        features = cashier_features.get_features(self.profile)
        for key in cashier_features.FEATURES:
            self.assertIn(key, features)
        # Eski ikki bayroq ham shu lug'atda.
        self.assertIn("bill_split", features)
        self.assertIn("virtual_keyboard", features)

    def test_money_touching_features_are_off_by_default(self):
        """Pulga tegadigan funksiya migratsiyadan keyin O'ZI yoqilib qolmasin."""
        for key in (
            "split_payment", "discount", "refunds", "cash_movements",
            "tips", "cashier_orders", "table_transfer", "customer_attach", "cash_drawer",
        ):
            self.assertFalse(
                bool(cashier_features.FEATURES[key]["default"]),
                f"{key} standart bo'yicha yoqilgan bo'lmasligi kerak",
            )

    def test_toggle_is_read_from_pos_profile(self):
        field = cashier_features.FEATURES["discount"]["fieldname"]
        original = frappe.db.get_value("POS Profile", self.profile, field)
        try:
            frappe.db.set_value("POS Profile", self.profile, field, 0)
            self.assertFalse(cashier_features.is_enabled(self.profile, "discount"))
            frappe.db.set_value("POS Profile", self.profile, field, 1)
            self.assertTrue(cashier_features.is_enabled(self.profile, "discount"))
        finally:
            frappe.db.set_value("POS Profile", self.profile, field, original or 0)

    def test_assert_enabled_rejects_disabled_feature(self):
        field = cashier_features.FEATURES["refunds"]["fieldname"]
        original = frappe.db.get_value("POS Profile", self.profile, field)
        try:
            frappe.db.set_value("POS Profile", self.profile, field, 0)
            with self.assertRaises(frappe.PermissionError):
                cashier_features.assert_enabled(self.profile, "refunds")
            frappe.db.set_value("POS Profile", self.profile, field, 1)
            cashier_features.assert_enabled(self.profile, "refunds")  # xato bermaydi
        finally:
            frappe.db.set_value("POS Profile", self.profile, field, original or 0)

    def test_unknown_feature_is_a_programming_error(self):
        with self.assertRaises(KeyError):
            cashier_features.is_enabled(self.profile, "does_not_exist")

    def test_explicit_zero_discount_limit_is_not_replaced_by_default(self):
        """`0` — ataylab nol (hamma chegirma tasdiq talab qiladi), standart 10 emas."""
        field = cashier_features.SETTINGS["max_cashier_discount_percent"]["fieldname"]
        original = frappe.db.get_value("POS Profile", self.profile, field)
        try:
            frappe.db.set_value("POS Profile", self.profile, field, 0)
            self.assertEqual(
                cashier_features.get_settings(self.profile)["max_cashier_discount_percent"], 0
            )
            frappe.db.set_value("POS Profile", self.profile, field, 7.5)
            self.assertEqual(
                cashier_features.get_settings(self.profile)["max_cashier_discount_percent"], 7.5
            )
        finally:
            frappe.db.set_value("POS Profile", self.profile, field, original or 0)

    def test_tip_options_are_parsed_and_junk_is_dropped(self):
        field = cashier_features.SETTINGS["tip_percent_options"]["fieldname"]
        original = frappe.db.get_value("POS Profile", self.profile, field)
        try:
            frappe.db.set_value("POS Profile", self.profile, field, "5, 10,abc,0, 15")
            self.assertEqual(
                cashier_features.get_settings(self.profile)["tip_percent_options"],
                [5.0, 10.0, 15.0],
            )
        finally:
            frappe.db.set_value("POS Profile", self.profile, field, original)

    def test_context_exposes_features_and_settings(self):
        ctx = cashier_api.get_cashier_context()
        self.assertIn("features", ctx)
        self.assertIn("feature_settings", ctx)
        self.assertEqual(ctx["features"]["bill_split"], ctx["enable_bill_split"])
        self.assertEqual(ctx["features"]["virtual_keyboard"], ctx["enable_virtual_keyboard"])


class TestNoCollisionWithOtherApps(FrappeTestCase):
    """Reestr boshqa ilovaning (URY) maydonlarini ustidan yozmasin.

    Bir marta shunday bo'lgan: `custom_enable_discount` URY'ning o'z maydoni edi,
    `create_custom_fields` uning yorlig'ini va tartibini jimgina o'zgartirdi.
    """

    URY_FIXTURE = "apps/ury/ury/fixtures/custom_field.json"

    def _foreign_fields(self):
        import os

        path = os.path.join(frappe.get_app_path("ury", ".."), "ury", "fixtures", "custom_field.json")
        if not os.path.exists(path):
            self.skipTest("URY fixture topilmadi")
        with open(path, encoding="utf-8") as handle:
            rows = json.load(handle)
        return {row["fieldname"]: row for row in rows if row.get("dt") in ("POS Profile", "User")}

    def test_registry_names_do_not_clash_with_ury_fixture_fields(self):
        foreign = self._foreign_fields()
        ours = [f["fieldname"] for f in cashier_features.FEATURES.values()]
        ours += [s["fieldname"] for s in cashier_features.SETTINGS.values()]
        ours += [cashier_features.SECTION_FIELD, manager_approval.PIN_FIELD]
        self.assertEqual([name for name in ours if name in foreign], [])

    def test_ury_discount_field_keeps_its_own_definition(self):
        row = self._foreign_fields()["custom_enable_discount"]
        label, module = frappe.db.get_value(
            "Custom Field", "POS Profile-custom_enable_discount", ["label", "module"]
        )
        self.assertEqual(label, row["label"])
        self.assertEqual(module, "URY")

    def test_create_fields_refuses_to_overwrite_a_foreign_field(self):
        fields = [{"fieldname": "custom_enable_discount"}]
        with self.assertRaises(frappe.ValidationError):
            cashier_features._assert_no_foreign_fields(fields)

    def test_discount_guard_honours_ury_desktop_flag(self):
        """URY'ning Desktop POS chegirmasi yoqiq bo'lsa qo'riqchi uni buzmasin."""
        from ozturkapp.ozturkapp.utils import cashier_billing

        profile = _pos_profile()
        ours = cashier_features.FEATURES["discount"]["fieldname"]
        original = frappe.db.get_value("POS Profile", profile, [ours, "custom_enable_discount"], as_dict=True)
        try:
            frappe.db.set_value("POS Profile", profile, {ours: 0, "custom_enable_discount": 0})
            self.assertFalse(cashier_billing._discount_allowed(profile))

            frappe.db.set_value("POS Profile", profile, {ours: 0, "custom_enable_discount": 1})
            self.assertTrue(cashier_billing._discount_allowed(profile))

            frappe.db.set_value("POS Profile", profile, {ours: 1, "custom_enable_discount": 0})
            self.assertTrue(cashier_billing._discount_allowed(profile))
        finally:
            frappe.db.set_value("POS Profile", profile, dict(original))


class TestKioskModeIsGone(FrappeTestCase):
    """To'liq ekran (kiosk) rejimi foydalanuvchi talabi bilan butunlay olib tashlangan."""

    def test_registry_has_no_kiosk_flag(self):
        self.assertNotIn("kiosk_mode", cashier_features.FEATURES)
        self.assertNotIn(
            "custom_enable_kiosk_mode",
            [f["fieldname"] for f in cashier_features.FEATURES.values()],
        )

    def test_context_does_not_offer_kiosk(self):
        ctx = cashier_api.get_cashier_context()
        self.assertNotIn("kiosk_mode", ctx["features"])

    def test_obsolete_field_is_removed_by_setup(self):
        self.assertIn("custom_enable_kiosk_mode", cashier_features.OBSOLETE_FIELDS)
        self.assertFalse(
            frappe.db.exists("Custom Field", "POS Profile-custom_enable_kiosk_mode"),
            "Eskirgan Custom Field o'chirilishi kerak (bench execute ...cashier_features.setup)",
        )

    def test_obsolete_cleanup_never_touches_a_foreign_field(self):
        """Faqat bizning (module bo'sh) maydon o'chiriladi — boshqa ilovaniki emas.

        HAQIQIY Custom Field YARATILMAYDI: u `ALTER TABLE` (DDL) bajaradi, MariaDB'da bu
        tranzaksiyani avtomatik COMMIT qiladi — test rollback'i uni qaytara olmaydi va yozuv
        dev bazada qolib ketadi (bir marta shunday bo'lgan). Shuning uchun DB chaqiruvlari mock.
        """
        from unittest import mock

        foreign = frappe._dict(name="POS Profile-custom_enable_kiosk_mode", module="URY")
        ours = frappe._dict(name="POS Profile-custom_enable_kiosk_mode", module=None)

        with mock.patch.object(frappe.db, "get_value", return_value=foreign), mock.patch.object(
            frappe, "delete_doc"
        ) as delete:
            cashier_features._remove_obsolete_fields()
        delete.assert_not_called()

        with mock.patch.object(frappe.db, "get_value", return_value=ours), mock.patch.object(
            frappe, "delete_doc"
        ) as delete:
            cashier_features._remove_obsolete_fields()
        delete.assert_called_once()
        self.assertEqual(delete.call_args.args[:2], ("Custom Field", ours.name))


class TestManagerApproval(FrappeTestCase):
    MANAGER = "pin-manager@example.com"
    CASHIER = "pin-cashier@example.com"
    OTHER_CASHIER = "pin-cashier2@example.com"
    PIN = "4321"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = cls._make_user(cls.MANAGER, ["URY Manager"], pin=cls.PIN)
        cls.cashier = cls._make_user(cls.CASHIER, ["URY Cashier"])
        cls.other_cashier = cls._make_user(cls.OTHER_CASHIER, ["URY Cashier"])

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
        return doc

    def setUp(self):
        self._reset_counters()
        frappe.set_user(self.CASHIER)

    def _reset_counters(self):
        """Menejer hisobchisi VA so'rovchi (kassir) hisobchisi — ikkalasi ham kesh'da saqlanadi."""
        manager_approval.reset_attempts(self.MANAGER)
        for user in (self.CASHIER, self.OTHER_CASHIER):
            manager_approval.reset_requester(user)

    def tearDown(self):
        frappe.set_user("Administrator")
        self._reset_counters()

    def _require(self, pin=None, user=None, **kwargs):
        approval = {"user": user or self.MANAGER, "pin": pin} if pin is not None else None
        return manager_approval.require("Sinov amali", approval, **kwargs)

    def test_missing_approval_raises_approval_required(self):
        with self.assertRaises(ApprovalRequired):
            self._require()

    def test_correct_pin_returns_the_manager(self):
        self.assertEqual(self._require(pin=self.PIN), self.MANAGER)

    def test_wrong_pin_is_rejected(self):
        with self.assertRaises(ApprovalRequired):
            self._require(pin="0000")

    def test_pin_is_accepted_as_json_string(self):
        approval = json.dumps({"user": self.MANAGER, "pin": self.PIN})
        self.assertEqual(manager_approval.require("Sinov", approval), self.MANAGER)

    def test_lockout_after_too_many_failures_even_with_correct_pin(self):
        for _ in range(manager_approval.MAX_ATTEMPTS):
            with self.assertRaises(ApprovalRequired):
                self._require(pin="0000")
        with self.assertRaises(ApprovalRequired) as ctx:
            self._require(pin=self.PIN)  # to'g'ri PIN ham o'tmaydi — bloklangan
        self.assertIn("daqiqa", str(ctx.exception))

    def test_success_resets_failure_counter(self):
        for _ in range(manager_approval.MAX_ATTEMPTS - 1):
            with self.assertRaises(ApprovalRequired):
                self._require(pin="0000")
        self.assertEqual(self._require(pin=self.PIN), self.MANAGER)
        with self.assertRaises(ApprovalRequired):
            self._require(pin="0000")  # hisob qaytadan boshlangan — bloklanmagan
        self.assertEqual(self._require(pin=self.PIN), self.MANAGER)

    def test_requester_cap_spans_all_managers(self):
        """Har menejerga alohida 5 urinish emas: kassirga jami `REQUESTER_MAX_ATTEMPTS` ta."""
        second = "pin-manager2@example.com"
        self._make_user(second, ["URY Manager"], pin="7777")
        for index in range(manager_approval.REQUESTER_MAX_ATTEMPTS):
            with self.assertRaises(ApprovalRequired):
                self._require(pin="0000", user=self.MANAGER if index % 2 else second)
        with self.assertRaises(ApprovalRequired) as ctx:
            self._require(pin="7777", user=second)          # to'g'ri PIN ham o'tmaydi
        self.assertIn("daqiqa", str(ctx.exception))
        manager_approval.reset_attempts(second)

    def test_non_manager_cannot_approve_even_with_a_pin(self):
        cashier_pin = "5555"
        frappe.db.set_value("User", self.CASHIER, "custom_pos_pin", None)
        from frappe.utils.password import set_encrypted_password

        set_encrypted_password("User", self.CASHIER, cashier_pin, manager_approval.PIN_FIELD)
        frappe.set_user(self.OTHER_CASHIER)  # boshqa KASSIR so'rayapti (menejer emas)
        with self.assertRaises(ApprovalRequired):
            manager_approval.require("Sinov", {"user": self.CASHIER, "pin": cashier_pin})

    def test_unknown_user_looks_the_same_as_wrong_pin(self):
        with self.assertRaises(ApprovalRequired) as ctx:
            self._require(pin="1234", user="nobody@example.com")
        self.assertIn("noto'g'ri", str(ctx.exception))

    def test_cashier_cannot_approve_own_action(self):
        with self.assertRaises(ApprovalRequired):
            manager_approval.require("Sinov", {"user": self.CASHIER, "pin": "1234"})

    def test_manager_at_the_till_needs_no_pin(self):
        frappe.set_user(self.MANAGER)
        self.assertEqual(manager_approval.require("Sinov", None), self.MANAGER)

    def test_disabled_manager_cannot_approve(self):
        frappe.db.set_value("User", self.MANAGER, "enabled", 0)
        try:
            with self.assertRaises(ApprovalRequired):
                self._require(pin=self.PIN)
        finally:
            frappe.db.set_value("User", self.MANAGER, "enabled", 1)

    def test_pin_format_is_validated_on_save(self):
        doc = frappe.get_doc("User", self.MANAGER)
        for bad in ("12", "abcd", "123456789", "12 34"):
            doc.custom_pos_pin = bad
            with self.assertRaises(frappe.ValidationError):
                manager_approval.validate_pin_format(doc)
        doc.custom_pos_pin = "987654"
        manager_approval.validate_pin_format(doc)  # xato bermaydi

    def test_masked_pin_is_not_revalidated(self):
        doc = frappe.get_doc("User", self.MANAGER)
        doc.custom_pos_pin = "*****"
        manager_approval.validate_pin_format(doc)  # o'zgarmagan parol — xato emas

    def test_approver_list_never_contains_the_pin(self):
        result = manager_approval.list_approvers()
        users = {row["user"] for row in result}
        self.assertIn(self.MANAGER, users)
        self.assertNotIn(self.CASHIER, users)
        for row in result:
            self.assertEqual(set(row), {"user", "full_name"})

    def test_audit_comment_is_written_on_success(self):
        invoice = frappe.db.get_value("POS Invoice", {}, "name")
        if not invoice:
            self.skipTest("POS Invoice yo'q")
        self._require(pin=self.PIN, reference_doctype="POS Invoice", reference_name=invoice)
        self.assertTrue(
            frappe.db.exists(
                "Comment",
                {
                    "reference_doctype": "POS Invoice",
                    "reference_name": invoice,
                    "content": ["like", "%Sinov amali%"],
                },
            )
        )

    def test_api_lists_approvers_for_a_cashier(self):
        frappe.set_user("Administrator")
        result = approval_api.get_approvers()
        self.assertIn("approvers", result)
        self.assertIn("self_approves", result)
        self.assertNotIn("pin", json.dumps(result).lower())
