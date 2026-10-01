# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Hisobotlar: taom tannarxi (Product Bundle) va ichki (sklad → filial) harakatlar.

Test o'z ma'lumotini yaratadi va oxirida qaytaradi (FrappeTestCase rollback):
    xomashyo A, B (zaxira) + taom D (zaxirasiz, Product Bundle: 0.2 A + 0.1 B)
    Sklad: kirim A 10 × 10 000, B 10 × 5 000
    BST:   Sklad → filial, A 5 + B 5  (tan narxda: 75 000)
    Filial: tashqi mijozga SI (update_stock) — D × 3 @ 20 000  → tannarx 3 × 2 500

Kerak: ikki kompaniya (ota guruhi bilan), filial ombori va ichki kontragentlar
— bo'lmasa SKIP (BST testi bilan bir xil shartlar).

Ishga tushirish::

    bench --site ozturk.local run-tests --app ozturkapp \
        --module ozturkapp.ozturkapp.tests.test_intercompany_reports
"""

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, today

from ozturkapp.ozturkapp.doctype.branch_stock_transfer.test_branch_stock_transfer import (
    FROM_COMPANY,
    TO_COMPANY,
    TO_WAREHOUSE,
    _from_warehouse,
    _prereqs_ok,
)

A_RATE, B_RATE = 10000, 5000
DISH_QTY, DISH_PRICE = 3, 20000
DISH_UNIT_COST = 0.2 * A_RATE + 0.1 * B_RATE        # 2 500
BST_AMOUNT = 5 * A_RATE + 5 * B_RATE                 # 75 000


def _run(report, filters):
    from frappe.desk.query_report import run

    return run(report, filters=filters, ignore_prepared_report=True)


def _rows(result):
    return result.get("result") or []


@unittest.skipUnless(
    _prereqs_ok() and frappe.db.get_value("Company", FROM_COMPANY, "parent_company"),
    "Kompaniyalar, guruh yoki ichki kontragentlar topilmadi",
)
class TestIntercompanyReports(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.group = frappe.db.get_value("Company", FROM_COMPANY, "parent_company")
        cls.from_wh = _from_warehouse()
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
            "doctype": "Stock Entry", "stock_entry_type": "Material Receipt", "company": FROM_COMPANY,
            "items": [
                {"item_code": cls.a, "qty": 10, "t_warehouse": cls.from_wh, "basic_rate": A_RATE},
                {"item_code": cls.b, "qty": 10, "t_warehouse": cls.from_wh, "basic_rate": B_RATE},
            ],
        })
        se.insert(ignore_permissions=True)
        se.submit()

        bst = frappe.new_doc("Branch Stock Transfer")
        bst.posting_date = today()
        bst.from_company, bst.from_warehouse = FROM_COMPANY, cls.from_wh
        bst.to_company, bst.to_warehouse = TO_COMPANY, TO_WAREHOUSE
        bst.append("items", {"source_type": "Item", "reference": cls.a, "qty": 5})
        bst.append("items", {"source_type": "Item", "reference": cls.b, "qty": 5})
        bst.insert()
        bst.submit()
        cls.bst = bst

        customer = frappe.get_doc({
            "doctype": "Customer", "customer_name": f"_Test Rep Mijoz {suffix}",
            "customer_group": "All Customer Groups", "territory": "All Territories",
        }).insert(ignore_permissions=True).name
        si = frappe.new_doc("Sales Invoice")
        si.company = TO_COMPANY
        si.customer = customer
        si.update_stock = 1
        si.set_warehouse = TO_WAREHOUSE
        si.ignore_pricing_rule = 1
        si.append("items", {"item_code": cls.dish, "qty": DISH_QTY, "rate": DISH_PRICE,
                            "warehouse": TO_WAREHOUSE})
        si.insert(ignore_permissions=True)
        si.submit()
        cls.si = si

    # ── Prodaja Sheets ────────────────────────────────────────────
    def test_prodaja_dish_cost_from_packed_items(self):
        rows = _rows(_run("Prodaja Sheets", {"from_date": today(), "to_date": today(),
                                             "company": TO_COMPANY}))
        mine = [r for r in rows if r.get("sales_invoice") == self.si.name]
        self.assertEqual(len(mine), 1)
        self.assertAlmostEqual(flt(mine[0]["cost_amount"]), DISH_QTY * DISH_UNIT_COST, places=2)
        self.assertAlmostEqual(flt(mine[0]["amount"]), DISH_QTY * DISH_PRICE, places=2)

        # Packed Item yo'lining o'zi ham (SLE'siz zaxira yo'l) shu natijani beradi
        packed = frappe.get_all("Packed Item", filters={"parent": self.si.name},
                                fields=["incoming_rate", "qty"])
        self.assertAlmostEqual(sum(flt(p.incoming_rate) * flt(p.qty) for p in packed),
                               DISH_QTY * DISH_UNIT_COST, places=2)

    def test_prodaja_pos_bundle_uses_current_valuation(self):
        from ozturkapp.ozturkapp.report.prodaja_sheets.prodaja_sheets import attach_costs

        row = frappe._dict(voucher_type="POS Invoice", sales_invoice="_no_such_pos", row_name="_x",
                           item_code=self.dish, stock_qty=2, warehouse=TO_WAREHOUSE)
        attach_costs([row])
        self.assertAlmostEqual(flt(row.cost_amount), 2 * DISH_UNIT_COST, places=2)

    def test_prodaja_excludes_internal_by_default(self):
        base = {"from_date": today(), "to_date": today(), "company": FROM_COMPANY}
        rows = _rows(_run("Prodaja Sheets", base))
        self.assertFalse([r for r in rows if r.get("sales_invoice") == self.bst.sales_invoice])

        rows = _rows(_run("Prodaja Sheets", dict(base, include_internal=1)))
        mine = [r for r in rows if r.get("sales_invoice") == self.bst.sales_invoice]
        self.assertEqual(len(mine), 2)
        self.assertTrue(all(r["is_internal"] for r in mine))
        # Tan narxda: tushum == tannarx
        self.assertAlmostEqual(sum(flt(r["amount"]) for r in mine), BST_AMOUNT, places=2)
        self.assertAlmostEqual(sum(flt(r["cost_amount"]) for r in mine), BST_AMOUNT, places=2)

    # ── Prixod Sheets ─────────────────────────────────────────────
    def test_prixod_excludes_internal_by_default(self):
        base = {"from_date": today(), "to_date": today(), "company": TO_COMPANY}
        rows = _rows(_run("Prixod Sheets", base))
        self.assertFalse([r for r in rows if r.get("purchase_invoice") == self.bst.purchase_invoice])
        rows = _rows(_run("Prixod Sheets", dict(base, include_internal=1)))
        mine = [r for r in rows if r.get("purchase_invoice") == self.bst.purchase_invoice]
        self.assertEqual(len(mine), 2)
        self.assertTrue(all(r["is_internal"] for r in mine))

    # ── Material Report ───────────────────────────────────────────
    def test_material_report_intercompany_columns(self):
        def row(company, item):
            rows = _rows(_run("Material Report", {"from_date": today(), "to_date": today(),
                                                  "company": company, "item_code": item}))
            return next(r for r in rows if r.get("item_code") == item)

        src = row(FROM_COMPANY, self.a)
        self.assertAlmostEqual(flt(src["ic_out_qty"]), 5)
        self.assertAlmostEqual(flt(src["sales_qty"]), 0)
        self.assertAlmostEqual(flt(src["closing_qty"]), 5)

        dst = row(TO_COMPANY, self.a)
        self.assertAlmostEqual(flt(dst["ic_in_qty"]), 5)
        self.assertAlmostEqual(flt(dst["purchase_qty"]), 0)
        # Taom sotuvi masalliqni "Sotilgan" sifatida chiqaradi (ichki emas)
        self.assertAlmostEqual(flt(dst["sales_qty"]), DISH_QTY * 0.2)
        self.assertAlmostEqual(flt(dst["closing_qty"]), 5 - DISH_QTY * 0.2)

    # ── PL Obshi ──────────────────────────────────────────────────
    def test_pl_obshi_eliminates_internal_sales(self):
        result = _run("PL Obshi", {"company": self.group, "from_date": today(), "to_date": today(),
                                   "periodicity": "Monthly", "show_internal": 1})
        rows = _rows(result)
        fk = result["columns"][1]["fieldname"]

        def val(label):
            return flt(next(r for r in rows if r.get("label") == label)[fk])

        company_revenue = sum(flt(r[fk]) for r in rows
                              if r.get("indent") == 1 and str(r.get("label", "")).startswith("Выручка "))
        internal = [r for r in rows if str(r.get("label", "")).startswith("(−) Ички айланма")]
        self.assertTrue(internal)
        self.assertLessEqual(flt(internal[0][fk]), -BST_AMOUNT + 0.01)
        self.assertAlmostEqual(val("Итого выручка"), company_revenue + flt(internal[0][fk]), places=2)
        # Elimination foydaga ta'sir qilmaydi
        self.assertAlmostEqual(val("Операционная прибыль"),
                               val("Операционная прибыль (компаниялар кесимида)"), places=2)

    # ── Intercompany Sverka ───────────────────────────────────────
    def test_intercompany_sverka_balances_and_documents(self):
        rows = _rows(_run("Intercompany Sverka", {"from_date": today(), "to_date": today(),
                                                  "company": self.group}))
        pair = next(r for r in rows if r.get("row_type") == "root"
                    and r.get("label") == f"{FROM_COMPANY}  →  {TO_COMPANY}")
        self.assertGreaterEqual(flt(pair["seller_amount"]), BST_AMOUNT - 0.01)
        self.assertAlmostEqual(flt(pair["diff"]), 0, places=2)

        doc = next(r for r in rows if r.get("sales_invoice") == self.bst.sales_invoice)
        self.assertEqual(doc["purchase_invoice"], self.bst.purchase_invoice)
        self.assertEqual(doc["branch_stock_transfer"], self.bst.name)
        self.assertAlmostEqual(flt(doc["diff"]), 0, places=2)

    # ── Kontragent Otchet / DDS / Expense Analysis ────────────────
    def test_kontragent_otchet_marks_internal(self):
        rows = _rows(_run("Kontragent Otchet", {"from_date": today(), "to_date": today(),
                                                "company": FROM_COMPANY, "party_type": "Customer"}))
        internal = [r for r in rows if r.get("is_internal")]
        self.assertTrue(internal)
        self.assertTrue(all(str(r["category"]).startswith("Ichki") for r in internal))

        only_ext = _rows(_run("Kontragent Otchet", {"from_date": today(), "to_date": today(),
                                                    "company": FROM_COMPANY, "party_type": "Customer",
                                                    "internal": "Faqat tashqi"}))
        self.assertFalse([r for r in only_ext if r.get("is_internal")])

    def test_other_reports_run(self):
        base = {"from_date": today(), "to_date": today()}
        _run("DDS", base)
        _run("DDS", dict(base, category="Ички (филиал/склад)"))
        for extra in ({}, {"category": "Ички (филиал/склад)"}, {"category": "Тақсимланган (склад харажати)"},
                      {"category": "Поставщики"}, {"exclude_allocation": 1, "root_type": "All"}):
            _run("Expense Analysis", dict(base, **extra))


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
