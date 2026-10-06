# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""«PL Hisoboti» va «Balans Hisoboti» — GL bilan raqamma-raqam mos kelishi."""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, getdate

from ozturkapp.ozturkapp.report.balans_hisoboti import balans_hisoboti as balans
from ozturkapp.ozturkapp.report.fin_utils import build_period_list, fk
from ozturkapp.ozturkapp.report.pl_hisoboti import pl_hisoboti as pl


def _company():
	return frappe.db.get_value("Company", {"is_group": 0}, "name")


def _filters(periodicity="Monthly"):
	dates = frappe.db.sql(
		"SELECT MIN(posting_date), MAX(posting_date) FROM `tabGL Entry` WHERE is_cancelled = 0")[0]
	return frappe._dict(
		company=_company(),
		from_date=str(dates[0] or "2026-01-01"),
		to_date=str(dates[1] or "2026-12-31"),
		periodicity=periodicity,
	)


def _row(data, label):
	return next(r for r in data if r["label"] == label)


def _gl_sum(company, root_types, from_date=None, to_date=None):
	conds = ["g.company = %(company)s", "g.is_cancelled = 0",
			 "g.voucher_type != 'Period Closing Voucher'", "a.root_type IN %(roots)s"]
	if from_date:
		conds.append("g.posting_date >= %(from_date)s")
	if to_date:
		conds.append("g.posting_date <= %(to_date)s")
	return flt(frappe.db.sql(
		f"""SELECT SUM(g.debit - g.credit) FROM `tabGL Entry` g
			JOIN `tabAccount` a ON a.name = g.account WHERE {' AND '.join(conds)}""",
		{"company": company, "roots": tuple(root_types),
		 "from_date": from_date, "to_date": to_date})[0][0])


class TestPLHisoboti(FrappeTestCase):
	def test_net_profit_matches_gl(self):
		f = _filters()
		_cols, data = pl.execute(f)
		net = _row(data, "Чистая прибыль")
		revenue = _row(data, "Выручка от реализации продукции")
		for p in build_period_list(f):
			key = fk(p["key"])
			fd, td = str(p["from_date"]), str(p["to_date"])
			self.assertAlmostEqual(
				flt(net[key]), -_gl_sum(f.company, ["Income", "Expense"], fd, td), places=2, msg=key)
			self.assertAlmostEqual(
				flt(revenue[key]), -_gl_sum(f.company, ["Income"], fd, td), places=2, msg=key)

	def test_profit_chain(self):
		_cols, data = pl.execute(_filters("Yearly"))
		for key in [k for k in data[0] if k.startswith("v_")]:
			v = lambda label: flt(_row(data, label)[key])  # noqa: E731
			self.assertAlmostEqual(
				v("Маржинальная прибыль"),
				v("Выручка от реализации продукции") - v("Себестоимость"), places=2)
			self.assertAlmostEqual(
				v("Операционная прибыль"),
				v("Маржинальная прибыль") - v("Операционные расходы"), places=2)

	def test_classification(self):
		"""Tannarx account_type bo'yicha, xarajat guruhlari NOMI bo'yicha
		(ichki darajalari bilan), qolgani «Бошқа харажатлар»ga."""
		period_list = build_period_list({"from_date": "2026-09-01", "to_date": "2026-09-30"})
		d = getdate("2026-09-10")

		def gl(account, root, atype="", lft=0, debit=0, credit=0, kind=""):
			return frappe._dict(posting_date=d, account=account, account_name=account, lft=lft,
								root_type=root, account_type=atype, adj_kind=kind,
								debit=debit, credit=credit)

		rows = [
			gl("Sales", "Income", credit=1000),
			gl("Service Charge", "Income", credit=100),
			gl("COGS", "Expense", "Cost of Goods Sold", debit=400),
			gl("Stock Adj", "Expense", "Stock Adjustment", debit=30, kind="mfg"),
			gl("Stock Adj", "Expense", "Stock Adjustment", debit=20, kind="writeoff"),
			gl("Kassa farq", "Expense", lft=999, debit=5),
			gl("Аренда", "Expense", lft=11, debit=100),
			gl("Обед Ужин (Адм)", "Expense", lft=21, debit=50),
			gl("Round Off", "Expense", lft=40, debit=1),
		]
		ranges = {"op": [(10, 15)], "adm": [(20, 25)]}
		pdata = pl.aggregate(period_list, rows, [], ranges)
		p = pdata["periods"][period_list[0]["key"]]

		self.assertEqual(pl.revenue(p), 1100)
		self.assertEqual((p["cogs_raw"], p["cogs_adj"], p["brak"], p["kassa"]), (400, 30, 20, 5))
		self.assertEqual(p["op"], {"Аренда": 100})
		self.assertEqual(p["adm"], {"Обед Ужин (Адм)": 50})
		self.assertEqual(p["other"], {"Round Off": 1})
		self.assertEqual(pl.net_profit(p), 1100 - 455 - 100 - 50 - 1)


class TestBalansHisoboti(FrappeTestCase):
	def test_balance_is_balanced(self):
		f = _filters()
		_cols, data = balans.execute(f)
		diff = _row(data, "РАЗНИЦА (назорат)")
		cash = _row(data, "Денежные средства")
		for p in build_period_list(f):
			key = fk(p["key"])
			self.assertAlmostEqual(flt(diff[key]), 0, places=2, msg=key)
			gl_cash = flt(frappe.db.sql(
				"""SELECT SUM(g.debit - g.credit) FROM `tabGL Entry` g
					JOIN `tabAccount` a ON a.name = g.account
					WHERE g.company = %s AND g.is_cancelled = 0 AND g.posting_date <= %s
					  AND g.voucher_type != 'Period Closing Voucher'
					  AND a.account_type IN ('Cash', 'Bank')""",
				(f.company, p["to_date"]))[0][0])
			self.assertAlmostEqual(flt(cash[key]), gl_cash, places=2, msg=key)

	def test_period_profit_matches_pl(self):
		f = _filters()
		_c, pl_data = pl.execute(f)
		_c, bs_data = balans.execute(f)
		net = _row(pl_data, "Чистая прибыль")
		cur = _row(bs_data, "Прибыль (давр ичида)")
		for p in build_period_list(f):
			key = fk(p["key"])
			self.assertAlmostEqual(flt(net[key]), flt(cur[key]), places=2, msg=key)

	def test_requires_dates(self):
		self.assertRaises(frappe.ValidationError, balans.execute, {"company": _company()})
