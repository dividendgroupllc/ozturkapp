# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Expense Allocation uchun Journal Entry havola maydoni.

`Journal Entry.custom_expense_allocation` — taqsimot JE'larini belgilaydi:
  * keyingi oy hovuzni hisoblashda ular CHIQARIB tashlanadi (aks holda
    Sklad'dagi kredit yozuvi "manfiy xarajat" bo'lib qayta taqsimlanardi);
  * JE ro'yxatida filtr va Expense Allocation'ning "Connections"i uchun.

Hisob (account) yaratilmaydi — Jazira'dan farqli ravishda hisoblar hujjatda
tanlanadi (Sklad hisoblar rejasi keyin qayta nomlanadi).

Idempotent — har migrate'da chaqiriladi (setup/after_migrate.py).
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

JE_FIELDS = {
	"Journal Entry": [
		{
			"fieldname": "custom_expense_allocation",
			"label": "Expense Allocation",
			"fieldtype": "Link",
			"options": "Expense Allocation",
			"insert_after": "user_remark",
			"read_only": 1,
			"no_copy": 1,
			"print_hide": 1,
			"in_standard_filter": 1,
			"description": "Xarajat taqsimoti (Expense Allocation) orqali yaratilgan yozuv",
		}
	]
}


def setup():
	create_custom_fields(JE_FIELDS, ignore_validate=True)
