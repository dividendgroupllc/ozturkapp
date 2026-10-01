# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Prodaja Sheets: taom (Product Bundle) tannarxi + POS menyu filtri.

Test o'z ma'lumotini yaratadi va oxirida qaytaradi (FrappeTestCase rollback):
    xomashyo A, B (zaxira) + taom D (zaxirasiz, Product Bundle: 0.2 A + 0.1 B)
    Kirim: A 10 × 10 000, B 10 × 5 000
    SI (update_stock) — D × 3 @ 20 000  → tannarx 3 × 2 500

Ishga tushirish::

    bench --site ozturk.local run-tests --app ozturkapp \
        --module ozturkapp.ozturkapp.tests.test_report_costs
"""

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, today

COMPANY = "O'zturk Maksim Gorkiy"
A_RATE, B_RATE = 10000, 5000
DISH_QTY, DISH_PRICE = 3, 20000
DISH_UNIT_COST = 0.2 * A_RATE + 0.1 * B_RATE        # 2 500


def _run(report, filters):
    from frappe.desk.query_report import run

    return run(report, filters=filters, ignore_prepared_report=True)


def _warehouse():
    return frappe.db.get_value("Warehouse", {"company": COMPANY, "is_group": 0, "name": ["like", "Stores%"]}) \
        or frappe.db.get_value("Warehouse", {"company": COMPANY, "is_group": 0})


@unittest.skipUnless(frappe.db.exists("Company", COMPANY) and _warehouse(), "Kompaniya yoki ombor topilmadi")
class TestProdajaBundleCost(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.wh = _warehouse()
        suffix = frappe.generate_hash(length=5)

        def item(name, stock=1):
            return frappe.get_doc({
                "doctype": "Item", "item_name": f"_Test Rep {name} {suffix}",
                "item_group": "All Item Groups", "stock_uom": "Kg" if stock else "Nos",
                "is_stock_item": stock,
            }).insert(ignore_permissions=True).name

        cls.a, cls.b, cls.dish = item("A"), item("B"), item("Taom", stock=0)
        frappe.get_doc({
            "doctype": "Product Bundle", "new_item_code": cls.dish,
            "items": [{"item_code": cls.a, "qty": 0.2}, {"item_code": cls.b, "qty": 0.1}],
        }).insert(ignore_permissions=True)

        se = frappe.get_doc({
            "doctype": "Stock Entry", "stock_entry_type": "Material Receipt", "company": COMPANY,
            "items": [
                {"item_code": cls.a, "qty": 10, "t_warehouse": cls.wh, "basic_rate": A_RATE},
                {"item_code": cls.b, "qty": 10, "t_warehouse": cls.wh, "basic_rate": B_RATE},
            ],
        })
        se.insert(ignore_permissions=True)
        se.submit()

        customer = frappe.get_doc({
            "doctype": "Customer", "customer_name": f"_Test Rep Mijoz {suffix}",
            "customer_group": "All Customer Groups", "territory": "All Territories",
        }).insert(ignore_permissions=True).name
        si = frappe.new_doc("Sales Invoice")
        si.company = COMPANY
        si.customer = customer
        si.update_stock = 1
        si.set_warehouse = cls.wh
        si.ignore_pricing_rule = 1
        si.append("items", {"item_code": cls.dish, "qty": DISH_QTY, "rate": DISH_PRICE, "warehouse": cls.wh})
        si.insert(ignore_permissions=True)
        si.submit()
        cls.si = si

    def test_dish_cost_from_stock_ledger(self):
        rows = _run("Prodaja Sheets", {"from_date": today(), "to_date": today(), "company": COMPANY}).get("result") or []
        mine = [r for r in rows if isinstance(r, dict) and r.get("sales_invoice") == self.si.name]
        self.assertEqual(len(mine), 1)
        self.assertAlmostEqual(flt(mine[0]["cost_amount"]), DISH_QTY * DISH_UNIT_COST, places=2)
        self.assertAlmostEqual(flt(mine[0]["amount"]), DISH_QTY * DISH_PRICE, places=2)

    def test_dish_cost_from_packed_items_without_sle(self):
        from ozturkapp.ozturkapp.report.prodaja_sheets.prodaja_sheets import attach_costs

        row_name = self.si.items[0].name
        row = frappe._dict(voucher_type="Sales Invoice", sales_invoice=self.si.name, row_name=row_name,
                           update_stock=0, item_cost=0)
        attach_costs([row])
        self.assertAlmostEqual(flt(row.cost_amount), DISH_QTY * DISH_UNIT_COST, places=2)

    def test_pos_bundle_uses_current_valuation(self):
        from ozturkapp.ozturkapp.report.prodaja_sheets.prodaja_sheets import attach_costs

        row = frappe._dict(voucher_type="POS Invoice", sales_invoice="_no_such_pos", row_name="_x",
                           item_code=self.dish, stock_qty=2, warehouse=self.wh)
        attach_costs([row])
        self.assertAlmostEqual(flt(row.cost_amount), 2 * DISH_UNIT_COST, places=2)


class TestUryMenuFilter(FrappeTestCase):
    def test_disabled_items_are_hidden(self):
        from ozturkapp.ozturkapp.overrides.ury_menu import filter_disabled_items

        on = frappe.get_doc({"doctype": "Item", "item_name": "_Test Menyu Yoqiq " + frappe.generate_hash(length=5),
                             "item_group": "All Item Groups", "stock_uom": "Nos", "is_stock_item": 0}
                            ).insert(ignore_permissions=True).name
        off = frappe.get_doc({"doctype": "Item", "item_name": "_Test Menyu Ochiq " + frappe.generate_hash(length=5),
                              "item_group": "All Item Groups", "stock_uom": "Nos", "is_stock_item": 0,
                              "disabled": 1}).insert(ignore_permissions=True).name
        data = filter_disabled_items({"items": [{"item": on}, {"item": off}], "name": "M"})
        self.assertEqual([r["item"] for r in data["items"]], [on])
        self.assertEqual(filter_disabled_items(None), None)

    def test_override_is_registered(self):
        hooks = frappe.get_hooks("override_whitelisted_methods")
        self.assertEqual(hooks.get("ury.ury_pos.api.getRestaurantMenu"),
                         ["ozturkapp.ozturkapp.overrides.ury_menu.getRestaurantMenu"])
