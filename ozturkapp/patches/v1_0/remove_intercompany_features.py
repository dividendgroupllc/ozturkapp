"""Kompaniyalararo (inter-company) funksiyalar olib tashlandi.

Öztürk bitta kompaniya ("O'zturk Maksim Gorkiy") bo'lib ishlaydi — Sklad va
guruh kompaniyasi yo'q. Shu sababli ilovadan quyidagilar o'chirildi va bu
patch ularning bazadagi qoldiqlarini tozalaydi:

* Report: "PL Obshi", "Intercompany Sverka";
* DocType: "Expense Allocation" (+ 3 ta jadval), "Branch Stock Transfer"
  (+ "Branch Stock Transfer Item");
* Custom Field: Journal Entry.custom_expense_allocation.

Branch Stock Transfer: faqat barcha yozuvlar bekor qilingan (yoki qoralama)
bo'lsa o'chiriladi. Hali tasdiqlangan (docstatus=1) BST bo'lsa — u
o'chirilmaydi, xabar chiqariladi (avval bekor qilib, patch'ni `run-patch
--force` bilan qayta ishga tushirish kerak). Idempotent: mavjud bo'lmagan narsa o'tkazib yuboriladi.
"""

import frappe

REPORTS = ("PL Obshi", "Intercompany Sverka")

EXPENSE_ALLOCATION = "Expense Allocation"
EXPENSE_ALLOCATION_CHILDREN = (
	"Expense Allocation Account",
	"Expense Allocation Branch",
	"Expense Allocation Item",
)

BRANCH_STOCK_TRANSFER = "Branch Stock Transfer"
BRANCH_STOCK_TRANSFER_CHILDREN = ("Branch Stock Transfer Item",)

CUSTOM_FIELDS = (("Journal Entry", "custom_expense_allocation"),)


def execute():
	for report in REPORTS:
		if frappe.db.exists("Report", report):
			frappe.delete_doc("Report", report, force=True, ignore_permissions=True)

	for dt, fieldname in CUSTOM_FIELDS:
		name = f"{dt}-{fieldname}"
		if frappe.db.exists("Custom Field", name):
			frappe.delete_doc("Custom Field", name, force=True, ignore_permissions=True)
		if frappe.db.has_column(dt, fieldname):
			frappe.db.sql_ddl(f"ALTER TABLE `tab{dt}` DROP COLUMN `{fieldname}`")

	_drop_doctype(EXPENSE_ALLOCATION, EXPENSE_ALLOCATION_CHILDREN)

	submitted = _submitted(BRANCH_STOCK_TRANSFER)
	if submitted:
		print(
			f"remove_intercompany_features: '{BRANCH_STOCK_TRANSFER}' O'CHIRILMADI — "
			f"tasdiqlangan yozuvlar bor: {', '.join(submitted)}. "
			"Ularni bekor qiling va qayta ishga tushiring: bench --site <sayt> run-patch "
			"ozturkapp.patches.v1_0.remove_intercompany_features --force"
		)
	else:
		_drop_doctype(BRANCH_STOCK_TRANSFER, BRANCH_STOCK_TRANSFER_CHILDREN)

	frappe.clear_cache()


def _submitted(doctype):
	if not frappe.db.table_exists(doctype):
		return []
	# Xom SQL — DocType meta allaqachon o'chirilgan bo'lishi mumkin
	return frappe.db.sql_list(f"SELECT name FROM `tab{doctype}` WHERE docstatus = 1")


def _drop_doctype(doctype, children):
	"""Yozuvlarni (jadval qatorlari bilan), DocType'larni va jadvallarni o'chiradi.

	Migrate paytida `delete_doc("DocType")` jadvalni DROP qilmaydi — shuning
	uchun bu yerda alohida."""
	for dt in (doctype, *children):
		if frappe.db.table_exists(dt):
			frappe.db.delete(dt)

	for dt in (doctype, *children):
		if frappe.db.exists("DocType", dt):
			frappe.delete_doc("DocType", dt, force=True, ignore_permissions=True)
		frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `tab{dt}`")
