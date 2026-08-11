# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""after_migrate orkestratori.

Frappe after_migrate hooklarni ketma-ket chaqiradi — bittasi xato bersa,
keyingilari o'tkazib yuboriladi. Shuning uchun har bir sozlamani ALOHIDA
try/except bilan chaqiramiz: bittasi yiqilsa ham qolganlari ishlaydi, xato
esa Error Log'ga yoziladi.
"""

import frappe


def run():
	from ozturkapp.ozturkapp.setup.custom_fields import create_fields, create_property_setters
	from ozturkapp.ozturkapp.setup.kassa_setup import create_party_types
	from ozturkapp.ozturkapp.setup.print_format_setup import create_sales_order_print_format
	from ozturkapp.ozturkapp.setup.ury_custom_fields import (
		create_fields as create_ury_pos_fields,
	)

	tasks = [
		create_fields,
		create_property_setters,
		create_ury_pos_fields,
		create_party_types,
		create_sales_order_print_format,
	]
	for fn in tasks:
		try:
			fn()
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"after_migrate: {fn.__name__}")
