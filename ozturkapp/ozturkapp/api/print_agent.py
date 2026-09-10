# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Restoran print-agenti uchun API (monoblokdagi Python xizmati chaqiradi).

AUTENTIFIKATSIYA
================
Agent alohida foydalanuvchi (`print-agent@...`, rol: `Print Agent`) va
API kalit/sekret bilan kiradi: `Authorization: token key:secret`.
Kalitlar `setup/print_setup.py: issue_agent_credentials` orqali beriladi.

Agent FAQAT shu uchta metodni chaqiradi:
    pull_jobs  -> Pending topshiriqlarni oladi (Printing ga o'tadi)
    ack        -> natija (ok / xato)
    heartbeat  -> "tirikman" (kassa oynasida ko'rsatiladi)
"""

import frappe
from frappe import _
from frappe.utils import cint

from ozturkapp.ozturkapp.utils import print_queue

AGENT_ROLES = ("Print Agent", "System Manager")


def require_agent():
    if frappe.session.user == "Guest":
        frappe.throw(_("Kirish talab qilinadi"), frappe.AuthenticationError)
    roles = set(frappe.get_roles())
    if not roles.intersection(AGENT_ROLES):
        frappe.throw(_("Faqat print-agent uchun"), frappe.PermissionError)


def _assert_branch(branch: str):
    if not branch or not frappe.db.exists("Branch", branch):
        frappe.throw(_("Filial topilmadi: {0}").format(branch))


@frappe.whitelist()
def pull_jobs(branch, agent=None, limit=None):
    """Navbatdagi topshiriqlarni olish. Ro'yxat bo'sh bo'lishi mumkin."""
    require_agent()
    _assert_branch(branch)
    agent = (agent or frappe.session.user)[:140]
    jobs = print_queue.pull_jobs(branch, agent, cint(limit) or print_queue.PULL_LIMIT)
    return {"jobs": jobs, "server_time": str(frappe.utils.now_datetime())}


@frappe.whitelist()
def ack(job, ok, error=None, agent=None):
    """Chop etish natijasi."""
    require_agent()
    return print_queue.ack(job, cint(ok) == 1, error=error, agent=agent or frappe.session.user)


@frappe.whitelist()
def heartbeat(branch, agent=None, info=None):
    """Agent tirik. `info` — {"version", "host", "printers": {...}} (ixtiyoriy)."""
    require_agent()
    _assert_branch(branch)
    if isinstance(info, str):
        try:
            info = frappe.parse_json(info)
        except Exception:
            info = {"raw": info[:200]}
    print_queue.heartbeat(branch, (agent or frappe.session.user)[:140], info or {})
    return {"ok": True, "pending": print_queue.queue_summary(branch)["pending"]}


@frappe.whitelist()
def get_printers(branch):
    """Agent o'z filialidagi printerlarni ko'rishi uchun (diagnostika)."""
    require_agent()
    _assert_branch(branch)
    return [
        {"name": p["name"], "host": p["ip_address"], "port": cint(p["port"]), "role": p["role"]}
        for p in print_queue.get_printers(branch)
    ]
