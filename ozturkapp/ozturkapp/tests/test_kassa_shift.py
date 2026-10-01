# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Operator Kassa harakati g'aladon naqdidan bo'lsa — smenaga o'zi bog'lanadi (utils/kassa_shift.py)."""

import frappe
from frappe.utils import flt, nowdate

from ozturkapp.ozturkapp.tests.test_cashier_shift import ShiftTestCase
from ozturkapp.ozturkapp.utils import kassa_shift, pos_closing


class TestKassaShift(ShiftTestCase):
    def only_shift(self):
        """Sinov smenasidan boshqa ochiq smenalar (dev bazadagi qoldiq) bu test ichida yopilgan."""
        opening = self.open_shift().name
        frappe.db.sql(
            "update `tabPOS Opening Entry` set status='Closed' where status='Open' and name != %s", opening
        )
        return opening

    def expense_account(self):
        return frappe.db.get_value(
            "Account", {"company": self.company, "root_type": "Expense", "is_group": 0,
                        "account_type": ["in", ["", None]]}, "name")

    def kassa(self, oborot="Расход", summa=50000, mode=None):
        doc = frappe.get_doc({
            "doctype": "Kassa", "date": nowdate(), "oborot": oborot, "company": self.company,
            "source_account": mode or self.cash_mode, "party_type": "Расходы",
            "expense_kontragent": self.expense_account(), "summa": summa,
            "primechaniya": "test",
        })
        doc.insert(ignore_permissions=True)
        doc.submit()
        return doc

    def expected_cash(self, opening):
        entry = pos_closing.make_closing_entry_from_opening(frappe.get_doc("POS Opening Entry", opening))
        return sum(flt(r.expected_amount) for r in entry.payment_reconciliation if r.mode_of_payment == self.cash_mode)

    def test_expense_during_open_shift_reduces_expected_cash(self):
        opening = self.only_shift()
        before = self.expected_cash(opening)
        doc = self.kassa(summa=50000)
        doc.reload()
        self.assertEqual(doc.pos_opening_entry, opening)
        self.assertFalse(doc.pos_shift_after_close)
        self.assertAlmostEqual(self.expected_cash(opening), before - 50000, places=2)

        # Bekor qilingan Kassa hisobdan chiqadi.
        doc.cancel()
        self.assertAlmostEqual(self.expected_cash(opening), before, places=2)

    def test_income_during_open_shift_increases_expected_cash(self):
        opening = self.only_shift()
        before = self.expected_cash(opening)
        self.kassa(oborot="Приход", summa=30000)
        self.assertAlmostEqual(self.expected_cash(opening), before + 30000, places=2)

    def test_non_drawer_mode_is_not_linked(self):
        self.open_shift()
        other = frappe.db.get_value(
            "Mode of Payment Account",
            {"company": self.company, "parent": ["not in", list(kassa_shift.drawer_modes())]}, "parent")
        if not other:
            self.skipTest("g'aladondan tashqari to'lov turi yo'q")
        doc = frappe._dict(oborot="Расход", summa=1000, source_account=other)
        drawer = {m for m in kassa_shift.effects(doc) if m in kassa_shift.drawer_modes()}
        self.assertFalse(drawer)

    def test_after_close_expense_covers_oldest_shortage_fifo(self):
        """Yopilgan smenalarning kamomadi FIFO bo'yicha (eng eskisi birinchi) yopiladi."""
        from unittest.mock import patch
        # Ochiq smena yo'q, ikkita yopilgan smena: eskisida -40 000, yangisida -20 000 kamomad.
        diffs = {"OLD": -40000.0, "NEW": -20000.0}
        closed = [frappe._dict(name="OLD"), frappe._dict(name="NEW")]
        doc = frappe._dict(oborot="Расход", summa=40000, source_account=self.cash_mode,
                           transfer_source_display=None, target_account=None)
        saved = {}
        doc.db_set = lambda values: saved.update(values)
        real_get_value = frappe.db.get_value

        def get_value(doctype, filters=None, fieldname="name", *args, **kwargs):
            if doctype == "POS Opening Entry" and isinstance(filters, dict) and filters.get("status") == "Open":
                return None
            return real_get_value(doctype, filters, fieldname, *args, **kwargs)

        with patch.object(frappe.db, "get_value", side_effect=get_value), \
                patch.object(frappe, "get_all", side_effect=lambda dt, **kw: closed if dt == "POS Opening Entry"
                             else frappe.get_list(dt, ignore_permissions=True, **kw)), \
                patch.object(kassa_shift, "_closed_cash_difference", side_effect=lambda op, mode: diffs[op]), \
                patch.object(kassa_shift, "drawer_modes", return_value={self.cash_mode}), \
                patch.object(kassa_shift, "_profiles_for", return_value=[self.profile]):
            kassa_shift.link(doc)
        self.assertEqual(saved, {"pos_opening_entry": "OLD", "pos_shift_after_close": 1})

        # Eski smena qoplangan bo'lsa — keyingisiga o'tadi.
        diffs["OLD"] = 0.0
        saved.clear()
        with patch.object(frappe.db, "get_value", side_effect=get_value), \
                patch.object(frappe, "get_all", side_effect=lambda dt, **kw: closed if dt == "POS Opening Entry"
                             else frappe.get_list(dt, ignore_permissions=True, **kw)), \
                patch.object(kassa_shift, "_closed_cash_difference", side_effect=lambda op, mode: diffs[op]), \
                patch.object(kassa_shift, "drawer_modes", return_value={self.cash_mode}), \
                patch.object(kassa_shift, "_profiles_for", return_value=[self.profile]):
            kassa_shift.link(doc)
        self.assertEqual(saved.get("pos_opening_entry"), "NEW")
