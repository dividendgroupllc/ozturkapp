# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa Filial — xarajat egasi (filial) va uning kompaniyasi.

`company` kompaniyalararo xarajatni belgilaydi: Kassa boshqa kompaniya
kassasidan to'lasa, xarajat shu kompaniya kitobiga yoziladi. Guruh kompaniyasi
(yoki bo'sh) — eski xatti-harakat: xarajat kassa kompaniyasi kitobida.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class KassaFilial(Document):
    def validate(self):
        if self.expense_group and self.company:
            acc_company = frappe.db.get_value("Account", self.expense_group, "company")
            if acc_company and acc_company != self.company:
                frappe.throw(_("Xarajat guruhi '{0}' kompaniyasiga tegishli bo'lishi kerak.").format(self.company))
        if self.mode_of_payment and self.company:
            from ozturkapp.ozturkapp.utils.intercompany import resolve_mop_account

            resolve_mop_account(self.mode_of_payment, self.company)
