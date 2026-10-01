# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa qoldiqlari — «Finance Manager» workspace'idagi HTML blok uchun.

Jazira'dagi «Kassa qoldigini» blokidan farqi: u brauzerdan har bir to'lov
turi uchun alohida `get_mode_of_payment_info` + `get_balance_on` so'rovini
yuborardi (N×2 so'rov) va kompaniya/to'lov turlari nomlari kodga yozilgan
edi. Bu yerda bitta so'rov: faol to'lov turlari va ularning hisoblari
`Mode of Payment Account` dan olinadi — yangi kassa (masalan «Kassa Oybek»)
qo'shilsa blok o'zi ko'rsatadi.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, today

ROLES = ("Accounts Manager", "Accounts User", "System Manager")


@frappe.whitelist()
def get_cash_balances(date=None, company=None):
    """`date` kuni oxiridagi qoldiq — har bir faol to'lov turi bo'yicha.

    Returns:
        dict: {"date", "company", "groups": [{"type", "label", "total", "rows": [...]}], "total"}
    """
    frappe.only_for(ROLES)
    from erpnext.accounts.utils import get_balance_on

    date = str(getdate(date or today()))
    company = company or frappe.defaults.get_user_default("Company") or frappe.db.get_value(
        "Company", {"is_group": 0}, "name"
    )

    rows = frappe.db.sql(
        """
        SELECT mp.name AS mode_of_payment, mp.type, mpa.default_account AS account
        FROM `tabMode of Payment` mp
        JOIN `tabMode of Payment Account` mpa ON mpa.parent = mp.name
        WHERE mp.enabled = 1 AND mpa.company = %s AND IFNULL(mpa.default_account, '') != ''
        ORDER BY mp.type = 'Cash' DESC, mp.name
        """,
        company,
        as_dict=True,
    )

    groups = {}
    for row in rows:
        row.balance = flt(get_balance_on(account=row.account, date=date, company=company), 2)
        kind = "Cash" if row.type == "Cash" else "Bank"
        group = groups.setdefault(kind, {
            "type": kind,
            "label": _("Naqd kassalar") if kind == "Cash" else _("Karta va bank"),
            "total": 0.0,
            "rows": [],
        })
        group["rows"].append(row)
        group["total"] = flt(group["total"] + row.balance, 2)

    ordered = [groups[k] for k in ("Cash", "Bank") if k in groups]
    return {
        "date": date,
        "company": company,
        "groups": ordered,
        "total": flt(sum(g["total"] for g in ordered), 2),
    }
