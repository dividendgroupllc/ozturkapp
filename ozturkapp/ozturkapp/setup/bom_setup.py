# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Xomashyo (Сырьё) tovarlari va menyu taomlari uchun retsept (BOM).

NEGA BU KERAK
=============
Menyudagi taomlar bazaga tayyor mahsulot sifatida kiritilgan, lekin ular
NIMADAN tayyorlanishi tizimda yo'q edi. Shuning uchun:

* taomning haqiqiy tannarxini hisoblab bo'lmaydi — `item_costs.py` da
  tasvirlangan muammo (tannarxsiz tovar bo'lsa KASSA YOPILMAYDI) faqat
  qo'lda kiritilgan taxminiy raqam bilan yopilgan edi;
* omborda go'sht, un, sabzavot harakati ko'rinmaydi — faqat tayyor taom
  minus bo'ladi;
* narx o'zgarganda (masalan go'sht qimmatlashsa) tannarxni qayta hisoblash
  imkoni yo'q.

BOM (Bill of Materials — retsept) shu uchun: har bir taom = xomashyolar
ro'yxati. ERPNext BOM narxini xomashyoning baholash narxidan (valuation
rate) yig'adi va taomning tannarxini o'zi chiqaradi.

NIMA YARATILADI
===============
1. `Сырьё` guruhida xomashyo tovarlari (un, go'sht, sabzavot, ziravor...).
   O'lchov birligi: Gram / Millilitre / Nos.
2. Har bir menyu taomiga BOM (`Напитки` guruhidan tashqari — ichimliklar
   tayyorlanmaydi, sotib olinadi va shundayligicha sotiladi).

Non (`Хлеб` guruhi) ikki xil rolda: uning o'z BOM'i bor (un + xamirturush),
va u dyoner/iskender BOM'ida yarim tayyor mahsulot sifatida ishlatiladi.
Shuning uchun BOM'lar bog'liqlik tartibida yaratiladi — avval non, keyin
uni ishlatadigan taomlar.

ISHLATISH
=========
    # Xomashyo + BOM yaratish (idempotent — qayta ishga tushirsa bo'ladi)
    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.bom_setup.setup

    # Holatni ko'rish: tannarx / sotuv / marja
    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.bom_setup.report

BOM tannarxi shu yerdayoq taomlarning `valuation_rate` maydoniga
ko'chiriladi — POS smenasi yopilganda stok provodkasi shundan narx oladi.
Sinxronizatsiya mantig'i va uning cheklovlari: `utils/bom_valuation.py`.

DIQQAT
======
Miqdorlar va narxlar BOSHLANG'ICH qiymat — oshpaz bilan tekshirilishi va
haqiqiy retseptga moslanishi kerak. Narxlar UZS'da, o'lchov birligiga
nisbatan (masalan go'sht: 1 gramm uchun 120 so'm).
"""

import frappe
from frappe.utils import flt

from ozturkapp.ozturkapp.utils import bom_valuation

RAW_GROUP = "Сырьё"
EXCLUDED_GROUPS = ("Напитки",)

# code, nomi, o'lchov birligi, 1 birlik narxi (UZS)
RAW_MATERIALS = (
	# --- Go'sht ---
	("RM-BEEF-DONER", "Мясо для дёнера (говядина)", "Gram", 120),
	("RM-CHICKEN-DONER", "Мясо для дёнера (курица)", "Gram", 55),
	("RM-BEEF-CUBED", "Говядина кусковая (кушбаши)", "Gram", 110),
	("RM-BEEF-MINCED", "Фарш говяжий", "Gram", 95),
	("RM-CHICKEN-MINCED", "Фарш куриный", "Gram", 60),
	# --- Un va xamir ---
	("RM-FLOUR", "Мука пшеничная", "Gram", 6),
	("RM-YEAST", "Дрожжи", "Gram", 40),
	("RM-SUGAR", "Сахар", "Gram", 12),
	("RM-SESAME", "Кунжут", "Gram", 45),
	("RM-GOBIT-BUN", "Булочка гобит", "Nos", 3000),
	# --- Don mahsulotlari ---
	("RM-RICE", "Рис", "Gram", 15),
	("RM-VERMICELLI", "Вермишель (шехрие)", "Gram", 16),
	("RM-LENTIL-RED", "Чечевица красная", "Gram", 18),
	# --- Sabzavot ---
	("RM-POTATO", "Картофель", "Gram", 6),
	("RM-TOMATO", "Помидор", "Gram", 12),
	("RM-CUCUMBER", "Огурец", "Gram", 10),
	("RM-ONION", "Лук репчатый", "Gram", 5),
	("RM-LETTUCE", "Салат айсберг", "Gram", 14),
	("RM-ARUGULA", "Руккола", "Gram", 35),
	("RM-PARSLEY", "Петрушка", "Gram", 20),
	("RM-BELL-PEPPER", "Перец болгарский", "Gram", 18),
	("RM-CARROT", "Морковь", "Gram", 6),
	("RM-LEMON", "Лимон", "Gram", 15),
	("RM-GARLIC", "Чеснок", "Gram", 25),
	# --- Sut mahsulotlari ---
	("RM-CHEESE-KASHAR", "Сыр кашар", "Gram", 95),
	("RM-BUTTER", "Масло сливочное", "Gram", 85),
	("RM-YOGURT", "Йогурт", "Gram", 25),
	("RM-MILK", "Молоко", "Millilitre", 12),
	("RM-EGG", "Яйцо", "Nos", 1500),
	# --- Yog', pasta, ziravor ---
	("RM-OIL-SUNFLOWER", "Масло подсолнечное", "Millilitre", 22),
	("RM-OIL-OLIVE", "Масло оливковое", "Millilitre", 95),
	("RM-TOMATO-PASTE", "Томатная паста", "Gram", 30),
	("RM-PEPPER-PASTE", "Перечная паста", "Gram", 35),
	("RM-SALT", "Соль", "Gram", 2),
	("RM-PEPPER-BLACK", "Перец чёрный молотый", "Gram", 120),
	("RM-PEPPER-RED", "Перец красный (хлопья)", "Gram", 60),
	("RM-SUMAC", "Сумах", "Gram", 80),
	("RM-OREGANO", "Орегано", "Gram", 90),
	("RM-CUMIN", "Зира", "Gram", 70),
)

# Retseptlar: 1 porsiya uchun (xomashyo kodi, miqdor).
# Nos birligidagi tarkib (non, tuxum) BUTUN son bo'lishi shart —
# ERPNext "Nos" uchun kasr miqdorga ruxsat bermaydi.
RECIPES = {
	# ---------- Хлеб (o'zi sotiladi + boshqa taomlarga yarim tayyor) ----------
	"LAVASH BREAD": (
		("RM-FLOUR", 150),
		("RM-SALT", 3),
		("RM-OIL-SUNFLOWER", 5),
	),
	"TIRNAK BREAD": (
		("RM-FLOUR", 250),
		("RM-YEAST", 5),
		("RM-SALT", 5),
		("RM-SUGAR", 4),
		("RM-OIL-SUNFLOWER", 8),
		("RM-SESAME", 4),
		("RM-MILK", 20),
	),
	"AFGAN BREAD": (
		("RM-FLOUR", 300),
		("RM-YEAST", 6),
		("RM-SALT", 6),
		("RM-SUGAR", 5),
		("RM-OIL-SUNFLOWER", 10),
		("RM-SESAME", 5),
	),
	# ---------- Гарниры ----------
	"FRENCH FRIES": (
		("RM-POTATO", 250),
		("RM-OIL-SUNFLOWER", 30),
		("RM-SALT", 2),
	),
	"PILAV": (
		("RM-RICE", 130),
		("RM-VERMICELLI", 15),
		("RM-BUTTER", 15),
		("RM-OIL-SUNFLOWER", 5),
		("RM-SALT", 3),
	),
	# ---------- Дёнер и Лаваш ----------
	"DÖNER DURUM": (
		("LAVASH BREAD", 1),
		("RM-BEEF-DONER", 120),
		("RM-TOMATO", 30),
		("RM-ONION", 20),
		("RM-LETTUCE", 20),
		("RM-POTATO", 40),
		("RM-OIL-SUNFLOWER", 8),
		("RM-SUMAC", 1),
	),
	"DÖNER DURUM CHICKEN": (
		("LAVASH BREAD", 1),
		("RM-CHICKEN-DONER", 120),
		("RM-TOMATO", 30),
		("RM-ONION", 20),
		("RM-LETTUCE", 20),
		("RM-POTATO", 40),
		("RM-OIL-SUNFLOWER", 8),
		("RM-SUMAC", 1),
	),
	"GOBIT DÖNER": (
		("RM-GOBIT-BUN", 1),
		("RM-BEEF-DONER", 100),
		("RM-TOMATO", 25),
		("RM-ONION", 15),
		("RM-LETTUCE", 15),
		("RM-POTATO", 30),
		("RM-OIL-SUNFLOWER", 6),
		("RM-SUMAC", 1),
	),
	"GOBIT DÖNER CHICKEN": (
		("RM-GOBIT-BUN", 1),
		("RM-CHICKEN-DONER", 100),
		("RM-TOMATO", 25),
		("RM-ONION", 15),
		("RM-LETTUCE", 15),
		("RM-POTATO", 30),
		("RM-OIL-SUNFLOWER", 6),
		("RM-SUMAC", 1),
	),
	"LAVASH ÜSTÜ": (
		("LAVASH BREAD", 1),
		("RM-BEEF-DONER", 150),
		("RM-TOMATO", 40),
		("RM-BELL-PEPPER", 25),
		("RM-TOMATO-PASTE", 20),
		("RM-BUTTER", 12),
		("RM-OREGANO", 1),
	),
	"LAVASH ÜSTÜ CHICKEN": (
		("LAVASH BREAD", 1),
		("RM-CHICKEN-DONER", 150),
		("RM-TOMATO", 40),
		("RM-BELL-PEPPER", 25),
		("RM-TOMATO-PASTE", 20),
		("RM-BUTTER", 12),
		("RM-OREGANO", 1),
	),
	"PORTION DÖNER": (
		("TIRNAK BREAD", 1),
		("RM-BEEF-DONER", 180),
		("RM-TOMATO", 40),
		("RM-BELL-PEPPER", 25),
		("RM-ONION", 20),
		("RM-PARSLEY", 5),
		("RM-SUMAC", 1),
	),
	"PORTION DÖNER CHICKEN": (
		("TIRNAK BREAD", 1),
		("RM-CHICKEN-DONER", 180),
		("RM-TOMATO", 40),
		("RM-BELL-PEPPER", 25),
		("RM-ONION", 20),
		("RM-PARSLEY", 5),
		("RM-SUMAC", 1),
	),
	# ---------- Кебаб и Мясные блюда ----------
	"ISKENDER KEBAB": (
		("TIRNAK BREAD", 1),
		("RM-BEEF-DONER", 150),
		("RM-TOMATO-PASTE", 45),
		("RM-BUTTER", 25),
		("RM-YOGURT", 60),
		("RM-OIL-SUNFLOWER", 8),
		("RM-SALT", 2),
	),
	"ISKENDER KEBAB CHICKEN": (
		("TIRNAK BREAD", 1),
		("RM-CHICKEN-DONER", 150),
		("RM-TOMATO-PASTE", 45),
		("RM-BUTTER", 25),
		("RM-YOGURT", 60),
		("RM-OIL-SUNFLOWER", 8),
		("RM-SALT", 2),
	),
	"TANDIR BEYTI": (
		("LAVASH BREAD", 1),
		("RM-BEEF-MINCED", 180),
		("RM-ONION", 25),
		("RM-PARSLEY", 8),
		("RM-TOMATO-PASTE", 20),
		("RM-YOGURT", 40),
		("RM-BUTTER", 15),
		("RM-SALT", 3),
		("RM-PEPPER-BLACK", 1),
		("RM-PEPPER-RED", 2),
		("RM-CUMIN", 1),
	),
	"TANDIR BEYTI CHICKEN": (
		("LAVASH BREAD", 1),
		("RM-CHICKEN-MINCED", 180),
		("RM-ONION", 25),
		("RM-PARSLEY", 8),
		("RM-TOMATO-PASTE", 20),
		("RM-YOGURT", 40),
		("RM-BUTTER", 15),
		("RM-SALT", 3),
		("RM-PEPPER-BLACK", 1),
		("RM-PEPPER-RED", 2),
		("RM-CUMIN", 1),
	),
	# ---------- Пиде и Лахмаджун ----------
	"CLOSED PIDE": (
		("RM-FLOUR", 200),
		("RM-YEAST", 4),
		("RM-SALT", 4),
		("RM-OIL-SUNFLOWER", 10),
		("RM-CHEESE-KASHAR", 80),
		("RM-BEEF-MINCED", 60),
		("RM-BUTTER", 8),
	),
	"KUŞBAŞI PIDE": (
		("RM-FLOUR", 220),
		("RM-YEAST", 4),
		("RM-SALT", 4),
		("RM-OIL-SUNFLOWER", 10),
		("RM-BEEF-CUBED", 120),
		("RM-BELL-PEPPER", 30),
		("RM-TOMATO", 30),
		("RM-BUTTER", 10),
		("RM-PEPPER-BLACK", 1),
	),
	"MIXED PIDE": (
		("RM-FLOUR", 220),
		("RM-YEAST", 4),
		("RM-SALT", 4),
		("RM-OIL-SUNFLOWER", 10),
		("RM-BEEF-MINCED", 70),
		("RM-CHEESE-KASHAR", 60),
		("RM-BELL-PEPPER", 25),
		("RM-TOMATO", 25),
		("RM-EGG", 1),
		("RM-BUTTER", 8),
	),
	"PIDE WITH CHEESE": (
		("RM-FLOUR", 200),
		("RM-YEAST", 4),
		("RM-SALT", 4),
		("RM-OIL-SUNFLOWER", 10),
		("RM-CHEESE-KASHAR", 130),
		("RM-BUTTER", 10),
		("RM-EGG", 1),
	),
	"LAHMACUN": (
		("RM-FLOUR", 120),
		("RM-YEAST", 3),
		("RM-SALT", 2),
		("RM-OIL-SUNFLOWER", 5),
		("RM-BEEF-MINCED", 60),
		("RM-ONION", 30),
		("RM-TOMATO", 30),
		("RM-BELL-PEPPER", 20),
		("RM-PARSLEY", 10),
		("RM-PEPPER-PASTE", 10),
		("RM-PEPPER-RED", 1),
	),
	# ---------- Салаты ----------
	"ARUGULA SALAD": (
		("RM-ARUGULA", 60),
		("RM-TOMATO", 60),
		("RM-LEMON", 20),
		("RM-OIL-OLIVE", 10),
		("RM-SALT", 1),
		("RM-SUMAC", 1),
	),
	"ÇOBAN SALAD": (
		("RM-TOMATO", 80),
		("RM-CUCUMBER", 70),
		("RM-ONION", 30),
		("RM-BELL-PEPPER", 30),
		("RM-PARSLEY", 10),
		("RM-LEMON", 10),
		("RM-OIL-OLIVE", 8),
		("RM-SALT", 2),
	),
	"SEASONAL SALAD": (
		("RM-LETTUCE", 60),
		("RM-TOMATO", 50),
		("RM-CUCUMBER", 50),
		("RM-CARROT", 30),
		("RM-BELL-PEPPER", 20),
		("RM-LEMON", 10),
		("RM-OIL-OLIVE", 8),
		("RM-SALT", 2),
	),
	# ---------- Супы ----------
	"LENTIL SOUP": (
		("RM-LENTIL-RED", 70),
		("RM-ONION", 25),
		("RM-CARROT", 25),
		("RM-POTATO", 30),
		("RM-FLOUR", 8),
		("RM-BUTTER", 10),
		("RM-TOMATO-PASTE", 10),
		("RM-LEMON", 10),
		("RM-SALT", 3),
		("RM-PEPPER-RED", 1),
	),
}


# ---------------------------------------------------------------- yordamchi


def _company() -> str:
	company = frappe.defaults.get_defaults().get("company")
	if not company:
		companies = frappe.get_all("Company", pluck="name", limit=1)
		company = companies[0] if companies else None
	if not company:
		frappe.throw("Kompaniya topilmadi — avval Company yarating")
	return company


def _default_warehouse(company: str) -> str:
	"""Xomashyo ombori — mavjud tovarlar bilan bir xil bo'lsin."""
	warehouse = frappe.db.get_value(
		"Item Default",
		{"company": company, "default_warehouse": ["is", "set"]},
		"default_warehouse",
	)
	if warehouse:
		return warehouse
	return frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")


def _ensure_raw_group():
	if frappe.db.exists("Item Group", RAW_GROUP):
		return
	frappe.get_doc(
		{
			"doctype": "Item Group",
			"item_group_name": RAW_GROUP,
			"parent_item_group": "All Item Groups",
			"is_group": 0,
		}
	).insert(ignore_permissions=True)


def _ordered_recipes(codes: list) -> list:
	"""BOM'larni bog'liqlik tartibida qaytaradi.

	Non taomlar ichida yarim tayyor mahsulot bo'lgani uchun uning BOM'i
	avval yaratilishi kerak — aks holda dyoner BOM'i non tannarxini nol
	deb oladi.
	"""
	pending = {code: RECIPES[code] for code in codes}
	order = []
	while pending:
		ready = sorted(
			code
			for code, lines in pending.items()
			if not any(child in pending for child, _qty in lines)
		)
		if not ready:
			frappe.throw(f"Retseptlarda halqa bor: {', '.join(sorted(pending))}")
		for code in ready:
			order.append(code)
			pending.pop(code)
	return order


# ---------------------------------------------------------------- yaratish


def _ensure_raw_items(company: str, warehouse: str) -> int:
	created = 0
	for code, name, uom, rate in RAW_MATERIALS:
		if frappe.db.exists("Item", code):
			# Mavjud tovarga tegmaymiz, faqat narxi yo'q bo'lsa to'ldiramiz.
			if not flt(frappe.db.get_value("Item", code, "valuation_rate")):
				frappe.db.set_value("Item", code, "valuation_rate", rate)
			continue

		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": name,
				"description": name,
				"item_group": RAW_GROUP,
				"stock_uom": uom,
				"is_stock_item": 1,
				"is_purchase_item": 1,
				"is_sales_item": 0,
				"include_item_in_manufacturing": 1,
				"valuation_rate": rate,
				"item_defaults": [{"company": company, "default_warehouse": warehouse}],
			}
		).insert(ignore_permissions=True)
		created += 1
	return created


def _create_bom(item_code: str, lines: tuple, company: str, currency: str) -> str:
	bom = frappe.get_doc(
		{
			"doctype": "BOM",
			"item": item_code,
			"company": company,
			"currency": currency,
			"quantity": 1,
			"is_active": 1,
			"is_default": 1,
			"with_operations": 0,
			"rm_cost_as_per": "Valuation Rate",
			"items": [
				{
					"item_code": child,
					"qty": qty,
					"uom": frappe.db.get_value("Item", child, "stock_uom"),
				}
				for child, qty in lines
			],
		}
	)
	bom.insert(ignore_permissions=True)
	bom.submit()
	return bom.name


def setup():
	"""Xomashyo tovarlari va menyu BOM'larini yaratadi (idempotent)."""
	company = _company()
	currency = frappe.db.get_value("Company", company, "default_currency")
	warehouse = _default_warehouse(company)

	_ensure_raw_group()
	created_items = _ensure_raw_items(company, warehouse)
	print(f"✅ Xomashyo: {created_items} ta yangi tovar ({len(RAW_MATERIALS)} tadan)")

	# Retsepti bor, bazada mavjud va ichimlik bo'lmagan taomlar.
	todo, skipped = [], []
	for code in RECIPES:
		group = frappe.db.get_value("Item", code, "item_group")
		if not group:
			skipped.append(f"{code} (tovar yo'q)")
			continue
		if group in EXCLUDED_GROUPS:
			skipped.append(f"{code} ({group})")
			continue
		if frappe.db.exists("BOM", {"item": code, "docstatus": 1, "is_active": 1}):
			continue
		todo.append(code)

	created_boms = 0
	for code in _ordered_recipes(todo):
		name = _create_bom(code, RECIPES[code], company, currency)
		cost = flt(frappe.db.get_value("BOM", name, "total_cost"))
		print(f"   {code:<24}{name:<22}{cost:>12,.0f}")
		created_boms += 1

	frappe.db.commit()
	print(f"✅ BOM: {created_boms} ta yaratildi")
	if skipped:
		print(f"⏭️  O'tkazib yuborildi: {', '.join(skipped)}")

	# Tannarxni taomlarga ko'chiramiz — POS smenasi shu maydondan o'qiydi.
	# BOM'lar shu yerda yaratilgani uchun narx qayta hisoblanishi shart emas.
	bom_valuation.sync_all(update_costs=0)

	gaps = missing_recipes()
	if gaps:
		print(f"⚠️  Retsepti yo'q taomlar: {', '.join(gaps)}")
	return created_boms


# ---------------------------------------------------------------- hisobot


def missing_recipes() -> list:
	"""BOM'i yo'q taomlar (`Напитки` va xomashyodan tashqari)."""
	raw_codes = {code for code, _name, _uom, _rate in RAW_MATERIALS}
	items = frappe.get_all(
		"Item",
		filters={
			"item_group": ["not in", (*EXCLUDED_GROUPS, RAW_GROUP)],
			"disabled": 0,
			"has_variants": 0,
		},
		pluck="name",
	)
	return sorted(
		code
		for code in items
		if code not in raw_codes
		and not frappe.db.exists("BOM", {"item": code, "docstatus": 1, "is_active": 1})
	)


def report():
	"""Har bir taomning BOM tannarxi, sotuv narxi va marjasi."""
	rows = frappe.get_all(
		"BOM",
		filters={"docstatus": 1, "is_active": 1},
		fields=["name", "item", "total_cost"],
		order_by="item",
	)
	if not rows:
		print("⏭️  BOM topilmadi — avval setup() ni ishga tushiring")
		return

	print(f"{'ТАОМ':<26}{'ТАННАРХ':>12}{'СОТУВ':>12}{'МАРЖА':>8}")
	print("-" * 58)
	for row in rows:
		price = flt(
			frappe.db.get_value(
				"Item Price", {"item_code": row.item, "selling": 1}, "price_list_rate"
			)
		)
		cost = flt(row.total_cost)
		margin = f"{(price - cost) / price * 100:.0f}%" if price else "—"
		print(f"{row.item[:25]:<26}{cost:>12,.0f}{price:>12,.0f}{margin:>8}")

	gaps = missing_recipes()
	print("-" * 58)
	print(f"Jami {len(rows)} ta BOM, retsepti yo'q: {len(gaps)} ta")
	if gaps:
		print(f"⚠️  {', '.join(gaps)}")
	return rows
