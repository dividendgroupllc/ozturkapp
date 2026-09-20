# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Brauzersiz tekshiruv: shim + haqiqiy whitelisted funksiyalar (`--selftest`).

Haydovchi (Node) ishlatadigan yo'lning aynan o'zi: shim `api_request` orqali form-kodlangan
POST. Bu yerda faqat asosiy zanjir sinaladi — batafsil senariylar `driver/` da.
"""

import json
from urllib.parse import urlencode

from ozturkapp.ozturkapp.tests.e2e import fixtures

API = "/api/method/ozturkapp.ozturkapp.api."


def call(shim, method, http="POST", **args):
    body = urlencode({k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in args.items()})
    headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "Accept": "application/json",
               "X-Requested-With": "XMLHttpRequest"}
    url = f"http://127.0.0.1:8000{API}{method}"
    if http == "GET":
        url += "?" + body
        body = None
    reply = shim.api_request(http, url, headers, body)
    data = json.loads(reply["body"])
    return reply["status"], data


def run(shim, hold: int = 0):
    checks = []

    def check(name, condition, detail=""):
        checks.append({"name": name, "ok": bool(condition), "detail": detail})
        print(f"  [{'OK' if condition else 'XATO'}] {name} {detail}")

    shim.control("actor", {"user": fixtures.CASHIER})
    status, data = call(shim, "cashier.get_cashier_context", http="GET")
    ctx = data.get("message") or {}
    check("context (GET)", status == 200 and ctx, f"status={status} {data.get('exc_type', '')}")
    check("shift closed at start", not (ctx.get("shift") or {}).get("open"))
    check("all features on", all((ctx.get("features") or {}).get(k) for k in ("split_payment", "discount", "refunds", "tips")),
          json.dumps(ctx.get("features")))

    # Frappe GET so'rovini rollback qiladi: `open_shift` GET ni qabul qilsa ham smena ochilmaydi.
    rows = [{"mode_of_payment": mode, "opening_amount": 1000} for mode in ctx.get("cash_modes") or []]
    status, data = call(shim, "cashier.open_shift", http="GET", balance_details=rows)
    check("GET open_shift so'rovni qabul qildi (methods=['POST'] cheklovi yo'q)", status == 200, f"status={status}")
    status2, data2 = call(shim, "cashier.get_cashier_context", http="GET")
    check("GET orqali open_shift smenani ochmaydi (so'rov rollback qilinadi)",
          not ((data2.get("message") or {}).get("shift") or {}).get("open"), f"status={status}")

    _guard_checks(shim, check)

    if hold:
        # `verify_guarantee.py`: jarayonni ochiq tranzaksiyada (yozuvlar bilan) ushlab turadi —
        # tashqaridan SIGINT/SIGTERM/SIGKILL yuboriladi va bazaning o'zgarmasligi tekshiriladi.
        import sys
        import time

        print("E2E_HOLDING", flush=True)
        sys.stdout.flush()
        time.sleep(hold)

    return {"ok": all(c["ok"] for c in checks), "checks": checks}


def _guard_checks(shim, check):
    """Himoya: DDL/COMMIT rad etiladi, virtual commit haqiqiy COMMIT emas."""
    import frappe

    from ozturkapp.ozturkapp.tests.e2e.guard import ForbiddenStatement

    def attempt(statement):
        shim._fresh_context("Administrator")
        try:
            frappe.db.sql(statement)
        except ForbiddenStatement:
            return True
        except Exception:  # noqa: BLE001
            return False
        return False

    for statement in (
        "create table `e2e_should_not_exist` (a int)",
        "alter table `tabSeries` add column `e2e_x` int",
        "drop table if exists `e2e_should_not_exist`",
        "truncate table `tabSeries`",
        "commit",
        "start transaction",
        "begin",
        "set autocommit = 1",
        "rollback",
        "lock tables `tabSeries` write",
        "/* x */ COMMIT",
    ):
        check(f"rad etildi: {statement[:40]}", attempt(statement))

    from ozturkapp.ozturkapp.tests.e2e.guard import fingerprint_connection

    shim._fresh_context("Administrator")
    profile = frappe.db.get_value("POS Profile", {}, "name")
    outside = fingerprint_connection()
    try:
        cursor = outside.cursor()
        cursor.execute("select custom_kot_alert from `tabPOS Profile` where name = %s", (profile,))
        committed = cursor.fetchone()[0]

        frappe.db.set_value("POS Profile", profile, "custom_kot_alert", 0 if committed else 1)
        frappe.db.commit()
        check("ichkarida o'zgarish ko'rinadi", frappe.db.get_value("POS Profile", profile, "custom_kot_alert") != committed)

        cursor.execute("select custom_kot_alert from `tabPOS Profile` where name = %s", (profile,))
        check("frappe.db.commit() haqiqiy COMMIT emas: mustaqil ulanish eski qiymatni ko'radi",
              cursor.fetchone()[0] == committed)
    finally:
        outside.close()
