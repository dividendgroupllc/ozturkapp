# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa yopilganda Telegram xabari: summalar, format, yuborish va xatolar."""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from ozturkapp.ozturkapp.utils import kassa_telegram as kt

TYPES = {"Нахт Davron": "Cash", "Kassa Oybek": "Cash"}


def row(mode, expected, closing=None, opening=0):
    return frappe._dict(mode_of_payment=mode, opening_amount=opening, expected_amount=expected,
                        closing_amount=expected if closing is None else closing)


def closing(rows):
    return frappe._dict(name="POS-CLO-TEST", user="Administrator", period_end_date="2026-10-02 23:10:00",
                        posting_date="2026-10-02", payment_reconciliation=rows)


def fake_type(doctype, name, field):
    return TYPES.get(name, "Bank")


class TestKassaTelegram(FrappeTestCase):
    # POS-CLO-2026-00003 (production) summalari
    ROWS = [
        row("Нахт Davron", 15490466, closing=262000),
        row("Paymee", 2061400),
        row("Click", 1772120),
        row("Terminal HUMO", 78360),
        row("Terminal UZCARD", 1477680),
    ]

    def message(self, rows):
        with patch.object(kt.frappe.db, "get_value", side_effect=fake_type):
            return kt.build_message(closing(rows))

    def test_money_format(self):
        self.assertEqual(kt.money(15490466), "15 490 466")
        self.assertEqual(kt.money(0), "0")
        self.assertEqual(kt.money(999.6), "1 000")

    def test_message_format(self):
        lines = self.message(self.ROWS).split("\n")
        self.assertEqual(lines[2:], [
            "Paymee - 2 061 400",
            "Click - 1 772 120",
            "Terminal Uzcard - 1 477 680",
            "Terminal Humo - 78 360",
            "",
            "Bank Jami: 5 389 560",
            "",
            "Naxt  savdo - 15 490 466",
            "Naxt  yopildi - 262 000",
            "",
            "Jami Sotuv: 20 880 026",
        ])
        self.assertIn("POS-CLO-TEST", lines[0])

    def test_opening_subtracted_and_missing_modes_zero(self):
        t = None
        with patch.object(kt.frappe.db, "get_value", side_effect=fake_type):
            t = kt.closing_totals(closing([row("Нахт Davron", 600, closing=550, opening=100),
                                           row("Click", 300, opening=50)]))
        self.assertEqual(dict(t["banks"]), {"Paymee": 0, "Click": 250, "Terminal Uzcard": 0, "Terminal Humo": 0})
        self.assertEqual((t["bank_total"], t["cash_sales"], t["cash_counted"], t["total"]), (250, 500, 550, 750))

    def test_unknown_bank_mode_appended_and_counted(self):
        with patch.object(kt.frappe.db, "get_value", side_effect=fake_type):
            t = kt.closing_totals(closing([row("Uzum", 1000), row("Paymee", 500)]))
        self.assertEqual(t["banks"][-1], ("Uzum", 1000))
        self.assertEqual(t["bank_total"], 1500)

    def test_send_message_posts_to_telegram(self):
        response = MagicMock(content=b"x", status_code=200)
        response.json.return_value = {"ok": True}
        with patch.object(kt, "_config", return_value=(None, "123:ABC", "-1001")), \
                patch.object(kt.requests, "post", return_value=response) as post:
            kt.send_message("salom")
        url = post.call_args.args[0]
        self.assertEqual(url, "https://api.telegram.org/bot123:ABC/sendMessage")
        self.assertEqual(post.call_args.kwargs["json"], {"chat_id": "-1001", "text": "salom"})

    def test_send_message_raises_on_telegram_error(self):
        response = MagicMock(content=b"x", status_code=400)
        response.json.return_value = {"ok": False, "description": "Bad Request: chat not found"}
        with patch.object(kt, "_config", return_value=(None, "123:ABC", "-1001")), \
                patch.object(kt.requests, "post", return_value=response):
            with self.assertRaisesRegex(frappe.ValidationError, "chat not found"):
                kt.send_message("salom")

    def test_notify_closing_never_raises(self):
        with patch.object(kt.frappe, "get_doc", return_value=closing(self.ROWS)), \
                patch.object(kt, "build_message", return_value="x"), \
                patch.object(kt, "send_message", side_effect=frappe.ValidationError("down")), \
                patch.object(kt.frappe, "log_error") as log:
            kt.notify_closing("POS-CLO-TEST")
        log.assert_called_once()

    def test_enqueue_only_when_enabled(self):
        doc = frappe._dict(name="POS-CLO-TEST")
        off = frappe._dict(enabled=0)
        on = frappe._dict(enabled=1)
        with patch.object(kt.frappe, "enqueue") as enq:
            with patch.object(kt, "_config", return_value=(off, "t", "c")):
                kt.enqueue_closing(doc)
            with patch.object(kt, "_config", return_value=(on, "", "c")):
                kt.enqueue_closing(doc)
            enq.assert_not_called()
            with patch.object(kt, "_config", return_value=(on, "t", "c")):
                kt.enqueue_closing(doc)
        enq.assert_called_once()
        self.assertTrue(enq.call_args.kwargs["enqueue_after_commit"])
        self.assertEqual(enq.call_args.kwargs["closing_entry"], "POS-CLO-TEST")

    def test_settings_page_roundtrip(self):
        settings = frappe.get_doc("Kassa Telegram")
        settings.update({"enabled": 1, "bot_token": "123:SECRET", "chat_id": "-100777"})
        settings.save(ignore_permissions=True)
        cfg, token, chat_id = kt._config()
        self.assertEqual((cfg.enabled, token, chat_id), (1, "123:SECRET", "-100777"))
        settings.enabled = 1
        settings.chat_id = ""
        self.assertRaises(frappe.ValidationError, settings.save, ignore_permissions=True)
        frappe.db.rollback()
