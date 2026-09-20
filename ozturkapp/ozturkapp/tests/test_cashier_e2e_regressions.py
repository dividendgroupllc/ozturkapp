# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""E2E (haqiqiy interfeys + haqiqiy backend) sinovlari topgan nosozliklarning regressiya testlari.

Ishga tushirish::

    cd /home/sherzod/frappe-bench && flock /tmp/ozturk_tests.lock \
        bench --site ozturk.local run-tests \
        --module ozturkapp.ozturkapp.tests.test_cashier_e2e_regressions

E2E harnessi: `tests/e2e/README.md`. Har bir test o'zi topilgan senariy nomini aytadi.
"""

import json
import os
import subprocess
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from ozturkapp.ozturkapp.api import cash_movements
from ozturkapp.ozturkapp.utils import cashier_permissions, manager_approval, order_items


class TestSyncConflictMessage(FrappeTestCase):
    """E2E `q`: kassir eskirgan `last_modified_time` bilan yuboradi.

    URY `sync_order` konfliktda O'ZINING inglizcha `msgprint`ini qo'shadi. Xato oynasida
    kassir ikki qator ko'rardi: inglizcha "Please reload the page..." va o'zbekcha xabar.
    """

    URY_TEXT = "This order has been modified. Please reload the page to retrieve the latest edits."

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.local.message_log = []

    def tearDown(self):
        frappe.local.message_log = []

    def _conflicting_sync_order(self, **kwargs):
        frappe.msgprint(self.URY_TEXT, title="Order has been modified", indicator="red")
        return {"status": "Failure"}

    def test_conflict_reports_only_the_uzbek_message(self):
        scope = cashier_permissions.resolve_scope()

        with mock.patch(
            "ozturkapp.ozturkapp.overrides.ury_order.sync_order", self._conflicting_sync_order
        ):
            with self.assertRaises(frappe.ValidationError):
                order_items.run_sync_order(
                    scope,
                    items=[{"item": "X", "qty": 1}],
                    customer=scope.default_customer,
                    pax=1,
                    comments=None,
                    table=None,
                    room=None,
                    existing="INV-E2E",
                    last_modified_time="2020-01-01 00:00:00",
                )

        texts = [
            (entry.get("message") if isinstance(entry, dict) else str(entry))
            for entry in frappe.local.message_log
        ]
        self.assertTrue(any("Buyurtma o'zgargan" in text for text in texts), texts)
        self.assertFalse(any("This order has been modified" in text for text in texts), texts)


class TestTaxLabel(FrappeTestCase):
    """E2E `b`: panelda «Xizmat haqi 12% (12%)» — server tavsifi foizni o'zi yozadi.

    Kassir ikki marta hisoblangan deb o'ylardi. `taxLabel()` foizni faqat tavsifda yo'q bo'lsa qo'shadi.
    """

    ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "public", "js", "cashier")

    def _read(self, relative):
        with open(os.path.join(self.ROOT, relative), encoding="utf-8") as handle:
            return handle.read()

    def test_helper_does_not_repeat_the_percent(self):
        script = (
            "import { taxLabel } from './util/format.js';"
            "process.stdout.write(JSON.stringify(["
            "taxLabel({description: 'Xizmat haqi 12%', rate: 12}),"
            "taxLabel({description: 'Xizmat haqi', rate: 12}),"
            "taxLabel({description: 'Choychaqa', rate: 0}),"
            "taxLabel({description: 'Xizmat haqi 12.5%', rate: 12.5})]))"
        )
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=self.ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            ["Xizmat haqi 12%", "Xizmat haqi (12%)", "Choychaqa", "Xizmat haqi 12.5%"],
        )

    def test_panel_and_history_use_the_helper(self):
        for relative in ("ui/panel.js", "ui/history.js"):
            source = self._read(relative)
            self.assertIn("taxLabel(tax)", source, relative)
            self.assertNotIn("tax.rate ? ` (", source, relative)


class TestMoneyFormatInMessages(FrappeTestCase):
    """E2E `i`: PIN oynasi «Kassadan chiqarish лв 150,000.00» ko'rsatdi.

    `utils/money.py` aynan shu formatni («лв», vergul, `.00`) taqiqlaydi: ekranning o'zi
    «150 000» deb yozadi. Kassir foydalanadigan server xabarlari ham shu qoidaga amal qiladi.
    """

    CASHIER = "regress-money-cashier@example.com"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        if not frappe.db.exists("User", cls.CASHIER):
            frappe.get_doc(
                {
                    "doctype": "User",
                    "email": cls.CASHIER,
                    "first_name": "regress",
                    "send_welcome_email": 0,
                    "roles": [{"role": "URY Cashier"}],
                }
            ).insert(ignore_permissions=True)

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.message_log = []

    def test_payout_approval_message_uses_the_app_money_format(self):
        frappe.set_user(self.CASHIER)
        frappe.local.message_log = []
        scope = frappe._dict(pos_profile=cashier_permissions.resolve_scope("Administrator").pos_profile)
        movement = frappe._dict(kind=cash_movements.KIND_OUT, amount=1250000, category="x", reason="y")

        with mock.patch.object(
            cash_movements.cashier_features,
            "get_settings",
            return_value={"cash_payout_approval_limit": 100000},
        ):
            with self.assertRaises(manager_approval.ApprovalRequired):
                cash_movements._approve(movement, scope, "SHIFT", None)

        text = " ".join(str(entry.get("message")) for entry in frappe.local.message_log)
        self.assertIn("1 250 000", text)
        self.assertNotIn("лв", text)
        self.assertNotIn("1,250,000", text)

    def test_billing_messages_do_not_use_frappe_currency_formatting(self):
        import inspect

        from ozturkapp.ozturkapp.api import billing

        self.assertNotIn("format_value", inspect.getsource(billing))
        self.assertNotIn("format_value", inspect.getsource(cash_movements))
