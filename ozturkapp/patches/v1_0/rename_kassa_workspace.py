"""«Kassa» workspace'i «Kassa Paneli» deb qayta nomlandi.

Frappe'da workspace va doctype ro'yxati BIR XIL manzilda turadi: `/app/kassa`.
Router avval workspace'ni tekshiradi (`frappe/router.js: convert_to_standard_route`),
shuning uchun «Kassa» doctype ro'yxatiga kirib bo'lmasdi — workspace ochilardi.

Yangi workspace JSON'dan migrate paytida yaratiladi; bu patch eskisini o'chiradi
va unga bog'langan havolalarni yangisiga o'tkazadi.
"""

import frappe

OLD = "Kassa"
NEW = "Kassa Paneli"


def execute():
	if not frappe.db.exists("Workspace", OLD):
		return
	if frappe.db.get_value("Workspace", OLD, "module") != "Ozturkapp":
		return

	if frappe.db.exists("Workspace", NEW):
		frappe.db.set_value("User", {"default_workspace": OLD}, "default_workspace", NEW)
		frappe.db.set_value("Workspace", {"parent_page": OLD}, "parent_page", NEW)

	frappe.delete_doc("Workspace", OLD, force=True, ignore_permissions=True)
