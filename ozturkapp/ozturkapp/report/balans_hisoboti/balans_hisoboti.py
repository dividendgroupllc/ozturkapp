# Copyright (c) 2026, Ozturkapp
# License: MIT

"""
Balans Hisoboti — Jazira formatidagi balans (bitta kompaniya).

Tuzilishi Jazira'ning «Balance Obshi» / «Balance Calculation» hisobotlaridan:
har ustun — davr OXIRI holatiga qoldiq, qatorlar tabiat bo'yicha:

    АКТИВЫ    Основные средства / Запасы / Денежные средства /
              Дебиторская задолженность / Прочие активы
    ПАССИВЫ   Капитал (капитал schetlari, накопленная прибыль, давр
              фойдаси, дивидендлар) / Кредиторская задолженность
    РАЗНИЦА   (nazorat, doim 0) va Рабочий капитал

Kontragent qoldiqlari HAR PARTIYA bo'yicha nettolanadi va ISHORASIGA
qarab tomonga qo'yiladi: debet qoldiq — aktiv (Дебиторка), kredit —
passiv (Кредиторка). Standart Balance Sheet esa schet qoldig'ini umumiy
nettolaydi — bir xodimning avansi boshqasining qarzi bilan yopilib,
ikkalasi ham ko'rinmay qoladi.

Jazira'dan farqlari: kompaniya kesimi o'rniga schet (kassa, ombor) va
kontragent (har bir xodim/ta'minotchi/ta'sischi) kesimi; dividend schetlari
raqam (3200/3201) o'rniga nom bo'yicha («Divident <Ism>» — setup/
dividend_setup.py, «Dividends Paid»).

«РАЗНИЦА» doim 0 bo'lishi KAFOLATLANGAN: har bir GL yozuvi aynan bitta
katakka tushadi, ikki yoqlama yozuvda esa jami debet = jami kredit.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

from ozturkapp.ozturkapp.report.fin_utils import (
	build_period_list,
	divider,
	fk,
	get_company,
	row,
)

FIXED_TYPES = {"Fixed Asset", "Accumulated Depreciation", "Capital Work in Progress"}
DIVIDEND_PREFIXES = ("divident", "dividend")

# Kontragent turi -> qator yorlig'i. Har klass IKKALA tomonda ham uchrashi
# mumkin (masalan ta'minotchiga ortiqcha to'langan oy — aktivda).
PARTY_CLASSES = {
	"Customer": "cust",
	"Supplier": "supp",
	"Employee": "emp",
	"Shareholder": "share",
}
PARTY_AR_LABELS = {
	"cust": "Клиент",
	"emp": "Сотрудник",
	"supp": "Поставщик (аванслар)",
	"share": "Учредитель",
	"other": "Прочие лицо",
	"nopar": "Бошқа (партиясиз)",
}
PARTY_AP_LABELS = {
	"supp": "Поставщик",
	"emp": "Сотрудник",
	"cust": "Клиент (аванслар)",
	"share": "Учредитель",
	"other": "Прочие лицо",
	"nopar": "Бошқа (партиясиз)",
}
PARTY_ORDER = ["supp", "cust", "emp", "share", "other", "nopar"]


def execute(filters=None):
	filters = frappe._dict(filters or {})
	company = get_company(filters)
	period_list = build_period_list(filters)
	data = compute(period_list, company)
	return get_columns(period_list), build_rows(period_list, data)


def get_columns(period_list):
	cols = [{"fieldname": "label", "label": _("Кўрсаткич"), "fieldtype": "Data", "width": 340}]
	for p in period_list:
		cols.append({
			"fieldname": fk(p["key"]),
			"label": str(p["to_date"]),  # balans — davr OXIRI holatiga
			"fieldtype": "Currency",
			"options": "currency",
			"width": 150,
		})
	return cols


# ─── Ma'lumot yig'ish ────────────────────────────────────────────────────────

def fetch_gl(company, max_date):
	return frappe.db.sql(
		"""
		SELECT
			g.posting_date,
			a.name AS account,
			a.account_name,
			a.root_type,
			IFNULL(a.account_type, '') AS account_type,
			IFNULL(g.party_type, '') AS party_type,
			IFNULL(g.party, '') AS party,
			SUM(g.debit - g.credit) AS net
		FROM `tabGL Entry` g
		INNER JOIN `tabAccount` a ON a.name = g.account
		WHERE g.is_cancelled = 0
		  AND g.company = %(company)s
		  AND g.posting_date <= %(max_date)s
		  AND g.voucher_type != 'Period Closing Voucher'
		GROUP BY g.posting_date, g.account, g.party_type, g.party
		""",
		{"company": company, "max_date": max_date},
		as_dict=True,
	)


def party_display_names(party_keys):
	"""(party_type, party) -> ko'rinadigan nom (xodim ismi, ta'sischi nomi...)."""
	title_fields = {
		"Customer": "customer_name",
		"Supplier": "supplier_name",
		"Employee": "employee_name",
		"Shareholder": "title",
	}
	by_type = {}
	for ptype, party in party_keys:
		by_type.setdefault(ptype, set()).add(party)

	names = {}
	for ptype, parties in by_type.items():
		field = title_fields.get(ptype)
		if not field or not frappe.db.exists("DocType", ptype):
			continue
		for name, title in frappe.get_all(
			ptype, filters={"name": ["in", list(parties)]},
			fields=["name", field], as_list=True,
		):
			if title:
				names[(ptype, name)] = title
	return names


def _is_dividend(account_name):
	return (account_name or "").strip().lower().startswith(DIVIDEND_PREFIXES)


def compute(period_list, company):
	"""Har ustun (davr oxiri) uchun barcha kataklarni yig'adi."""
	ends = [getdate(p["to_date"]) for p in period_list]
	starts = [getdate(p["from_date"]) for p in period_list]
	n = len(period_list)

	static = {}        # (bucket, account) -> [ustun qoldig'i]
	party = {}         # (klass, party_type, party) -> [ustun qoldig'i]
	labels = {}        # account -> account_name
	prior_profit = [0.0] * n   # davr BOSHIGACHA yig'ilgan foyda (kredit+)
	cur_profit = [0.0] * n     # davr ICHIDAGI foyda

	def add(dct, key, idxs, val):
		arr = dct.setdefault(key, [0.0] * n)
		for i in idxs:
			arr[i] += val

	for r in fetch_gl(company, str(ends[-1])):
		d = getdate(r.posting_date)
		net = flt(r.net)

		# ── Foyda (P&L) — kapitalning «Накопленная прибыль» qismi ────────
		if r.root_type in ("Income", "Expense"):
			for i in range(n):
				if d < starts[i]:
					prior_profit[i] -= net
				elif d <= ends[i]:
					cur_profit[i] -= net
			continue

		cum_idx = [i for i in range(n) if d <= ends[i]]
		labels[r.account] = r.account_name or r.account
		at = r.account_type

		if at in ("Receivable", "Payable"):
			if r.party_type:
				klass = PARTY_CLASSES.get(r.party_type, "other")
				key = (klass, r.party_type, r.party)
			else:
				key = ("nopar", "", r.account)
			add(party, key, cum_idx, net)
		elif at == "Stock":
			add(static, ("stock", r.account), cum_idx, net)
		elif at in ("Cash", "Bank"):
			add(static, ("cash", r.account), cum_idx, net)
		elif at in FIXED_TYPES:
			add(static, ("fixed", r.account), cum_idx, net)
		elif r.root_type == "Asset":
			add(static, ("other_asset", r.account), cum_idx, net)
		elif r.root_type == "Liability":
			add(static, ("other_liab", r.account), cum_idx, net)
		elif r.root_type == "Equity":
			bucket = "dividend" if _is_dividend(r.account_name) else "equity"
			add(static, (bucket, r.account), cum_idx, net)

	return {
		"n": n,
		"static": static,
		"party": party,
		"labels": labels,
		"party_names": party_display_names(
			{(pt, p) for (_k, pt, p) in party if pt}),
		"prior_profit": prior_profit,
		"cur_profit": cur_profit,
	}


# ─── Qatorlar ────────────────────────────────────────────────────────────────

def build_rows(period_list, data):
	n = data["n"]
	fkeys = [fk(p["key"]) for p in period_list]
	labels = data["labels"]
	party_names = data["party_names"]

	def vm(arr):
		return dict(zip(fkeys, arr))

	# Faqat KO'RINISH uchun: 1 so'mdan kichik qoldiq qatori chiqarilmaydi.
	# Jamilar esa doim BARCHA qoldiqdan hisoblanadi — aks holda yashirilgan
	# tiyinlar РАЗНИЦА'ni buzardi.
	def all_zero(arr):
		return all(abs(flt(v)) < 1 for v in arr)

	def sum_arrays(arrs):
		out = [0.0] * n
		for a in arrs:
			for i in range(n):
				out[i] += a[i]
		return out

	def by_size(items):
		return sorted(items, key=lambda x: -abs(x[1][-1]))

	def static_items(bucket, flip=False):
		"""Bo'lim schetlari (hammasi) — kattadan kichikka."""
		items = []
		for (b, account), arr in data["static"].items():
			if b == bucket:
				items.append((labels.get(account, account), [(-v if flip else v) for v in arr]))
		return by_size(items)

	def detail_rows(items, prefix="", indent=2, is_cost=False):
		return [row(f"{prefix}{lb}", vm(arr), "detail", indent, is_cost)
				for lb, arr in items if not all_zero(arr)]

	# ── Kontragentlar: har ustunda ishorasiga qarab ikki tomonga ──────────
	# klass -> [(nom, [qoldiq])] — aktiv (debet) va passiv (kredit) alohida
	ar_parties, ap_parties = {}, {}
	for (klass, ptype, party), arr in data["party"].items():
		name = party_names.get((ptype, party)) or labels.get(party) or party
		ar_parties.setdefault(klass, []).append((name, [v if v > 0 else 0.0 for v in arr]))
		ap_parties.setdefault(klass, []).append((name, [-v if v < 0 else 0.0 for v in arr]))

	def party_block(side_parties, side_labels, indent, is_cost=False):
		"""Klass qatori + ichida har bir kontragent. Jami bilan birga qaytadi."""
		out, totals = [], []
		for klass in PARTY_ORDER:
			items = side_parties.get(klass) or []
			total = sum_arrays([a for _l, a in items])
			totals.append(total)
			if all_zero(total):
				continue
			out.append(row(side_labels[klass], vm(total), "detail", indent, is_cost))
			out.extend(detail_rows(by_size(items), indent=indent + 1, is_cost=is_cost))
		return out, sum_arrays(totals)

	ar_rows, ar_t = party_block(ar_parties, PARTY_AR_LABELS, 2)
	ap_rows, ap_t = party_block(ap_parties, PARTY_AP_LABELS, 2, is_cost=True)

	fixed_items = static_items("fixed")
	stock_items = static_items("stock")
	cash_items = static_items("cash")
	oa_items = static_items("other_asset")
	ol_items = static_items("other_liab", flip=True)
	eq_items = static_items("equity", flip=True)
	div_items = static_items("dividend", flip=True)

	def total(items):
		return sum_arrays([a for _l, a in items])

	fixed_t, stock_t, cash_t, oa_t = total(fixed_items), total(stock_items), total(cash_items), total(oa_items)
	ol_t, eq_t, div_t = total(ol_items), total(eq_items), total(div_items)
	prior, cur = data["prior_profit"], data["cur_profit"]

	assets_t = sum_arrays([fixed_t, stock_t, cash_t, ar_t, oa_t])
	kredit_t = sum_arrays([ap_t, ol_t])
	capital_t = sum_arrays([eq_t, prior, cur, div_t])
	passives_t = sum_arrays([capital_t, kredit_t])
	diff = [assets_t[i] - passives_t[i] for i in range(n)]
	working = [assets_t[i] - kredit_t[i] for i in range(n)]

	rows = []

	# ══ АКТИВЫ ═══════════════════════════════════════════════════════════
	rows.append(row("АКТИВЫ", vm(assets_t), "root", 0))
	if not all_zero(fixed_t):
		rows.append(row("Основные средства", vm(fixed_t), "sub", 1))
		rows.extend(detail_rows(fixed_items))

	rows.append(row("Запасы", vm(stock_t), "sub", 1))
	rows.extend(detail_rows(stock_items))

	rows.append(row("Денежные средства", vm(cash_t), "sub", 1))
	rows.extend(detail_rows(cash_items))

	rows.append(row("Дебиторская задолженность", vm(ar_t), "sub", 1))
	rows.extend(ar_rows)

	if not all_zero(oa_t):
		rows.append(row("Прочие активы", vm(oa_t), "sub", 1))
		rows.extend(detail_rows(oa_items))

	rows.append(row("ИТОГО АКТИВЫ", vm(assets_t), "result", 0))
	rows.append(divider(fkeys))

	# ══ ПАССИВЫ ══════════════════════════════════════════════════════════
	rows.append(row("ПАССИВЫ", vm(passives_t), "root", 0))

	rows.append(row("Капитал", vm(capital_t), "sub", 1))
	rows.extend(detail_rows(eq_items))
	rows.append(row("Накопл. прибыль — прошлых периодов", vm(prior), "detail", 2))
	rows.append(row("Прибыль (давр ичида)", vm(cur), "detail", 2))
	rows.extend(detail_rows(div_items, "Дивиденды: "))

	rows.append(row("Кредиторская задолженность", vm(kredit_t), "sub", 1, is_cost=True))
	rows.extend(ap_rows)
	rows.extend(detail_rows(ol_items, "Бошқа мажбурият: ", is_cost=True))

	rows.append(row("ИТОГО ПАССИВЫ", vm(passives_t), "result", 0))
	rows.append(row("РАЗНИЦА (назорат)", vm(diff), "result", 0))
	rows.append(divider(fkeys))
	rows.append(row("Рабочий капитал (Активы − Кредиторка)", vm(working), "sub", 0))

	return rows
