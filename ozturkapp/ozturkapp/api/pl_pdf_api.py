import json
import os

import frappe
from frappe import _
from frappe.utils import flt, format_datetime, now_datetime
from frappe.utils.pdf import get_pdf


@frappe.whitelist()
def generate_pl_pdf(filters):
	"""Generate a branded Ozturk PDF of the Profit and Loss Statement report.

	Reuses ERPNext's own report engine for the numbers (so it automatically
	follows whatever Chart of Accounts / groupings the company actually has),
	and renders them into a Ozturk-styled HTML -> PDF, rather than a
	pixel-positioned static template.
	"""
	if isinstance(filters, str):
		filters = json.loads(filters)
	filters = frappe._dict(filters or {})

	if not filters.get("company"):
		frappe.throw(_("Аввал компанияни танланг"))

	filters.setdefault("filter_based_on", "Fiscal Year")
	filters.setdefault("periodicity", "Yearly")
	filters.setdefault("accumulated_values", 0)

	if filters.filter_based_on == "Date Range":
		if not filters.get("period_start_date") or not filters.get("period_end_date"):
			frappe.throw(_("Аввал сана оралиғини танланг"))
	else:
		if not filters.get("from_fiscal_year") or not filters.get("to_fiscal_year"):
			frappe.throw(_("Аввал молиявий йилни танланг"))

	from erpnext.accounts.report.profit_and_loss_statement.profit_and_loss_statement import execute

	columns, data = execute(filters)[:2]

	period_columns = [c for c in columns if c.get("fieldname") not in ("account", "currency", "total")]
	show_total_column = any(c.get("fieldname") == "total" for c in columns)

	company = filters.company
	currency = filters.get("presentation_currency") or frappe.get_cached_value(
		"Company", company, "default_currency"
	)

	rows = _build_rows(data, period_columns, show_total_column)
	layout = _table_layout(len(period_columns), show_total_column)

	context = {
		"company": company,
		"currency": currency,
		"period_label": _period_label(filters),
		"print_datetime": format_datetime(now_datetime(), "dd.MM.yyyy HH:mm"),
		"period_headers": [c["label"] for c in period_columns],
		"show_total_column": show_total_column,
		"rows": rows,
		"layout": layout,
	}

	html = _render_pdf_html(context)

	pdf_options = {
		"page-size": "A4",
		"orientation": "Landscape" if len(period_columns) > 1 else "Portrait",
		"margin-top": "12mm",
		"margin-right": "10mm",
		"margin-bottom": "12mm",
		"margin-left": "10mm",
		"encoding": "UTF-8",
	}

	try:
		pdf_content = get_pdf(html, options=pdf_options)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Ozturk PL PDF Error")
		frappe.throw(_("PDF яратишда хатолик юз берди. Логни текширинг."))

	safe_company = frappe.scrub(company)
	timestamp = now_datetime().strftime("%Y%m%d%H%M%S")
	filename = f"PL-{safe_company}-{timestamp}.pdf"

	site_path = frappe.get_site_path("private", "files", filename)
	with open(site_path, "wb") as f:
		f.write(pdf_content)

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"file_url": f"/private/files/{filename}",
			"is_private": 1,
			"folder": "Home/Attachments",
		}
	)
	file_doc.flags.ignore_permissions = True
	file_doc.insert()

	return {"file_url": file_doc.file_url, "file_name": filename}


def _clean_label(name):
	"""Strip the leading/trailing quote Frappe's report engine adds to
	synthetic rows (Total/Profit) so DataTable treats them as literal text —
	not meant to be shown, we render our own HTML so we don't need it."""
	name = name or ""
	if name.startswith("'") and name.endswith("'") and len(name) > 1:
		return name[1:-1]
	if name.startswith("'"):
		return name[1:]
	return name


def _fmt_currency(value):
	try:
		v = flt(value)
	except (TypeError, ValueError):
		v = 0
	if abs(v) < 0.5:
		return "–"
	sign = "-" if v < 0 else ""
	return f"{sign}{abs(v):,.0f}".replace(",", " ")


def _build_rows(data, period_columns, show_total_column):
	rows = []
	last_data_idx = None
	zebra_idx = 0
	for row in data:
		if not row:
			rows.append({"blank": True})
			continue

		raw_label = row.get("account_name") or row.get("account") or ""
		# Total/Profit rows come pre-wrapped in a leading quote by ERPNext
		# (a DataTable-only convention) — that's how we tell a synthetic
		# aggregate row apart from a real Account row structurally.
		is_synthetic = raw_label.startswith("'")
		label = _clean_label(raw_label)
		is_bold = not row.get("parent_account")
		is_group = bool(row.get("is_group"))
		indent = flt(row.get("indent") or 0)
		total_val = flt(row.get("total"))

		if is_bold and not is_synthetic:
			# Real root-level category header (e.g. "Income", "Expenses")
			style = "headline"
		elif is_bold and is_synthetic:
			# "Total Income (Credit)" / "Total Expense (Debit)" subtotal
			style = "subtotal"
		elif is_group:
			# Mid-level group header (e.g. "Direct Income", "Stock Expenses")
			style = "subheader"
		else:
			style = "leaf"
			zebra_idx += 1

		rows.append(
			{
				"blank": False,
				"label": label,
				"indent": indent,
				"style": style,
				"zebra_odd": bool(zebra_idx % 2) if style == "leaf" else False,
				"is_profit": False,
				"negative": total_val < 0,
				"period_values": [_fmt_currency(row.get(c["fieldname"])) for c in period_columns],
				"total": _fmt_currency(row.get("total")) if show_total_column else None,
			}
		)
		last_data_idx = len(rows) - 1

	# The net Profit/Loss row is always the last row execute() appends
	# (see profit_and_loss_statement.execute: data.append(net_profit_loss)) —
	# detect it structurally rather than matching translated label text.
	if last_data_idx is not None:
		rows[last_data_idx]["is_profit"] = True
		rows[last_data_idx]["style"] = "headline"

	return rows


def _table_layout(n_period_cols, show_total_column):
	"""Column widths / font size that scale down as more period columns
	(e.g. 12 monthly columns) are packed onto the page."""
	n_cols = n_period_cols + (1 if show_total_column else 0)

	if n_cols <= 1:
		account_width, font_size = 56, 10
	elif n_cols <= 4:
		account_width, font_size = 40, 9.5
	elif n_cols <= 8:
		account_width, font_size = 28, 8
	else:
		account_width, font_size = 20, 7

	period_width = (100 - account_width) / n_cols if n_cols else 0

	return {
		"account_width": account_width,
		"period_width": period_width,
		"font_size": font_size,
	}


def _period_label(filters):
	if filters.get("filter_based_on") == "Date Range":
		start = filters.get("period_start_date") or ""
		end = filters.get("period_end_date") or ""
		return f"{start} — {end}"

	from_fy = filters.get("from_fiscal_year") or ""
	to_fy = filters.get("to_fiscal_year") or ""
	if from_fy and to_fy and from_fy != to_fy:
		return f"{from_fy} — {to_fy}"
	return from_fy or to_fy or ""


def _render_pdf_html(context):
	template_path = os.path.join(os.path.dirname(__file__), "pl_pdf_template.html")
	with open(template_path, "r", encoding="utf-8") as f:
		template_str = f.read()
	return frappe.render_template(template_str, context)
