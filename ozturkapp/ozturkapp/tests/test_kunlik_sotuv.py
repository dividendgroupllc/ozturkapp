# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""«Kunlik sotuv» hisoboti sotuv dashboardi bilan bir xil raqam berishi kerak."""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, flt, today

from ozturkapp.ozturkapp.api.sales_dashboard import get_dashboard
from ozturkapp.ozturkapp.report.kunlik_sotuv import kunlik_sotuv as report


class TestKunlikSotuv(FrappeTestCase):
    def test_totals_match_sales_dashboard(self):
        frappe.set_user("Administrator")
        filters = dict(from_date=add_days(today(), -90), to_date=today())
        columns, data, _msg, _chart, _summary = report.execute(filters)
        dash = get_dashboard(**filters)
        total = lambda field: sum(flt(r.get(field)) for r in data)  # noqa: E731

        self.assertAlmostEqual(total("revenue"), dash["checks"]["revenue"], places=2)
        self.assertAlmostEqual(total("discount"), dash["checks"]["discount"], places=2)

        labels = {c["fieldname"]: c["label"].split(":")[0] for c in columns}
        paid = {p["mode"]: p["amount"] for p in dash["payments"]}
        disc = {m["mode"]: m["discount"] for m in dash["discounts"]["by_mode"]}
        for field, mode in labels.items():
            if field.startswith("pay_"):
                self.assertAlmostEqual(total(field), paid.get(mode, 0), places=2, msg=mode)
            elif field.startswith("disc_"):
                self.assertAlmostEqual(total(field), disc.get(mode, 0), places=2, msg=mode)

        # Har bir qator — bitta chek: to'lovlar = tushum; chegirma ulushlari = chek chegirmasi.
        self.assertEqual(len({r["invoice"] for r in data}), len(data))
        for row in data:
            pays = sum(flt(v) for k, v in row.items() if k.startswith("pay_"))
            discs = sum(flt(v) for k, v in row.items() if k.startswith("disc_"))
            self.assertAlmostEqual(pays, row["revenue"], places=2, msg=row["invoice"])
            self.assertAlmostEqual(discs, row["discount"], places=2, msg=row["invoice"])
            self.assertAlmostEqual(row["gross"] - row["discount"], row["net"], places=2, msg=row["invoice"])

    def test_summary_cards_order(self):
        frappe.set_user("Administrator")
        _c, _d, _m, _ch, summary = report.execute(dict(from_date=add_days(today(), -30), to_date=today()))
        self.assertEqual([s["label"] for s in summary],
                         ["Yalpi sotuv", "Chegirma", "Tushum", "Xizmat haqi", "Cheklar soni"])

    def test_requires_dates(self):
        self.assertRaises(frappe.ValidationError, report.execute, {})
