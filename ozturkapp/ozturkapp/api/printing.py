# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa/oshxona sahifalari uchun chop etish API'si.

    print_bill(invoice)   -> mijoz chekini kassa printeriga navbatga qo'yadi
    reprint(job)          -> mavjud topshiriqni qayta chop etish
    test_print(printer)   -> sinov cheki
    get_status()          -> agent onlaynmi, navbatda nechta, printerlar

Printer sozlanmagan bo'lsa `print_bill` `{"queued": null, "reason": "no_printer"}`
qaytaradi. Sahifa bunda brauzer oynasini O'ZI OCHMAYDI — sababni kassirga
ko'rsatadi va brauzer yo'lini faqat kassir tanlasa ishlatadi
(`restaurant_cashier.js: printReceipt`).
"""

import frappe
from frappe import _

from ozturkapp.ozturkapp.utils import cashier_permissions, print_queue


def _resolve_branch_for_user():
    """Kassir/oshxona/menejer uchun filial — `resolve_branch` orqali."""
    return cashier_permissions.resolve_branch()


@frappe.whitelist()
def print_bill(invoice):
    """Mijoz chekini navbatga qo'yish (giveBill, reprint, split).

    HAR BIR CHAQIRUV LOGGA YOZILADI (`logs/ozturk_print.log`). Sababi:
    kassa sahifasi nosozlikni jimgina yutib yuborgan holat bo'lgan —
    chek chiqmasdi, Error Log esa bo'sh qolardi va so'rov serverga
    yetib kelgan-kelmagani ham noma'lum edi. Log shu savolga javob
    beradi: yozuv bor bo'lsa muammo serverda, yo'q bo'lsa — brauzerda.
    """
    logger = frappe.logger("ozturk_print")
    logger.info("print_bill: invoice=%s user=%s", invoice, frappe.session.user)

    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    # docstatus tekshirilmaydi: to'langan (submit) chekni ham qayta chop etish mumkin.
    cashier_permissions.assert_invoice_in_scope(invoice, scope)

    job = print_queue.enqueue_bill(invoice, scope)
    if not job:
        logger.warning("print_bill: kassa printeri topilmadi (filial=%s)", scope.branch)
        return {"queued": None, "reason": "no_printer"}
    agent = print_queue.agent_status(scope.branch)
    logger.info("print_bill: %s -> %s (agent_online=%s)", invoice, job, agent["online"])
    return {"queued": job, "agent_online": agent["online"]}


@frappe.whitelist()
def reprint(job):
    """Topshiriqni qayta navbatga qo'yish (Failed yoki Done bo'lsa ham)."""
    branch = _resolve_branch_for_user()
    job_branch = frappe.db.get_value("Ozturk Print Job", job, "branch")
    if not job_branch:
        frappe.throw(_("Topshiriq topilmadi: {0}").format(job))
    if job_branch != branch and "System Manager" not in frappe.get_roles():
        frappe.throw(_("Bu topshiriq boshqa filialga tegishli"), frappe.PermissionError)
    return {"queued": print_queue.requeue(job)}


@frappe.whitelist()
def test_print(printer):
    """Sinov cheki — printer sozlamasini tekshirish."""
    roles = set(frappe.get_roles())
    if not roles.intersection({"System Manager", "URY Manager", "URY Cashier"}):
        frappe.throw(_("Ruxsat yo'q"), frappe.PermissionError)
    label = f"Yubordi: {frappe.db.get_value('User', frappe.session.user, 'full_name') or frappe.session.user}"
    return {"queued": print_queue.enqueue_test(printer, label)}


@frappe.whitelist()
def get_status(branch=None):
    """Kassa sahifasi uchun: agent holati, navbat, printerlar."""
    branch = branch or _resolve_branch_for_user()
    agent = print_queue.agent_status(branch)
    summary = print_queue.queue_summary(branch)
    printers = [
        {"name": p["name"], "role": p["role"], "production_unit": p["production_unit"],
         "host": p["ip_address"]}
        for p in print_queue.get_printers(branch)
    ]
    return {
        "branch": branch,
        "agent_online": agent["online"],
        "agent": agent.get("agent"),
        "agent_seen_at": agent.get("at"),
        "pending": summary["pending"],
        "failed": summary["failed"],
        "printers": printers,
        "has_cashier_printer": any(p["role"] == print_queue.ROLE_CASHIER for p in printers),
    }


@frappe.whitelist()
def recent_jobs(branch=None, limit=20):
    """Oxirgi topshiriqlar (kassa oynasidagi 'Printer' paneli uchun)."""
    branch = branch or _resolve_branch_for_user()
    return frappe.get_all(
        "Ozturk Print Job",
        filters={"branch": branch},
        fields=["name", "title", "job_type", "printer", "status", "attempts", "error",
                "creation", "printed_at"],
        order_by="creation desc",
        limit=min(int(limit or 20), 100),
    )
