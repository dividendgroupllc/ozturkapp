"""Buyurtma bo'yicha avto ishlab chiqariladigan yarim tayyor mahsulotlarni belgilash.

Bir marta ishlaydi: keyin belgini Item formasida qo'lda o'zgartirish mumkin
(after_migrate'da bo'lsa, har migrate qo'lda olib tashlangan belgini qaytarardi).
Mahsulot nomi bo'yicha qidiriladi — saytda bo'lmasa o'tkazib yuboriladi.
Batafsil: utils/auto_manufacture.py
"""

import frappe

ITEM_NAMES = (
	"Non xamiri (tayyor)",
	"Suxarik",
	"Chechevitsa sho'rva (tayyor)",
	"Ezogelin sho'rva (tayyor)",
	"Guruch (pishirilgan)",
	"Iskandar sous",
	"BARDAK Choy Choynak",
	"Oq sous",
)


def execute():
	# post_model_sync patch'lari after_migrate'dan OLDIN ishlaydi — maydon
	# hali yaratilmagan bo'lishi mumkin.
	from ozturkapp.ozturkapp.setup.custom_fields import create_fields

	create_fields()

	for name in frappe.get_all("Item", filters={"item_name": ("in", ITEM_NAMES)}, pluck="name"):
		frappe.db.set_value("Item", name, "custom_auto_manufacture", 1, update_modified=False)
