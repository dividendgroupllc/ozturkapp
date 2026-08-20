# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa oynasi testlari.

Ishga tushirish::

    bench --site ozturk.local run-tests \
        --module ozturkapp.ozturkapp.tests.test_cashier

DIQQAT: bu yerda POS Invoice KONSOLIDATSIYASI sinalmaydi — ERPNext'ning
`consolidate_pos_invoices()` funksiyasi ichida `frappe.db.commit()` bor,
ya'ni u testning rollback'ini buzadi va saytda axlat qoldiradi.
Konsolidatsiya ERPNext'ning o'z testlari bilan qoplangan; biz faqat
POS Invoice darajasigacha tekshiramiz.
"""

import json
import re

import frappe
from frappe.tests.utils import FrappeTestCase

from ozturkapp.ozturkapp.api import cashier as cashier_api
from ozturkapp.ozturkapp.overrides import pos_invoice as pos_invoice_override
from ozturkapp.ozturkapp.utils import (
    cashier_billing,
    cashier_permissions,
    kitchen_status,
    order_cancel,
    table_status,
)


def _free_table(branch=None):
    """Ochiq cheki BO'LMAGAN stolni topadi.

    Bazada demo/haqiqiy buyurtma turgan bo'lishi mumkin — test o'sha stolni
    tanlab qolsa noto'g'ri yiqiladi. Shuning uchun har doim bo'sh stol
    tanlanadi.
    """
    filters = {"branch": branch} if branch else {}
    busy = set()
    for row in frappe.get_all(
        "POS Invoice", filters={"docstatus": 0}, fields=["restaurant_table"]
    ):
        if row.restaurant_table:
            busy.add(row.restaurant_table)

    for name in frappe.get_all("URY Table", filters=filters, pluck="name"):
        if name not in busy:
            return name
    return None


class TestPageAssets(FrappeTestCase):
    """Desk sahifasi resurslari to'g'ri yig'ilishini tekshiradi.

    NEGA BU TEST BOR
    ================
    Frappe HTML shablonni JS satriga aylantiradi (`frappe/build.py:420`)::

        frappe.templates["restaurant_cashier"] = ' ...HTML... ';

    Mazmun BITTA TIRNOQ ichida. `scrub_html_template()` apostrofni
    ekranlashi kerak edi, lekin v15.100.0 dagi kod no-op::

        return content.replace("'", "'")

    Shu sababli HTML dagi bitta xom apostrof (masalan "bo'lmadi") JS satrini
    erta yopadi va BUTUN sahifa skripti `SyntaxError` bilan yiqiladi —
    kassir ekranida hech nima ko'rinmaydi.

    Bu aynan sodir bo'lgan xato edi; test uning qaytishini bloklaydi.
    """

    PAGE = "restaurant-cashier"

    def _assets(self):
        page = frappe.get_doc("Page", self.PAGE)
        page.load_assets()
        return page

    def test_html_template_has_no_unescaped_apostrophe(self):
        page = self._assets()
        match = re.search(
            r"frappe\.templates\[\"restaurant_cashier\"\] = '(.*)';", page.script
        )
        self.assertIsNotNone(match, "HTML shabloni skriptga qo'shilmagan")

        self.assertNotIn(
            "'",
            match.group(1),
            "HTML shablonida xom apostrof bor — `&#39;` ishlating, "
            "aks holda sahifa skripti SyntaxError bilan yiqiladi",
        )

    def test_page_script_exposes_entry_points(self):
        script = self._assets().script
        self.assertRegex(script, r"frappe\.pages\[.restaurant-cashier.\]\.on_page_load")
        self.assertIn("ozturk.cashier.Screen", script)
        self.assertIn('render_template("restaurant_cashier"', script)

    def test_page_style_is_loaded(self):
        style = self._assets().style or ""
        self.assertIn(".rc-root", style)
        self.assertIn(".rc-table--OCCUPIED", style)

    def test_service_charge_rate_is_not_hardcoded_in_frontend(self):
        """Foiz faqat ERPNext shablonida turishi kerak (TZ §8)."""
        script = self._assets().script
        code = [
            line
            for line in script.split("\n")
            if not line.strip().startswith(("*", "//", "/*"))
        ]
        self.assertNotRegex(
            "\n".join(code),
            r"\b0\.12\b",
            "xizmat haqi foizi frontend'ga qattiq yozilgan",
        )


class TestTableStatus(FrappeTestCase):
    """Holat keltirib chiqarish — sof mantiq, bazaga tegmaydi."""

    def test_clusters_group_merged_tables(self):
        tables = [
            {"name": "T1", "merged_with": "T2"},
            {"name": "T2", "merged_with": "T1"},
            {"name": "T3", "merged_with": None},
        ]
        clusters = table_status.build_clusters(tables)

        self.assertEqual(sorted(clusters["T1"]), ["T1", "T2"])
        self.assertEqual(sorted(clusters["T2"]), ["T1", "T2"])
        self.assertEqual(clusters["T3"], ["T3"])

    def test_cluster_follows_one_sided_link(self):
        """`merged_with` faqat bitta tomonda yozilgan bo'lsa ham klaster to'liq."""
        tables = [
            {"name": "T1", "merged_with": "T2"},
            {"name": "T2", "merged_with": None},
        ]
        clusters = table_status.build_clusters(tables)
        self.assertEqual(sorted(clusters["T2"]), ["T1", "T2"])

    def test_order_on_any_member_occupies_whole_cluster(self):
        order_map = {"T1": frappe._dict(name="INV-1")}
        status = table_status.derive_status("T2", ["T1", "T2"], order_map, {})
        self.assertEqual(status, table_status.OCCUPIED)

    def test_status_precedence_order_beats_reservation(self):
        """Buyurtma bronni ustunlik bilan bosadi (TZ §5)."""
        order_map = {"T1": frappe._dict(name="INV-1")}
        reservations = {"T1": frappe._dict(name="RES-1")}

        self.assertEqual(
            table_status.derive_status("T1", ["T1"], order_map, reservations),
            table_status.OCCUPIED,
        )
        self.assertEqual(
            table_status.derive_status("T1", ["T1"], {}, reservations),
            table_status.RESERVED,
        )
        self.assertEqual(
            table_status.derive_status("T1", ["T1"], {}, {}),
            table_status.AVAILABLE,
        )

    def test_oldest_order_wins_for_table(self):
        """Bo'lingan hisobda stolni birinchi band qilgan chek asosiy bo'ladi."""
        orders = [
            frappe._dict(name="INV-1", restaurant_table="T1", custom_merged_tables=None),
            frappe._dict(name="INV-2", restaurant_table="T1", custom_merged_tables=None),
        ]
        self.assertEqual(table_status.map_orders_to_tables(orders)["T1"].name, "INV-1")
        self.assertEqual(table_status.count_orders_per_table(orders)["T1"], 2)


class TestFloorLayout(FrappeTestCase):
    """Zal rejasi koordinatalari (TZ §29)."""

    def test_auto_grid_when_no_stored_layout(self):
        """Koordinata bo'lmasa stollar ustma-ust tushmasligi kerak."""
        tables = [
            {"name": f"T{i}", "layout_x": 0, "layout_y": 0,
             "layout_width": 0, "layout_height": 0}
            for i in range(7)
        ]
        table_status.apply_layout(tables)

        positions = {(t["layout"]["x"], t["layout"]["y"]) for t in tables}
        self.assertEqual(len(positions), 7, "avtomatik to'r takrorlanmasligi kerak")
        self.assertTrue(all(t["layout"]["auto"] for t in tables))
        self.assertTrue(all(t["layout"]["width"] > 0 for t in tables))

    def test_rooms_do_not_overlap_in_all_rooms_view(self):
        """Har bir zalning koordinatasi o'z (0,0) idan boshlanadi.

        "Barcha zallar" ko'rinishida ular bloklarga ajratilmasa, "Ichki zal"
        ning 1-stoli "Tashqi zal" ning 1-stoli ustiga tushib qoladi.
        """
        tables = [
            {"name": "I-1", "restaurant_room": "Ichki zal", "layout_x": 0,
             "layout_y": 0, "layout_width": 120, "layout_height": 120},
            {"name": "I-2", "restaurant_room": "Ichki zal", "layout_x": 152,
             "layout_y": 0, "layout_width": 120, "layout_height": 120},
            {"name": "T-1", "restaurant_room": "Tashqi zal", "layout_x": 0,
             "layout_y": 0, "layout_width": 120, "layout_height": 120},
            {"name": "T-2", "restaurant_room": "Tashqi zal", "layout_x": 152,
             "layout_y": 0, "layout_width": 120, "layout_height": 120},
        ]
        bands = table_status.apply_layout(tables, stack_rooms=True)

        by_name = {t["name"]: t["layout"] for t in tables}
        boxes = [(t["name"], by_name[t["name"]]) for t in tables]

        # Hech bir juftlik kesishmasligi kerak.
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                (n1, a), (n2, b) = boxes[i], boxes[j]
                overlap = (
                    a["x"] < b["x"] + b["width"]
                    and b["x"] < a["x"] + a["width"]
                    and a["y"] < b["y"] + b["height"]
                    and b["y"] < a["y"] + a["height"]
                )
                self.assertFalse(overlap, f"{n1} va {n2} ustma-ust tushdi")

        self.assertEqual([b["room"] for b in bands], ["Ichki zal", "Tashqi zal"])
        self.assertEqual([b["count"] for b in bands], [2, 2])
        # Ikkinchi zal birinchisidan pastda turishi kerak.
        self.assertGreater(bands[1]["y"], bands[0]["y"])

    def test_room_title_never_overlaps_tables(self):
        """Zal sarlavhasi stol ustiga CHIQMASLIGI kerak.

        Sarlavha `y .. y + header_height` oralig'ida turadi; shu oraliqda
        birorta stol bo'lsa — nom stolni to'sib qo'yadi (ko'rilgan xato).
        """
        tables = [
            {"name": "I-1", "restaurant_room": "Ichki zal", "layout_x": 0,
             "layout_y": 0, "layout_width": 120, "layout_height": 120},
            {"name": "T-1", "restaurant_room": "Tashqi zal", "layout_x": 0,
             "layout_y": 0, "layout_width": 120, "layout_height": 120},
        ]
        bands = table_status.apply_layout(tables, stack_rooms=True)

        for band in bands:
            self.assertGreater(
                band["header_height"], 0, "sarlavha uchun joy ajratilmagan"
            )
            title_top = band["y"]
            title_bottom = band["y"] + band["header_height"]

            for t in tables:
                top = t["layout"]["y"]
                bottom = top + t["layout"]["height"]
                self.assertFalse(
                    top < title_bottom and title_top < bottom,
                    f"{band['room']} sarlavhasi {t['name']} stoli ustiga chiqdi",
                )

    def test_first_room_title_is_inside_canvas(self):
        """Birinchi zal sarlavhasi manfiy koordinataga tushmasligi kerak.

        Ilgari sarlavha `top: -11px` bilan blokdan TASHQARIGA chiqarilgan edi —
        birinchi zalda u tuval chegarasidan chiqib ketardi.
        """
        tables = [
            {"name": "I-1", "restaurant_room": "Ichki zal", "layout_x": 0,
             "layout_y": 0, "layout_width": 120, "layout_height": 120},
        ]
        bands = table_status.apply_layout(tables, stack_rooms=True)

        self.assertGreaterEqual(bands[0]["y"], 0)
        self.assertGreaterEqual(tables[0]["layout"]["y"], bands[0]["header_height"])

    def test_single_room_view_keeps_original_coordinates(self):
        """Bitta zal tanlanganda koordinatalar SURILMAYDI."""
        tables = [
            {"name": "I-1", "restaurant_room": "Ichki zal", "layout_x": 0,
             "layout_y": 0, "layout_width": 120, "layout_height": 120},
        ]
        bands = table_status.apply_layout(tables, stack_rooms=False)
        self.assertEqual(bands, [])
        self.assertEqual(tables[0]["layout"]["y"], 0)

    def test_stored_layout_is_respected(self):
        """URY Table yagona manba — saqlangan koordinata o'zgartirilmaydi."""
        tables = [
            {"name": "T1", "layout_x": 40, "layout_y": 90,
             "layout_width": 200, "layout_height": 100},
            {"name": "T2", "layout_x": 0, "layout_y": 0,
             "layout_width": 0, "layout_height": 0},
        ]
        table_status.apply_layout(tables)

        self.assertEqual(tables[0]["layout"]["x"], 40)
        self.assertEqual(tables[0]["layout"]["width"], 200)
        self.assertFalse(tables[0]["layout"]["auto"])

    def test_unpositioned_table_does_not_land_on_a_positioned_one(self):
        """Desk'da koordinatasiz qo'shilgan stol (0,0) ga tushmasligi kerak.

        Menejer yangi `URY Table` yaratganda `layout_*` maydonlari bo'sh
        qoladi. Ilgari bunday stol saqlangan koordinata deb qabul qilinib,
        (0,0) dagi mavjud stol ustiga chiqib qolardi.
        """
        tables = [
            {"name": "bor", "layout_x": 0, "layout_y": 0,
             "layout_width": 120, "layout_height": 120},      # (0,0) da HAQIQIY stol
            {"name": "yangi", "layout_x": 0, "layout_y": 0,
             "layout_width": 0, "layout_height": 0},          # Desk'dan, koordinatasiz
        ]
        table_status.apply_layout(tables)

        a, b = tables[0]["layout"], tables[1]["layout"]
        self.assertFalse(a["auto"])
        self.assertTrue(b["auto"], "koordinatasiz stol avtomatik joylashishi kerak")

        overlap = (
            a["x"] < b["x"] + b["width"]
            and b["x"] < a["x"] + a["width"]
            and a["y"] < b["y"] + b["height"]
            and b["y"] < a["y"] + a["height"]
        )
        self.assertFalse(overlap, "yangi stol mavjud stol ustiga tushdi")
        self.assertGreater(b["y"], a["y"], "yangi stol pastda turishi kerak")

    def test_is_positioned_heuristic(self):
        self.assertFalse(
            table_status.is_positioned(
                {"layout_x": 0, "layout_y": 0, "layout_width": 0, "layout_height": 0}
            )
        )
        # (0,0) da turgan, lekin o'lchami bor stol — joylashtirilgan hisoblanadi.
        self.assertTrue(
            table_status.is_positioned(
                {"layout_x": 0, "layout_y": 0, "layout_width": 120, "layout_height": 120}
            )
        )
        self.assertTrue(table_status.is_positioned({"layout_x": 152, "layout_y": 0}))


class TestServiceCharge(FrappeTestCase):
    """Xizmat haqi — foiz kodda emas, ERPNext shablonida (TZ §8)."""

    def test_rate_comes_from_tax_template_not_code(self):
        restaurant = frappe.db.get_value("URY Restaurant", {}, "name")
        if not restaurant:
            self.skipTest("URY Restaurant yo'q")

        config = cashier_billing.get_service_charge_config(restaurant)
        if not config["enabled"]:
            self.skipTest("Xizmat haqi sozlanmagan")

        rows = frappe.get_all(
            "Sales Taxes and Charges",
            filters={"parent": config["template"], "account_head": config["account"]},
            fields=["rate"],
        )
        self.assertEqual(config["rate"], rows[0].rate)

    def test_build_bill_reads_totals_never_recomputes(self):
        """`build_bill` summalarni O'ZI hisoblamaydi — hujjatdan oladi."""
        doc = frappe._dict(
            doctype="POS Invoice",
            name="TEST-INV",
            docstatus=0,
            creation=None,
            modified=None,
            customer="X",
            currency="UZS",
            net_total=100000,
            total=100000,
            grand_total=112000,
            rounded_total=112000,
            total_taxes_and_charges=12000,
            items=[],
            taxes=[],
            get=lambda key, default=None: None,
            precision=lambda field: 2,
        )
        bill = cashier_billing.build_bill(doc, include_kitchen=False)

        self.assertEqual(bill["subtotal"], 100000)
        self.assertEqual(bill["grand_total"], 112000)
        self.assertEqual(bill["total_taxes"], 12000)


class TestCashierPermissions(FrappeTestCase):
    """Server tomonidagi ruxsat nazorati (TZ §17)."""

    def test_user_without_role_is_rejected(self):
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": "rc-test-nobody@example.com",
                "first_name": "Ruxsatsiz",
                "send_welcome_email": 0,
            }
        ).insert(ignore_permissions=True)

        frappe.set_user(user.name)
        try:
            with self.assertRaises(frappe.PermissionError):
                cashier_permissions.require_cashier()
        finally:
            frappe.set_user("Administrator")

    def test_cashier_role_is_accepted(self):
        frappe.set_user("Administrator")
        cashier_permissions.require_cashier()  # xato bermasligi kerak

    def test_unknown_table_is_rejected(self):
        scope = cashier_permissions.resolve_scope()
        with self.assertRaises(Exception):
            cashier_permissions.assert_table_in_scope("MAVJUD-EMAS", scope)


class TestTableReleaseOnPayment(FrappeTestCase):
    """TZ §23 — stol faqat HAQIQATDA bo'shaganda bo'shatiladi.

    Bu — tuzatilgan xatoning regressiya testi. Ilgari `on_submit` stolni
    SHARTSIZ bo'shatardi, ya'ni hisob bo'lingan holatda birinchi chek
    to'langanida stol bo'shab qolardi.
    """

    def setUp(self):
        self.table = _free_table()
        if not self.table:
            self.skipTest("Ochiq cheksiz URY Table yo'q")
        self.branch = frappe.db.get_value("URY Table", self.table, "branch")
        self.created = []

    def tearDown(self):
        """`FrappeTestCase` rollback'i SINF darajasida ishlaydi (frappe/tests/utils.py:46),
        ya'ni bitta sinfdagi testlar bir-birining yozuvlarini ko'radi. Shuning
        uchun har bir test o'zidan keyin tozalaydi."""
        for name in self.created:
            frappe.db.delete("POS Invoice", {"name": name})
        frappe.db.set_value(
            "URY Table", self.table, "occupied", 0, update_modified=False
        )

    def _draft(self, printed=0):
        """Stolga bog'langan qoralama chek yozuvini yaratadi (yengil usul).

        To'liq `POS Invoice` hujjati kerak emas — `_open_invoices_for` faqat
        `docstatus`, `restaurant_table` va `custom_cancelled` ni o'qiydi.
        """
        name = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabPOS Invoice`
                (name, creation, modified, owner, modified_by, docstatus,
                 restaurant_table, branch, invoice_printed, custom_cancelled)
            values (%s, now(), now(), 'Administrator', 'Administrator', 0,
                 %s, %s, %s, 0)
            """,
            (name, self.table, self.branch, printed),
        )
        self.created.append(name)
        return name

    def test_table_stays_occupied_while_another_bill_is_open(self):
        other = self._draft(printed=1)
        paid = self._draft(printed=1)

        frappe.db.set_value("URY Table", self.table, "occupied", 1, update_modified=False)

        doc = frappe._dict(
            doctype="POS Invoice",
            name=paid,
            restaurant_table=self.table,
            custom_merged_tables=None,
            get=lambda key, default=None: {
                "restaurant_table": self.table,
                "custom_merged_tables": None,
            }.get(key, default),
        )
        pos_invoice_override._reconcile_tables(doc)

        self.assertEqual(
            frappe.db.get_value("URY Table", self.table, "occupied"),
            1,
            f"{other} hali to'lanmagan — stol band qolishi kerak",
        )

    def test_table_is_released_when_last_bill_is_paid(self):
        paid = self._draft(printed=1)
        frappe.db.set_value("URY Table", self.table, "occupied", 1, update_modified=False)

        doc = frappe._dict(
            doctype="POS Invoice",
            name=paid,
            restaurant_table=self.table,
            custom_merged_tables=None,
            get=lambda key, default=None: {
                "restaurant_table": self.table,
                "custom_merged_tables": None,
            }.get(key, default),
        )
        pos_invoice_override._reconcile_tables(doc)

        self.assertEqual(
            frappe.db.get_value("URY Table", self.table, "occupied"),
            0,
            "boshqa ochiq chek yo'q — stol bo'shashi kerak",
        )

    def test_cancelled_draft_does_not_hold_the_table(self):
        """Bekor qilingan qoralama stolni ushlab turmasligi kerak."""
        if not frappe.db.has_column("POS Invoice", "custom_cancelled"):
            self.skipTest("custom_cancelled maydoni yo'q")

        cancelled = self._draft(printed=0)
        frappe.db.set_value("POS Invoice", cancelled, "custom_cancelled", 1)
        paid = self._draft(printed=1)

        frappe.db.set_value("URY Table", self.table, "occupied", 1, update_modified=False)

        doc = frappe._dict(
            doctype="POS Invoice",
            name=paid,
            restaurant_table=self.table,
            custom_merged_tables=None,
            get=lambda key, default=None: {
                "restaurant_table": self.table,
                "custom_merged_tables": None,
            }.get(key, default),
        )
        pos_invoice_override._reconcile_tables(doc)

        self.assertEqual(frappe.db.get_value("URY Table", self.table, "occupied"), 0)


class TestActiveOrderAndBillPreview(FrappeTestCase):
    """Phase 2 — faol buyurtma va hisob ko'rinishi (TZ §1-§5, §8, §9)."""

    def setUp(self):
        self.scope = cashier_permissions.resolve_scope()
        self.table = _free_table(self.scope.branch)
        if not self.table:
            self.skipTest("Ochiq cheksiz URY Table yo'q")
        self.created = []
        self.original_occupied = frappe.db.get_value(
            "URY Table", self.table, "occupied"
        )

    def tearDown(self):
        for name in self.created:
            frappe.db.delete("POS Invoice", {"name": name})
        frappe.db.set_value(
            "URY Table",
            self.table,
            "occupied",
            self.original_occupied,
            update_modified=False,
        )

    # ── §8: OCCUPIED bayrog'i bor, buyurtma yo'q ──────────────────────

    def test_stale_occupied_flag_reports_issue_instead_of_empty_bill(self):
        from ozturkapp.ozturkapp.api.table import get_table_detail

        frappe.db.set_value(
            "URY Table", self.table, "occupied", 1, update_modified=False
        )

        detail = get_table_detail(self.table)

        self.assertIsNotNone(detail["issue"], "nomuvofiqlik haqida xabar yo'q")
        self.assertEqual(detail["issue"]["code"], "STALE_OCCUPIED_FLAG")
        self.assertTrue(detail["issue"]["message"])
        self.assertIsNone(detail["bill"], "jim bo'sh hisob ko'rsatilmasligi kerak")

    def test_no_order_is_created_by_viewing_a_table(self):
        """TZ §8/§12#10 — ko'rish hech narsa yaratmaydi."""
        from ozturkapp.ozturkapp.api.table import get_table_detail

        frappe.db.set_value(
            "URY Table", self.table, "occupied", 1, update_modified=False
        )
        before = frappe.db.count("POS Invoice")

        get_table_detail(self.table)
        get_table_detail(self.table)

        self.assertEqual(frappe.db.count("POS Invoice"), before)

    def test_available_table_has_no_issue(self):
        from ozturkapp.ozturkapp.api.table import get_table_detail

        frappe.db.set_value(
            "URY Table", self.table, "occupied", 0, update_modified=False
        )
        detail = get_table_detail(self.table)

        self.assertEqual(detail["status"], table_status.AVAILABLE)
        self.assertIsNone(detail["issue"])

    # ── §3-§4: hisob ko'rinishi tuzilmasi ─────────────────────────────

    def test_bill_preview_exposes_every_required_field(self):
        """TZ §4 da sanab o'tilgan kalitlarning hammasi bo'lishi kerak."""
        doc = frappe._dict(
            doctype="POS Invoice",
            name="TEST",
            docstatus=0,
            creation=None,
            modified=None,
            customer="X",
            currency="UZS",
            net_total=100000,
            total=100000,
            grand_total=112000,
            rounded_total=112000,
            total_taxes_and_charges=12000,
            items=[],
            taxes=[],
            get=lambda key, default=None: None,
            precision=lambda field: 2,
        )
        bill = cashier_billing.build_bill(doc, include_kitchen=False)

        for field in (
            "invoice", "table", "waiter", "customer", "pax", "items",
            "subtotal", "taxes", "service_charge", "grand_total", "currency",
        ):
            self.assertIn(field, bill, f"'{field}' maydoni yo'q")

    def test_bill_preview_is_read_only_by_construction(self):
        """`build_bill` hujjatga YOZMAYDI — faqat o'qiydi (TZ §5)."""
        import inspect

        source = inspect.getsource(cashier_billing.build_bill)
        for forbidden in (".save(", ".submit(", ".insert(", "db_set", "set_value"):
            self.assertNotIn(
                forbidden, source, f"build_bill ichida '{forbidden}' bo'lmasligi kerak"
            )

    def test_bill_preview_api_never_mutates(self):
        """`get_order_bill_preview` ham yozuv amali bajarmaydi."""
        import inspect

        from ozturkapp.ozturkapp.api.order import get_order_bill_preview

        source = inspect.getsource(get_order_bill_preview)
        for forbidden in (".save(", ".submit(", ".insert(", "db_set"):
            self.assertNotIn(forbidden, source)


class TestCashierCannotEditOrder(FrappeTestCase):
    """TZ §2, §12#11 — kassa sahifasi buyurtma tahrirlash vositasi EMAS."""

    FORBIDDEN = ("add_item", "remove_item", "update_qty", "update_item", "set_rate")

    def test_no_item_mutation_endpoints_exist(self):
        from ozturkapp.ozturkapp.api import billing, cashier, order, table

        for module in (billing, cashier, order, table):
            for name in dir(module):
                self.assertNotIn(
                    name,
                    self.FORBIDDEN,
                    f"{module.__name__}.{name} — mahsulot tahrirlash endpoint'i",
                )

    def test_page_script_has_no_item_editing_calls(self):
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()

        for needle in ("sync_order", "add_item", "remove_item", "cart"):
            self.assertNotIn(
                needle,
                page.script,
                f"kassa sahifasida '{needle}' bo'lmasligi kerak",
            )


class TestShiftManagement(FrappeTestCase):
    """Kassa smenasi — ochish va yopish (TZ: kassa ochish/yopish)."""

    def test_endpoints_exist(self):
        from ozturkapp.ozturkapp.api import cashier as cashier_api

        for fn in ("open_shift", "get_shift_closing_data", "close_shift"):
            self.assertTrue(hasattr(cashier_api, fn), f"'{fn}' yo'q")

    def test_logic_is_not_duplicated(self):
        """Smena mantig'i `desktop_pos` dan QAYTA ISHLATILADI."""
        import inspect

        from ozturkapp.ozturkapp.api import cashier as cashier_api

        self.assertIn("createPosOpening", inspect.getsource(cashier_api.open_shift))
        self.assertIn("createPosClosing", inspect.getsource(cashier_api.close_shift))
        self.assertIn(
            "getPosClosingData", inspect.getsource(cashier_api.get_shift_closing_data)
        )

    def test_cashier_never_sees_expected_amounts(self):
        """KO'R SANOQ — kassirga umumiy savdo va kutilgan summa berilmaydi."""
        from ozturkapp.ozturkapp.api import cashier as cashier_api

        if not frappe.db.exists("POS Opening Entry", {"status": "Open", "docstatus": 1}):
            self.skipTest("Ochiq smena yo'q")

        data = cashier_api.get_shift_closing_data()
        for leaked in ("grand_total", "net_total", "reconciliation", "expected_amount"):
            self.assertNotIn(leaked, data, f"'{leaked}' kassirga yuborilmasligi kerak")

        # Ruxsat etilgan yagona raqam.
        self.assertIn("total_invoices", data)
        self.assertIn("cash_modes", data)

    def test_only_cash_can_be_entered(self):
        """Kassir bank/karta summasini yubora olmasligi kerak."""
        from ozturkapp.ozturkapp.api.cashier import _cash_modes, _parse_counted_cash

        profile = cashier_permissions.resolve_scope().pos_profile
        cash = _cash_modes(profile)
        if not cash:
            self.skipTest("Naqd to'lov usuli yo'q")

        self.assertEqual(_parse_counted_cash({cash[0]: 1000}, profile), {cash[0]: 1000.0})

        non_cash = [
            m["mode_of_payment"]
            for m in cashier_billing.get_payment_methods(profile)
            if m["mode_of_payment"] not in cash
        ]
        if non_cash:
            with self.assertRaises(frappe.ValidationError):
                _parse_counted_cash({non_cash[0]: 1000}, profile)

        with self.assertRaises(frappe.ValidationError):
            _parse_counted_cash({cash[0]: -1}, profile)

    def test_page_has_blind_count_with_countdown(self):
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()

        self.assertIn("Cheklar soni", page.script)
        self.assertIn("countdownTimer", page.script)
        self.assertIn("modalLocked", page.script)

        # Kutilayotgan summa/savdo ekranda BO'LMASLIGI kerak.
        for leaked in ("Kutilgan", "Tushum", "expected_amount"):
            self.assertNotIn(leaked, page.script, f"'{leaked}' ko'rsatilmasligi kerak")

    def test_count_is_entered_twice(self):
        """Ikki bosqichli sanoq — xato raqam o'tib ketmasligi uchun."""
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()

        self.assertIn("renderCountStep", page.script)
        self.assertIn("Davom etish", page.script)
        # Ikkala kiritish solishtirilishi shart.
        self.assertIn("mismatch", page.script)

    def test_modal_handlers_are_detached_before_rebinding(self):
        """Oyna qayta chizilganda ESKI hodisa ishlovchilari yechilishi shart.

        `modalBody` — doimiy element: `innerHTML` almashsa ham unga
        delegatsiyalangan ishlovchilar QOLADI. Ikki bosqichli sanoqda ular
        to'planib, bitta bosishda ikkalasi ham ishlab ketardi — 2-bosqich
        tugmasi sanoqni QAYTADAN boshlab yuborardi (ko'rilgan xato).
        """
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()

        # Ishlovchi bog'laydigan har bir joy — ya'ni `const $body = ...` —
        # `.off()` bilan olinishi kerak. Faqat o'qish uchun ishlatilgan
        # joylar (masalan `.find(...).text(...)`) bunga kirmaydi.
        import re

        bindings = re.findall(r"const \$body = \$\(this\.el\.modalBody\)([^;]*);", page.script)
        self.assertTrue(bindings, "oyna ishlovchilari topilmadi")

        for suffix in bindings:
            self.assertIn(
                ".off()",
                suffix,
                "`$body` `.off()` bilan olinishi kerak — aks holda ishlovchilar to'planadi",
            )

    def test_countdown_starts_after_first_entry_not_before(self):
        """Sanoq 1-bosqichda EMAS, 2-bosqichda boshlanadi."""
        page = frappe.get_doc("Page", "restaurant-cashier")
        script = page.load_assets() or page.script

        step = page.script[page.script.index("renderCountStep(data, first)") :]
        block = step[: step.index("setModalLocked(locked)")]

        # Sanoq `if (second)` shoxida ishga tushishi kerak.
        self.assertIn("if (second) {", block)
        countdown_at = block.index("countdownTimer = setInterval")
        second_at = block.index("if (second) {")
        self.assertGreater(
            countdown_at, second_at, "sanoq 2-bosqichdan oldin boshlanmasligi kerak"
        )

    def test_closing_is_blocked_while_orders_are_open(self):
        """To'lanmagan buyurtma bo'lsa kassa yopilmaydi.

        ILGARI BU TEST MANBA MATNINI O'QIRDI (`assertIn('"docstatus": 0')`)
        va shu sababli haqiqiy xatoni o'tkazib yubordi: sanoq bekor
        qilingan cheklarni ham qo'shib hisoblardi, kassir esa
        «2 ta to'lanmagan buyurtma bor» xabarini ko'rib, ro'yxatdan hech
        narsa topa olmasdi. Endi tekshiruv HULQ-ATVOR darajasida.
        """
        from ozturkapp.ozturkapp.api import cashier as cashier_api

        scope = cashier_permissions.resolve_scope()
        if not cashier_api._get_shift(scope).get("open"):
            self.skipTest("Ochiq kassa smenasi yo'q")

        # Testlar Administrator nomidan ketadi, u esa POS Profile'ga
        # biriktirilgan kassir emas — `assert_shift_operator()` bizni
        # tekshirmoqchi bo'lgan qadamga yetkazmasdan to'xtatadi.
        # Shu bitta tekshiruvni vaqtincha chetlab o'tamiz.
        real_check = cashier_permissions.can_operate_shift
        cashier_permissions.can_operate_shift = lambda profile, user=None: True
        self.addCleanup(
            setattr, cashier_permissions, "can_operate_shift", real_check
        )

        table = _free_table(scope.branch)
        if not table:
            self.skipTest("Ochiq cheksiz URY Table yo'q")

        name = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabPOS Invoice`
                (name, creation, modified, owner, modified_by, docstatus,
                 restaurant_table, branch, invoice_printed, custom_cancelled)
            values (%s, now(), now(), 'Administrator', 'Administrator', 0,
                 %s, %s, 0, 0)
            """,
            (name, table, scope.branch),
        )

        try:
            with self.assertRaises(frappe.ValidationError) as caught:
                cashier_api.close_shift(json.dumps({}))
            self.assertIn("to'lanmagan buyurtma", str(caught.exception))
        finally:
            frappe.db.delete("POS Invoice", {"name": name})

    def test_page_has_shift_buttons(self):
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()
        # Ochish — bloklovchi ekran orqali, yopish — modal orqali.
        self.assertIn("renderShiftGate", page.script)
        self.assertIn("closeShiftDialog", page.script)

    def test_closed_shift_blocks_the_whole_page(self):
        """Smena yopiq bo'lsa kassa oynasi ko'rsatilmaydi — faqat ochish ekrani.

        Modal bo'lganda uni yopib ishlashda davom etish mumkin edi; endi
        `data-state="shift"` butun ish maydonini berkitadi.
        """
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()

        self.assertIn('this.setState("shift")', page.script)
        self.assertIn('.rc-root[data-state="shift"]', page.style or "")

    def test_shift_button_sits_next_to_refresh(self):
        """Tugma sahifaning «Yangilash» tugmasi yonida va ochiq smenada QIZIL."""
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()

        self.assertIn("this.page.custom_actions", page.script)
        self.assertIn("btn-danger", page.script)

        style = page.style or ""
        self.assertIn(".rc-shift-action", style)

        # Tugma `.rc-root` DAN TASHQARIDA turadi — u yerda `--rc-*`
        # o'zgaruvchilar aniqlanmagan. Ular ishlatilsa `background`
        # butunlay o'chib, tugma RANGSIZ qoladi (ko'rilgan xato).
        import re

        rule = re.search(r"\.rc-shift-action\.btn-danger.*?\}", style, re.S)
        self.assertIsNotNone(rule, "qizil rang qoidasi yo'q")
        self.assertNotIn("--rc-", rule.group(0), "komponent o'zgaruvchisi ishlatilgan")
        self.assertIn("--danger", rule.group(0))

    def test_shift_button_is_created_once(self):
        """`page.add_button()` ishlatilmasligi kerak — u mobil menyuni to'ldiradi."""
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()

        # Izohlarda eslatilishi mumkin — HAQIQIY chaqiruv bo'lmasligi kerak.
        self.assertNotIn("this.page.add_button(", page.script)
        self.assertIn("if (!this.$shiftBtn)", page.script)


class TestValuationRateGuard(FrappeTestCase):
    """Tannarxsiz tovar — kassa yopilmaydi, lekin sabab TUSHUNARLI aytiladi.

    ERPNext bu holatda konsolidatsiyada yiqiladi va asl xatoni `except`
    blokida yutib yuboradi (`pos_invoice_merge_log.py:600`), o'rniga
    chalg'ituvchi «Could not find Reference Name: POS-CLO-...» beradi —
    chunki rollback'dan keyin o'sha hujjatga izoh yozmoqchi bo'ladi.

    Shuning uchun tekshiruv ERPNext'dan OLDIN bajariladi.
    """

    def test_missing_costs_are_detected(self):
        from ozturkapp.ozturkapp.setup.item_costs import missing_costs

        gaps = missing_costs()
        for gap in gaps:
            self.assertTrue(
                frappe.db.get_value("Item", gap["item"], "is_stock_item"),
                "zaxira bo'lmagan tovar tannarx talab qilmaydi",
            )
            self.assertFalse(frappe.db.get_value("Item", gap["item"], "valuation_rate"))

    def test_close_shift_checks_costs_before_erpnext(self):
        import inspect

        from ozturkapp.ozturkapp.api import cashier as cashier_api

        source = inspect.getsource(cashier_api.close_shift)
        self.assertIn("missing_costs", source)

        # Tekshiruv ERPNext konsolidatsiyasidan OLDIN bo'lishi kerak.
        guard_at = source.index("missing_costs")
        consolidate_at = source.index("make_closing_entry_from_opening")
        self.assertLess(guard_at, consolidate_at)

    def test_context_warns_early(self):
        """Menejer buni smena OXIRIDA emas, BOSHIDA bilishi kerak."""
        import inspect

        from ozturkapp.ozturkapp.api import cashier as cashier_api

        self.assertIn(
            "NO_VALUATION_RATE", inspect.getsource(cashier_api._config_warnings)
        )


class TestInvoiceOwnership(FrappeTestCase):
    """Chek egasi — smenadagi KASSIR (ofitsant emas).

    ERPNext smena hisobotini `POS Invoice.owner` bo'yicha yig'adi
    (`pos_closing_entry.get_pos_invoices`). Egasi ofitsant bo'lib qolsa,
    uning buyurtmasi kassirning Z-hisobotiga tushmaydi.
    """

    def test_waiter_api_assigns_shift_user_as_owner(self):
        import inspect

        from ozturkapp.ozturkapp.api import waiter as waiter_api

        source = inspect.getsource(waiter_api.submit_order)
        self.assertIn("shift_user", source)
        self.assertIn("cashier=shift_user", source)
        self.assertIn("owner=shift_user", source)
        # Ofitsant alohida maydonda qolishi kerak.
        self.assertIn("waiter=frappe.session.user", source)

    def test_existing_invoices_have_cashier_owner(self):
        for row in frappe.get_all(
            "POS Invoice", filters={"docstatus": 0}, fields=["name", "owner", "waiter"]
        ):
            if not row.waiter:
                continue
            shift_user = frappe.db.get_value(
                "POS Opening Entry", {"status": "Open", "docstatus": 1}, "user"
            )
            if shift_user:
                self.assertEqual(
                    row.owner,
                    shift_user,
                    f"{row.name} egasi kassir bo'lishi kerak",
                )


class TestDefaultRoomSelection(FrappeTestCase):
    """Standart holat — BARCHA ZALLAR (kassir keyin o'zgartirib oladi)."""

    def test_cashier_defaults_to_all_rooms(self):
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()

        # `default_room` ga qaytish MANTIG'I bo'lmasligi kerak.
        self.assertNotIn("restaurant || {}).default_room", page.script)
        self.assertIn("this.room = saved ? saved : null", page.script)

    def test_floor_plan_without_room_returns_every_room(self):
        """`room=None` bo'lsa filialdagi BARCHA stol qaytadi."""
        from ozturkapp.ozturkapp.api import table as table_api

        scope = cashier_permissions.resolve_scope()
        everything = table_api.get_floor_plan()
        total = frappe.db.count("URY Table", {"branch": scope.branch})

        self.assertEqual(len(everything["tables"]), total)

        rooms = {t["restaurant_room"] for t in everything["tables"]}
        if len(rooms) > 1:
            # Ko'p zal bo'lsa — bloklarga ajratilgan bo'lishi kerak.
            self.assertTrue(everything["room_bands"])


class TestCashierCannotOccupyTable(FrappeTestCase):
    """Biznes qoidasi: kassir stolni QO'LDA band qila olmaydi.

    Stol faqat BUYURTMA orqali band bo'ladi va buyurtma yopilganda
    avtomatik bo'shaydi. Kassirning stolga ta'siri faqat bron bilan
    cheklangan.
    """

    def test_seat_table_endpoint_no_longer_exists(self):
        from ozturkapp.ozturkapp.api import table as table_api

        self.assertFalse(
            hasattr(table_api, "seat_table"),
            "kassir stolni qo'lda band qila olmasligi kerak",
        )

    def test_reservation_endpoints_exist(self):
        from ozturkapp.ozturkapp.api import table as table_api

        self.assertTrue(hasattr(table_api, "reserve_table"))
        self.assertTrue(hasattr(table_api, "cancel_reservation"))

    def test_cashier_page_has_no_seat_action(self):
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()
        self.assertNotIn('data-action="seat"', page.script)
        self.assertIn('data-action="reserve"', page.script)
        self.assertIn('data-action="unreserve"', page.script)

    def test_no_manual_release_button_in_normal_flow(self):
        """Stol faqat to'lovda bo'shaydi — oddiy panelda tugma yo'q.

        `release` amali FAQAT buzilgan holat (`STALE_OCCUPIED_FLAG`)
        panelida qoladi, aks holda bunday stolni tozalab bo'lmaydi.
        """
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()
        self.assertEqual(
            page.script.count('data-action="release"'),
            1,
            "bo'shatish tugmasi faqat xato panelida bo'lishi kerak",
        )

    def test_no_ury_pos_link(self):
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()
        self.assertNotIn('data-action="pos"', page.script)

    def test_bill_is_given_not_just_opened(self):
        """«Hisobni berish» — chekni belgilaydi VA chop etadi."""
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()
        self.assertIn('data-action="give-bill"', page.script)
        self.assertIn("giveBill", page.script)
        self.assertIn("printReceipt", page.script)
        # Alohida "chop etish" tugmasi bo'lmasligi kerak.
        self.assertNotIn('data-action="print"', page.script)

    def test_errors_are_shown_by_the_page_not_erpnext(self):
        """ERPNext'ning o'z msgprint oynasi chiqmasligi kerak."""
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()
        self.assertIn("silent: true", page.script)
        # jqXHR ichidagi haqiqiy xabar o'qilishi kerak ([object Object] emas).
        self.assertIn("responseJSON", page.script)

    def test_reserving_an_occupied_table_is_rejected(self):
        """Buyurtmasi bor stolni bron qilib bo'lmaydi."""
        from ozturkapp.ozturkapp.api import table as table_api

        occupied = None
        for row in frappe.get_all(
            "POS Invoice", filters={"docstatus": 0}, fields=["restaurant_table"]
        ):
            if row.restaurant_table:
                occupied = row.restaurant_table
                break
        if not occupied:
            self.skipTest("Band stol yo'q")

        with self.assertRaises(frappe.ValidationError):
            table_api.reserve_table(occupied, customer_name="Test")


class TestTableAutoReleaseOnPayment(FrappeTestCase):
    """To'lov yopilganda stol AVTOMATIK bo'shaydi."""

    def test_release_is_automatic_on_submit(self):
        """`on_submit` hook'i stolni o'zi bo'shatadi — qo'lda amal kerak emas."""
        import inspect

        source = inspect.getsource(pos_invoice_override.on_submit)
        self.assertIn("_reconcile_tables", source)

        hooks = frappe.get_hooks("doc_events").get("POS Invoice", {})
        self.assertIn(
            "ozturkapp.ozturkapp.overrides.pos_invoice.on_submit",
            hooks.get("on_submit", []),
        )


class TestCustomerReceipt(FrappeTestCase):
    """Mijoz cheki — o'zbekcha, ustunlar ajratilgan, 3 ta yakuniy qator."""

    FORMAT = "Ozturk Chek"

    def _html(self):
        return frappe.db.get_value("Print Format", self.FORMAT, "html") or ""

    def test_format_exists_and_is_attached(self):
        self.assertTrue(frappe.db.exists("Print Format", self.FORMAT))
        self.assertEqual(
            frappe.db.get_value("Print Format", self.FORMAT, "doc_type"), "POS Invoice"
        )

    def test_qty_and_rate_are_separate_columns(self):
        html = self._html()
        # Standart ERPNext formatidagi "qty @ rate" birikmasi BO'LMASLIGI kerak.
        self.assertNotIn("@ {{ item.get_formatted", html)
        self.assertIn('{{ item.qty | int }}', html)
        self.assertIn('{{ item.get_formatted("rate") }}', html)
        self.assertIn('{{ item.get_formatted("amount") }}', html)

    def test_exactly_three_total_rows(self):
        """Jami · xizmat haqi · umumiy summa — boshqasi yo'q."""
        html = self._html()
        self.assertIn("Jami", html)
        self.assertIn("Umumiy summa", html)
        for removed in ("Total Excl. Tax", "Grand Total", "Rounded Total", "Total Taxes"):
            self.assertNotIn(removed, html, f"'{removed}' qatori olib tashlanishi kerak")

    def test_text_is_uzbek(self):
        html = self._html()
        self.assertIn("Tashrifingiz uchun rahmat", html)
        self.assertNotIn("Thank you", html)
        for english in ("Receipt No", "Customer:", ">Qty<", ">Item<"):
            self.assertNotIn(english, html)

    def test_no_customer_row_on_receipt(self):
        """Chekda «Mijoz» qatori bo'lmasligi kerak.

        POS'da mijoz har doim standart texnik yozuv («... klient») bo'ladi
        va chekda ma'no bermaydi.

        DIQQAT: shablon MANBASI emas, RENDER natijasi tekshiriladi — manbada
        bu so'z Jinja izohi ichida uchraydi, lekin chiqishda bo'lmaydi.
        """
        invoice = frappe.db.get_value("POS Invoice", {"docstatus": 0}, "name")
        if not invoice:
            self.skipTest("Chek yo'q")

        rendered = frappe.get_print("POS Invoice", invoice, print_format=self.FORMAT)

        # `<style>` va `<script>` bloklarini olib tashlaymiz — ular ichida
        # izoh sifatida yozilgan so'zlar tekshiruvni chalg'itadi.
        body = re.sub(r"<(style|script)[^>]*>.*?</\1>", "", rendered, flags=re.S)

        self.assertNotIn("Mijoz", body)
        self.assertIn("Chek raqami", body)
        self.assertIn("Tashrifingiz uchun rahmat", body)

    def test_browser_headers_are_suppressed(self):
        """Brauzer qo'shadigan URL / sana / sahifa raqami chiqmasligi kerak."""
        html = self._html()
        self.assertIn("margin: 0mm", html)
        # Sahifa sarlavhasi mijoz nomi emas, chek raqami bo'lishi kerak.
        self.assertIn("document.title", html)

    def test_standard_erpnext_format_untouched(self):
        """ERPNext'ning o'z formatiga TEGILMAGAN."""
        standard = frappe.db.get_value("Print Format", "POS Invoice", "html") or ""
        self.assertIn("Thank you, please visit again.", standard)


class TestPaymentValidation(FrappeTestCase):
    """To'lov tekshiruvi frontend'ga bog'liq emas (TZ §17)."""

    def setUp(self):
        self.scope = cashier_permissions.resolve_scope()
        self.doc = frappe._dict(
            rounded_total=112000,
            grand_total=112000,
            precision=lambda field: 2,
        )

    def _validate(self, payments):
        from ozturkapp.ozturkapp.api.billing import _validate_payments

        return _validate_payments(json.dumps(payments), self.doc, self.scope)

    def test_underpayment_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._validate([{"mode_of_payment": "Cash", "amount": 50000}])

    def test_unknown_mode_of_payment_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._validate([{"mode_of_payment": "Bitcoin", "amount": 112000}])

    def test_empty_payment_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._validate([])

    def test_negative_amount_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._validate(
                [
                    {"mode_of_payment": "Cash", "amount": 200000},
                    {"mode_of_payment": "Cash", "amount": -50000},
                ]
            )

    def test_exact_payment_is_accepted(self):
        rows = self._validate([{"mode_of_payment": "Cash", "amount": 112000}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount"], 112000)


class TestSellingRequiresOpenShift(FrappeTestCase):
    """Kassa smenasi ochilmaguncha sotuv mumkin emas.

    Smena ochilishida kassir kassadagi naqd pulni sanab kiritadi — bu
    summasiz smena oxiridagi solishtiruvni hisoblab bo'lmaydi. ERPNext
    ham buni majburlaydi, lekin inglizcha va ancha kechroq bosqichda
    (`POS Invoice.validate_pos_opening_entry`).
    """

    def test_guard_exists(self):
        from ozturkapp.ozturkapp.utils import cashier_permissions

        self.assertTrue(hasattr(cashier_permissions, "assert_shift_open"))

    def test_sale_paths_are_guarded(self):
        """Hisob ochish, to'lov va ofitsant buyurtmasi — uchalasi ham."""
        import inspect

        from ozturkapp.ozturkapp.api import billing, waiter

        for fn in (billing.open_bill, billing.submit_payment, waiter.submit_order):
            with self.subTest(fn=fn.__name__):
                self.assertIn("assert_shift_open", inspect.getsource(fn))

    def test_message_tells_the_cashier_what_to_do(self):
        """Xato matni «nima qilay?» degan savolni qoldirmasligi kerak."""
        import inspect

        from ozturkapp.ozturkapp.utils import cashier_permissions

        source = inspect.getsource(cashier_permissions.assert_shift_open)
        self.assertIn("naqd pul", source)
        self.assertIn("Kassani ochish", source)


class TestOpeningIsCashOnly(FrappeTestCase):
    """Smena ochishda faqat NAQD pul kiritiladi.

    Bank/karta bo'yicha "boshlang'ich qoldiq" tushunchasi yo'q — u pul
    kassada emas, bankda turadi. ERPNext yopilishda uni o'zi qo'shadi
    (`make_closing_entry_from_opening`: ochilishda yo'q usul chek
    uchraganda `opening_amount = 0` bilan qatorga tushadi), shuning uchun
    naqd bilan cheklash hisobotni buzmaydi.
    """

    def setUp(self):
        self.profile = frappe.get_all("POS Profile", pluck="name")[0]
        self.cash = cashier_api._cash_modes(self.profile)

    def _build(self, rows):
        return cashier_api._opening_balance_details(json.dumps(rows), self.profile)

    def test_cash_mode_is_accepted(self):
        rows = self._build(
            [{"mode_of_payment": m, "opening_amount": 500000} for m in self.cash]
        )
        self.assertEqual([r["mode_of_payment"] for r in rows], self.cash)
        self.assertTrue(all(r["opening_amount"] == 500000 for r in rows))

    def test_bank_mode_is_rejected(self):
        bank = [
            m["mode_of_payment"]
            for m in cashier_billing.get_payment_methods(self.profile)
            if m["mode_of_payment"] not in self.cash
        ]
        if not bank:
            self.skipTest("Profilda naqd bo'lmagan usul yo'q")

        rows = [{"mode_of_payment": m, "opening_amount": 0} for m in self.cash]
        rows.append({"mode_of_payment": bank[0], "opening_amount": 999})

        with self.assertRaises(frappe.ValidationError):
            self._build(rows)

    def test_missing_cash_mode_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._build([])

    def test_negative_amount_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._build(
                [{"mode_of_payment": m, "opening_amount": -1} for m in self.cash]
            )

    def test_gate_form_uses_cash_modes_only(self):
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()
        self.assertIn("this.ctx.cash_modes", page.script)


class TestClosingAtZero(FrappeTestCase):
    """Savdo bo'lmagan smena 0 bilan yopiladi.

    Ilgari yopish oynasidagi maydon BO'SH turardi va bo'sh maydon rad
    etilardi — ya'ni chek yozilmagan smenani umuman yopib bo'lmasdi.
    """

    def setUp(self):
        self.profile = frappe.get_all("POS Profile", pluck="name")[0]
        self.cash = cashier_api._cash_modes(self.profile)

    def test_server_accepts_zero(self):
        parsed = cashier_api._parse_counted_cash(
            json.dumps({m: 0 for m in self.cash}), self.profile
        )
        self.assertEqual(parsed, {m: 0.0 for m in self.cash})

    def test_zero_opening_is_accepted(self):
        rows = cashier_api._opening_balance_details(
            json.dumps([{"mode_of_payment": m, "opening_amount": 0} for m in self.cash]),
            self.profile,
        )
        self.assertTrue(all(r["opening_amount"] == 0 for r in rows))

    def test_form_prefills_zero_only_without_sales(self):
        """Savdo bo'lsa maydon bo'sh qoladi — ko'r sanoq buzilmasin."""
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()
        self.assertIn('cint(data.total_invoices) ? "" : "0"', page.script)

    def test_error_message_mentions_zero(self):
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()
        self.assertIn("0 yozing", page.script)


class TestCashierSeesKitchenUpdates(FrappeTestCase):
    """Kassa oshxona holatini REALTIME ko'radi.

    Oshpaz taom holatini `URY KOT Items` da o'zgartiradi va POS Invoice'ga
    TEGMAYDI — ya'ni `on_pos_invoice_change` ishga tushmaydi va
    `ozturk_cashier_order` chiqmaydi. Kassa esa chek panelida
    "🍳 Tayyorlanmoqda (1/3)" ni ko'rsatadi.

    Shu sababli kassa oshxona kanaliga ALOHIDA obuna bo'lishi shart —
    aks holda ko'rsatkich qo'lda yangilanmaguncha qotib qoladi.
    """

    def test_context_exposes_kitchen_channel(self):
        import inspect

        from ozturkapp.ozturkapp.api import cashier as cashier_api

        source = inspect.getsource(cashier_api.get_cashier_context)
        self.assertIn('"kitchen_item": EVENT_ITEM', source)

    def test_page_subscribes_to_kitchen_channel(self):
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()
        self.assertIn("events.kitchen_item", page.script)

    def test_event_carries_branch_and_invoice(self):
        """Kassa filtrlari AYNAN shu ikki maydonga tayanadi."""
        import inspect

        from ozturkapp.ozturkapp.utils import kitchen_realtime

        source = inspect.getsource(kitchen_realtime.emit_item_change)
        self.assertIn('"branch": branch', source)   # isOurBranch()
        self.assertIn('"invoice": invoice', source)  # touchesSelection()


class TestOrderCancellation(FrappeTestCase):
    """Ofitsant xato zakaz olganda kassir uni bekor qiladi.

    QOIDA
    =====
        Oshxona hali BOSHLAMAGAN  ->  har qanday kassir
        Oshxona BOSHLAB YUBORGAN  ->  faqat menejer

    Qoida `utils/order_cancel.py` da, "boshlangan" degan fakt esa
    `kitchen_status.get_order_progress()` da — bu yerda ikkalasi ham
    HULQ-ATVOR darajasida sinaladi, manba matnini o'qib emas.
    """

    def setUp(self):
        self.scope = cashier_permissions.resolve_scope()
        self.table = _free_table(self.scope.branch)
        if not self.table:
            self.skipTest("Ochiq cheksiz URY Table yo'q")

        self.branch = self.scope.branch
        self.invoices, self.kots = [], []

        # Testlar Administrator nomidan ketadi, ya'ni u DOIM menejer.
        # Oddiy kassirni ko'rsatish uchun rol tekshiruvi vaqtincha
        # almashtiriladi — `tearDown` uni qaytaradi.
        self._real_supervisor_check = cashier_permissions.has_supervisor_role

    def tearDown(self):
        cashier_permissions.has_supervisor_role = self._real_supervisor_check

        for kot in self.kots:
            frappe.db.delete("URY KOT Items", {"parent": kot})
            frappe.db.delete("URY KOT", {"name": kot})
        for name in self.invoices:
            frappe.db.delete("POS Invoice", {"name": name})

        frappe.db.set_value(
            "URY Table", self.table, "occupied", 0, update_modified=False
        )

    # ── Yordamchilar ──────────────────────────────────────────────────

    def _as_cashier(self, supervisor: bool):
        """Menejer huquqi bor/yo'q holatini taqlid qiladi."""
        cashier_permissions.has_supervisor_role = lambda user=None: supervisor

    def _draft(self, table=None):
        """Qoralama chek qatori — to'liq hujjat kerak emas (yengil usul)."""
        table = self.table if table is None else table
        name = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabPOS Invoice`
                (name, creation, modified, owner, modified_by, docstatus,
                 restaurant_table, branch, invoice_printed, custom_cancelled,
                 grand_total, rounded_total)
            values (%s, now(), now(), 'Administrator', 'Administrator', 0,
                 %s, %s, 0, 0, 100, 100)
            """,
            (name, table or None, self.branch),
        )
        self.invoices.append(name)
        return name

    def _kot(self, invoice, statuses, kot_type="New Order"):
        """Chekka KOT va uning mahsulotlarini biriktiradi."""
        kot = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabURY KOT`
                (name, creation, modified, owner, modified_by, docstatus,
                 invoice, branch, type, order_status)
            values (%s, now(), now(), 'Administrator', 'Administrator', 1,
                 %s, %s, %s, 'Ready For Prepare')
            """,
            (kot, invoice, self.branch, kot_type),
        )
        self.kots.append(kot)

        for idx, status in enumerate(statuses, start=1):
            frappe.db.sql(
                """
                insert into `tabURY KOT Items`
                    (name, creation, modified, owner, modified_by, docstatus,
                     parent, parenttype, parentfield, idx, item, quantity,
                     custom_kitchen_status)
                values (%s, now(), now(), 'Administrator', 'Administrator', 1,
                     %s, 'URY KOT', 'kot_items', %s, %s, 1, %s)
                """,
                (
                    frappe.generate_hash(length=10),
                    kot,
                    idx,
                    f"TEST-TAOM-{idx}",
                    status,
                ),
            )
        return kot

    def _row(self, invoice):
        return frappe._dict(name=invoice)

    def _describe(self, invoice):
        """API qanday uzatsa, test ham shunday uzatadi (to'liq hujjat emas)."""
        return order_cancel.describe(
            frappe.db.get_value(
                "POS Invoice",
                invoice,
                ["name", "docstatus", "custom_cancelled"],
                as_dict=True,
            )
        )

    def _item_statuses(self, kot):
        return sorted(
            frappe.get_all(
                "URY KOT Items",
                filters={"parent": kot, "parenttype": "URY KOT"},
                pluck="custom_kitchen_status",
            )
        )

    # ── "Boshlangan" degan fakt ───────────────────────────────────────

    def test_start_time_prep_alone_does_not_mean_the_kitchen_started(self):
        """REGRESSIYA: `start_time_prep` ni bekor qilish shartiga bog'lab bo'lmaydi.

        `URY KOT.start_time_prep` DocType'da `default = "Now"` — u KOT
        YARATILGANDA to'ladi, oshpaz ishni boshlaganda emas. Unga tayangan
        tekshiruv "ish har doim boshlangan" deb javob berardi va kassir
        hech qachon bekor qila olmasdi.
        """
        invoice = self._draft()
        kot = self._kot(invoice, [kitchen_status.PENDING, kitchen_status.PENDING])

        # URY xuddi shunday yozadi.
        frappe.db.set_value(
            "URY KOT", kot, "start_time_prep", "12:00:00", update_modified=False
        )

        progress = kitchen_status.get_order_progress(invoice)

        self.assertTrue(progress["has_kot"])
        self.assertFalse(
            progress["started"],
            "hamma taom 'Kutilmoqda' — oshxona hali boshlamagan",
        )

    def test_one_item_beyond_pending_means_started(self):
        invoice = self._draft()
        self._kot(invoice, [kitchen_status.PENDING, kitchen_status.PREPARING])

        progress = kitchen_status.get_order_progress(invoice)

        self.assertTrue(progress["started"])
        self.assertEqual(progress["pending_count"], 1)
        self.assertEqual(
            [row["status"] for row in progress["started_items"]],
            [kitchen_status.PREPARING],
        )

    def test_cancelled_item_does_not_count_as_started(self):
        invoice = self._draft()
        self._kot(invoice, [kitchen_status.PENDING, kitchen_status.CANCELLED])

        self.assertFalse(kitchen_status.get_order_progress(invoice)["started"])

    def test_cancellation_kot_is_not_kitchen_work(self):
        """«Cancelled» KOT — ko'rsatma, ovqat emas (TZ §9)."""
        invoice = self._draft()
        self._kot(invoice, [kitchen_status.PREPARING], kot_type="Cancelled")

        self.assertFalse(kitchen_status.get_order_progress(invoice)["started"])

    def test_order_without_a_kot_can_be_cancelled(self):
        """KOT yaratilmagan (masalan oshxona sozlanmagan) chek ham bekor bo'ladi."""
        invoice = self._draft()

        progress = kitchen_status.get_order_progress(invoice)
        self.assertFalse(progress["has_kot"])
        self.assertFalse(progress["started"])

    # ── Kim bekor qila oladi ──────────────────────────────────────────

    def test_plain_cashier_may_cancel_before_the_kitchen_starts(self):
        invoice = self._draft()
        self._kot(invoice, [kitchen_status.PENDING, kitchen_status.PENDING])
        self._as_cashier(supervisor=False)

        state = self._describe(invoice)

        self.assertTrue(state["allowed"])
        self.assertFalse(state["requires_supervisor"])
        self.assertFalse(state["kitchen_started"])

    def test_plain_cashier_is_blocked_once_the_kitchen_starts(self):
        invoice = self._draft()
        self._kot(invoice, [kitchen_status.PREPARING])
        self._as_cashier(supervisor=False)

        state = self._describe(invoice)

        self.assertFalse(state["allowed"])
        self.assertTrue(state["requires_supervisor"])
        self.assertTrue(state["blocked_reason"], "sabab aytilishi kerak")

        with self.assertRaises(frappe.ValidationError):
            order_cancel.cancel_invoice(self._row(invoice), "xato zakaz", self.scope)

        self.assertEqual(
            frappe.db.get_value("POS Invoice", invoice, "custom_cancelled"),
            0,
            "rad etilgan urinish hech narsani o'zgartirmasligi kerak",
        )

    def test_manager_may_force_cancel_after_the_kitchen_started(self):
        invoice = self._draft()
        self._kot(invoice, [kitchen_status.PREPARING])
        self._as_cashier(supervisor=True)

        state = self._describe(invoice)

        self.assertTrue(state["allowed"])
        self.assertTrue(state["requires_supervisor"])
        self.assertTrue(state["warning"], "menejer ogohlantirilishi kerak")

        result = order_cancel.cancel_invoice(
            self._row(invoice), "mijoz voz kechdi", self.scope
        )

        self.assertTrue(result["kitchen_started"])
        self.assertEqual(
            frappe.db.get_value("POS Invoice", invoice, "custom_cancelled"), 1
        )

    def test_paid_order_can_never_be_cancelled(self):
        invoice = self._draft()
        frappe.db.set_value("POS Invoice", invoice, "docstatus", 1)

        state = self._describe(invoice)
        self.assertFalse(state["allowed"])
        self.assertFalse(state["requires_supervisor"])

    def test_reason_is_required(self):
        invoice = self._draft()
        self._as_cashier(supervisor=False)

        for empty in ("", "   ", "."):
            with self.assertRaises(frappe.ValidationError):
                order_cancel.cancel_invoice(self._row(invoice), empty, self.scope)

        self.assertEqual(
            frappe.db.get_value("POS Invoice", invoice, "custom_cancelled"), 0
        )

    # ── Bekor qilishning oqibatlari ───────────────────────────────────

    def test_cancelling_keeps_the_document_and_records_the_reason(self):
        invoice = self._draft()
        self._as_cashier(supervisor=False)

        order_cancel.cancel_invoice(
            self._row(invoice), "Ofitsant noto'g'ri stolga yozdi", self.scope
        )

        row = frappe.db.get_value(
            "POS Invoice",
            invoice,
            ["docstatus", "custom_cancelled", "cancel_reason"],
            as_dict=True,
        )
        self.assertEqual(row.docstatus, 0, "hujjat o'chirilmaydi va bekor qilinmaydi")
        self.assertEqual(row.custom_cancelled, 1)
        self.assertEqual(row.cancel_reason, "Ofitsant noto'g'ri stolga yozdi")

    def test_cancelling_closes_the_kitchen_ticket(self):
        """Busiz oshpaz bekor qilingan buyurtmani tayyorlab yuboradi."""
        invoice = self._draft()
        kot = self._kot(invoice, [kitchen_status.PENDING, kitchen_status.PENDING])
        self._as_cashier(supervisor=False)

        result = order_cancel.cancel_invoice(
            self._row(invoice), "xato zakaz", self.scope
        )

        self.assertEqual(result["cancelled_items"], 2)
        self.assertEqual(
            self._item_statuses(kot),
            [kitchen_status.CANCELLED, kitchen_status.CANCELLED],
        )
        self.assertNotEqual(
            frappe.db.get_value("URY KOT", kot, "order_status"),
            "Ready For Prepare",
            "URY Mosaic 'Ready For Prepare' bo'yicha filtrlaydi — chipta "
            "ekranda qolib ketmasligi kerak",
        )

    def test_served_items_are_left_alone(self):
        """Berilgan taom jismonan chiqib bo'lgan — uni bekor deb yozib bo'lmaydi."""
        invoice = self._draft()
        kot = self._kot(invoice, [kitchen_status.SERVED, kitchen_status.PENDING])
        self._as_cashier(supervisor=True)

        result = order_cancel.cancel_invoice(
            self._row(invoice), "mijoz qolganidan voz kechdi", self.scope
        )

        self.assertEqual(result["cancelled_items"], 1)
        self.assertEqual(
            self._item_statuses(kot),
            sorted([kitchen_status.CANCELLED, kitchen_status.SERVED]),
        )

    def test_cancelling_frees_the_table(self):
        invoice = self._draft()
        frappe.db.set_value(
            "URY Table", self.table, "occupied", 1, update_modified=False
        )
        self._as_cashier(supervisor=False)

        result = order_cancel.cancel_invoice(
            self._row(invoice), "xato zakaz", self.scope
        )

        self.assertIn(self.table, result["freed_tables"])
        self.assertEqual(frappe.db.get_value("URY Table", self.table, "occupied"), 0)

    def test_table_stays_occupied_while_another_bill_is_open(self):
        """Hisob bo'lingan bo'lsa stol band qolishi kerak (TZ §23)."""
        cancelled = self._draft()
        self._draft()  # o'sha stolda ikkinchi ochiq chek
        frappe.db.set_value(
            "URY Table", self.table, "occupied", 1, update_modified=False
        )
        self._as_cashier(supervisor=False)

        result = order_cancel.cancel_invoice(
            self._row(cancelled), "xato zakaz", self.scope
        )

        self.assertEqual(result["freed_tables"], [])
        self.assertEqual(frappe.db.get_value("URY Table", self.table, "occupied"), 1)

    def test_order_without_a_table_can_be_cancelled(self):
        """Desktop POS / olib ketish buyurtmalarida stol bo'lmaydi."""
        invoice = self._draft(table="")
        self._as_cashier(supervisor=False)

        result = order_cancel.cancel_invoice(
            self._row(invoice), "xato zakaz", self.scope
        )

        self.assertEqual(result["freed_tables"], [])
        self.assertEqual(
            frappe.db.get_value("POS Invoice", invoice, "custom_cancelled"), 1
        )

    def test_double_cancellation_is_rejected(self):
        invoice = self._draft()
        self._as_cashier(supervisor=False)
        order_cancel.cancel_invoice(self._row(invoice), "xato zakaz", self.scope)

        with self.assertRaises(frappe.ValidationError):
            order_cancel.cancel_invoice(self._row(invoice), "yana", self.scope)


class TestCancelledOrderDoesNotBlockClosing(FrappeTestCase):
    """REGRESSIYA: bekor qilingan chek kassani yopishga xalaqit bermaydi.

    ILGARI QANDAY BUZILGANDI
    ========================
    Bekor qilingan chek o'chirilmaydi — `docstatus = 0` bo'lib qoladi va
    faqat `custom_cancelled = 1` bilan belgilanadi. Smena yopish sanog'i
    esa xom `{"docstatus": 0, "branch": ...}` bo'yicha ishlardi, ya'ni
    ularni ham qo'shib yuborardi:

        «Filialda 2 ta to'lanmagan buyurtma bor» — lekin kassirning
        ro'yxati BO'SH edi (u `custom_cancelled = 0` bo'yicha
        filtrlangan). Kassir nimani yopishni topa olmay qolardi.
    """

    def setUp(self):
        self.scope = cashier_permissions.resolve_scope()
        self.table = _free_table(self.scope.branch)
        if not self.table:
            self.skipTest("Ochiq cheksiz URY Table yo'q")
        self.created = []

    def tearDown(self):
        for name in self.created:
            frappe.db.delete("POS Invoice", {"name": name})
        frappe.db.set_value(
            "URY Table", self.table, "occupied", 0, update_modified=False
        )

    def _draft(self, cancelled=0):
        name = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabPOS Invoice`
                (name, creation, modified, owner, modified_by, docstatus,
                 restaurant_table, branch, invoice_printed, custom_cancelled)
            values (%s, now(), now(), 'Administrator', 'Administrator', 0,
                 %s, %s, 0, %s)
            """,
            (name, self.table, self.scope.branch, cancelled),
        )
        self.created.append(name)
        return name

    def test_cancelled_draft_is_not_counted_as_open(self):
        from ozturkapp.ozturkapp.api import cashier as cashier_api

        before = cashier_api._open_order_count(self.scope)
        cancelled = self._draft(cancelled=1)

        self.assertEqual(
            cashier_api._open_order_count(self.scope),
            before,
            "bekor qilingan chek 'to'lanmagan buyurtma' sifatida sanalmasligi kerak",
        )
        self.assertTrue(
            frappe.db.exists("POS Invoice", cancelled),
            "hujjat audit uchun bazada qolishi kerak",
        )

    def test_live_draft_is_still_counted(self):
        from ozturkapp.ozturkapp.api import cashier as cashier_api

        before = cashier_api._open_order_count(self.scope)
        self._draft(cancelled=0)

        self.assertEqual(cashier_api._open_order_count(self.scope), before + 1)

    def test_count_and_list_come_from_the_same_source(self):
        """Sanoq bilan ro'yxat hech qachon bir-biriga zid bo'lmasligi kerak."""
        from ozturkapp.ozturkapp.api import cashier as cashier_api

        self._draft(cancelled=1)
        self._draft(cancelled=0)

        self.assertEqual(
            cashier_api._open_order_count(self.scope),
            len(table_status.get_open_orders(self.scope.branch)),
        )


class TestCashierPageCancelButton(FrappeTestCase):
    """Kassa sahifasida bekor qilish tugmasi va stolsiz buyurtma."""

    def setUp(self):
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()
        self.script = page.script

    def test_page_has_a_cancel_action(self):
        self.assertIn('data-action="cancel-order"', self.script)
        self.assertIn("cancelOrder(detail, button)", self.script)

    def test_cancel_asks_for_a_reason(self):
        self.assertIn("api.order.cancel_order", self.script)
        self.assertIn('fieldname: "reason"', self.script)

    def test_button_state_comes_from_the_server(self):
        """Tugma holati frontendda HISOBLANMAYDI (TZ §17)."""
        self.assertIn("bill.cancellation", self.script)
        self.assertIn("cancellation.requires_supervisor", self.script)

    def test_orders_without_a_table_can_be_opened(self):
        self.assertIn("selectOrder(invoice)", self.script)
        self.assertIn('data-invoice="', self.script)


class TestShiftOperatorRestriction(FrappeTestCase):
    """Kassani faqat POS Profile'ga biriktirilgan kassir ocha/yopa oladi.

    NEGA
    ====
    ERPNext Z-hisobotni `where owner = <POS Opening Entry.user>` bilan
    yig'adi (`pos_closing_entry.get_pos_invoices`). Smena boshqa
    foydalanuvchi nomiga ochilsa, cheklar hisobotdan tushib qoladi va
    `consolidated_invoice` siz osilib, buxgalteriyaga hech qachon
    yetib bormaydi.
    """

    def setUp(self):
        from ozturkapp.ozturkapp.api import cashier as cashier_api

        self.api = cashier_api
        self.scope = cashier_permissions.resolve_scope()
        self.profile = self.scope.pos_profile
        self.listed = cashier_permissions.pos_profile_users(self.profile)
        if not self.listed:
            self.skipTest("POS Profile'da `applicable_for_users` bo'sh")

    # ── Qoida ─────────────────────────────────────────────────────────

    def test_listed_user_may_operate(self):
        self.assertTrue(
            cashier_permissions.can_operate_shift(self.profile, self.listed[0])
        )

    def test_unlisted_user_may_not_operate(self):
        self.assertFalse(
            cashier_permissions.can_operate_shift(self.profile, "hech-kim@example.com")
        )

    def test_empty_profile_list_means_no_restriction(self):
        """Sozlanmagan profil butun kassani to'xtatib qo'ymasligi kerak."""
        self.assertTrue(cashier_permissions.can_operate_shift("", "hech-kim@example.com"))

    def test_operator_names_are_human_readable(self):
        """Xato xabarida e-pochta emas, ism ko'rinishi kerak (TZ §19)."""
        names = cashier_permissions.shift_operator_names(self.profile)
        self.assertTrue(names)

    # ── Hujjat darajasi — HAR QANDAY yo'lni qamrab oladi ──────────────

    def test_opening_entry_rejects_an_unlisted_owner(self):
        """Desk, Desktop POS yoki kassa sahifasi — farqi yo'q."""
        if "Administrator" in self.listed:
            self.skipTest("Administrator ro'yxatda — sinov ma'nosiz")

        with self.assertRaises(frappe.ValidationError):
            self._opening(user="Administrator").insert()

    def test_opening_entry_accepts_a_listed_owner(self):
        doc = self._opening(user=self.listed[0])
        doc.insert()
        self.assertTrue(doc.name)
        self.assertEqual(doc.docstatus, 0)

    def _opening(self, user):
        doc = frappe.new_doc("POS Opening Entry")
        doc.update(
            {
                "period_start_date": frappe.utils.now_datetime(),
                "posting_date": frappe.utils.nowdate(),
                "user": user,
                "pos_profile": self.profile,
                "company": frappe.db.get_value("POS Profile", self.profile, "company"),
            }
        )
        for mode in self.api._cash_modes(self.profile):
            doc.append("balance_details", {"mode_of_payment": mode, "opening_amount": 0})
        return doc

    # ── API darajasi — kassa sahifasi ─────────────────────────────────

    def test_open_shift_api_rejects_an_unlisted_user(self):
        """Testlar Administrator nomidan ketadi — u ro'yxatda yo'q."""
        if frappe.session.user in self.listed:
            self.skipTest("Joriy foydalanuvchi ro'yxatda — sinov ma'nosiz")

        with self.assertRaises(frappe.PermissionError):
            self.api.open_shift(json.dumps([]))

    def test_close_shift_api_rejects_an_unlisted_user(self):
        if frappe.session.user in self.listed:
            self.skipTest("Joriy foydalanuvchi ro'yxatda — sinov ma'nosiz")

        with self.assertRaises(frappe.PermissionError):
            self.api.close_shift(json.dumps({}))

    def test_context_tells_the_page_whether_it_may_operate(self):
        ctx = self.api.get_cashier_context()

        self.assertIn("can_operate_shift", ctx["permissions"])
        self.assertEqual(
            ctx["permissions"]["can_operate_shift"],
            frappe.session.user in self.listed,
        )
        self.assertTrue(ctx["shift_operators"], "kim ochishi aytilishi kerak")


class TestShiftGateForUnauthorizedUser(FrappeTestCase):
    """Kassa YOPIQ bo'lganda kim nima ko'radi.

        KASSIR (ocha oladi)      -> bloklovchi ekran + ochish formasi
        ADMINISTRATOR / MENEJER  -> bloklanmaydi, zal va buyurtmalar
                                    ko'rinadi, holat esa yuqori paneldagi
                                    QIZIL «Kassa yopiq» yozuvida turadi

    Sotuv amallarini server baribir rad etadi (`assert_shift_open`),
    shuning uchun kuzatuvchini bloklashning ma'nosi yo'q.
    """

    def setUp(self):
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()
        self.script = page.script

    def test_only_the_operator_is_blocked_by_the_gate(self):
        self.assertIn("!(this.ctx.shift || {}).open && canOperate", self.script)

    def test_status_is_shown_to_everyone_in_red(self):
        self.assertIn("rc-shift--closed", self.script)
        self.assertIn('__("Kassa yopiq")', self.script)

    def test_status_tells_who_may_open_the_register(self):
        self.assertIn("shift_operators", self.script)

    def test_close_button_is_hidden_for_unauthorized_user(self):
        self.assertIn("isOpen && canOperate", self.script)
