# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""POS'da sotiladigan taomlarning tannarxi — retseptdan (BOM).

NEGA BU KERAK
=============
POS Profile'da `update_stock = 1`. Smena yopilganda ERPNext sotilgan har
bir taom uchun ombor provodkasini yozadi va unga TANNARX kerak. Taom
sotib olinmaydi — tayyorlanadi, shuning uchun omborda uning kirimi yo'q.
Bunday holatda ERPNext tannarxni `Item.valuation_rate` maydonidan oladi:
bu `erpnext/stock/stock_ledger.py: get_valuation_rate()` zanjirining
oxirgi bo'g'ini (avval ombor daftari, keyin shu maydon).

Ilgari bu maydon qo'lda yoki sotuv narxining foizi sifatida to'ldirilardi
(`item_costs.set_from_margin`) — ya'ni TAXMINIY raqam edi. Endi u
retseptdan hisoblanadi: BOM = xomashyolar ro'yxati, xomashyo narxi
o'zgarsa BOM tannarxi, undan keyin taom tannarxi ham o'zi yangilanadi.

QANDAY ISHLAYDI
===============
1. BOM tasdiqlanganda yoki tannarxi qo'lda o'zgartirilganda —
   `on_bom_change` hooki darhol `Item.valuation_rate` ga yozadi.
2. Har kuni kechasi `sync_all()` xomashyo narxidan BOM'larni qayta
   hisoblaydi (`BOM.update_cost`) va natijani taomlarga ko'chiradi.
   Un yoki go'sht qimmatlashsa — ertasiga tannarx to'g'ri bo'ladi.
   Ko'p bosqichli BOM ham to'g'ri yangilanadi: non tannarxi o'zgarsa,
   `update_parent=True` uni ishlatadigan dyoner BOM'ini ham yangilaydi.

CHEKLOV — O'QING
================
`Item.valuation_rate` — ZAXIRA qiymat. ERPNext undan FAQAT tovar uchun
ombor harakati (Stock Ledger Entry) hali yo'q bo'lganda foydalanadi.
Birinchi sotuvdan keyin taomning FIFO navbatida manfiy qoldiq paydo
bo'ladi (`allow_negative_stock = 1`), va keyingi sotuvlar O'SHA
navbatdagi narxni oladi — bu maydon keyin o'zgarsa ham.

Ya'ni: bu modul tannarxni birinchi sotuvgacha BOM bo'yicha to'g'ri
qo'yadi, keyin esa ombor daftari o'z narxini eslab qoladi.

Tannarx umr bo'yi retseptga ergashishi uchun taomlar haqiqatan ishlab
chiqarilishi kerak — BOM bo'yicha "Stock Entry (Manufacture)", yoki sotuv
paytida xomashyoni hisobdan chiqarish (backflush). Bu alohida qadam.

ISHLATISH
=========
    # Hozir barcha taomlarga BOM tannarxini yozish
    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.utils.bom_valuation.sync_all
"""

import frappe
from frappe.utils import flt


def bom_unit_cost(bom) -> float:
	"""BOM'ning 1 birlik (1 porsiya) uchun tannarxi."""
	if isinstance(bom, str):
		bom = frappe.db.get_value("BOM", bom, ["total_cost", "quantity"], as_dict=True)
	if not bom:
		return 0.0
	return flt(bom.total_cost) / (flt(bom.quantity) or 1)


def sync_item(item_code: str) -> float:
	"""Tovarning `valuation_rate` ini uning asosiy BOM'idan yangilaydi.

	Returns:
		Qo'llangan tannarx; BOM yo'q yoki tannarxi nol bo'lsa — 0.
	"""
	default_bom = frappe.db.get_value("Item", item_code, "default_bom")
	if not default_bom:
		return 0.0

	rate = bom_unit_cost(default_bom)
	if not rate:
		# Tannarxi nol BOM bilan mavjud qiymatni O'CHIRMAYMIZ — aks holda
		# tovar tannarxsiz qoladi va smena yopilmaydi (item_costs.py).
		return 0.0

	if flt(frappe.db.get_value("Item", item_code, "valuation_rate")) != rate:
		frappe.db.set_value("Item", item_code, "valuation_rate", rate)
	return rate


def on_bom_change(doc, method=None):
	"""`BOM` tasdiqlanganda / tasdiqdan keyin o'zgarganda — tovarga ko'chiradi."""
	if doc.docstatus != 1 or not doc.is_active or not doc.is_default:
		return
	sync_item(doc.item)


def on_bom_cancel(doc, method=None):
	"""BOM bekor qilinganda — tovarda qolgan asosiy BOM'dan qayta oladi.

	Hujjatning o'z `on_cancel` metodi hookdan OLDIN ishlaydi, ya'ni
	`manage_default_bom()` `Item.default_bom` ni allaqachon yangilagan.
	Boshqa BOM qolmagan bo'lsa — eski tannarx joyida qoladi (yuqoriga qarang).
	"""
	sync_item(doc.item)


def sync_all(update_costs: int = 1) -> int:
	"""Barcha asosiy BOM'lardan taomlar tannarxini yangilaydi.

	Kunlik scheduler jobi (`hooks.py: scheduler_events.daily`).

	Args:
		update_costs: 1 bo'lsa avval BOM'lar xomashyoning joriy narxidan
			qayta hisoblanadi.
	"""
	boms = frappe.get_all(
		"BOM",
		filters={"docstatus": 1, "is_active": 1, "is_default": 1},
		fields=["name", "item"],
		order_by="creation",
	)

	if int(update_costs or 0):
		# `update_cost` "Cost Updated" alertini chiqaradi — fon jobida keraksiz.
		frappe.flags.mute_messages = True
		try:
			for bom in boms:
				frappe.get_doc("BOM", bom.name).update_cost(update_parent=True)
		finally:
			frappe.flags.mute_messages = False

	synced = sum(1 for bom in boms if sync_item(bom.item))
	frappe.db.commit()

	print(f"✅ {synced} ta taom tannarxi BOM'dan olindi ({len(boms)} ta BOM'dan)")
	return synced
