# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa: kompaniyani aniqlash va kompaniyalararo oqimlar.

Ikki savdo kompaniyasi (Sklad + filial) va ularning ichki kontragentlari
kerak — bo'lmasa SKIP. Kassa (MoP), Sklad sozlamasi, supplier, ishchi va
filial testning o'zida yaratiladi; FrappeTestCase oxirida hammasi rollback.

Har bir oqimda GL yozuvlari tekshiriladi: pul qaysi kompaniya kitobida, qaysi
hisob/kontragentga tushgani — chunki aynan shu Branch Stock Transfer qarzini
yopadi (Sklad: Debtors[filial], filial: Creditors[Sklad]).
"""

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, today

from ozturkapp.ozturkapp.utils.intercompany import resolve_mop_account

SKLAD = "O'zturk Sklad"
BRANCH = "O'zturk Maksim Gorkiy"

SKLAD_MOP = "_Test Kassa Sklad"
BRANCH_MOP = "_Test Kassa Filial"
SHARED_MOP = "_Test Kassa Umumiy"
SUPPLIER = "_Test Kassa Supplier"
FILIAL = "_Test Kassa Filial OSK"
SUMMA = 150000


def _leaf(company, **filters):
    return frappe.db.get_value(
        "Account", dict(company=company, is_group=0, **filters), "name", order_by="lft asc"
    )


def _cash_accounts(company):
    """Kompaniyaning naqd (Cash) leaf hisoblari — mavjudlaridan foydalanamiz:
    bola kompaniyada hisob yaratish ERPNext'da faqat guruh kompaniyasi orqali."""
    return frappe.get_all(
        "Account", filters={"company": company, "account_type": "Cash", "is_group": 0, "disabled": 0},
        pluck="name", order_by="name asc",
    )


def _prereqs_ok():
    return bool(
        frappe.db.exists("Company", SKLAD)
        and frappe.db.exists("Company", BRANCH)
        and frappe.db.exists("Customer", {"is_internal_customer": 1, "represents_company": BRANCH})
        and frappe.db.exists("Customer", {"is_internal_customer": 1, "represents_company": SKLAD})
        and frappe.db.exists("Supplier", {"is_internal_supplier": 1, "represents_company": SKLAD})
        and frappe.db.exists("DocType", "Ozturk Settings")
        and len(_cash_accounts(SKLAD)) >= 1
        and len(_cash_accounts(BRANCH)) >= 2
    )


@unittest.skipUnless(_prereqs_ok(), "Sklad/filial kompaniyalari yoki ichki kontragentlar topilmadi")
class TestKassa(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")

        cls.sklad_cash = _cash_accounts(SKLAD)[0]
        cls.branch_cash, cls.branch_cash_2 = _cash_accounts(BRANCH)[:2]

        def mop(name, rows):
            if frappe.db.exists("Mode of Payment", name):
                frappe.delete_doc("Mode of Payment", name, force=True)
            frappe.get_doc({
                "doctype": "Mode of Payment", "mode_of_payment": name, "type": "Cash", "enabled": 1,
                "accounts": [{"company": c, "default_account": a} for c, a in rows],
            }).insert(ignore_permissions=True)

        mop(SKLAD_MOP, [(SKLAD, cls.sklad_cash)])
        mop(BRANCH_MOP, [(BRANCH, cls.branch_cash)])
        # Bir nechta kompaniyada hisobi bor kassa — ataylab filial qatori 2-o'rinda
        mop(SHARED_MOP, [(SKLAD, cls.sklad_cash), (BRANCH, cls.branch_cash_2)])

        settings = frappe.get_single("Ozturk Settings")
        settings.sklad_company = SKLAD
        settings.sklad_warehouse = None
        settings.set("company_cash", [
            {"company": SKLAD, "mode_of_payment": SKLAD_MOP},
            {"company": BRANCH, "mode_of_payment": BRANCH_MOP},
        ])
        settings.save(ignore_permissions=True)

        if not frappe.db.exists("Supplier", SUPPLIER):
            frappe.get_doc({
                "doctype": "Supplier", "supplier_name": SUPPLIER,
                "supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name"),
            }).insert(ignore_permissions=True)

        cls.sklad_expense = _leaf(SKLAD, root_type="Expense", account_type="")
        cls.branch_expense = _leaf(BRANCH, root_type="Expense", account_type="")
        if not frappe.db.exists("Kassa Filial", FILIAL):
            frappe.get_doc({
                "doctype": "Kassa Filial", "filial_name": FILIAL, "is_active": 1, "company": SKLAD,
            }).insert(ignore_permissions=True)

        cls.sklad_employee = frappe.get_doc({
            "doctype": "Employee", "first_name": "_Test Kassa Ishchi", "gender": "Male",
            "date_of_birth": "1990-01-01", "date_of_joining": "2024-01-01",
            "company": SKLAD, "status": "Active",
        }).insert(ignore_permissions=True).name

    # ── yordamchilar ──────────────────────────────────────────────
    def _kassa(self, **kw):
        doc = frappe.new_doc("Kassa")
        doc.date = today()
        doc.summa = SUMMA
        # new_doc foydalanuvchining standart kompaniyasini qo'yadi — testlar
        # kompaniyani kassadan aniqlashni ham tekshiradi
        doc.company = None
        doc.update(kw)
        return doc

    def _submit(self, **kw):
        doc = self._kassa(**kw)
        doc.insert(ignore_permissions=True)
        doc.submit()
        doc.reload()
        return doc

    def _gl(self, voucher_no):
        """{account: (debit - credit, party_type, party)} + kompaniya."""
        rows = frappe.get_all(
            "GL Entry", filters={"voucher_no": voucher_no, "is_cancelled": 0},
            fields=["account", "party_type", "party", "debit", "credit", "company"],
        )
        self.assertTrue(rows, f"{voucher_no} uchun GL yo'q")
        companies = {r.company for r in rows}
        self.assertEqual(len(companies), 1)
        out = {}
        for r in rows:
            prev = out.get(r.account, (0, None, None))
            out[r.account] = (prev[0] + flt(r.debit) - flt(r.credit), r.party_type or prev[1], r.party or prev[2])
        return companies.pop(), out

    def _assert_cancelled(self, kassa, fields):
        kassa.cancel()
        for f in fields:
            name = kassa.get(f)
            self.assertTrue(name, f)
            self.assertEqual(frappe.db.get_value(
                "Journal Entry" if f == "journal_entry" else "Payment Entry", name, "docstatus"), 2, f)

    @staticmethod
    def _creditors(company):
        return frappe.get_cached_value("Company", company, "default_payable_account")

    @staticmethod
    def _debtors(company):
        return frappe.get_cached_value("Company", company, "default_receivable_account")

    # ── kompaniyani aniqlash ──────────────────────────────────────
    def test_mop_resolution_is_explicit(self):
        """Bir nechta kompaniyali kassa: kompaniyasiz — xato, kompaniya bilan — aynan o'sha qator."""
        self.assertTrue(resolve_mop_account(SHARED_MOP, throw=False)["ambiguous"])
        self.assertRaises(frappe.ValidationError, resolve_mop_account, SHARED_MOP)
        self.assertEqual(resolve_mop_account(SHARED_MOP, BRANCH)["account"], self.branch_cash_2)
        self.assertEqual(resolve_mop_account(SHARED_MOP, SKLAD)["account"], self.sklad_cash)
        self.assertRaises(frappe.ValidationError, resolve_mop_account, BRANCH_MOP, SKLAD)
        # Bitta kompaniyali kassa — kompaniya avtomatik
        self.assertEqual(resolve_mop_account(BRANCH_MOP)["company"], BRANCH)

        # Kompaniya tanlanmagan (insert standart kompaniyani qo'yadi — shuning
        # uchun to'g'ridan-to'g'ri validatsiya chaqiriladi)
        doc = self._kassa(oborot="Расход", source_account=SHARED_MOP, party_type="Расходы",
                          expense_kontragent=self.branch_expense)
        self.assertRaises(frappe.ValidationError, doc.set_company_and_accounts)
        # Bitta kompaniyali kassa standart kompaniyadan ustun
        doc = self._kassa(oborot="Расход", company=BRANCH, source_account=SKLAD_MOP,
                          party_type="Расходы", expense_kontragent=self.sklad_expense)
        doc.set_company_and_accounts()
        self.assertEqual((doc.company, doc.payment_account), (SKLAD, self.sklad_cash))

        doc = self._kassa(oborot="Расход", company=BRANCH, source_account=SHARED_MOP,
                          party_type="Расходы", expense_kontragent=self.branch_expense)
        doc.insert(ignore_permissions=True)
        self.assertEqual(doc.payment_account, self.branch_cash_2)

    # ── eski oqimlar ishlayveradi ─────────────────────────────────
    def test_plain_supplier_payment_single_company(self):
        k = self._submit(oborot="Расход", source_account=BRANCH_MOP, party_type="Supplier",
                         kontragent=SUPPLIER, via_sklad=0)
        self.assertEqual(k.company, BRANCH)
        self.assertFalse(k.payment_entry_receive)
        company, gl = self._gl(k.payment_entry)
        self.assertEqual(company, BRANCH)
        self.assertEqual(gl[self.branch_cash][0], -SUMMA)
        self.assertEqual(frappe.db.get_value("Payment Entry", k.payment_entry, "custom_kassa"), k.name)
        self._assert_cancelled(k, ["payment_entry"])

    def test_same_company_transfer(self):
        k = self._submit(oborot="Перемещение", company=BRANCH,
                         transfer_source_display=BRANCH_MOP, target_account=SHARED_MOP)
        company, gl = self._gl(k.journal_entry)
        self.assertEqual(company, BRANCH)
        self.assertEqual(gl[self.branch_cash][0], -SUMMA)
        self.assertEqual(gl[self.branch_cash_2][0], SUMMA)
        self._assert_cancelled(k, ["journal_entry"])

    # ── (e) validatsiya ───────────────────────────────────────────
    def test_cross_company_transfer_gives_clear_error(self):
        doc = self._kassa(oborot="Перемещение", transfer_source_display=BRANCH_MOP, target_account=SKLAD_MOP)
        with self.assertRaises(frappe.ValidationError) as ctx:
            doc.insert(ignore_permissions=True)
        self.assertIn("Перемещение faqat bitta kompaniya", str(ctx.exception))

    def test_self_payment_blocked(self):
        own = frappe.db.get_value("Customer", {"is_internal_customer": 1, "represents_company": BRANCH})
        doc = self._kassa(oborot="Расход", source_account=BRANCH_MOP, party_type="Customer", kontragent=own)
        self.assertRaises(frappe.ValidationError, doc.insert)

    # ── (a) Sklad <-> filial hisob-kitobi ─────────────────────────
    def test_branch_pays_sklad(self):
        """Filial Sklad'ga to'laydi: filialda Дт Creditors[Sklad], Skladda Кт Debtors[filial]."""
        sklad_supplier = frappe.db.get_value("Supplier", {"is_internal_supplier": 1, "represents_company": SKLAD})
        branch_customer = frappe.db.get_value("Customer", {"is_internal_customer": 1, "represents_company": BRANCH})
        sklad_customer = frappe.db.get_value("Customer", {"is_internal_customer": 1, "represents_company": SKLAD})

        # Foydalanuvchi xato tomonni (Customer) tanlasa ham Supplier'ga to'g'rilanadi
        k = self._submit(oborot="Расход", source_account=BRANCH_MOP, party_type="Customer",
                         kontragent=sklad_customer)
        self.assertEqual((k.party_type, k.kontragent), ("Supplier", sklad_supplier))

        company, gl = self._gl(k.payment_entry)
        self.assertEqual(company, BRANCH)
        self.assertEqual(gl[self.branch_cash][0], -SUMMA)
        self.assertEqual(gl[self._creditors(BRANCH)], (SUMMA, "Supplier", sklad_supplier))

        company, gl = self._gl(k.payment_entry_receive)
        self.assertEqual(company, SKLAD)
        self.assertEqual(gl[self.sklad_cash][0], SUMMA)
        self.assertEqual(gl[self._debtors(SKLAD)], (-SUMMA, "Customer", branch_customer))

        self._assert_cancelled(k, ["payment_entry", "payment_entry_receive"])

    def test_sklad_pays_branch(self):
        branch_customer = frappe.db.get_value("Customer", {"is_internal_customer": 1, "represents_company": BRANCH})
        k = self._submit(oborot="Расход", source_account=SKLAD_MOP, party_type="Customer",
                         kontragent=branch_customer)
        company, gl = self._gl(k.payment_entry)
        self.assertEqual(company, SKLAD)
        self.assertEqual(gl[self._debtors(SKLAD)][0], SUMMA)
        company, gl = self._gl(k.payment_entry_receive)
        self.assertEqual(company, BRANCH)
        self.assertEqual(gl[self.branch_cash][0], SUMMA)
        self.assertEqual(gl[self._creditors(BRANCH)][0], -SUMMA)
        self._assert_cancelled(k, ["payment_entry", "payment_entry_receive"])

    def test_branch_receives_from_sklad(self):
        """Приход filial kassasida, kontragent Sklad — pul Sklad kassasidan chiqadi."""
        sklad_supplier = frappe.db.get_value("Supplier", {"is_internal_supplier": 1, "represents_company": SKLAD})
        k = self._submit(oborot="Приход", source_account=BRANCH_MOP, party_type="Supplier",
                         kontragent=sklad_supplier)
        company, gl = self._gl(k.payment_entry)
        self.assertEqual(company, BRANCH)
        self.assertEqual(gl[self.branch_cash][0], SUMMA)
        company, gl = self._gl(k.payment_entry_receive)
        self.assertEqual(company, SKLAD)
        self.assertEqual(gl[self.sklad_cash][0], -SUMMA)
        self._assert_cancelled(k, ["payment_entry", "payment_entry_receive"])

    # ── (b) supplier'ga Sklad nomidan ─────────────────────────────
    def test_supplier_paid_via_sklad(self):
        k = self._submit(oborot="Расход", source_account=BRANCH_MOP, party_type="Supplier",
                         kontragent=SUPPLIER, via_sklad=1)
        company, gl = self._gl(k.payment_entry)
        self.assertEqual(company, BRANCH)
        self.assertEqual(gl[self.branch_cash][0], -SUMMA)
        self.assertEqual(gl[self._creditors(BRANCH)][0], SUMMA)

        company, gl = self._gl(k.payment_entry_receive)
        self.assertEqual(company, SKLAD)
        self.assertEqual(gl[self.sklad_cash][0], SUMMA)

        company, gl = self._gl(k.payment_entry_supplier)
        self.assertEqual(company, SKLAD)
        self.assertEqual(gl[self.sklad_cash][0], -SUMMA)
        self.assertEqual(gl[self._creditors(SKLAD)], (SUMMA, "Supplier", SUPPLIER))

        self._assert_cancelled(k, ["payment_entry", "payment_entry_receive", "payment_entry_supplier"])

    def test_via_sklad_ignored_on_sklad_kassa(self):
        k = self._kassa(oborot="Расход", source_account=SKLAD_MOP, party_type="Supplier",
                        kontragent=SUPPLIER, via_sklad=1)
        k.insert(ignore_permissions=True)
        self.assertEqual(k.via_sklad, 0)

    # ── (c) kompaniyalararo xarajat ───────────────────────────────
    def test_intercompany_expense(self):
        k = self._submit(oborot="Расход", source_account=BRANCH_MOP, party_type="Расходы",
                         filial=FILIAL, expense_kontragent=self.sklad_expense)
        company, gl = self._gl(k.payment_entry)
        self.assertEqual(company, BRANCH)
        self.assertEqual(gl[self.branch_cash][0], -SUMMA)
        company, gl = self._gl(k.payment_entry_receive)
        self.assertEqual(company, SKLAD)
        company, gl = self._gl(k.journal_entry)
        self.assertEqual(company, SKLAD)
        self.assertEqual(gl[self.sklad_expense][0], SUMMA)
        self.assertEqual(gl[self.sklad_cash][0], -SUMMA)
        self._assert_cancelled(k, ["journal_entry", "payment_entry_receive", "payment_entry"])

    def test_expense_account_must_match_filial_company(self):
        doc = self._kassa(oborot="Расход", source_account=BRANCH_MOP, party_type="Расходы",
                          filial=FILIAL, expense_kontragent=self.branch_expense)
        self.assertRaises(frappe.ValidationError, doc.insert)

    # ── (d) boshqa kompaniya ishchisi ─────────────────────────────
    def test_employee_of_other_company(self):
        k = self._submit(oborot="Расход", source_account=BRANCH_MOP, party_type="Employee",
                         kontragent=self.sklad_employee)
        self.assertEqual(self._gl(k.payment_entry)[0], BRANCH)
        self.assertEqual(self._gl(k.payment_entry_receive)[0], SKLAD)
        company, gl = self._gl(k.payment_entry_supplier)
        self.assertEqual(company, SKLAD)
        self.assertEqual(gl[self.sklad_cash][0], -SUMMA)
        self._assert_cancelled(k, ["payment_entry", "payment_entry_receive", "payment_entry_supplier"])

    # ── amend: eski havolalar ko'chmaydi ──────────────────────────
    def test_amend_does_not_carry_links(self):
        k = self._submit(oborot="Расход", source_account=BRANCH_MOP, party_type="Supplier",
                         kontragent=SUPPLIER, via_sklad=1)
        k.cancel()
        amended = frappe.copy_doc(k)
        amended.amended_from = k.name
        amended.docstatus = 0
        # Amend nusxasida Frappe no_copy maydonlarni saqlaydi — shuni taqlid qilamiz
        for f in ("payment_entry", "payment_entry_receive", "payment_entry_supplier"):
            amended.set(f, k.get(f))
        amended.insert(ignore_permissions=True)
        for f in ("payment_entry", "payment_entry_receive", "payment_entry_supplier", "journal_entry"):
            self.assertFalse(amended.get(f), f)

    # ── setup: ichki kontragentlar idempotent ─────────────────────
    def test_intercompany_parties_setup_idempotent(self):
        from ozturkapp.ozturkapp.setup.intercompany_setup import ensure_intercompany_parties

        ensure_intercompany_parties()
        before = frappe.db.count("Allowed To Transact With")
        ensure_intercompany_parties()
        self.assertEqual(frappe.db.count("Allowed To Transact With"), before)

        companies = frappe.get_all("Company", filters={"is_group": 0}, pluck="name")
        for company in companies:
            for doctype, flag in (("Customer", "is_internal_customer"), ("Supplier", "is_internal_supplier")):
                name = frappe.db.get_value(doctype, {flag: 1, "represents_company": company})
                self.assertTrue(name, f"{doctype} {company}")
                allowed = set(frappe.get_all("Allowed To Transact With",
                                             filters={"parent": name, "parenttype": doctype}, pluck="company"))
                self.assertTrue(set(companies) - {company} <= allowed, f"{doctype} {name}")
