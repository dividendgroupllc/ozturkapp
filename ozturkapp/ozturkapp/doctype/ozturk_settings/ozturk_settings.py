# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Ozturk Settings — kompaniyalararo kassa sozlamalari (Single).

Kod kompaniya/ombor NOMLARINI bilmaydi: «Sklad qaysi kompaniya» va «har bir
kompaniyaning naqd kassasi qaysi» savollari faqat shu yerdan o'qiladi
(`utils/intercompany.py`). Yangi filial qo'shilganda kod o'zgarmaydi — shu
jadvalga bitta qator qo'shiladi.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class OzturkSettings(Document):
    def validate(self):
        self._validate_sklad()
        self._validate_company_cash()

    def _validate_sklad(self):
        wh_company = (
            frappe.db.get_value("Warehouse", self.sklad_warehouse, "company")
            if self.sklad_warehouse else None
        )
        if not self.sklad_company and wh_company:
            self.sklad_company = wh_company
        if self.sklad_company and wh_company and wh_company != self.sklad_company:
            frappe.throw(_("'{0}' ombori '{1}' kompaniyasiga tegishli emas.").format(
                self.sklad_warehouse, self.sklad_company))
        if self.sklad_company and frappe.db.get_value("Company", self.sklad_company, "is_group"):
            frappe.throw(_("Sklad kompaniyasi guruh kompaniyasi bo'lishi mumkin emas."))

    def _validate_company_cash(self):
        from ozturkapp.ozturkapp.utils.intercompany import resolve_mop_account

        seen = set()
        for row in self.company_cash:
            if row.company in seen:
                frappe.throw(_("{0}-qator: '{1}' kompaniyasi ikki marta kiritilgan.").format(
                    row.idx, row.company))
            seen.add(row.company)
            # MoP shu kompaniya uchun hisobga ega bo'lishi shart (aks holda xato)
            row.account = resolve_mop_account(row.mode_of_payment, row.company)["account"]
