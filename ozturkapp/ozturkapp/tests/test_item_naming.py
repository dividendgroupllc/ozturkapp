# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Item kodi — avtomatik `ITEM-####` (utils/item_naming.py, setup/item_codes.py)."""

import re
from unittest import mock

import frappe
from frappe.model.rename_doc import rename_doc
from frappe.tests.utils import FrappeTestCase

from ozturkapp.ozturkapp.setup import item_codes
from ozturkapp.ozturkapp.utils import item_naming

CODE = re.compile(r"^ITEM-(\d{4,})$")


class TestItemNaming(FrappeTestCase):
    def setUp(self):
        self.addCleanup(frappe.clear_cache, doctype="Item")
        item_naming.apply_form_settings()
        self.group = frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name", limit=1)[0]

    def _item(self, item_name, **extra):
        return frappe.get_doc(
            {
                "doctype": "Item",
                "item_name": item_name,
                "item_group": self.group,
                "stock_uom": "Nos",
                "is_stock_item": 0,
                **extra,
            }
        ).insert(ignore_permissions=True)

    @staticmethod
    def _number(code):
        return int(CODE.match(code).group(1))

    # ── kod berish ─────────────────────────────────────────────────

    def test_new_item_gets_the_next_code_in_the_series(self):
        first = self._item("_Test Naming A")
        second = self._item("_Test Naming B")

        self.assertRegex(first.name, CODE)
        self.assertEqual(first.item_code, first.name, "name va item_code bir xil bo'lishi shart")
        self.assertEqual(self._number(second.name), self._number(first.name) + 1)
        self.assertEqual(len(first.name), len("ITEM-0001"), "kamida 4 xonali")

    def test_a_code_typed_by_the_user_is_ignored(self):
        item = self._item("_Test Naming Typed", item_code="MY OWN CODE")

        self.assertRegex(item.name, CODE)
        self.assertFalse(frappe.db.exists("Item", "MY OWN CODE"))

    def test_duplicating_an_item_does_not_collide_on_its_code(self):
        # "Duplicate" eski kodni nusxalaydi; maydon o'qiladigan bo'lgani uchun
        # foydalanuvchi uni tuzata olmaydi — tizim o'zi yangisini berishi kerak.
        original = self._item("_Test Naming Original")
        copy = self._item("_Test Naming Copy", item_code=original.item_code)

        self.assertNotEqual(copy.name, original.name)
        self.assertRegex(copy.name, CODE)

    def test_item_name_is_left_as_entered(self):
        item = self._item("_Test Naming Dish")

        self.assertEqual(frappe.db.get_value("Item", item.name, "item_name"), "_Test Naming Dish")

    # ── seriya hisoblagichi ────────────────────────────────────────

    def test_series_floor_lifts_the_counter_above_existing_codes(self):
        item = self._item("_Test Naming Floor")
        rename_doc(
            "Item", item.name, "ITEM-0900", force=True, ignore_permissions=True,
            rebuild_search=False, show_alert=False,
        )

        self.assertGreaterEqual(item_naming.ensure_series_floor(), 900)
        self.assertGreaterEqual(item_naming.series_current(), 900)
        self.assertGreater(self._number(item_naming.next_item_code()), 900)

    def test_next_code_skips_a_number_that_is_already_taken(self):
        item = self._item("_Test Naming Skip")
        taken = f"ITEM-{item_naming.series_current() + 1:04d}"
        rename_doc(
            "Item", item.name, taken, force=True, ignore_permissions=True,
            rebuild_search=False, show_alert=False,
        )

        self.assertNotEqual(item_naming.next_item_code(), taken)

    # ── forma sozlamalari ──────────────────────────────────────────

    def test_item_form_settings(self):
        frappe.clear_cache(doctype="Item")
        meta = frappe.get_meta("Item")

        self.assertTrue(meta.get_field("item_code").read_only)
        self.assertFalse(meta.get_field("item_code").reqd, "bo'sh va o'qiladigan maydon majburiy bo'lmasin")
        self.assertFalse(meta.get_field("item_code").hidden)
        self.assertTrue(meta.get_field("item_name").reqd)
        self.assertFalse(meta.allow_rename, "Rename kodni o'zgartirishning ikkinchi yo'li")

    def test_saving_stock_settings_keeps_item_code_optional(self):
        # ERPNext Stock Settings saqlanganda item_code'ni majburiy qilib qo'yadi.
        frappe.get_doc("Stock Settings").save()

        frappe.clear_cache(doctype="Item")
        self.assertFalse(frappe.get_meta("Item").get_field("item_code").reqd)
        self.assertTrue(frappe.get_meta("Item").get_field("item_code").read_only)


class TestRenumberItems(FrappeTestCase):
    def setUp(self):
        self.addCleanup(frappe.clear_cache, doctype="Item")
        item_naming.apply_form_settings()
        self.group = frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name", limit=1)[0]

    def _legacy(self, item_name, legacy_code):
        item = frappe.get_doc(
            {
                "doctype": "Item",
                "item_name": item_name,
                "item_group": self.group,
                "stock_uom": "Nos",
                "is_stock_item": 0,
            }
        ).insert(ignore_permissions=True)
        rename_doc(
            "Item", item.name, legacy_code, force=True, ignore_permissions=True,
            rebuild_search=False, show_alert=False,
        )
        return legacy_code

    def test_plan_numbers_legacy_items_in_creation_order_and_skips_conforming_ones(self):
        first = self._legacy("_Test Legacy 1", "_TEST LEGACY 1")
        second = self._legacy("_Test Legacy 2", "_TEST LEGACY 2")
        conforming = frappe.get_doc(
            {"doctype": "Item", "item_name": "_Test Conforming", "item_group": self.group,
             "stock_uom": "Nos", "is_stock_item": 0}
        ).insert(ignore_permissions=True)

        plan = {old: code for old, code, *_ in item_codes.build_plan()}

        self.assertNotIn(conforming.name, plan)
        self.assertRegex(plan[first], CODE)
        self.assertLess(plan[first], plan[second], "eski Item kichik raqam olishi kerak")
        self.assertEqual(len(set(plan.values())), len(plan), "kodlar takrorlanmasin")
        self.assertFalse(
            set(plan.values()) & set(frappe.get_all("Item", pluck="name")),
            "reja band kodni tanlamasligi kerak",
        )

    def test_planning_does_not_write_anything(self):
        self._legacy("_Test Legacy Plan", "_TEST LEGACY PLAN")
        before = item_naming.series_current()

        item_codes.build_plan()

        self.assertEqual(item_naming.series_current(), before)
        self.assertTrue(frappe.db.exists("Item", "_TEST LEGACY PLAN"))

    def test_rename_keeps_the_dish_name_even_when_it_equalled_the_old_code(self):
        # Production'dagi barcha Item'larda item_name == item_code edi va
        # `Item.before_rename` uni yangi kodga almashtirib yuboradi.
        legacy = self._legacy("_Test Same Name", "_TEST SAME NAME")
        frappe.db.set_value("Item", legacy, "item_name", legacy)

        code = item_codes.rename_item(legacy)

        self.assertRegex(code, CODE)
        self.assertFalse(frappe.db.exists("Item", legacy))
        self.assertEqual(frappe.db.get_value("Item", code, "item_name"), "_TEST SAME NAME")
        self.assertEqual(frappe.db.get_value("Item", code, "item_code"), code)

    def test_quick_items_json_is_remapped(self):
        profile = frappe._dict(name="P1", custom_quick_items='{"1": "OLD", "2": "KEEP"}')
        with mock.patch.object(frappe, "get_all", return_value=[profile]), \
                mock.patch.object(frappe, "get_meta"), \
                mock.patch.object(frappe.db, "set_value") as set_value:
            item_codes._remap_quick_items({"OLD": "ITEM-0001"})

        set_value.assert_called_once()
        saved = set_value.call_args.args[3]
        self.assertEqual(saved, '{"1": "ITEM-0001", "2": "KEEP"}')

    def test_quick_items_without_a_match_are_not_rewritten(self):
        profile = frappe._dict(name="P1", custom_quick_items='{"1": "KEEP"}')
        with mock.patch.object(frappe, "get_all", return_value=[profile]), \
                mock.patch.object(frappe, "get_meta"), \
                mock.patch.object(frappe.db, "set_value") as set_value:
            item_codes._remap_quick_items({"OLD": "ITEM-0001"})

        set_value.assert_not_called()
