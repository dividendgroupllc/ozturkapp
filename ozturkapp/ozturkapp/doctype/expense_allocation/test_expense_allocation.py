# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Expense Allocation: Sklad ma'muriy xarajatini filialga qayta yozish.

Test o'zi Sklad kompaniyasida xarajat JE yaratadi (bo'sh oyda — mart 2026),
taqsimlaydi va ikkala kompaniyadagi yozuvlarni tekshiradi. Har test oxirida
tranzaksiya ROLLBACK qilinadi — saytda hech narsa qolmaydi.

Kerak: ikkala kompaniya, standart hisoblar rejasi (Salary/Office Rent/...)
va ichki kontragentlar — bo'lmasa SKIP.
"""

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from ozturkapp.ozturkapp.doctype.expense_allocation.expense_allocation import split_amount

SOURCE = "O'zturk Sklad"
BRANCH = "O'zturk Maksim Gorkiy"
MONTH = "2026-03-01"
POSTING = "2026-03-10"


def _acc(name, company):
    abbr = frappe.db.get_value("Company", company, "abbr")
    return f"{name} - {abbr}"


def _prereqs_ok():
    return bool(
        frappe.db.exists("Company", SOURCE)
        and frappe.db.exists("Company", BRANCH)
        and frappe.db.exists("Account", _acc("Salary", SOURCE))
        and frappe.db.exists("Account", _acc("Office Rent", SOURCE))
        and frappe.db.exists("Account", _acc("Salary", BRANCH))
        and frappe.db.exists("Account", _acc("Cash", SOURCE))
        and frappe.db.exists("Customer", {"is_internal_customer": 1, "represents_company": BRANCH})
        and frappe.db.exists("Supplier", {"is_internal_supplier": 1, "represents_company": SOURCE})
        and not frappe.db.exists("Expense Allocation", {"source_company": SOURCE, "from_date": MONTH, "docstatus": 1})
    )


def _gl_net(account, voucher_no=None):
    filters = {"account": account, "is_cancelled": 0, "posting_date": ["between", [MONTH, "2026-03-31"]]}
    if voucher_no:
        filters["voucher_no"] = voucher_no
    rows = frappe.get_all("GL Entry", filters=filters, fields=["sum(debit - credit) as net"])
    return flt(rows[0].net if rows else 0, 2)


@unittest.skipUnless(_prereqs_ok(), "Kompaniyalar, hisoblar yoki ichki kontragentlar topilmadi")
class TestExpenseAllocation(FrappeTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.customer = frappe.db.get_value(
            "Customer", {"is_internal_customer": 1, "represents_company": BRANCH}, "name")
        self.supplier = frappe.db.get_value(
            "Supplier", {"is_internal_supplier": 1, "represents_company": SOURCE}, "name")
        # Oy bo'sh bo'lishi shart — aks holda kutilgan summalar mos kelmaydi.
        self.assertFalse(
            frappe.db.exists("GL Entry", {"company": SOURCE, "is_cancelled": 0,
                                          "posting_date": ["between", [MONTH, "2026-03-31"]]}),
            "Test oyi (2026-03) Sklad'da bo'sh bo'lishi kerak",
        )

    def tearDown(self):
        frappe.db.rollback()

    # -- yordamchilar --------------------------------------------------
    def _expense_je(self, lines):
        """Sklad'da xarajat JE: lines = {hisob nomi: summa}, kredit — Cash."""
        je = frappe.new_doc("Journal Entry")
        je.company, je.posting_date = SOURCE, POSTING
        total = 0
        for name, amount in lines.items():
            je.append("accounts", {"account": _acc(name, SOURCE), "debit_in_account_currency": amount,
                                   "cost_center": _acc("Main", SOURCE)})
            total += amount
        je.append("accounts", {"account": _acc("Cash", SOURCE), "credit_in_account_currency": total})
        je.insert(ignore_permissions=True)
        je.submit()
        return je

    def _new(self, **kw):
        doc = frappe.new_doc("Expense Allocation")
        doc.from_date = "2026-03-15"   # oyning istalgan kuni — 1-kunga suriladi
        doc.source_company = SOURCE
        doc.update(kw)
        return doc

    def _je_lines(self, name):
        return frappe.get_all(
            "Journal Entry Account", filters={"parent": name},
            fields=["account", "party_type", "party", "debit", "credit"], order_by="idx")

    # -- testlar -------------------------------------------------------
    def test_defaults_and_preview(self):
        self._expense_je({"Salary": 600000, "Office Rent": 400000, "Round Off": 5})
        doc = self._new()
        doc.insert()

        self.assertEqual(str(doc.from_date), MONTH)
        self.assertEqual(str(doc.to_date), "2026-03-31")
        # Standart: Indirect Expenses kiradi, Round Off chiqariladi; yagona filial — 100%.
        self.assertIn(_acc("Indirect Expenses", SOURCE), [r.account for r in doc.accounts if not r.exclude])
        self.assertIn(_acc("Round Off", SOURCE), [r.account for r in doc.accounts if r.exclude])
        self.assertEqual([r.company for r in doc.branches], [BRANCH])
        self.assertAlmostEqual(flt(doc.branches[0].percent), 100)
        self.assertEqual(doc.branches[0].customer, self.customer)
        self.assertEqual(doc.source_supplier, self.supplier)

        self.assertEqual(flt(doc.total_expense_amount), 1000000)
        self.assertEqual(flt(doc.allocated_total), 1000000)
        self.assertEqual(flt(doc.remaining_amount), 0)
        self.assertEqual(doc.status, "Calculated")

        preview = doc.get_preview()
        self.assertEqual(preview["warnings"], [])
        src = preview["source"]
        self.assertEqual(sum(l["debit"] for l in src["accounts"]), sum(l["credit"] for l in src["accounts"]))
        br = preview["branches"][0]
        self.assertEqual(sum(l["debit"] for l in br["accounts"]), 1000000)
        self.assertEqual(sum(l["credit"] for l in br["accounts"]), 1000000)

    def test_submit_books_both_companies_and_cancel(self):
        self._expense_je({"Salary": 600000, "Office Rent": 400000})
        doc = self._new()
        doc.insert()
        doc.submit()
        doc.reload()

        self.assertEqual(doc.status, "Submitted")
        self.assertTrue(doc.source_journal_entry)
        branch_je = doc.branches[0].journal_entry
        self.assertTrue(branch_je)

        # Manba: Дт Debtors [Customer: filial] / Кт asl xarajat hisoblari
        src = self._je_lines(doc.source_journal_entry)
        debtor = [l for l in src if l.party_type == "Customer"]
        self.assertEqual(len(debtor), 1)
        self.assertEqual(debtor[0].party, self.customer)
        self.assertEqual(debtor[0].account, _acc("Debtors", SOURCE))
        self.assertEqual(flt(debtor[0].debit), 1000000)
        credits = {l.account: flt(l.credit) for l in src if l.credit}
        self.assertEqual(credits, {_acc("Salary", SOURCE): 600000, _acc("Office Rent", SOURCE): 400000})

        # Filial: Дт mos xarajat hisoblari / Кт Creditors [Supplier: Sklad]
        br = self._je_lines(branch_je)
        debits = {l.account: flt(l.debit) for l in br if l.debit}
        self.assertEqual(debits, {_acc("Salary", BRANCH): 600000, _acc("Office Rent", BRANCH): 400000})
        creditor = [l for l in br if l.credit]
        self.assertEqual(len(creditor), 1)
        self.assertEqual(creditor[0].account, _acc("Creditors", BRANCH))
        self.assertEqual((creditor[0].party_type, creditor[0].party), ("Supplier", self.supplier))
        self.assertEqual(flt(creditor[0].credit), 1000000)

        # Sklad xarajati oy bo'yicha nolga tushdi, filialda paydo bo'ldi.
        self.assertEqual(_gl_net(_acc("Salary", SOURCE)), 0)
        self.assertEqual(_gl_net(_acc("Salary", BRANCH), branch_je), 600000)
        self.assertEqual(
            frappe.db.get_value("Journal Entry", branch_je, "custom_expense_allocation"), doc.name)

        # Hovuz taqsimot JE'larini qayta sanamaydi (aks holda keyingi hujjat 0 ko'rardi).
        probe = self._new()
        probe._set_period()
        probe._validate_source()
        probe._set_defaults()
        self.assertEqual(sum(r["net_amount"] for r in probe.get_pool()), 1000000)

        # Idempotent: shu oy uchun ikkinchi hujjat saqlanmaydi.
        with self.assertRaises(frappe.ValidationError):
            self._new().insert()

        # Bekor qilish — ikkala JE bekor, xarajat Sklad'ga qaytadi.
        doc.cancel()
        self.assertEqual(frappe.db.get_value("Journal Entry", doc.source_journal_entry, "docstatus"), 2)
        self.assertEqual(frappe.db.get_value("Journal Entry", branch_je, "docstatus"), 2)
        self.assertEqual(_gl_net(_acc("Salary", SOURCE)), 600000)
        self.assertEqual(frappe.db.get_value("Expense Allocation", doc.name, "status"), "Cancelled")

        # Bekor qilingandan keyin shu oyni qayta taqsimlash mumkin.
        again = self._new()
        again.insert()
        self.assertEqual(flt(again.allocated_total), 1000000)

    def test_partial_percent_single_account_and_contra(self):
        self._expense_je({"Salary": 600000, "Office Rent": 400000})
        doc = self._new(branch_account_mode="Bitta hisob",
                        source_contra_account=_acc("Administrative Expenses", SOURCE))
        doc.append("branches", {"company": BRANCH, "percent": 60,
                                "expense_account": _acc("Administrative Expenses", BRANCH)})
        doc.insert()
        self.assertEqual(flt(doc.allocated_total), 600000)
        self.assertEqual(flt(doc.remaining_amount), 400000)
        by_acc = {r.account: (flt(r.allocated_amount), flt(r.remaining_amount)) for r in doc.expenses}
        self.assertEqual(by_acc[_acc("Salary", SOURCE)], (360000, 240000))
        self.assertTrue(doc.get_preview()["warnings"])  # «manbada qoladi» ogohlantirishi

        doc.submit()
        doc.reload()
        src = self._je_lines(doc.source_journal_entry)
        self.assertEqual({l.account: flt(l.credit) for l in src if l.credit},
                         {_acc("Administrative Expenses", SOURCE): 600000})
        br = self._je_lines(doc.branches[0].journal_entry)
        self.assertEqual({l.account: flt(l.debit) for l in br if l.debit},
                         {_acc("Administrative Expenses", BRANCH): 600000})

    def test_stale_preview_blocks_submit(self):
        self._expense_je({"Salary": 100000})
        doc = self._new()
        doc.insert()
        self._expense_je({"Office Rent": 50000})   # saqlangandan KEYIN yangi xarajat
        with self.assertRaises(frappe.ValidationError):
            doc.submit()

    def test_validations(self):
        self._expense_je({"Salary": 100000})
        with self.assertRaises(frappe.ValidationError):   # guruh kompaniya manba bo'lolmaydi
            self._new(source_company=frappe.db.get_value("Company", SOURCE, "parent_company")).insert()
        with self.assertRaises(frappe.ValidationError):   # 100% dan oshib ketdi
            doc = self._new()
            doc.append("branches", {"company": BRANCH, "percent": 120})
            doc.insert()
        with self.assertRaises(frappe.ValidationError):   # manba filial bo'lolmaydi
            doc = self._new()
            doc.append("branches", {"company": SOURCE, "percent": 50})
            doc.insert()

    def test_revenue_basis_single_branch(self):
        self._expense_je({"Salary": 100000})
        doc = self._new(allocation_basis="Tushum ulushi")
        doc.insert()
        # Martda filial tushumi yo'q — taqsimlanadigan narsa yo'q, submit to'xtaydi.
        self.assertEqual(flt(doc.allocated_total), 0)
        with self.assertRaises(frappe.ValidationError):
            doc.submit()

    def test_split_amount_rounding(self):
        parts = split_amount(100, [1, 1, 1])
        self.assertEqual(round(sum(parts), 2), 100)
        parts = split_amount(1000.01, [333.33, 333.33, -10, 343.35])
        self.assertEqual(round(sum(parts), 2), 1000.01)
        self.assertEqual(split_amount(5, [0, 0]), [0.0, 0.0])
