# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa yopilganda Telegram guruhga kassa qoldig'i.

Sozlamalar — «Kassa Telegram» sahifasi (bot token, guruh ID). POS Closing
Entry submit bo'lganda (`overrides/pos_closing_entry`) xabar FON vazifasida,
tranzaksiya commit bo'lgandan keyin yuboriladi: Telegram ishlamasa ham kassa
yopilishi to'xtamaydi, xato «Error Log» ga yoziladi.

Summalar smena bo'yicha (`payment_reconciliation`, ochilish summasi ayirilgan):

    Bank turlari      = kutilgan − ochilish  (Paymee, Click, Terminal Uzcard, Terminal Humo, ...)
    Naxt savdo        = naqd kutilgan − ochilish
    Naxt yopildi      = kassir sanab kiritgan naqd (closing_amount)
    Jami Sotuv        = Bank Jami + Naxt savdo
"""

import frappe
import requests
from frappe import _
from frappe.utils import flt, format_datetime

SETTINGS = "Kassa Telegram"
TIMEOUT = 15

#: Bank turlari xabarda shu tartibda va shu nomlar bilan; boshqalari oxiridan.
BANK_ORDER = (
    ("paymee", "Paymee"),
    ("click", "Click"),
    ("terminal uzcard", "Terminal Uzcard"),
    ("terminal humo", "Terminal Humo"),
)


def money(value) -> str:
    """12345678.4 -> «12 345 678»."""
    return f"{round(flt(value)):,}".replace(",", " ")


def closing_totals(closing) -> dict:
    """Kassa yopilishidan bank turlari, naqd savdo va sanalgan naqd."""
    known = {key: 0.0 for key, _label in BANK_ORDER}
    others, cash_sales, cash_counted = {}, 0.0, 0.0
    for row in closing.get("payment_reconciliation") or []:
        sold = flt(row.expected_amount) - flt(row.opening_amount)
        if frappe.db.get_value("Mode of Payment", row.mode_of_payment, "type") == "Cash":
            cash_sales += sold
            cash_counted += flt(row.closing_amount)
            continue
        key = (row.mode_of_payment or "").strip().lower()
        if key in known:
            known[key] += sold
        else:
            others[row.mode_of_payment] = others.get(row.mode_of_payment, 0.0) + sold

    banks = [(label, known[key]) for key, label in BANK_ORDER]
    banks += list(others.items())
    bank_total = sum(amount for _label, amount in banks)
    return {
        "banks": banks,
        "bank_total": bank_total,
        "cash_sales": cash_sales,
        "cash_counted": cash_counted,
        "total": bank_total + cash_sales,
    }


def build_message(closing) -> str:
    t = closing_totals(closing)
    cashier = frappe.db.get_value("User", closing.user, "full_name") or closing.user
    period = format_datetime(closing.period_end_date or closing.posting_date, "dd.MM.yyyy HH:mm")
    lines = [f"Kassa yopildi: {period} ({cashier}, {closing.name})", ""]
    lines += [f"{label} - {money(amount)}" for label, amount in t["banks"]]
    lines += [
        "",
        f"Bank Jami: {money(t['bank_total'])}",
        "",
        f"Naxt  savdo - {money(t['cash_sales'])}",
        f"Naxt  yopildi - {money(t['cash_counted'])}",
        "",
        f"Jami Sotuv: {money(t['total'])}",
    ]
    return "\n".join(lines)


def _config():
    settings = frappe.get_cached_doc(SETTINGS)
    token = settings.get_password("bot_token", raise_exception=False) if settings.bot_token else None
    return settings, (token or "").strip(), (settings.chat_id or "").strip()


def send_message(text: str, raise_errors: bool = False):
    """Guruhga xabar. `raise_errors` — sinov tugmasi uchun (xato foydalanuvchiga)."""
    _settings, token, chat_id = _config()
    if not (token and chat_id):
        frappe.throw(_("Kassa Telegram: Bot token va Guruh ID kiritilmagan"))
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=TIMEOUT,
        )
        body = response.json() if response.content else {}
    except (requests.RequestException, ValueError) as exc:
        frappe.throw(_("Telegram bilan aloqa yo'q: {0}").format(exc))
    if not body.get("ok"):
        frappe.throw(_("Telegram xatosi: {0}").format(body.get("description") or response.status_code))
    return body


def notify_closing(closing_entry: str):
    """Fon vazifasi: kassa yopilishi xabari. Xato kassa yopilishiga ta'sir qilmaydi."""
    try:
        send_message(build_message(frappe.get_doc("POS Closing Entry", closing_entry)))
    except Exception:
        frappe.log_error(title=f"Kassa Telegram: {closing_entry}")


def enqueue_closing(closing):
    """POS Closing Entry submit'idan chaqiriladi — sozlama yoqilgan bo'lsa."""
    settings, token, chat_id = _config()
    if not (settings.enabled and token and chat_id):
        return
    frappe.enqueue(
        notify_closing,
        queue="short",
        enqueue_after_commit=True,
        job_id=f"kassa_telegram::{closing.name}",
        deduplicate=True,
        now=frappe.flags.in_test,
        closing_entry=closing.name,
    )
