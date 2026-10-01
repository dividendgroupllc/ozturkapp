# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa Filial — xarajatlarni guruhlash birligi (zal, oshxona, ma'muriyat...).

Bitta kompaniyali saytda filial = kassa kompaniyasi ichidagi xarajat egasi.
Saytda guruh kompaniyasi («O'zturk») ham bor — xarajat guruhi yoki kassa
boshqa kompaniyaniki bo'lsa, Kassa'da «Xarajat kontragenti» ro'yxati bo'sh
chiqardi yoki xarajat noto'g'ri kitobga ketardi. Shuning uchun saqlashdayoq
tekshiriladi.
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
            from ozturkapp.ozturkapp.doctype.kassa.kassa import resolve_mop_account

            resolve_mop_account(self.mode_of_payment, self.company)
