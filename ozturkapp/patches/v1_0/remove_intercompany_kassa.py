"""Kassa'ning kompaniyalararo qismi olib tashlandi — bazadagi qoldiqlarni tozalash.

Öztürk bitta kompaniya ("O'zturk Maksim Gorkiy") bo'lib ishlaydi. Kassa'dan
kompaniyalararo oqimlar (Sklad orqali to'lov, kompaniyalararo xarajat, boshqa
kompaniya ishchisi, ichki kontragent bilan hisob-kitob) va ularning
sozlamalari olib tashlandi:

* DocType: "Ozturk Settings" (Single) va "Ozturk Company Cash" (uning jadvali);
* Kassa maydonlari: via_sklad, payment_entry_receive, payment_entry_supplier.

Kassa maydonlari standart (JSON) maydon edi — migrate ularning DocField'ini
JSON bo'yicha olib tashlaydi, jadval ustunlari esa qoladi (zararsiz, Frappe
ustunlarni avtomatik o'chirmaydi). Bu patch faqat ular nomiga qolib ketgan
Custom Field / Property Setter'larni o'chiradi.

Idempotent: mavjud bo'lmagan narsa o'tkazib yuboriladi.
"""

import frappe

SETTINGS = "Ozturk Settings"
COMPANY_CASH = "Ozturk Company Cash"

KASSA_FIELDS = ("via_sklad", "payment_entry_receive", "payment_entry_supplier")


def execute():
	_drop_settings()
	_drop_kassa_field_leftovers()
	frappe.clear_cache()


def _drop_settings():
	"""Sozlama qiymatlari (Singles), jadval qatorlari va DocType'larni o'chiradi."""
	frappe.db.delete("Singles", {"doctype": SETTINGS})

	if frappe.db.table_exists(COMPANY_CASH):
		frappe.db.delete(COMPANY_CASH)

	for dt in (SETTINGS, COMPANY_CASH):
		frappe.db.delete("Property Setter", {"doc_type": dt})
		frappe.db.delete("Custom Field", {"dt": dt})
		if frappe.db.exists("DocType", dt):
			frappe.delete_doc("DocType", dt, force=True, ignore_permissions=True)

	# Migrate paytida `delete_doc("DocType")` jadvalni DROP qilmaydi
	frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `tab{COMPANY_CASH}`")


def _drop_kassa_field_leftovers():
	for fieldname in KASSA_FIELDS:
		for name in frappe.get_all(
			"Custom Field", filters={"dt": "Kassa", "fieldname": fieldname}, pluck="name"
		):
			frappe.delete_doc("Custom Field", name, force=True, ignore_permissions=True)
		frappe.db.delete("Property Setter", {"doc_type": "Kassa", "field_name": fieldname})
