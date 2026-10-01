# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""«Kassa qoldiqlari» bloki: server qoldiqlari va workspace sozlamasi."""

import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, today

from ozturkapp.ozturkapp.api import cash_balances
from ozturkapp.ozturkapp.setup import finance_workspace_setup as fws


class TestCashBalances(FrappeTestCase):
    def setUp(self):
        frappe.set_user("Administrator")

    def test_balances_match_general_ledger(self):
        data = cash_balances.get_cash_balances(date=today())
        self.assertTrue(data["groups"], "to'lov turlari topilmadi")
        for group in data["groups"]:
            self.assertAlmostEqual(group["total"], sum(r["balance"] for r in group["rows"]), places=2)
            for row in group["rows"]:
                gl = frappe.db.sql(
                    """select ifnull(sum(debit) - sum(credit), 0) from `tabGL Entry`
                    where account=%s and is_cancelled=0 and posting_date <= %s""",
                    (row["account"], data["date"]),
                )[0][0]
                self.assertAlmostEqual(row["balance"], flt(gl), places=2, msg=row["mode_of_payment"])
        self.assertAlmostEqual(data["total"], sum(g["total"] for g in data["groups"]), places=2)
        # Naqd kassalar birinchi guruh
        self.assertEqual(data["groups"][0]["type"], "Cash")

    def test_requires_accounts_role(self):
        user = "_test_no_accounts@example.com"
        if not frappe.db.exists("User", user):
            frappe.get_doc({"doctype": "User", "email": user, "first_name": "NoAcc",
                            "send_welcome_email": 0}).insert(ignore_permissions=True)
        frappe.set_user(user)
        try:
            self.assertRaises(frappe.PermissionError, cash_balances.get_cash_balances)
        finally:
            frappe.set_user("Administrator")

    def test_setup_adds_block_once(self):
        name = "_Test Finance WS"
        if not frappe.db.exists("Workspace", name):
            frappe.get_doc({"doctype": "Workspace", "label": name, "title": name, "public": 1,
                            "content": json.dumps([{"id": "h1", "type": "header",
                                                    "data": {"text": name, "col": 12}}])}).insert(ignore_permissions=True)
        fws.ensure_block()
        self.assertTrue(fws.add_to_workspace(name))
        self.assertFalse(fws.add_to_workspace(name))      # ikkinchi marta qo'shilmaydi
        ws = frappe.get_doc("Workspace", name)
        blocks = [b for b in json.loads(ws.content) if b.get("type") == "custom_block"]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["data"]["custom_block_name"], fws.BLOCK)
        block = frappe.get_doc("Custom HTML Block", fws.BLOCK)
        self.assertIn("get_cash_balances", block.script)
        self.assertTrue({r.role for r in block.roles} & set(fws.ROLES))
