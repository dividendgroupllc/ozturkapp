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
	from ozturkapp.ozturkapp.setup.receipt_format import setup as setup_receipt_format
	from ozturkapp.ozturkapp.setup.cancelled_orders import (
		detach_tables,
		reconcile_cancel_kots,
	)
	from ozturkapp.ozturkapp.setup.ury_custom_fields import (
		create_fields as create_ury_pos_fields,
	)
	from ozturkapp.ozturkapp.setup.kitchen_setup import setup as setup_kitchen
	from ozturkapp.ozturkapp.setup.service_charge import setup as setup_service_charge
	from ozturkapp.ozturkapp.setup.waiter_setup import setup as setup_waiter
	from ozturkapp.ozturkapp.setup.ury_permissions import create_permissions
	from ozturkapp.ozturkapp.setup.bill_split_setup import setup as setup_bill_split
	from ozturkapp.ozturkapp.setup.virtual_keyboard_setup import setup as setup_virtual_keyboard
	from ozturkapp.ozturkapp.setup.print_setup import setup as setup_printing
	from ozturkapp.ozturkapp.setup.cashier_features import setup as setup_cashier_features
	from ozturkapp.ozturkapp.utils.manager_approval import setup as setup_manager_approval
	from ozturkapp.ozturkapp.setup.cashier_billing_setup import setup as setup_cashier_billing
	from ozturkapp.ozturkapp.setup.cashier_orders_setup import setup as setup_cashier_orders
	from ozturkapp.ozturkapp.setup.cashier_shift_setup import setup as setup_cashier_shift

	tasks = [
		create_fields,
		create_property_setters,
		create_ury_pos_fields,
		# DIQQAT: `create_ury_pos_fields` dan KEYIN — u
		# `custom_cancelled_table` maydonini yaratadi va tozalash o'sha
		# maydonga yozadi.
		detach_tables,
		# Tuzatishdan oldin yaratilgan bekor-KOT'lar: bekor qilingan taom
		# oshxona ekranida «Kutilmoqda» bo'lib osilib qolgan edi.
		reconcile_cancel_kots,
		create_permissions,
		create_party_types,
		create_sales_order_print_format,
		# Xizmat haqi (12%) — ERPNext soliq shabloni orqali (TZ §8).
		# Idempotent: mavjud shablon va foizga tegmaydi.
		setup_service_charge,
		# Oshxona KDS — URY KOT Items custom fieldlari, rol, ruxsatlar (TZ §21).
		setup_kitchen,
		# Ofitsant mobil ilovasi — hisob so'rash maydonlari va ruxsatlar.
		setup_waiter,
		# Mijoz cheki — o'zbekcha format.
		setup_receipt_format,
		# Kassada hisobni taqsimlash — POS Profile darajasidagi yoqish/o'chirish.
		setup_bill_split,
		# Kassa yopish/to'lov ekranidagi virtual klaviatura — yoqish/o'chirish.
		setup_virtual_keyboard,
		# Chop etish navbati — `Print Agent` roli va agent foydalanuvchisi.
		setup_printing,
		# Kassa funksiyalari reestri — POS Profile'dagi yoqish/o'chirish
		# bayroqlari (chegirma, aralash to'lov, stol ko'chirish, ...).
		# `setup_virtual_keyboard` dan KEYIN: maydonlar shundan keyin joylashadi.
		setup_cashier_features,
		# Menejer PIN maydoni (`User.custom_pos_pin`).
		setup_manager_approval,
		# Quyidagi uchtasi — har bir yo'nalishning O'Z maydonlari. Ular
		# `setup_cashier_features` dan KEYIN turadi (POS Profile maydonlari
		# ularga bog'liq bo'lishi mumkin).
		setup_cashier_billing,
		setup_cashier_orders,
		setup_cashier_shift,
	]
	for fn in tasks:
		try:
			fn()
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"after_migrate: {fn.__name__}")
