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
            "Расход - 0",
            "",
            "Jami Sotuv: 20 880 026",
        ])
        self.assertIn("POS-CLO-TEST", lines[0])

    def test_expense_shown_separately_from_balance(self):
        doc = closing(self.ROWS)
        doc["custom_cash_expense"] = 100000
        with patch.object(kt.frappe.db, "get_value", side_effect=fake_type):
            text = kt.build_message(doc)
        self.assertIn("Naxt  yopildi - 162 000", text)
        self.assertIn("Расход - 100 000", text)
        self.assertIn("Naxt  savdo - 15 490 466", text)

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
                patch("ozturkapp.ozturkapp.utils.kassa_report.build_report_pdf", return_value=None), \
                patch.object(kt, "send_message", side_effect=frappe.ValidationError("down")), \
                patch.object(kt.frappe, "log_error") as log:
            kt.notify_closing("POS-CLO-TEST")
        log.assert_called_once()

    def test_notify_closing_sends_pdf_with_summary_caption(self):
        with patch.object(kt.frappe, "get_doc", return_value=closing(self.ROWS)), \
                patch.object(kt, "build_message", return_value="xulosa"), \
                patch("ozturkapp.ozturkapp.utils.kassa_report.build_report_pdf", return_value=b"%PDF"), \
                patch.object(kt, "send_document") as doc, \
                patch.object(kt, "send_message") as msg:
            kt.notify_closing("POS-CLO-TEST")
        doc.assert_called_once_with(b"%PDF", "Kassa_POS-CLO-TEST.pdf", caption="xulosa")
        msg.assert_not_called()

    def test_notify_closing_falls_back_to_text_when_pdf_fails(self):
        with patch.object(kt.frappe, "get_doc", return_value=closing(self.ROWS)), \
                patch.object(kt, "build_message", return_value="xulosa"), \
                patch("ozturkapp.ozturkapp.utils.kassa_report.build_report_pdf", side_effect=RuntimeError("wk")), \
                patch.object(kt, "send_document") as doc, \
                patch.object(kt, "send_message") as msg, \
                patch.object(kt.frappe, "log_error") as log:
            kt.notify_closing("POS-CLO-TEST")
        msg.assert_called_once_with("xulosa")
        doc.assert_not_called()
        log.assert_called_once()

    def test_meat_kind_and_kg(self):
        from ozturkapp.ozturkapp.utils.kassa_report import item_kg, meat_kind

        self.assertEqual(meat_kind("ISKANDAR KABOB Mol go'shti 160 gr"), "beef")
        self.assertEqual(meat_kind("TOMBIK DONAR Tovuq go'shti 80 gr"), "chicken")
        self.assertEqual(meat_kind("PORTION DÖNER CHICKEN"), "chicken")
        self.assertEqual(meat_kind("PORTION DÖNER"), "beef")
        self.assertEqual(meat_kind("CLOSED PIDE"), "other")
        self.assertAlmostEqual(item_kg("DURUM Tovuqli 120gr"), 0.12)
        self.assertAlmostEqual(item_kg("GO'SHT Mol go'shti 0.5 kg"), 0.5)
        self.assertEqual(item_kg("LAHMAJUN"), 0)

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

    def test_report_pdf_renders(self):
        from datetime import date

        from ozturkapp.ozturkapp.utils.kassa_report import render_pdf

        table = {
            "columns": [{"mode": "A", "label": "Нахт"}, {"mode": "B", "label": "Click"}],
            "opening": [1000.0, 0.0],
            "kirim": [{"label": "Savdo", "cells": [500.0, 250.0]}],
            "chiqim": [{"label": "Postavshik", "cells": [200.0, 0.0]}],
            "closing": [1300.0, 250.0],
        }
        kinds = {k: {"amount": 1.0, "kg": 0.5, "share": 33.3} for k in ("beef", "chicken", "other")}
        stats = {"total": 750.0, "checks": 2, "average": 375.0, "discount": 0, "returns_count": 0,
                 "returns_amount": 0, "kinds": kinds}
        pdf = render_pdf(date(2026, 10, 3), table, stats, {"footer": "x"})
        self.assertTrue(pdf.startswith(b"%PDF"))

