# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Yarim tayyor mahsulotni buyurtma bo'yicha avtomatik ishlab chiqarish.

MUAMMO
======
Taomlar Product Bundle: chek smena yopilganda Sales Invoice'ga
konsolidatsiya bo'lib, bundle tarkibidagi yarim tayyor mahsulotni
(Non xamiri, Guruch (pishirilgan), Iskandar sous, ...) ombordan chiqaradi.
Lekin ular hech qachon ishlab chiqarilmaydi — qoldiq doim minusda, xomashyo
esa hisobdan chiqmaydi.

QOIDA
=====
`Item.custom_auto_manufacture = 1` bo'lgan mahsulot uchun chekdagi kerakli
miqdor (o'zi sotilsa — sotilgan miqdor, taom tarkibida bo'lsa —
taom soni × bundle miqdori) default BOM bo'yicha "Manufacture" Stock Entry
bilan ishlab chiqariladi. Stock Entry `custom_pos_invoice` orqali chekka
bog'lanadi.

Chek holati har o'zgarganda ishlab chiqarilgan miqdor kerakli miqdorga
TENGLASHTIRILADI (`reconcile`):

    taom qo'shildi        ->  yetishmagan qism uchun yangi Stock Entry
    taom olib tashlandi   ->  shu mahsulot Stock Entry'lari bekor qilinadi
                              va qolgan miqdor uchun bittasi qayta yaratiladi
    buyurtma bekor bo'ldi ->  hammasi bekor qilinadi (xomashyo qaytadi)

Qaytarish cheklari (`is_return`) ishlab chiqarishga tegmaydi — taom
tayyorlangan, xomashyo sarflangan.

QAYERDAN CHAQIRILADI
====================
* POS Invoice `on_update` / `on_submit` / `on_cancel` — `on_doc_change`;
* `order_cancel.cancel_invoice` — chek `db.set_value` bilan bekor qilinadi,
  hujjat hodisasi ishlamaydi;
* URY KOT `on_submit` ("Cancelled") — URY `cancel_order` ham chekni
  `db.set_value` bilan bekor qiladi.

Ishlab chiqarish fon vazifasida bajariladi — ofitsant kutib qolmaydi.
"""

import frappe
from frappe.utils import cint, flt, nowdate, nowtime

from ozturkapp.ozturkapp.services.bom_service import bom_service
from ozturkapp.ozturkapp.services.stock_service import StockEntryConfig, stock_service

#: Stock Entry miqdori shu aniqlikda yaxlitlanadi.
QTY_PRECISION = 3

LOCK_TIMEOUT = 120


def logger():
	return frappe.logger("ozturk_auto_manufacture")


# ═══════════════════════════════════════════════════════════════════
#  Hisob — chekka qancha kerak, qancha ishlab chiqarilgan
# ═══════════════════════════════════════════════════════════════════


def auto_items() -> set:
	"""Avto ishlab chiqariladigan mahsulotlar."""
	if not frappe.db.has_column("Item", "custom_auto_manufacture"):
		return set()
	return set(frappe.get_all("Item", filters={"custom_auto_manufacture": 1}, pluck="name"))


def required_qty(doc, items: set = None) -> dict:
	"""Chek uchun har bir avto mahsulotdan qancha kerak: {item_code: qty}."""
	if cint(doc.get("docstatus")) == 2 or cint(doc.get("custom_cancelled")) or cint(doc.get("is_return")):
		return {}

	items = auto_items() if items is None else items
	if not items:
		return {}

	rows = [r for r in doc.get("items") or [] if r.item_code]
	bundles = _bundle_parts({r.item_code for r in rows}, items)

	required = {}
	for row in rows:
		qty = flt(row.get("stock_qty")) or flt(row.qty) * (flt(row.get("conversion_factor")) or 1)
		if row.item_code in items:
			required[row.item_code] = required.get(row.item_code, 0) + qty
		for part, part_qty in bundles.get(row.item_code, ()):
			required[part] = required.get(part, 0) + qty * part_qty

	return {k: flt(v, QTY_PRECISION) for k, v in required.items() if flt(v, QTY_PRECISION) > 0}


def _bundle_parts(parents: set, items: set) -> dict:
	"""Taom -> [(avto mahsulot, bitta taomdagi miqdor)]."""
	if not parents:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT pb.new_item_code AS parent, pbi.item_code, pbi.qty
		FROM `tabProduct Bundle Item` pbi
		JOIN `tabProduct Bundle` pb ON pb.name = pbi.parent
		WHERE pb.new_item_code IN %(parents)s
			AND pbi.item_code IN %(items)s
			AND IFNULL(pb.disabled, 0) = 0
		""",
		{"parents": tuple(parents), "items": tuple(items)},
		as_dict=True,
	)
	out = {}
	for r in rows:
		out.setdefault(r.parent, []).append((r.item_code, flt(r.qty)))
	return out


def produced_entries(invoice: str) -> dict:
	"""Chek bo'yicha submit qilingan ishlab chiqarish: {item_code: [(se, qty)]}."""
	if not frappe.db.has_column("Stock Entry", "custom_pos_invoice"):
		return {}
	rows = frappe.db.sql(
		"""
		SELECT se.name, sed.item_code, sed.transfer_qty AS qty
		FROM `tabStock Entry` se
		JOIN `tabStock Entry Detail` sed ON sed.parent = se.name AND sed.is_finished_item = 1
		WHERE se.custom_pos_invoice = %s AND se.docstatus = 1
			AND se.stock_entry_type = 'Manufacture'
		ORDER BY se.creation
		""",
		invoice,
		as_dict=True,
	)
	out = {}
	for r in rows:
		out.setdefault(r.item_code, []).append((r.name, flt(r.qty)))
	return out


def _produced_totals(entries: dict) -> dict:
	return {k: flt(sum(q for _, q in v), QTY_PRECISION) for k, v in entries.items()}


def is_in_sync(required: dict, produced: dict) -> bool:
	keys = set(required) | set(produced)
	return all(abs(flt(required.get(k)) - flt(produced.get(k))) < 0.0005 for k in keys)


# ═══════════════════════════════════════════════════════════════════
#  Hook'lar — fonga navbatga qo'yish
# ═══════════════════════════════════════════════════════════════════


def on_doc_change(doc, method=None):
	"""POS Invoice hodisasi: kerakli va ishlab chiqarilgan farq qilsa — navbatga."""
	try:
		if cint(doc.get("is_return")):
			return
		required = required_qty(doc)
		if not required and not frappe.db.exists("Stock Entry", {"custom_pos_invoice": doc.name}):
			return
		if is_in_sync(required, _produced_totals(produced_entries(doc.name))):
			return
		enqueue(doc.name)
	except Exception:
		# Ishlab chiqarish xatosi buyurtmani to'xtatmasin.
		frappe.log_error(title=f"Avto ishlab chiqarish: {doc.name}")


def on_kot_submit(doc, method=None):
	"""URY `cancel_order` chekni hodisasiz bekor qiladi — KOT orqali ushlaymiz."""
	if doc.get("type") == "Cancelled" and doc.get("invoice"):
		schedule(doc.invoice)


def schedule(invoice: str):
	"""Chek holati hodisasiz o'zgarganda (bekor qilish) — mavjud bo'lsa navbatga."""
	try:
		if invoice and frappe.db.has_column("Stock Entry", "custom_pos_invoice") and frappe.db.exists(
			"Stock Entry", {"custom_pos_invoice": invoice, "docstatus": 1}
		):
			enqueue(invoice)
	except Exception:
		frappe.log_error(title=f"Avto ishlab chiqarish: {invoice}")


def enqueue(invoice: str):
	frappe.enqueue(
		"ozturkapp.ozturkapp.utils.auto_manufacture.reconcile",
		queue="default",
		timeout=600,
		enqueue_after_commit=True,
		invoice=invoice,
	)


# ═══════════════════════════════════════════════════════════════════
#  Tenglashtirish — fon vazifasi
# ═══════════════════════════════════════════════════════════════════


def reconcile(invoice: str) -> dict:
	"""Chek bo'yicha ishlab chiqarishni kerakli miqdorga tenglashtiradi.

	Idempotent: necha marta chaqirilsa ham natija bir xil. Bir chek uchun
	parallel vazifalar qulf bilan navbatlashadi — commit qulf ichida, aks
	holda keyingi vazifa hali ko'rinmaydigan Stock Entry'ni bilmay qayta
	yaratardi.
	"""
	lock = frappe.cache.lock(
		f"{frappe.local.site}:ozturk_auto_manufacture:{invoice}",
		timeout=LOCK_TIMEOUT,
		blocking_timeout=LOCK_TIMEOUT,
	)
	if not lock.acquire():
		raise frappe.ValidationError(f"Avto ishlab chiqarish qulfi olinmadi: {invoice}")
	try:
		result = _reconcile(invoice)
		frappe.db.commit()
		return result
	except Exception:
		frappe.db.rollback()
		raise
	finally:
		lock.release()


def _reconcile(invoice: str) -> dict:
	if not frappe.db.exists("POS Invoice", invoice):
		return {"created": [], "cancelled": []}

	doc = frappe.get_doc("POS Invoice", invoice)
	if cint(doc.is_return):
		return {"created": [], "cancelled": []}

	required = required_qty(doc)
	entries = produced_entries(invoice)
	produced = _produced_totals(entries)

	warehouse = (
		frappe.db.get_value("POS Profile", doc.pos_profile, "warehouse") if doc.pos_profile else None
	) or doc.get("set_warehouse") or next((r.warehouse for r in doc.items if r.warehouse), None)

	created, cancelled = [], []
	for item in sorted(set(required) | set(produced)):
		need = flt(required.get(item), QTY_PRECISION)
		have = flt(produced.get(item), QTY_PRECISION)
		if abs(need - have) < 0.0005:
			continue

		if need < have:
			# Manufacture'ni qisman kamaytirib bo'lmaydi — bu mahsulot
			# hujjatlari bekor qilinib, qolgan miqdor qayta ishlab chiqariladi.
			names = [name for name, _ in reversed(entries.get(item, []))]
			stock_service.cancel_stock_entries(names)
			cancelled.extend(names)
			have = 0

		qty = flt(need - have, QTY_PRECISION)
		if qty <= 0:
			continue

		bom = bom_service.get_default_bom(item)
		if not bom:
			logger().warning("Avto ishlab chiqarish: %s uchun faol BOM yo'q (chek %s)", item, invoice)
			continue
		if not warehouse:
			logger().warning("Avto ishlab chiqarish: chek %s uchun ombor topilmadi", invoice)
			continue

		config = StockEntryConfig(
			company=doc.company,
			warehouse=warehouse,
			posting_date=nowdate(),
			posting_time=nowtime(),
			extra_fields={
				# Aks holda ERPNext `fg_completed_qty` ni 0 qiladi.
				"from_bom": 1,
				"use_multi_level_bom": 1,
				"custom_pos_invoice": invoice,
				"remarks": f"Buyurtma bo'yicha avto ishlab chiqarish: {invoice}",
			},
			flags={"ozturk_auto_manufacture": True},
		)
		created.extend(
			stock_service.create_manufacture_entries(
				[{"item_code": item, "qty": qty, "bom": bom}], config, submit=True
			)
		)

	if created or cancelled:
		logger().info(
			"Avto ishlab chiqarish: chek=%s | yaratildi=%s | bekor=%s",
			invoice,
			",".join(created) or "-",
			",".join(cancelled) or "-",
		)
	return {"created": created, "cancelled": cancelled}
