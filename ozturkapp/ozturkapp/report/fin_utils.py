# Copyright (c) 2026, Ozturkapp
# License: MIT

"""PL Hisoboti va Balans Hisoboti uchun umumiy yordamchilar.

Davrlarga bo'lish dvigateli Jazira'ning pl_hisoboti reportidan olingan —
ikkala hisobot bir xil ustunlarni (oy/chorak/yarim yil/yil) ko'rsatishi
uchun manba bitta.
"""

from datetime import date, timedelta

import frappe
from frappe import _
from frappe.utils import getdate

PERIODICITIES = ("Yearly", "Half-Yearly", "Quarterly", "Monthly")


def get_company(filters):
	company = filters.get("company") or frappe.defaults.get_user_default("Company")
	if not company:
		frappe.throw(_("Аввал компанияни танланг"))
	return company


def build_period_list(filters):
	"""[{key, label, from_date, to_date}] — sana oralig'i davrlarga bo'linadi.

	Birinchi va oxirgi davr filtrdagi sanaga qirqiladi (masalan 15-sentabrdan
	boshlansa, sentabr ustuni 15-30 ni ko'rsatadi)."""
	if not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("Аввал 'Дан' ва 'Гача' санасини танланг"))

	from_date = getdate(filters.get("from_date"))
	to_date = getdate(filters.get("to_date"))
	if from_date > to_date:
		frappe.throw(_("'Дан' санаси 'Гача' санасидан кейин бўлмаслиги керак"))

	periodicity = filters.get("periodicity") or "Monthly"
	if periodicity not in PERIODICITIES:
		periodicity = "Monthly"

	periods = []
	current = from_date
	while current <= to_date:
		y, m = current.year, current.month

		if periodicity == "Monthly":
			p_start = date(y, m, 1)
			next_cur = date(y, m + 1, 1) if m < 12 else date(y + 1, 1, 1)
			label = p_start.strftime("%b %Y")
		elif periodicity == "Quarterly":
			q = (m - 1) // 3
			p_start = date(y, q * 3 + 1, 1)
			next_cur = date(y, q * 3 + 4, 1) if q < 3 else date(y + 1, 1, 1)
			label = f"Q{q + 1} {y}"
		elif periodicity == "Half-Yearly":
			if m <= 6:
				p_start, next_cur, label = date(y, 1, 1), date(y, 7, 1), f"H1 {y}"
			else:
				p_start, next_cur, label = date(y, 7, 1), date(y + 1, 1, 1), f"H2 {y}"
		else:
			p_start, next_cur, label = date(y, 1, 1), date(y + 1, 1, 1), str(y)

		p_end = next_cur - timedelta(days=1)
		periods.append({
			"key": label,
			"label": label,
			"from_date": max(p_start, from_date),
			"to_date": min(p_end, to_date),
		})
		current = next_cur

	return periods


def period_key(posting_date, period_list):
	posting_date = getdate(posting_date)
	for p in period_list:
		if p["from_date"] <= posting_date <= p["to_date"]:
			return p["key"]
	return None


def fk(key):
	"""Davr kalitidan ustun fieldname'i."""
	return "v_" + key.replace(" ", "_").replace("-", "_")


def row(label, value_map=None, row_type="detail", indent=0, is_cost=False):
	"""Hisobot qatori. row_type: root / sub / detail / result / percent /
	ratio / qty / divider — JS formatter va PDF shu bo'yicha bezaydi."""
	r = {
		"label": label,
		"row_type": row_type,
		"indent": indent,
		"is_cost": 1 if is_cost else 0,
	}
	r.update(value_map or {})
	return r


def divider(fkeys):
	r = row("", row_type="divider")
	for key in fkeys:
		r[key] = None
	return r
