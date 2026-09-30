# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa funksiyalari reestri va menejer amallari (faqat rol, PIN yo'q) testlari.

Ishga tushirish::

    bench --site ozturk.local run-tests \
        --module ozturkapp.ozturkapp.tests.test_cashier_features
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

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

    def test_cashier_discount_limit_setting_is_gone(self):
        """Chegirma menejer tasdig'isiz — kassir chegarasi sozlamasi olib tashlangan."""
        self.assertNotIn("max_cashier_discount_percent", cashier_features.SETTINGS)
        self.assertNotIn("max_cashier_discount_percent", cashier_features.get_settings(self.profile))
        self.assertIn("custom_max_cashier_discount_percent", cashier_features.OBSOLETE_FIELDS)
        self.assertFalse(
            frappe.db.exists("Custom Field", "POS Profile-custom_max_cashier_discount_percent"),
            "Eskirgan Custom Field o'chirilishi kerak (bench execute ...cashier_features.setup)",
        )

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
        ours += [cashier_features.SECTION_FIELD]
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

        # Ro'yxatda bir nechta eskirgan maydon bor — bu yerda bittasi bilan sinaymiz.
        with mock.patch.object(
            cashier_features, "OBSOLETE_FIELDS", ("custom_enable_kiosk_mode",)
        ), mock.patch.object(frappe.db, "get_value", return_value=ours), mock.patch.object(
            frappe, "delete_doc"
        ) as delete:
            cashier_features._remove_obsolete_fields()
        delete.assert_called_once()
        self.assertEqual(delete.call_args.args[:2], ("Custom Field", ours.name))


class TestManagerOnly(FrappeTestCase):
    """Menejer amali: PIN yo'q — joriy foydalanuvchi menejer bo'lsagina o'tadi."""

    MANAGER = "pin-manager@example.com"
    CASHIER = "pin-cashier@example.com"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = cls._make_user(cls.MANAGER, ["URY Manager"])
        cls.cashier = cls._make_user(cls.CASHIER, ["URY Cashier"])

    @staticmethod
    def _make_user(email, roles):
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
        doc.insert(ignore_permissions=True)
        return doc

    def setUp(self):
        frappe.set_user(self.CASHIER)

    def tearDown(self):
        frappe.set_user("Administrator")

    def _invoice(self):
        invoice = frappe.db.get_value("POS Invoice", {}, "name")
        if not invoice:
            self.skipTest("POS Invoice yo'q")
        return invoice

    def _comment_exists(self, invoice, text="%Sinov amali%"):
        return frappe.db.exists(
            "Comment",
            {"reference_doctype": "POS Invoice", "reference_name": invoice, "content": ["like", text]},
        )

    def test_cashier_is_rejected(self):
        with self.assertRaises(ApprovalRequired) as ctx:
            manager_approval.require("Sinov amali")
        self.assertIn("faqat menejer", str(ctx.exception))
        self.assertIn("Sinov amali", str(ctx.exception))

    def test_approval_required_is_a_403_validation_error(self):
        self.assertTrue(issubclass(ApprovalRequired, frappe.ValidationError))
        self.assertEqual(ApprovalRequired.http_status_code, 403)

    def test_manager_at_the_till_passes(self):
        frappe.set_user(self.MANAGER)
        self.assertTrue(cashier_permissions.has_supervisor_role())
        self.assertEqual(manager_approval.require("Sinov"), self.MANAGER)

    def test_system_manager_passes(self):
        frappe.set_user("Administrator")
        self.assertEqual(manager_approval.require("Sinov"), "Administrator")

    def test_someone_elses_pin_cannot_be_passed_any_more(self):
        """Eski `approval={"user", "pin"}` parametri yo'q — kassir menejer nomidan o'ta olmaydi."""
        with self.assertRaises(TypeError):
            manager_approval.require("Sinov", approval={"user": self.MANAGER, "pin": "4321"})

    def test_audit_comment_is_written_on_success(self):
        invoice = self._invoice()
        frappe.set_user(self.MANAGER)
        manager_approval.require(
            "Sinov amali", reference_doctype="POS Invoice", reference_name=invoice, details="sabab"
        )
        self.assertTrue(self._comment_exists(invoice, "%Menejer amali%Sinov amali%sabab%"))

    def test_no_audit_comment_when_rejected(self):
        invoice = self._invoice()
        with self.assertRaises(ApprovalRequired):
            manager_approval.require(
                "Sinov amali (rad)", reference_doctype="POS Invoice", reference_name=invoice
            )
        self.assertFalse(self._comment_exists(invoice, "%Sinov amali (rad)%"))


class TestPinIsGone(FrappeTestCase):
    """Menejer PIN-kodi foydalanuvchi talabi bilan butunlay olib tashlangan."""

    def test_user_has_no_pin_field(self):
        self.assertEqual(manager_approval.OBSOLETE_PIN_FIELD, "custom_pos_pin")
        self.assertFalse(frappe.get_meta("User").has_field("custom_pos_pin"))
        self.assertFalse(
            frappe.db.exists("Custom Field", {"dt": "User", "fieldname": "custom_pos_pin"}),
            "Eskirgan maydon o'chirilishi kerak (bench execute ...manager_approval.setup)",
        )

    def test_user_validate_hook_is_removed(self):
        hooks = frappe.get_hooks("doc_events").get("User", {})
        self.assertNotIn("validate_pin_format", json.dumps(hooks))

    def test_approver_api_is_removed(self):
        import importlib.util

        self.assertIsNone(importlib.util.find_spec("ozturkapp.ozturkapp.api.approval"))
        for name in ("list_approvers", "validate_pin_format", "reset_attempts", "reset_requester",
                     "failed_attempts", "PIN_FIELD", "MAX_ATTEMPTS", "REQUESTER_MAX_ATTEMPTS"):
            self.assertFalse(hasattr(manager_approval, name), name)
