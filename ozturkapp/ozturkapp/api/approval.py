# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Menejer tasdig'i — frontend uchun yordamchi API.

PIN'ni TEKSHIRISH bu yerda EMAS: u tasdiq talab qiladigan amalning o'z
so'rovida (`manager_approval.require`) bajariladi. Bu modul faqat PIN oynasi
uchun menejerlar ro'yxatini beradi.
"""

import frappe

from ozturkapp.ozturkapp.utils import cashier_permissions, manager_approval


@frappe.whitelist()
def get_approvers():
    """PIN oynasida ko'rsatiladigan menejerlar.

    Returns:
        dict: `approvers` — `[{"user", "full_name"}]` (PIN'siz);
              `self_approves` — joriy foydalanuvchi o'zi menejer bo'lsa
              PIN so'ralmaydi.
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()

    return {
        "approvers": manager_approval.list_approvers(scope.branch),
        "self_approves": cashier_permissions.has_supervisor_role(),
    }
