# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Sotuv dashboardi — qaysi mahsulotdan qancha sotildi va qancha chegirma berildi.

MANBA — POS INVOICE
===================
Restoran sotuvi POS Invoice'da yashaydi. Smena yopilganda ERPNext ularni
bitta Sales Invoice'ga birlashtiradi (status "Consolidated") — Sales
Invoice'ni ham qo'shsak har bir sotuv IKKI MARTA sanalardi. Shuning uchun
faqat topshirilgan (`docstatus = 1`) POS Invoice olinadi, holati (Paid yoki
Consolidated) muhim emas.

Qaytarishlar (`is_return = 1`) manfiy miqdor va summa bilan keladi — ular
yig'indiga qo'shilib sotuvni avtomatik kamaytiradi (sof sotuv).

CHEGIRMA IKKI XIL
=================
    qator chegirmasi   `discount_amount` — BIR DONA uchun (narx ro'yxati - narx)
    chek chegirmasi    `distributed_discount_amount` — kassir chekka bergan
                       chegirma (utils/discounts.py), ERPNext uni qatorlarga
                       summa ulushiga qarab taqsimlaydi — butun qator uchun

    chegirma  = discount_amount * qty + distributed_discount_amount
    sof summa = amount - distributed_discount_amount   (mijoz to'lagan)
    yalpi     = sof summa + chegirma                   (chegirmasiz narxda)

Xizmat haqi (soliq qatori) va choychaqa mahsulotga tegishli emas — ular
bu hisobotga KIRMAYDI, shuning uchun jami chekning `grand_total` idan kam.
"""

import frappe
from frappe import _
from frappe.utils import add_days, date_diff, flt, getdate

ALLOWED_ROLES = ("System Manager", "URY Manager", "Accounts Manager", "Accounts User")

# Chegirmani yaxlitlash qoldig'idan ajratish chegarasi (so'm).
EPSILON = 0.5


def _check_access():
	if not set(frappe.get_roles()).intersection(ALLOWED_ROLES):
		frappe.throw(_("Sotuv dashboardini ko'rishga ruxsat yo'q"), frappe.PermissionError)


def _conditions(filters):
	conds = [
		"pi.docstatus = 1",
		"pi.posting_date BETWEEN %(from_date)s AND %(to_date)s",
	]
	for field in ("company", "branch"):
		if filters.get(field):
			conds.append(f"pi.{field} = %({field})s")
	if filters.get("item_group"):
		conds.append("i.item_group = %(item_group)s")
	return " AND ".join(conds)


def _parse_filters(from_date, to_date, company, branch, item_group):
	if not from_date or not to_date:
		frappe.throw(_("Sana oralig'i majburiy"))
	from_date, to_date = getdate(from_date), getdate(to_date)
	if from_date > to_date:
		frappe.throw(_("Boshlanish sanasi tugash sanasidan katta bo'lishi mumkin emas"))
	return {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"branch": branch,
		"item_group": item_group,
	}


# Qator darajasidagi ifodalar — bir joyda, hamma so'rov bir xil hisoblasin.
DISCOUNT_SQL = "(IFNULL(i.discount_amount, 0) * i.qty + IFNULL(i.distributed_discount_amount, 0))"
NET_SQL = "(i.amount - IFNULL(i.distributed_discount_amount, 0))"


@frappe.whitelist()
def get_dashboard(from_date=None, to_date=None, company=None, branch=None, item_group=None):
	_check_access()
	filters = _parse_filters(from_date, to_date, company, branch, item_group)
	where = _conditions(filters)

	items = frappe.db.sql(
		f"""
		SELECT
			i.item_code,
			MAX(i.item_name) AS item_name,
			MAX(i.item_group) AS item_group,
			MAX(i.uom) AS uom,
			SUM(i.qty) AS qty,
			SUM({NET_SQL}) AS net_amount,
			SUM({DISCOUNT_SQL}) AS discount,
			SUM(CASE WHEN ABS({DISCOUNT_SQL}) > {EPSILON} THEN i.qty ELSE 0 END) AS discounted_qty,
			COUNT(DISTINCT CASE WHEN pi.is_return = 0 THEN pi.name END) AS invoices
		FROM `tabPOS Invoice Item` i
		JOIN `tabPOS Invoice` pi ON pi.name = i.parent
		WHERE {where}
		GROUP BY i.item_code
		ORDER BY net_amount DESC
		""",
		filters,
		as_dict=True,
	)

	for row in items:
		row.gross_amount = flt(row.net_amount) + flt(row.discount)
		row.avg_price = flt(row.net_amount) / flt(row.qty) if flt(row.qty) else 0

	summary = frappe.db.sql(
		f"""
		SELECT
			COUNT(DISTINCT CASE WHEN pi.is_return = 0 THEN pi.name END) AS invoices,
			COUNT(DISTINCT CASE WHEN pi.is_return = 0 AND ABS({DISCOUNT_SQL}) > {EPSILON}
				THEN pi.name END) AS discounted_invoices,
			COUNT(DISTINCT CASE WHEN pi.is_return = 1 THEN pi.name END) AS returns,
			SUM(CASE WHEN pi.is_return = 1 THEN -{NET_SQL} ELSE 0 END) AS return_amount
		FROM `tabPOS Invoice Item` i
		JOIN `tabPOS Invoice` pi ON pi.name = i.parent
		WHERE {where}
		""",
		filters,
		as_dict=True,
	)[0]

	totals = {
		"qty": sum(flt(r.qty) for r in items),
		"net_amount": sum(flt(r.net_amount) for r in items),
		"discount": sum(flt(r.discount) for r in items),
		"discounted_qty": sum(flt(r.discounted_qty) for r in items),
		"gross_amount": sum(flt(r.gross_amount) for r in items),
		"items": len(items),
		"invoices": summary.invoices or 0,
		"discounted_invoices": summary.discounted_invoices or 0,
		"returns": summary.returns or 0,
		"return_amount": flt(summary.return_amount),
	}
	totals["avg_check"] = totals["net_amount"] / totals["invoices"] if totals["invoices"] else 0
	totals["discount_percent"] = (
		totals["discount"] / totals["gross_amount"] * 100 if totals["gross_amount"] else 0
	)

	return {
		"items": items,
		"totals": totals,
		"daily": _daily(filters, where),
		"currency": _currency(filters.get("company")),
	}


def _daily(filters, where):
	rows = frappe.db.sql(
		f"""
		SELECT pi.posting_date AS date, SUM({NET_SQL}) AS net_amount, SUM({DISCOUNT_SQL}) AS discount
		FROM `tabPOS Invoice Item` i
		JOIN `tabPOS Invoice` pi ON pi.name = i.parent
		WHERE {where}
		GROUP BY pi.posting_date
		""",
		filters,
		as_dict=True,
	)
	by_date = {getdate(r.date): r for r in rows}

	# Sotuvsiz kunlar ham grafikda nol bo'lib ko'rinsin — bo'shliq yo'qolmasin.
	days = []
	for n in range(min(date_diff(filters["to_date"], filters["from_date"]), 366) + 1):
		day = add_days(filters["from_date"], n)
		row = by_date.get(getdate(day))
		days.append({
			"date": str(day),
			"net_amount": flt(row.net_amount) if row else 0,
			"discount": flt(row.discount) if row else 0,
		})
	return days


def _currency(company):
	company = company or frappe.defaults.get_user_default("Company")
	if company:
		return frappe.get_cached_value("Company", company, "default_currency")
	return frappe.db.get_default("currency")



# ─────────────────────────── Excel ───────────────────────────

# (maydon, sarlavha, kenglik, format) — dashboard jadvalidagi tartib bilan bir xil.
EXCEL_COLUMNS = (
	("item_code", "Kod", 12, None),
	("item_name", "Mahsulot", 34, None),
	("item_group", "Guruh", 22, None),
	("qty", "Sotilgan soni", 14, "#,##0.##"),
	("avg_price", "O'rtacha narx", 16, "#,##0"),
	("gross_amount", "Yalpi summa", 18, "#,##0"),
	("discounted_qty", "Chegirmada sotilgan", 20, "#,##0.##"),
	("discount", "Umumiy chegirma", 18, "#,##0"),
	("net_amount", "Sof summa", 18, "#,##0"),
)
SUM_FIELDS = ("qty", "gross_amount", "discounted_qty", "discount", "net_amount")
SORTABLE = {c[0] for c in EXCEL_COLUMNS}


@frappe.whitelist()
def download_excel(
	from_date=None, to_date=None, company=None, branch=None, item_group=None,
	search=None, sort_field=None, sort_dir=None,
):
	"""Dashboard jadvalini .xlsx qilib beradi — ekrandagi qidiruv va saralash bilan."""
	from io import BytesIO

	from openpyxl import Workbook
	from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

	data = get_dashboard(from_date, to_date, company, branch, item_group)
	rows = data["items"]

	search = (search or "").strip().lower()
	if search:
		rows = [
			r for r in rows
			if any(search in (r.get(f) or "").lower() for f in ("item_name", "item_code", "item_group"))
		]
	if sort_field in SORTABLE:
		is_text = sort_field in ("item_code", "item_name", "item_group")
		rows.sort(
			key=lambda r: (r.get(sort_field) or "").lower() if is_text else flt(r.get(sort_field)),
			reverse=sort_dir != "asc",
		)

	wb = Workbook()
	ws = wb.active
	ws.title = "Sotuv"
	last_col = len(EXCEL_COLUMNS)
	bold = Font(bold=True)
	thin = Side(style="thin", color="D0D5DD")
	border = Border(top=thin, bottom=thin, left=thin, right=thin)
	head_fill = PatternFill("solid", fgColor="2490EF")
	total_fill = PatternFill("solid", fgColor="EEF2F6")

	# Sarlavha va davr
	ws.cell(row=1, column=1, value="Mahsulotlar bo'yicha sotuv").font = Font(bold=True, size=14)
	period = f"{frappe.format(from_date, 'Date')} — {frappe.format(to_date, 'Date')}"
	extra = ", ".join(v for v in (branch, item_group) if v)
	ws.cell(row=2, column=1, value=f"Davr: {period}" + (f"  |  {extra}" if extra else "")).font = Font(color="6B7785")
	ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
	ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=last_col)

	header_row = 4
	for col, (_f, label, width, _fmt) in enumerate(EXCEL_COLUMNS, 1):
		c = ws.cell(row=header_row, column=col, value=label)
		c.font = Font(bold=True, color="FFFFFF")
		c.fill = head_fill
		c.border = border
		c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
		ws.column_dimensions[c.column_letter].width = width
	ws.row_dimensions[header_row].height = 30

	row_no = header_row
	for r in rows:
		row_no += 1
		for col, (field, _l, _w, fmt) in enumerate(EXCEL_COLUMNS, 1):
			value = r.get(field)
			c = ws.cell(row=row_no, column=col, value=flt(value, 2) if fmt else (value or ""))
			c.border = border
			if fmt:
				c.number_format = fmt

	# Jami qatori
	row_no += 1
	ws.cell(row=row_no, column=1, value=f"Jami ({len(rows)})")
	for col, (field, _l, _w, fmt) in enumerate(EXCEL_COLUMNS, 1):
		c = ws.cell(row=row_no, column=col)
		if field in SUM_FIELDS:
			c.value = flt(sum(flt(r.get(field)) for r in rows), 2)
			c.number_format = fmt
		c.font = bold
		c.fill = total_fill
		c.border = border

	ws.freeze_panes = ws.cell(row=header_row + 1, column=3)
	ws.auto_filter.ref = f"A{header_row}:{ws.cell(row=header_row, column=last_col).column_letter}{row_no - 1}"

	out = BytesIO()
	wb.save(out)
	frappe.response["filename"] = f"sotuv_{getdate(from_date)}_{getdate(to_date)}.xlsx"
	frappe.response["filecontent"] = out.getvalue()
	frappe.response["type"] = "binary"
