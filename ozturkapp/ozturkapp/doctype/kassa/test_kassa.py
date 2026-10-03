# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa (bitta kompaniyali): oqimlar va kompaniyani aniqlash.

Asosiy kompaniya («O'zturk Maksim Gorkiy») va unda kamida 2 ta naqd (Cash)
hisob kerak — bo'lmasa SKIP. Kassalar (MoP), supplier, filial, divident
Party Type/hisobi testning o'zida yaratiladi; FrappeTestCase oxirida
hammasi rollback.

Har bir oqimda GL yozuvlari tekshiriladi: pul qaysi kompaniya kitobida, qaysi
hisob/kontragentga tushgani. Kompaniyani aniqlash testlari guruh kompaniyasi
(«O'zturk») qatorini ham o'z ichiga oladi — avval MoP'ning tasodifiy qatori
olinib, kassa guruh kitobiga ham tushib qolardi.
"""

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, today

from ozturkapp.ozturkapp.doctype.kassa.kassa import (
    get_mode_of_payment_info,
    resolve_mop_account,
)

COMPANY = "O'zturk Maksim Gorkiy"

MOP = "_Test Kassa Asosiy"
MOP_2 = "_Test Kassa Ikkinchi"
MOP_SAME_ACC = "_Test Kassa Shu Hisob"
MOP_GROUP = "_Test Kassa Guruh"
MOP_SHARED = "_Test Kassa Umumiy"
MOP_ONLY_GROUP = "_Test Kassa Faqat Guruh"
SUPPLIER = "_Test Kassa Supplier"
CUSTOMER = "_Test Kassa Customer"
FILIAL = "_Test Kassa Filial"
DIVIDEND = "Divident _Test Kassa"
SUMMA = 150000


def _leaf(company, **filters):
    return frappe.db.get_value(
        "Account", dict(company=company, is_group=0, disabled=0, **filters), "name", order_by="lft asc"
    )


def _cash_accounts(company):
    return frappe.get_all(
        "Account", filters={"company": company, "account_type": "Cash", "is_group": 0, "disabled": 0},
        pluck="name", order_by="name asc",
    )


def _group_company():
    """Kompaniyaning guruh (ota) kompaniyasi — masalan «O'zturk»."""
    parent = frappe.db.get_value("Company", COMPANY, "parent_company")
    return parent if parent and frappe.db.get_value("Company", parent, "is_group") else None


def _other_trading_company():
    """Boshqa guruh bo'lmagan kompaniya (bo'lsa) — noaniq kassa testi uchun."""
    return frappe.db.get_value(
        "Company", {"is_group": 0, "name": ["!=", COMPANY]}, "name", order_by="name asc"
    )


def _prereqs_ok():
    return bool(frappe.db.exists("Company", COMPANY) and len(_cash_accounts(COMPANY)) >= 2)


@unittest.skipUnless(_prereqs_ok(), f"'{COMPANY}' yoki uning 2 ta naqd hisobi topilmadi")
class TestKassa(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")

        cls.cash, cls.cash_2 = _cash_accounts(COMPANY)[:2]
        cls.group = _group_company()
        cls.group_cash = _leaf(cls.group, account_type="Cash") if cls.group else None
        cls.other = _other_trading_company()
        cls.other_cash = _leaf(cls.other, account_type="Cash") if cls.other else None

        cls._mop(MOP, [(COMPANY, cls.cash)])
        cls._mop(MOP_2, [(COMPANY, cls.cash_2)])
        cls._mop(MOP_SAME_ACC, [(COMPANY, cls.cash)])
        if cls.group_cash:
            # Guruh qatori ATAYLAB birinchi — avval aynan shu olinib qolardi
            cls._mop(MOP_GROUP, [(cls.group, cls.group_cash), (COMPANY, cls.cash_2)])
            cls._mop(MOP_ONLY_GROUP, [(cls.group, cls.group_cash)])
        if cls.other_cash:
            # Ikki savdo kompaniyasi — kompaniya qatori 2-o'rinda
            cls._mop(MOP_SHARED, [(cls.other, cls.other_cash), (COMPANY, cls.cash_2)])

        if not frappe.db.exists("Supplier", SUPPLIER):
            frappe.get_doc({
                "doctype": "Supplier", "supplier_name": SUPPLIER,
                "supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name"),
            }).insert(ignore_permissions=True)
        if not frappe.db.exists("Customer", CUSTOMER):
            frappe.get_doc({
                "doctype": "Customer", "customer_name": CUSTOMER,
                "customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
                "territory": frappe.db.get_value("Territory", {"is_group": 0}, "name"),
            }).insert(ignore_permissions=True)

        cls.expense = _leaf(COMPANY, root_type="Expense", account_type="")
        if not frappe.db.exists("Kassa Filial", FILIAL):
            frappe.get_doc({
                "doctype": "Kassa Filial", "filial_name": FILIAL, "is_active": 1, "company": COMPANY,
            }).insert(ignore_permissions=True)

    @staticmethod
    def _mop(name, rows):
        if frappe.db.exists("Mode of Payment", name):
            frappe.delete_doc("Mode of Payment", name, force=True)
        frappe.get_doc({
            "doctype": "Mode of Payment", "mode_of_payment": name, "type": "Cash", "enabled": 1,
            "accounts": [{"company": c, "default_account": a} for c, a in rows],
        }).insert(ignore_permissions=True)

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
        """(kompaniya, {account: (debit - credit, party_type, party)})."""
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

    def _assert_cancelled(self, kassa, fieldname):
        doctype = "Journal Entry" if fieldname == "journal_entry" else "Payment Entry"
        name = kassa.get(fieldname)
        self.assertTrue(name, fieldname)
        self.assertEqual(frappe.db.get_value(doctype, name, "custom_kassa"), kassa.name)
        kassa.cancel()
        self.assertEqual(frappe.db.get_value(doctype, name, "docstatus"), 2)

    # ── kompaniyani aniqlash ──────────────────────────────────────
    def test_single_company_mop(self):
        """Bitta qatorli kassa — kompaniya avtomatik, standart kompaniyadan ustun."""
        info = get_mode_of_payment_info(MOP)
        self.assertEqual((info["company"], info["account"]), (COMPANY, self.cash))
        self.assertFalse(info["ambiguous"])

        k = self._kassa(oborot="Расход", source_account=MOP, party_type="Расходы",
                        expense_kontragent=self.expense)
        k.insert(ignore_permissions=True)
        self.assertEqual((k.company, k.payment_account), (COMPANY, self.cash))

    def test_group_company_row_ignored(self):
        """Guruh kompaniyasi qatori (hatto 1-o'rinda) olinmaydi."""
        if not self.group_cash:
            self.skipTest("Guruh kompaniyasi yo'q")
        info = resolve_mop_account(MOP_GROUP)
        self.assertEqual((info["company"], info["account"]), (COMPANY, self.cash_2))
        self.assertEqual(resolve_mop_account(MOP_GROUP, self.group)["account"], self.group_cash)

        # Forma guruhni standart kompaniya qilib qo'ysa ham — kassa ustun
        k = self._kassa(oborot="Расход", company=self.group, source_account=MOP_GROUP,
                        party_type="Расходы", expense_kontragent=self.expense)
        k.set_company_and_accounts()
        self.assertEqual((k.company, k.payment_account), (COMPANY, self.cash_2))

        # Faqat guruh kitobidagi kassa — Kassa rad etadi
        k = self._kassa(oborot="Расход", source_account=MOP_ONLY_GROUP, party_type="Расходы",
                        expense_kontragent=self.expense)
        with self.assertRaises(frappe.ValidationError) as ctx:
            k.set_company_and_accounts()
        self.assertIn("guruh kompaniyasi", str(ctx.exception))

    def test_multi_company_mop_needs_company(self):
        """Ikki savdo kompaniyali kassa: kompaniyasiz — xato, kompaniya bilan — aynan o'sha qator."""
        if not self.other_cash:
            self.skipTest("Ikkinchi savdo kompaniyasi yo'q")
        self.assertTrue(resolve_mop_account(MOP_SHARED, throw=False)["ambiguous"])
        self.assertRaises(frappe.ValidationError, resolve_mop_account, MOP_SHARED)
        self.assertEqual(resolve_mop_account(MOP_SHARED, COMPANY)["account"], self.cash_2)
        self.assertEqual(resolve_mop_account(MOP_SHARED, self.other)["account"], self.other_cash)

        k = self._kassa(oborot="Расход", source_account=MOP_SHARED, party_type="Расходы",
                        expense_kontragent=self.expense)
        self.assertRaises(frappe.ValidationError, k.set_company_and_accounts)

        k = self._kassa(oborot="Расход", company=COMPANY, source_account=MOP_SHARED,
                        party_type="Расходы", expense_kontragent=self.expense)
        k.insert(ignore_permissions=True)
        self.assertEqual((k.company, k.payment_account), (COMPANY, self.cash_2))

    def test_mop_without_row_for_company(self):
        self.assertRaises(frappe.ValidationError, resolve_mop_account, MOP, "_Yo'q Kompaniya")
        self.assertEqual(resolve_mop_account(MOP, "_Yo'q Kompaniya", throw=False)["account"], "")

    # ── Payment Entry oqimlari ────────────────────────────────────
    def test_supplier_payment(self):
        k = self._submit(oborot="Расход", source_account=MOP, party_type="Supplier", kontragent=SUPPLIER)
        self.assertFalse(k.journal_entry)
        company, gl = self._gl(k.payment_entry)
        self.assertEqual(company, COMPANY)
        self.assertEqual(gl[self.cash][0], -SUMMA)
        payable = frappe.get_cached_value("Company", COMPANY, "default_payable_account")
        self.assertEqual(gl[payable], (SUMMA, "Supplier", SUPPLIER))
        self._assert_cancelled(k, "payment_entry")

    def test_customer_receipt(self):
        k = self._submit(oborot="Приход", source_account=MOP, party_type="Customer", kontragent=CUSTOMER)
        self.assertEqual(frappe.db.get_value("Payment Entry", k.payment_entry, "payment_type"), "Receive")
        company, gl = self._gl(k.payment_entry)
        self.assertEqual(company, COMPANY)
        self.assertEqual(gl[self.cash][0], SUMMA)
        receivable = frappe.get_cached_value("Company", COMPANY, "default_receivable_account")
        self.assertEqual(gl[receivable], (-SUMMA, "Customer", CUSTOMER))
        self._assert_cancelled(k, "payment_entry")

    # ── Journal Entry oqimlari ────────────────────────────────────
    def test_expense_journal_entry(self):
        k = self._submit(oborot="Расход", source_account=MOP, party_type="Расходы",
                         expense_kontragent=self.expense)
        self.assertFalse(k.payment_entry)
        company, gl = self._gl(k.journal_entry)
        self.assertEqual(company, COMPANY)
        self.assertEqual(gl[self.expense][0], SUMMA)
        self.assertEqual(gl[self.cash][0], -SUMMA)
        self._assert_cancelled(k, "journal_entry")

    def test_expense_account_of_other_company_rejected(self):
        other_expense = _leaf(self.group or self.other or "", root_type="Expense")
        if not other_expense:
            self.skipTest("Boshqa kompaniya xarajat hisobi yo'q")
        k = self._kassa(oborot="Расход", source_account=MOP, party_type="Расходы",
                        expense_kontragent=other_expense)
        self.assertRaises(frappe.ValidationError, k.insert)

    def test_dividend(self):
        if not frappe.db.exists("Party Type", DIVIDEND):
            pt = frappe.new_doc("Party Type")
            pt.party_type = DIVIDEND
            pt.account_type = "Payable"
            pt.flags.ignore_links = True
            pt.insert(ignore_permissions=True)
        equity_parent = frappe.db.get_value(
            "Account", {"company": COMPANY, "root_type": "Equity", "is_group": 1}, "name", order_by="lft asc")
        acc = frappe.get_doc({
            "doctype": "Account", "account_name": DIVIDEND, "company": COMPANY,
            "parent_account": equity_parent, "is_group": 0,
        })
        # Bola kompaniyada hisob ERPNext'da odatda guruh orqali yaratiladi
        acc.flags.ignore_root_company_validation = True
        acc.insert(ignore_permissions=True)

        k = self._submit(oborot="Расход", source_account=MOP, party_type=DIVIDEND)
        company, gl = self._gl(k.journal_entry)
        self.assertEqual(company, COMPANY)
        self.assertEqual(gl[acc.name][0], SUMMA)
        self.assertEqual(gl[self.cash][0], -SUMMA)
        self._assert_cancelled(k, "journal_entry")

    def test_transfer(self):
        k = self._submit(oborot="Перемещение", transfer_source_display=MOP, target_account=MOP_2)
        company, gl = self._gl(k.journal_entry)
        self.assertEqual(company, COMPANY)
        self.assertEqual(gl[self.cash][0], -SUMMA)
        self.assertEqual(gl[self.cash_2][0], SUMMA)
        self._assert_cancelled(k, "journal_entry")

    def test_transfer_same_account_clear_error(self):
        k = self._kassa(oborot="Перемещение", transfer_source_display=MOP, target_account=MOP_SAME_ACC)
        with self.assertRaises(frappe.ValidationError) as ctx:
            k.insert(ignore_permissions=True)
        self.assertIn("bir xil hisobga", str(ctx.exception))

    # ── Kassa Filial validatsiyasi ────────────────────────────────
    def test_filial_validation(self):
        foreign = self.group or self.other
        if not foreign:
            self.skipTest("Boshqa kompaniya yo'q")
        doc = frappe.get_doc({
            "doctype": "Kassa Filial", "filial_name": "_Test Kassa Filial Xato", "company": COMPANY,
            "expense_group": frappe.db.get_value(
                "Account", {"company": foreign, "root_type": "Expense", "is_group": 1}, "name"),
        })
        self.assertRaises(frappe.ValidationError, doc.insert)
        if self.other_cash:
            doc = frappe.get_doc({
                "doctype": "Kassa Filial", "filial_name": "_Test Kassa Filial Xato 2",
                "company": self.other, "mode_of_payment": MOP,
            })
            self.assertRaises(frappe.ValidationError, doc.insert)

    # ── amend: eski havolalar ko'chmaydi ──────────────────────────
    def test_amend_does_not_carry_links(self):
        k = self._submit(oborot="Расход", source_account=MOP, party_type="Supplier", kontragent=SUPPLIER)
        k.cancel()
        amended = frappe.copy_doc(k)
        amended.amended_from = k.name
        amended.docstatus = 0
        # Amend nusxasida Frappe no_copy maydonlarni saqlaydi — shuni taqlid qilamiz
        amended.payment_entry = k.payment_entry
        amended.insert(ignore_permissions=True)
        self.assertFalse(amended.payment_entry)
        amended.submit()
        self.assertTrue(amended.payment_entry)
        self.assertNotEqual(amended.payment_entry, k.payment_entry)

    # ── custom_kassa havolasi va backfill ─────────────────────────
    def test_link_backfill(self):
        from ozturkapp.ozturkapp.setup.kassa_setup import ensure_kassa_link_fields

        k = self._submit(oborot="Расход", source_account=MOP, party_type="Supplier", kontragent=SUPPLIER)
        frappe.db.set_value("Payment Entry", k.payment_entry, "custom_kassa", None, update_modified=False)
        ensure_kassa_link_fields()
        ensure_kassa_link_fields()  # idempotent
        self.assertEqual(frappe.db.get_value("Payment Entry", k.payment_entry, "custom_kassa"), k.name)
