# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Chop etish navbati uchun sozlash — rol, agent foydalanuvchisi, kalitlar.

Ishga tushirish::

    # Rol va ruxsatlar (after_migrate da avtomatik)
    bench --site ozturk.local execute ozturkapp.ozturkapp.setup.print_setup.setup

    # Agent uchun API kalit/sekret (BIR MARTA ko'rsatiladi, saqlab oling)
    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.print_setup.issue_agent_credentials

    # Filial printerlarini yaratish (misol)
    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.print_setup.create_printer \
        --kwargs '{"branch": "Ozturk", "printer_name": "Kassa", "ip_address": "192.168.1.251", "role": "Kassa"}'
"""

import frappe
from frappe import _

AGENT_ROLE = "Print Agent"
AGENT_EMAIL = "print-agent@ozturk.local"
AGENT_FULL_NAME = "Ozturk Print Agent"


def setup():
    create_role()
    ensure_agent_user()
    frappe.db.commit()


def create_role():
    if frappe.db.exists("Role", AGENT_ROLE):
        return
    frappe.get_doc({
        "doctype": "Role",
        "role_name": AGENT_ROLE,
        "desk_access": 0,
        "is_custom": 1,
    }).insert(ignore_permissions=True)


def ensure_agent_user():
    """Agent foydalanuvchisi — parolsiz, faqat API kalit bilan kiradi."""
    if frappe.db.exists("User", AGENT_EMAIL):
        user = frappe.get_doc("User", AGENT_EMAIL)
        if AGENT_ROLE not in [r.role for r in user.roles]:
            user.append("roles", {"role": AGENT_ROLE})
            user.save(ignore_permissions=True)
        return user

    user = frappe.get_doc({
        "doctype": "User",
        "email": AGENT_EMAIL,
        "first_name": AGENT_FULL_NAME,
        "user_type": "System User",
        "send_welcome_email": 0,
        "enabled": 1,
        "roles": [{"role": AGENT_ROLE}],
    })
    user.flags.ignore_permissions = True
    user.flags.no_welcome_mail = True
    user.insert()
    return user


def issue_agent_credentials():
    """Yangi API kalit/sekret yaratish va chop etish (eskisi bekor bo'ladi)."""
    user = ensure_agent_user()
    api_key = user.api_key or frappe.generate_hash(length=15)
    api_secret = frappe.generate_hash(length=15)
    user.api_key = api_key
    user.api_secret = api_secret
    user.save(ignore_permissions=True)
    frappe.db.commit()
    print("\n=== PRINT AGENT CREDENTIALS (bir marta ko'rsatiladi) ===")
    print(f"site      : {frappe.utils.get_url()}")
    print(f"user      : {AGENT_EMAIL}")
    print(f"api_key   : {api_key}")
    print(f"api_secret: {api_secret}")
    print("Authorization: token {api_key}:{api_secret}\n")
    return {"api_key": api_key, "api_secret": api_secret}


def create_printer(branch, printer_name, ip_address, role="Kassa", production_unit=None,
                   port=9100, paper_width="80", codepage="cp866"):
    """Printer yozuvini yaratish/yangilash (idempotent)."""
    if frappe.db.exists("Ozturk Printer", printer_name):
        doc = frappe.get_doc("Ozturk Printer", printer_name)
    else:
        doc = frappe.new_doc("Ozturk Printer")
        doc.printer_name = printer_name
    doc.update({
        "branch": branch,
        "ip_address": ip_address,
        "role": role,
        "production_unit": production_unit,
        "port": port,
        "paper_width": str(paper_width),
        "codepage": codepage,
        "enabled": 1,
    })
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    print(f"Printer '{doc.name}': {doc.ip_address}:{doc.port} ({doc.role})")
    return doc.name
