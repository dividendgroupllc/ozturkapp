# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Smena cheklari KUN bo'yicha alohida Sales Invoice'larga konsolidatsiya qilinadi."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime, getdate

from ozturkapp.ozturkapp.overrides import pos_closing_entry as ov


def ref(name):
    return frappe._dict(pos_invoice=name)


class TestPOSClosingByDay(FrappeTestCase):
    STAMPS = {
        "INV-A": ("2026-09-21", "21:00:00"),
        "INV-B": ("2026-09-20", "13:00:00"),
        "INV-C": ("2026-09-21", "09:00:00"),
        "INV-D": ("2026-09-20", "10:00:00"),
    }

    def stamp(self, row):
        d, t = self.STAMPS[row.pos_invoice]
        return getdate(d), get_datetime(f"{d} {t}")

    def test_groups_by_day_in_chronological_order(self):
        rows = [ref(n) for n in self.STAMPS]
        # ERPNext xaritasi: bitta mijoz, bitta o'lchov guruhi — tartib saqlanadi.
        fake_map = lambda invoices: {"Mijoz": {"dim": list(invoices)}}  # noqa: E731
        with patch.object(ov, "_posted_at", side_effect=self.stamp), \
                patch.object(ov.merge, "get_invoice_customer_map", side_effect=fake_map):
            result = ov.invoice_map_by_day(rows)

        groups = result["Mijoz"]
        self.assertEqual(list(groups), ["dim|2026-09-20", "dim|2026-09-21"])
        self.assertEqual([r.pos_invoice for r in groups["dim|2026-09-20"]], ["INV-D", "INV-B"])
        self.assertEqual([r.pos_invoice for r in groups["dim|2026-09-21"]], ["INV-C", "INV-A"])

    def test_dimension_groups_are_kept_separate(self):
        rows = [ref(n) for n in self.STAMPS]
        fake_map = lambda invoices: {"Mijoz": {  # noqa: E731
            "dimA": [r for r in invoices if r.pos_invoice in ("INV-A", "INV-B")],
            "dimB": [r for r in invoices if r.pos_invoice in ("INV-C", "INV-D")],
        }}
        with patch.object(ov, "_posted_at", side_effect=self.stamp), \
                patch.object(ov.merge, "get_invoice_customer_map", side_effect=fake_map):
            groups = ov.invoice_map_by_day(rows)["Mijoz"]
        self.assertEqual(sorted(groups), ["dimA|2026-09-20", "dimA|2026-09-21", "dimB|2026-09-20", "dimB|2026-09-21"])
        self.assertEqual(sum(len(v) for v in groups.values()), 4)

    def test_closing_entry_class_is_overridden(self):
        self.assertIs(frappe.get_doc({"doctype": "POS Closing Entry"}).__class__, ov.OzturkPOSClosingEntry)

    def test_large_closing_is_queued_with_day_map(self):
        closing = frappe._dict(pos_transactions=[ref(f"INV-{i}") for i in range(12)])
        closing.set_status = lambda **kw: None
        with patch.object(ov, "invoice_map_by_day", return_value={"M": {}}) as by_day, \
                patch.object(ov.merge, "enqueue_job") as enqueue, \
                patch.object(ov.merge, "create_merge_logs") as direct:
            ov.consolidate_by_day(closing)
        by_day.assert_called_once()
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.kwargs["invoice_by_customer"], {"M": {}})
        direct.assert_not_called()
