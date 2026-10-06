# Copyright (c) 2026, Ozturkapp
# License: MIT

"""
PL Hisoboti — Jazira formatidagi foyda-zarar hisoboti (bitta kompaniya).

Tuzilishi Jazira'ning «PL Calculation» filial blokidan olingan:

    Sotuvlar soni (чек) / ўртача чек
    Выручка                  (daromad schetlari bo'yicha)
    Себестоимость            (хом ашё / омбор тафовути / касса фарқи / брак)
    Маржинальная прибыль     + рентабельность %
    Операционные расходы     («Операционный» guruhi, schetma-schet)
    Операционная прибыль     + рентабельность %
    Адм Расход               («Адм» guruhi, schetma-schet)
    Бошқа харажатлар         (qolgan barcha Expense schetlar)
    Чистая прибыль           + рентабельность %

Jazira'dan farqlari
───────────────────
· Jazira schetlarni RAQAM bo'yicha (52001/52002/5119...) ajratadi. Ozturk
  hisoblar rejasida raqam yo'q, shuning uchun: tannarx — account_type
  bo'yicha, xarajat guruhlari — «Операционный» va «Адм» guruh schetlarining
  NOMI bo'yicha (ichidagi barcha darajadagi schetlar, lft/rgt orqali).
· Jazira «5200 Indirect Expenses» ostidagi guruhsiz schetlarni hisobotdan
  tashlab yuboradi. Bu yerda ular «Бошқа харажатлар» bo'limiga tushadi —
  GL'dagi har bir so'm hisobotda ko'rinadi, Чистая прибыль standart ERPNext
  P&L'idagi Net Profit bilan teng bo'ladi.
· Chek soni — POS Invoice'lar (qaytarishsiz). Ozturk sotuvi POS orqali
  bo'ladi; smena yopilganda ular bitta umumiy Sales Invoice'ga jamlanadi,
  shuning uchun Sales Invoice sanash chek sonini bermaydi.
"""

import frappe
from frappe import _
from frappe.utils import flt

from ozturkapp.ozturkapp.report.fin_utils import (
	build_period_list,
	divider,
	fk,
	get_company,
	period_key,
	row,
)

# Xarajat guruh schetlarining nomlari (kichik harfda, solishtirish shunday).
OP_GROUP_NAMES = ("операционный",)
ADM_GROUP_NAMES = ("адм", "административный", "административные расходы")

# Xarajat schetlari orasida turgan, lekin mohiyatan tannarx bo'lgan schetlar —
# NOMI bo'yicha topiladi (raqam yo'q). Hozir prod'da bunday schet yo'q;
# ochilsa qator o'zi paydo bo'ladi.
KASSA_NAME_PATTERNS = ("kassa farq", "касса фарқ", "касса фарк", "касса разниц")
BRAK_NAME_PATTERNS = ("brak", "брак")

# «Stock Adjustment» ichidagi ombor tafovuti hujjat turiga qarab ajratiladi.
# Material Issue (списание) — daromad keltirmasdan hisobdan chiqqan tovar,
# Jazira'dagi kabi «Брак» qatoriga olinadi.
BRAK_ADJ_KINDS = {"brak", "writeoff"}
ADJ_KIND_LABELS = [
	("recon", "инвентаризация фарқи (Stock Reconciliation)"),
	("mfg", "ишлаб чиқариш фарқи (Manufacture)"),
	("other", "бошқа омбор тафовути"),
]

COST_BUCKETS = [
	("cogs_raw", "Сырьевая себестоимость"),
	("cogs_adj", "Убыток от себестоимости"),
	("kassa", "Foyda/Zarar Kassa"),
	("brak", "Брак / списание (Material Issue)"),
]


# ─── Entry point ─────────────────────────────────────────────────────────────

def execute(filters=None):
	filters = frappe._dict(filters or {})
	company = get_company(filters)
	period_list = build_period_list(filters)

	from_date = str(period_list[0]["from_date"])
	to_date = str(period_list[-1]["to_date"])

	pdata = aggregate(
		period_list,
		fetch_gl(company, from_date, to_date),
		fetch_checks(company, from_date, to_date),
		get_group_ranges(company),
	)
	return get_columns(period_list), build_rows(period_list, pdata)


def get_columns(period_list):
	cols = [{"fieldname": "label", "label": _("Кўрсаткич"), "fieldtype": "Data", "width": 340}]
	for p in period_list:
		cols.append({
			"fieldname": fk(p["key"]),
			"label": p["label"],
			"fieldtype": "Currency",
			"options": "currency",
			"width": 150,
		})
	return cols


# ─── Ma'lumot yig'ish ────────────────────────────────────────────────────────

def fetch_gl(company, from_date, to_date):
	return frappe.db.sql(
		"""
		SELECT
			gle.posting_date,
			acc.name AS account,
			acc.account_name,
			acc.lft,
			acc.root_type,
			IFNULL(acc.account_type, '') AS account_type,
			CASE
				WHEN IFNULL(acc.account_type, '') != 'Stock Adjustment' THEN ''
				WHEN LOWER(IFNULL(se.stock_entry_type, '')) LIKE '%%brak%%'
				  OR LOWER(IFNULL(se.stock_entry_type, '')) LIKE '%%брак%%' THEN 'brak'
				WHEN gle.voucher_type = 'Stock Reconciliation' THEN 'recon'
				WHEN se.purpose = 'Material Issue' THEN 'writeoff'
				WHEN se.purpose = 'Manufacture' THEN 'mfg'
				ELSE 'other'
			END AS adj_kind,
			SUM(gle.debit) AS debit,
			SUM(gle.credit) AS credit
		FROM `tabGL Entry` gle
		JOIN `tabAccount` acc ON acc.name = gle.account
		LEFT JOIN `tabStock Entry` se
			ON se.name = gle.voucher_no AND gle.voucher_type = 'Stock Entry'
		WHERE gle.company = %(company)s
		  AND gle.is_cancelled = 0
		  AND gle.posting_date BETWEEN %(from_date)s AND %(to_date)s
		  -- Yil yopilganda Period Closing Voucher yillik natijani teskari
		  -- yozadi; chiqarilmasa hisobot nolga tushib qolardi.
		  AND gle.voucher_type != 'Period Closing Voucher'
		  AND acc.root_type IN ('Income', 'Expense')
		GROUP BY gle.posting_date, gle.account, adj_kind
		""",
		{"company": company, "from_date": from_date, "to_date": to_date},
		as_dict=True,
	)


def fetch_checks(company, from_date, to_date):
	"""Kunlik chek soni va cheklar summasi (qaytarishsiz POS Invoice)."""
	return frappe.db.sql(
		"""
		SELECT posting_date, COUNT(*) AS cnt, SUM(grand_total) AS amount
		FROM `tabPOS Invoice`
		WHERE company = %(company)s
		  AND docstatus = 1
		  AND is_return = 0
		  AND posting_date BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY posting_date
		""",
		{"company": company, "from_date": from_date, "to_date": to_date},
		as_dict=True,
	)


def get_group_ranges(company):
	"""{"op": [(lft, rgt)], "adm": [...]} — xarajat guruhlarining daraxt oralig'i."""
	groups = frappe.get_all(
		"Account",
		filters={"company": company, "root_type": "Expense", "is_group": 1},
		fields=["account_name", "lft", "rgt"],
	)
	ranges = {"op": [], "adm": []}
	for g in groups:
		name = (g.account_name or "").strip().lower()
		if name in OP_GROUP_NAMES:
			ranges["op"].append((g.lft, g.rgt))
		elif name in ADM_GROUP_NAMES:
			ranges["adm"].append((g.lft, g.rgt))
	return ranges


def _in_ranges(lft, ranges):
	return any(lo < lft < hi for lo, hi in ranges)


def _classify_cost_bucket(account_name):
	name = (account_name or "").strip().lower()
	if any(p in name for p in BRAK_NAME_PATTERNS):
		return "brak"
	if any(p in name for p in KASSA_NAME_PATTERNS):
		return "kassa"
	return None


# ─── Yig'ish ─────────────────────────────────────────────────────────────────

def _empty_period():
	return {
		"revenue": {},          # schet -> summa
		"cogs_raw": 0, "cogs_adj": 0, "kassa": 0, "brak": 0,
		"adj": {},              # ombor tafovuti sababi -> summa
		"op": {}, "adm": {}, "other": {},
		"checks": 0, "check_amount": 0,
	}


def aggregate(period_list, gl_rows, check_rows, group_ranges):
	pdata = {p["key"]: _empty_period() for p in period_list}
	labels = {}

	for r in gl_rows:
		pk = period_key(r.posting_date, period_list)
		if not pk:
			continue
		d = pdata[pk]
		labels[r.account] = r.account_name or r.account
		net = flt(r.debit) - flt(r.credit)

		if r.root_type == "Income":
			d["revenue"][r.account] = d["revenue"].get(r.account, 0) - net
		elif r.account_type == "Cost of Goods Sold":
			d["cogs_raw"] += net
		elif r.account_type == "Stock Adjustment":
			kind = r.adj_kind or "other"
			if kind in BRAK_ADJ_KINDS:
				d["brak"] += net
			else:
				d["cogs_adj"] += net
				d["adj"][kind] = d["adj"].get(kind, 0) + net
		elif _classify_cost_bucket(r.account_name):
			# Kassa farqi va brak — xarajat schetida tursa ham mohiyatan tannarx;
			# guruhdan OLDIN tekshiriladi, aks holda ikki marta sanalardi.
			d[_classify_cost_bucket(r.account_name)] += net
		else:
			if _in_ranges(r.lft, group_ranges["op"]):
				bucket = "op"
			elif _in_ranges(r.lft, group_ranges["adm"]):
				bucket = "adm"
			else:
				bucket = "other"
			d[bucket][r.account] = d[bucket].get(r.account, 0) + net

	for r in check_rows:
		pk = period_key(r.posting_date, period_list)
		if pk:
			pdata[pk]["checks"] += int(r.cnt or 0)
			pdata[pk]["check_amount"] += flt(r.amount)

	return {"periods": pdata, "labels": labels}


# ─── Hisoblash ───────────────────────────────────────────────────────────────

def revenue(d):
	return sum(d["revenue"].values())


def cogs(d):
	return flt(d["cogs_raw"]) + flt(d["cogs_adj"]) + flt(d["kassa"]) + flt(d["brak"])


def marginal(d):
	return revenue(d) - cogs(d)


def op_profit(d):
	return marginal(d) - sum(d["op"].values())


def net_profit(d):
	return op_profit(d) - sum(d["adm"].values()) - sum(d["other"].values())


# ─── Qatorlar ────────────────────────────────────────────────────────────────

def build_rows(period_list, pdata):
	periods = pdata["periods"]
	labels = pdata["labels"]
	fkeys = [fk(p["key"]) for p in period_list]

	def per_period(fn):
		return {fk(p["key"]): fn(periods[p["key"]]) for p in period_list}

	def pct(numer):
		return per_period(lambda d: numer(d) / revenue(d) * 100 if revenue(d) else 0)

	def all_zero(value_map):
		return all(abs(flt(v)) < 0.005 for v in value_map.values())

	def account_rows(bucket, indent, is_cost):
		"""Bo'lim ichidagi schetlar — davrlar yig'indisi bo'yicha kattadan kichikka."""
		totals = {}
		for p in period_list:
			for acc, amt in periods[p["key"]][bucket].items():
				totals[acc] = totals.get(acc, 0) + flt(amt)
		out = []
		for acc in sorted(totals, key=lambda a: -abs(totals[a])):
			vm = per_period(lambda d, a=acc: flt(d[bucket].get(a, 0)))
			if not all_zero(vm):
				out.append(row(labels.get(acc, acc), vm, "detail", indent, is_cost))
		return out

	rows = []

	# ── Cheklar ──────────────────────────────────────────────────────────────
	rows.append(row("Sotuvlar soni (чек)", per_period(lambda d: d["checks"]), "qty", 0))
	rows.append(row(
		"ўртача чек",
		per_period(lambda d: d["check_amount"] / d["checks"] if d["checks"] else 0),
		"ratio", 1,
	))
	rows.append(divider(fkeys))

	# ── Выручка ──────────────────────────────────────────────────────────────
	rows.append(row("Выручка от реализации продукции", per_period(revenue), "root", 0))
	rows.extend(account_rows("revenue", 1, False))

	# ── Себестоимость ────────────────────────────────────────────────────────
	rows.append(row("Себестоимость", per_period(cogs), "root", 0, is_cost=True))
	for bucket, blabel in COST_BUCKETS:
		bvm = per_period(lambda d, b=bucket: flt(d[b]))
		if all_zero(bvm):
			continue
		rows.append(row(blabel, bvm, "sub", 1, is_cost=True))
		if bucket == "cogs_adj":
			for kind, kind_label in ADJ_KIND_LABELS:
				kvm = per_period(lambda d, k=kind: flt(d["adj"].get(k, 0)))
				if not all_zero(kvm):
					rows.append(row(kind_label, kvm, "detail", 2, is_cost=True))

	rows.append(row("Маржинальная прибыль", per_period(marginal), "result", 0))
	rows.append(row("Рентабельность по маржинальному доходу, %", pct(marginal), "percent", 1))
	rows.append(divider(fkeys))

	# ── Операционные расходы ─────────────────────────────────────────────────
	rows.append(row(
		"Операционные расходы", per_period(lambda d: sum(d["op"].values())),
		"root", 0, is_cost=True))
	rows.extend(account_rows("op", 1, True))

	rows.append(row("Операционная прибыль", per_period(op_profit), "result", 0))
	rows.append(row("Рентабельность по операционной прибыли, %", pct(op_profit), "percent", 1))
	rows.append(divider(fkeys))

	# ── Адм Расход ───────────────────────────────────────────────────────────
	rows.append(row(
		"Адм Расход", per_period(lambda d: sum(d["adm"].values())),
		"root", 0, is_cost=True))
	rows.extend(account_rows("adm", 1, True))

	# ── Бошқа харажатлар (guruhga kirmagan Expense schetlar) ─────────────────
	other_vm = per_period(lambda d: sum(d["other"].values()))
	if not all_zero(other_vm):
		rows.append(row("Бошқа харажатлар", other_vm, "root", 0, is_cost=True))
		rows.extend(account_rows("other", 1, True))
	rows.append(divider(fkeys))

	rows.append(row("Чистая прибыль", per_period(net_profit), "result", 0))
	rows.append(row("Рентабельность по чистой прибыли, %", pct(net_profit), "percent", 1))

	return rows
