# Copyright (c) 2026, Ozturkapp
# License: MIT

"""PL Hisoboti va Balans Hisoboti uchun umumiy PDF.

Dizayn Jazira'ning report/pl_pdf.py modulidan olingan (barcha qatorlar
yoyilgan holda, daraja chiziqlari, sarlavha har sahifada). Farqi: Jazira
WeasyPrint ishlatadi, Ozturk serverida esa u ishlamaydi (libpango yo'q),
shuning uchun Frappe'ning get_pdf (wkhtmltopdf) ishlatiladi — u flex va
@page footer'ni bilmaydi, sahifa raqami wkhtmltopdf opsiyasi orqali.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt
from frappe.utils.pdf import get_pdf

C_HEADER_BG = "#0f2942"
C_SECTION_BG = "#c0392b"
C_RESULT_BG = "#a3120f"
C_SUB_BG = "#fdebd3"
C_NEG = "#c0392b"


def _num(v):
	n = round(flt(v))
	txt = f"{abs(n):,.0f}".replace(",", " ")
	return f"({txt})" if n < 0 else txt


def _cell_text(r, value):
	rt = r.get("row_type")
	if rt == "percent":
		return f"{round(flt(value))}%"
	return _num(value)


def _row_html(r, fkeys):
	rt = r.get("row_type") or "detail"
	if rt == "divider":
		return f'<tr class="r-divider"><td colspan="{len(fkeys) + 1}"></td></tr>'

	level = int(r.get("indent") or 0)
	label = frappe.utils.escape_html(r.get("label") or "")
	cells = [f'<td class="lbl" style="padding-left:{6 + level * 12}pt">{label}</td>']
	for key in fkeys:
		v = r.get(key)
		if v is None or v == "":
			cells.append('<td class="val"></td>')
			continue
		txt = _cell_text(r, v)
		neg = " neg" if txt.startswith("(") else ""
		cells.append(f'<td class="val{neg}">{txt}</td>')
	return f'<tr class="r-{rt}">{"".join(cells)}</tr>'


def build_html(title, subtitle, columns, data, meta_lines):
	period_cols = columns[1:]
	fkeys = [c["fieldname"] for c in period_cols]
	n = max(len(fkeys), 1)
	font = 9.5 if n <= 4 else (8.5 if n <= 8 else 7.5)
	label_pct = 40 if n <= 2 else (30 if n <= 6 else 22)
	value_pct = (100 - label_pct) / n

	head = "".join(
		f'<th class="val">{frappe.utils.escape_html(str(c["label"]))}</th>' for c in period_cols)
	cols = f'<col style="width:{label_pct}%">' + f'<col style="width:{value_pct:.2f}%">' * len(fkeys)
	body = "".join(_row_html(r, fkeys) for r in data)
	meta = "<br>".join(frappe.utils.escape_html(m) for m in meta_lines)

	return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {{ font-family: 'DejaVu Sans', Arial, sans-serif; font-size: {font}pt; color: #111; }}
  .hdr {{ width: 100%; background: {C_HEADER_BG}; color: #fff; margin-bottom: 8pt;
          border-collapse: collapse; }}
  .hdr td {{ padding: 9pt 12pt; }}
  .hdr .kick {{ font-size: {font - 2}pt; letter-spacing: 1.4pt; text-transform: uppercase; color: #9fb3c8; }}
  .hdr .ttl {{ font-size: {font + 5}pt; font-weight: bold; }}
  .hdr .rgt {{ text-align: right; font-size: {font - 1}pt; color: #d5dee8; }}

  table.rep {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
  thead {{ display: table-header-group; }}
  tr {{ page-break-inside: avoid; }}
  thead th {{ background: #eef1f4; color: #0f2942; font-weight: bold; text-align: right;
              padding: 4pt 6pt; border-bottom: 1.5pt solid #cfd6dd; }}
  thead th.lbl {{ text-align: left; }}
  td {{ padding: 3pt 6pt; }}
  td.lbl {{ text-align: left; overflow: hidden; white-space: nowrap; }}
  td.val {{ text-align: right; white-space: nowrap; }}
  td.neg {{ color: {C_NEG}; }}

  tr.r-root td {{ background: {C_SECTION_BG}; color: #fff; font-weight: bold; text-transform: uppercase; }}
  tr.r-result td {{ background: {C_RESULT_BG}; color: #fff; font-weight: bold; }}
  tr.r-root td.neg, tr.r-result td.neg {{ color: #ffd9d4; }}
  tr.r-sub td, tr.r-qty td {{ background: {C_SUB_BG}; font-weight: bold; }}
  tr.r-detail td {{ color: #333; font-size: {font - .8}pt; border-bottom: .4pt solid #eef1f5; }}
  tr.r-percent td, tr.r-ratio td {{ color: #64748b; font-style: italic; font-size: {font - 1}pt;
                                    border-bottom: .4pt solid #eef1f5; }}
  tr.r-divider td {{ padding: 3pt 0; }}
</style></head>
<body>
  <table class="hdr"><tr>
    <td><div class="kick">{frappe.utils.escape_html(subtitle)}</div>
        <div class="ttl">{frappe.utils.escape_html(title)}</div></td>
    <td class="rgt">{meta}</td>
  </tr></table>
  <table class="rep">
    <colgroup>{cols}</colgroup>
    <thead><tr><th class="lbl">Кўрсаткич</th>{head}</tr></thead>
    <tbody>{body}</tbody>
  </table>
</body></html>"""


def check_report_permission(report_name):
	"""PDF endpoint'i hisobotning O'ZI bilan bir xil huquqni talab qilsin —
	whitelist faqat «tizimga kirganmi» deb tekshiradi."""
	if not frappe.db.exists("Report", report_name):
		frappe.throw(_("Ҳисобот топилмади: {0}").format(report_name))
	if not frappe.get_doc("Report", report_name).is_permitted():
		raise frappe.PermissionError(
			_("Сизда «{0}» ҳисоботини кўриш ҳуқуқи йўқ").format(report_name))


def send(filters, execute_fn, report_name, subtitle, filename_prefix):
	"""Hisobotni PDF qilib brauzerga yuboradi (serverda saqlanmaydi)."""
	check_report_permission(report_name)

	if isinstance(filters, str):
		filters = json.loads(filters)
	filters = frappe._dict(filters or {})

	columns, data = execute_fn(filters)
	company = filters.get("company") or ""
	meta = [
		f"{filters.get('from_date') or ''} — {filters.get('to_date') or ''}",
		str(filters.get("periodicity") or ""),
	]
	html = build_html(company, subtitle, columns, data, meta)

	n = len(columns) - 1
	pdf = get_pdf(html, options={
		"page-size": "A4" if n <= 8 else "A3",
		"orientation": "Landscape",
		"margin-top": "10mm",
		"margin-bottom": "12mm",
		"margin-left": "9mm",
		"margin-right": "9mm",
		"footer-right": "[page] / [topage]",
		"footer-font-size": "7",
		"encoding": "UTF-8",
	})

	frappe.local.response.filename = "%s_%s_%s.pdf" % (
		filename_prefix,
		str(filters.get("from_date") or "")[:10],
		str(filters.get("to_date") or "")[:10],
	)
	frappe.local.response.filecontent = pdf
	frappe.local.response.type = "download"
