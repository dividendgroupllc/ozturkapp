# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""
Kassa — naqd pul harakati (Приход / Расход / Перемещение), ko'p kompaniyali.

Kompaniya AYNAN «Kompaniya» maydonidan olinadi. Kassa (Mode of Payment) bitta
kompaniyada hisobga ega bo'lsa maydon avtomatik to'ladi; bir nechta bo'lsa —
foydalanuvchi tanlaydi (avval tasodifiy birinchi qator olinardi, batafsil:
`utils/intercompany.py:resolve_mop_account`).

Bir kompaniya ichidagi oqimlar:
  * Приход/Расход + Customer/Supplier/Employee/Shareholder -> Payment Entry
  * Приход/Расход + «Расходы»                              -> Journal Entry
  * Приход/Расход + «Divident ...»                         -> Journal Entry
  * Перемещение (hisobdan hisobga, BITTA kompaniya ichida) -> Journal Entry

Kompaniyalararo oqimlar (Jazira'dan ko'chirilgan, Sklad = `Ozturk Settings`):
  A) Sklad <-> filial hisob-kitobi: kontragent = boshqa kompaniyani ifodalovchi
     ichki Customer/Supplier. Ikkala kompaniya kitobida Payment Entry —
     Branch Stock Transfer qoldirgan debitor/kreditor qarz shu bilan yopiladi.
  B) Supplier'ga filial kassasidan Sklad NOMIDAN to'lov («Sklad nomidan
     to'lash»): filial -> Sklad (2 PE) + Sklad -> supplier (1 PE).
  C) Kompaniyalararo xarajat: «Расходы» + boshqa kompaniya filiali — pul
     o'sha kompaniya kassasiga o'tadi (2 PE), xarajat o'sha kitobda (JE).
  D) Boshqa kompaniya ishchisiga to'lov: pul ishchining kompaniyasiga o'tadi
     (2 PE), ishchiga o'sha kompaniya kitobida to'lanadi (1 PE).

Kompaniyalar orasida pul doim ichki kontragent orqali yuradi — qaysi tarafda
Customer, qaysi tarafda Supplier ekanini `intercompany_party` hal qiladi
(Branch Stock Transfer bilan bir xil yo'nalish). Barcha hujjatlar Kassa bilan
birga bekor qilinadi.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from ozturkapp.ozturkapp.utils.intercompany import (
    get_company_cash,
    get_represented_company,
    get_sklad_company,
    intercompany_party,
    is_group_company,
    resolve_mop_account,
)

# «Divident ...» nomli Party Type'lar divident sifatida qaraladi. Hisob raqami
# qattiq yozilmagan: shu nomdagi Equity hisobi qidiriladi (masalan Party Type
# «Divident Aziz» -> account_name «Divident Aziz»).
DIVIDEND_PARTY_PREFIX = "Divident"

# Kontragent bilan pul muomalasi Payment Entry orqali yuritiladi
PARTY_TYPES_PE = ("Customer", "Supplier", "Employee", "Shareholder")


def _pe_link(name):
    return f'<a href="/app/payment-entry/{name}">{name}</a>'


def _je_link(name):
    return f'<a href="/app/journal-entry/{name}">{name}</a>'


class Kassa(Document):
    """Kassa Document."""

    # Submit yaratadigan buxgalteriya havola maydonlari. Bular faqat shu
    # hujjatning O'ZIGA yoziladi — yangi yozuvda doim bo'sh bo'lishi shart.
    ACCOUNTING_LINK_FIELDS = (
        "payment_entry",
        "payment_entry_receive",
        "payment_entry_supplier",
        "journal_entry",
    )

    def insert(self, *args, **kwargs):
        # Duplicate/Amend'da eski Kassaning JE/PE havolalari ko'chib qolmasin.
        # `no_copy` Duplicate'da ishlaydi, lekin AMEND'da emas (Frappe amend
        # nusxasida no_copy maydonlarni saqlaydi). Tozalash before_insert'da
        # EMAS, shu yerda: `_validate_links()` before_insert'dan OLDIN ishlaydi
        # va bekor qilingan hujjatga havola «Cannot link cancelled document»
        # xatosini berardi.
        if self.is_new():
            for fieldname in self.ACCOUNTING_LINK_FIELDS:
                self.set(fieldname, None)
        return super().insert(*args, **kwargs)

    def validate(self):
        self.validate_summa()
        self.set_company_and_accounts()
        self.normalize_internal_party()
        self.validate_kontragent()
        self.validate_transfer()
        self.validate_expense_kontragent()
        self.validate_via_sklad()
        self.clear_irrelevant_fields()
        self.validate_intercompany_setup()

    def validate_summa(self):
        """Summa > 0 bo'lishi kerak."""
        if self.summa <= 0:
            frappe.throw(_("Summa 0 dan katta bo'lishi kerak"))

    # -------------------------------------------------------------------------
    # KOMPANIYA VA HISOBLAR
    # -------------------------------------------------------------------------

    def set_company_and_accounts(self):
        """Kompaniya + kassa hisobini aniqlash (deterministik).

        * Kassa (MoP) bitta savdo kompaniyasida hisobga ega -> kompaniya shu
          (forma standart kompaniyani oldindan qo'yadi, lekin bitta kompaniyali
          kassada ikkinchi variant yo'q — kassa ustun).
        * Bir nechta kompaniyada -> «Kompaniya» maydonidagi qator AYNAN olinadi;
          maydon bo'sh yoki kassa unda hisobga ega bo'lmasa — xato.
        """
        mop = self.transfer_source_display if self.oborot == "Перемещение" else self.source_account
        if mop:
            info = self._resolve_kassa(mop)
            self.company = info["company"]
            self.payment_account = info["account"]

        if is_group_company(self.company):
            frappe.throw(_("'{0}' guruh kompaniyasi — kassa yuritmaydi. Savdo kompaniyasini tanlang.").format(
                self.company))

        if self.oborot == "Перемещение" and self.target_account and self.company:
            self.payment_account_2 = self._resolve_transfer_target()

        # Приход + Supplier/Employee/Shareholder uchun ogohlantirish (faqat
        # o'z kitobidagi kontragent uchun ma'noli)
        if (
            self.oborot == "Приход"
            and self.party_type in ("Supplier", "Employee", "Shareholder")
            and not self._counterparty_company()
            and not self._is_cross_company_employee()
        ):
            self._warn_prihod_payable_party()

    def _resolve_kassa(self, mop):
        """Bitta kompaniyali kassa -> o'sha; bir nechta -> `self.company` qatori."""
        info = resolve_mop_account(mop, throw=False)
        if info["account"]:
            return info
        # Hisob umuman yo'q yoki kompaniya tanlanmagan — tushunarli xato bilan
        return resolve_mop_account(mop, self.company if info["companies"] else None)

    def _resolve_transfer_target(self):
        """Перемещение maqsad hisobi — FAQAT manba kompaniyasida.

        Kompaniyalar o'rtasida pul ko'chirish bitta Journal Entry bilan bo'lmaydi
        (ERPNext «Account does not belong to company» deydi). Shuning uchun
        tushunarli xato va to'g'ri yo'l ko'rsatiladi.
        """
        info = resolve_mop_account(self.target_account, self.company, throw=False)
        if info["account"]:
            return info["account"]

        others = [c for c in info["companies"] if c != self.company]
        if others:
            frappe.throw(
                _("Перемещение faqat bitta kompaniya ichida bo'ladi: '{0}' kassasi '{1}' kompaniyasiga "
                  "emas, {2} ga tegishli.<br><br>Kompaniyalar o'rtasida pul o'tkazish uchun «Расход» "
                  "tanlang va kontragent sifatida qabul qiluvchi kompaniyani ifodalovchi ichki "
                  "Customer/Supplier'ni ko'rsating — pul ikkala kompaniya kitobida yoziladi.").format(
                    self.target_account, self.company, ", ".join(others)
                ),
                title=_("Kompaniyalararo Перемещение"),
            )
        frappe.throw(_("'{0}' uchun hisob topilmadi.").format(self.target_account))

    def normalize_internal_party(self):
        """Ichki kontragentni savdo yo'nalishiga moslash.

        Filial kassasida «O'zturk Sklad» Customer sifatida tanlansa, Sklad
        filial kitobida Debtors'ga tushib qolardi, Branch Stock Transfer qarzi
        esa Creditors'da (Supplier). Qarz yopilishi uchun AYNAN o'sha kontragent
        ishlatiladi.
        """
        if not self._is_internal_settlement():
            return
        target = self._counterparty_company()
        party_type, party = intercompany_party(target, self.company)
        if (party_type, party) != (self.party_type, self.kontragent):
            frappe.msgprint(
                _("'{0}' kompaniyasi bilan hisob-kitob {1} «{2}» orqali yoziladi "
                  "(Branch Stock Transfer qarzi shu kontragentda).").format(target, party_type, party),
                indicator="blue",
                alert=True,
            )
            self.party_type, self.kontragent = party_type, party

    def validate_kontragent(self):
        """O'ziga to'lov va guruh kompaniyasi bilan hisob-kitobni taqiqlash."""
        target = self._counterparty_company()
        if not target:
            return
        if self.company and target == self.company:
            frappe.throw(
                _("'{0}' — kassa kompaniyasining ({1}) o'zini ifodalovchi ichki kontragent. "
                  "Bu o'ziga to'lov bo'lib qoladi. Boshqa kontragent yoki boshqa kassani tanlang.").format(
                    self.kontragent, self.company
                )
            )
        if self.oborot in ("Приход", "Расход") and is_group_company(target):
            frappe.throw(
                _("'{0}' guruh kompaniyasi — u bilan kassa hisob-kitobi yuritilmaydi.").format(target)
            )

    def validate_transfer(self):
        """Перемещение uchun validatsiya."""
        if self.oborot != "Перемещение":
            return
        if not self.transfer_source_display:
            frappe.throw(_("'Qaysi hisobdan' majburiy"))
        if not self.target_account:
            frappe.throw(_("'Qaysi hisobga' majburiy"))
        if self.transfer_source_display == self.target_account:
            frappe.throw(_("Manba va maqsad hisob bir xil bo'lishi mumkin emas"))
        if self.payment_account and self.payment_account == self.payment_account_2:
            frappe.throw(_("Ikkala kassa bir xil hisobga ({0}) bog'langan — o'tkazmaning ma'nosi yo'q.").format(
                self.payment_account))

    def validate_expense_kontragent(self):
        """FAQAT «Расходы» uchun tekshiruv.

        - Xarajat hisobi «Expense» root turida bo'lishi kerak.
        - Amortizatsiya (Depreciation) hisobi kassadan to'lanmaydi -> taqiqlanadi.
        - Hisob XARAJAT kompaniyasiga tegishli: filial tanlangan bo'lsa — filial
          kompaniyasi (kompaniyalararo xarajat), aks holda kassa kompaniyasi.
        """
        if self.party_type != "Расходы" or not self.expense_kontragent:
            return

        account_data = frappe.db.get_value(
            "Account",
            self.expense_kontragent,
            ["root_type", "company", "account_type"],
            as_dict=True,
        )
        if not account_data:
            frappe.throw(_("Xarajat kontragenti topilmadi."))

        if account_data.root_type != "Expense":
            frappe.throw(_("Xarajat kontragenti faqat Expense account bo'lishi kerak."))

        if account_data.account_type == "Depreciation":
            frappe.throw(_(
                "Amortizatsiya (Depreciation) hisobini kassadan to'lab bo'lmaydi. "
                "U Asset moduli orqali avtomatik yoziladi. Boshqa xarajat hisobini tanlang."
            ))

        expense_company = self._expense_company()
        if expense_company and account_data.company != expense_company:
            if expense_company != self.company:
                msg = _("Xarajat hisobi '{0}' filiali kompaniyasiga ({1}) tegishli bo'lishi kerak.").format(
                    self.filial, expense_company)
            else:
                msg = _("Xarajat hisobi '{0}' kompaniyasiga tegishli bo'lishi kerak.").format(expense_company)
            frappe.throw(msg)

    def validate_via_sklad(self):
        """«Sklad nomidan to'lash» faqat filial kassasidan tashqi supplierga Расход'da."""
        if not self.via_sklad:
            return
        sklad = get_sklad_company()
        if (
            self.oborot != "Расход"
            or self.party_type != "Supplier"
            or self._counterparty_company()
            or not sklad
            or self.company == sklad
        ):
            self.via_sklad = 0

    def validate_intercompany_setup(self):
        """Kompaniyalararo oqim uchun sozlamani SAQLASHDAYOQ tekshirish.

        Aks holda yetishmagan kassa/ichki kontragent faqat submit paytida,
        ikkinchi kompaniya hujjati yaratilayotganda chiqardi.
        """
        other = None
        if self._is_intercompany_expense():
            other = self._expense_company()
        elif self._is_internal_settlement():
            other = self._counterparty_company()
        elif self._is_supplier_via_sklad():
            other = get_sklad_company()
        elif self._is_cross_company_employee():
            other = self._employee_company()
        if not other:
            return
        if is_group_company(other):
            frappe.throw(_("'{0}' guruh kompaniyasi — u orqali kassa hisob-kitobi yuritilmaydi.").format(other))
        self._other_company_cash(other)
        intercompany_party(other, self.company)
        intercompany_party(self.company, other)

    def _warn_prihod_payable_party(self):
        """Приход + Supplier/Employee/Shareholder uchun ogohlantirish.

        Bu holat odatda «avans qaytishi» uchun ishlatiladi:
        - Avval Supplier/Employee/Shareholder ga avans berilgan (Расход)
        - Endi ular pulni qaytaryapti (Приход)
        """
        if not self.kontragent or not self.company:
            return

        try:
            from erpnext.accounts.party import get_party_account

            party_account = get_party_account(self.party_type, self.kontragent, self.company)
            if not party_account:
                return

            balance = frappe.db.sql("""
                SELECT SUM(debit) - SUM(credit) as balance
                FROM `tabGL Entry`
                WHERE account = %s
                    AND party_type = %s
                    AND party = %s
                    AND is_cancelled = 0
            """, (party_account, self.party_type, self.kontragent), as_dict=True)

            current_balance = balance[0].balance if balance and balance[0].balance else 0

            if current_balance <= 0:
                frappe.msgprint(
                    _("⚠️ Diqqat: '{0}' ({1}) uchun avans balansi topilmadi yoki 0.<br><br>"
                      "Bu operatsiya odatda <b>avans qaytishi</b> uchun ishlatiladi — "
                      "ya'ni avval siz ularga pul bergansiz (Расход), endi ular qaytaryapti.<br><br>"
                      "Joriy balans: {2}<br><br>"
                      "Agar bu oddiy daromad bo'lsa, 'Customer' tanlang.").format(
                        self.kontragent, self.party_type, current_balance
                    ),
                    title=_("Avans qaytishi haqida"),
                    indicator="orange",
                )
        except Exception:
            # Ogohlantirish yiqilsa ham hujjat saqlanaversin
            pass

    def clear_irrelevant_fields(self):
        """Oborot ga qarab keraksiz maydonlarni tozalash."""
        if self.oborot == "Перемещение":
            self.party_type = None
            self.kontragent = None
            self.expense_kontragent = None
            self.filial = None
            self.source_account = None
            self.source_balance = 0
            self.via_sklad = 0
        else:
            self.transfer_source_display = None
            self.transfer_source_balance = 0
            self.target_account = None
            self.target_balance = 0
            self.payment_account_2 = None

            if self.party_type != "Расходы":
                self.filial = None

    # -------------------------------------------------------------------------
    # OQIMNI ANIQLASH
    # -------------------------------------------------------------------------

    def _counterparty_company(self):
        """Kontragent ichki Customer/Supplier bo'lsa — u ifodalovchi kompaniya."""
        return get_represented_company(self.party_type, self.kontragent)

    def _is_internal_settlement(self):
        """A) Boshqa kompaniya bilan hisob-kitob (ichki kontragent orqali)."""
        if self.oborot not in ("Приход", "Расход") or not self.company:
            return False
        target = self._counterparty_company()
        return bool(target and target != self.company)

    def _is_supplier_via_sklad(self):
        """B) Filial kassasidan supplierga Sklad nomidan to'lov."""
        if not self.via_sklad or self.oborot != "Расход" or self.party_type != "Supplier":
            return False
        if not self.kontragent or self._counterparty_company():
            return False
        sklad = get_sklad_company()
        return bool(sklad and self.company and self.company != sklad)

    def _expense_company(self):
        """Xarajat kimning kitobiga yoziladi: filial kompaniyasi yoki kassa kompaniyasi."""
        if self.party_type == "Расходы" and self.filial:
            company = frappe.db.get_value("Kassa Filial", self.filial, "company")
            # Guruh kompaniyasi xarajat yuritmaydi — eski filiallar (bitta
            # kompaniyali davrda guruhga bog'langan) kassa kompaniyasida qoladi.
            if company and not is_group_company(company):
                return company
        return self.company

    def _is_intercompany_expense(self):
        """C) «Расходы» + boshqa kompaniya filiali."""
        if self.oborot not in ("Приход", "Расход") or self.party_type != "Расходы" or not self.filial:
            return False
        target = self._expense_company()
        return bool(target and self.company and target != self.company)

    def _employee_company(self):
        if self.party_type != "Employee" or not self.kontragent:
            return None
        return frappe.db.get_value("Employee", self.kontragent, "company")

    def _is_cross_company_employee(self):
        """D) Ishchi boshqa kompaniyada ishlaydi (Employee.company)."""
        if self.oborot not in ("Приход", "Расход"):
            return False
        emp_company = self._employee_company()
        return bool(emp_company and self.company and emp_company != self.company)

    # =========================================================================
    # ACCOUNTING (Payment Entry / Journal Entry)
    # =========================================================================

    def on_submit(self):
        """Submit bo'lganda mos buxgalteriya hujjat(lar)ini yaratish."""
        # Himoya: duplicate/amend orqali nusxalangan eski havolalarni tozalaymiz.
        self._reset_accounting_links()

        if self._is_intercompany_expense():
            self.create_intercompany_expense()
        elif self._is_internal_settlement():
            self.create_internal_settlement()
        elif self._is_supplier_via_sklad():
            self.create_supplier_payment_via_sklad()
        elif self._is_cross_company_employee():
            self.create_cross_company_employee_payment()
        elif self.oborot in ("Приход", "Расход") and self.party_type in PARTY_TYPES_PE:
            self.create_payment_entry()
        else:
            self.create_journal_entry()

    def on_cancel(self):
        """Cancel bo'lganda yaratilgan barcha hujjatlarni bekor qilish."""
        self.cancel_accounting_documents()

    def _reset_accounting_links(self):
        """Buxgalteriya havola maydonlarini bo'shatish.

        Duplicate/amend qilinganda bu maydonlar asl hujjatdan nusxalanib qolishi
        mumkin va cancel paytida ASL hujjatning PE/JE'sini xato bekor qilardi.
        """
        for fieldname in self.ACCOUNTING_LINK_FIELDS:
            self.set(fieldname, None)
        if not self.is_new():
            frappe.db.set_value(
                "Kassa",
                self.name,
                {f: None for f in self.ACCOUNTING_LINK_FIELDS},
                update_modified=False,
            )

    def _set_links(self, **links):
        """Yaratilgan hujjatlarni DB'ga va xotiradagi hujjatga yozish."""
        frappe.db.set_value("Kassa", self.name, links)
        for fieldname, value in links.items():
            self.set(fieldname, value)

    def create_journal_entry(self):
        """«Расходы», divident va Перемещение uchun Journal Entry yaratish."""
        if not self.company:
            frappe.throw(_("Company topilmadi"))

        je = frappe.new_doc("Journal Entry")
        je.voucher_type = "Journal Entry"
        je.posting_date = self.date
        je.company = self.company
        je.user_remark = f"Kassa: {self.name} - {self.oborot}"
        if self.primechaniya:
            je.user_remark += f" | {self.primechaniya}"
        self._tag(je)

        if self.oborot == "Перемещение":
            self._add_transfer_entries(je)
        elif self.oborot == "Приход":
            self._add_income_entries(je)
        elif self.oborot == "Расход":
            self._add_expense_entries(je)

        je.insert(ignore_permissions=True)
        je.submit()

        self._set_links(journal_entry=je.name)
        frappe.msgprint(_("Journal Entry yaratildi: {0}").format(_je_link(je.name)), indicator="green")

    def create_payment_entry(self):
        """Kontragent bilan pul muomalasi uchun Payment Entry yaratish.

        Приход -> Receive (Дт kassa / Кт party)
        Расход -> Pay     (Дт party / Кт kassa)
        """
        if not self.company:
            frappe.throw(_("Company topilmadi"))
        if not self.kontragent:
            frappe.throw(_("Kontragent tanlanmagan"))

        party_account = self._get_party_account()
        if not party_account:
            frappe.throw(_("'{0}' uchun hisob (Account) topilmadi").format(self.kontragent))

        payment_type = "Receive" if self.oborot == "Приход" else "Pay"
        if payment_type == "Receive":
            paid_from, paid_to = party_account, self.payment_account
        else:
            paid_from, paid_to = self.payment_account, party_account

        pe_name = self._submit_payment_entry(
            payment_type=payment_type,
            company=self.company,
            mode_of_payment=self.source_account,
            party_type=self.party_type,
            party=self.kontragent,
            paid_from=paid_from,
            paid_to=paid_to,
        )
        self._set_links(payment_entry=pe_name)
        frappe.msgprint(_("Payment Entry yaratildi: {0}").format(_pe_link(pe_name)), indicator="green")

    # -------------------------------------------------------------------------
    # KOMPANIYALARARO PUL KO'CHIRISH (barcha oqimlarning asosi)
    # -------------------------------------------------------------------------

    def _other_company_cash(self, company):
        """Ikkinchi kompaniyaning kassasi -> (account, mop).

        Xarajat filialida o'z kassasi (Kassa Filial.mode_of_payment) ko'rsatilgan
        bo'lsa — o'sha, aks holda kompaniya kassasi (`Ozturk Settings`).
        """
        if self.party_type == "Расходы" and self.filial:
            mop = frappe.db.get_value("Kassa Filial", self.filial, "mode_of_payment")
            if mop and frappe.db.get_value("Kassa Filial", self.filial, "company") == company:
                info = resolve_mop_account(mop, company, throw=False)
                if info["account"]:
                    return info["account"], mop
        return get_company_cash(company)

    def _move_cash(self, from_company, from_cash, from_mop, to_company, to_cash, to_mop):
        """Pulni bir kompaniya kassasidan boshqasiga ko'chirish — 2 Payment Entry.

          from: Pay     Дт [to_company kontragenti] / Кт from_cash
          to:   Receive Дт to_cash / Кт [from_company kontragenti]

        Kontragentlar `intercompany_party` bo'yicha: Sklad kitobida filial =
        Customer (Debtors), filial kitobida Sklad = Supplier (Creditors) —
        Branch Stock Transfer qarzlari aynan shu hisoblarda.
        Qaytaradi (pe_from, pe_to).
        """
        from erpnext.accounts.party import get_party_account

        from_pt, from_party = intercompany_party(to_company, from_company)
        to_pt, to_party = intercompany_party(from_company, to_company)

        pe_from = self._submit_payment_entry(
            payment_type="Pay", company=from_company, mode_of_payment=from_mop,
            party_type=from_pt, party=from_party,
            paid_from=from_cash, paid_to=get_party_account(from_pt, from_party, from_company),
        )
        pe_to = self._submit_payment_entry(
            payment_type="Receive", company=to_company, mode_of_payment=to_mop,
            party_type=to_pt, party=to_party,
            paid_from=get_party_account(to_pt, to_party, to_company), paid_to=to_cash,
        )
        return pe_from, pe_to

    # -------------------------------------------------------------------------
    # A) SKLAD <-> FILIAL HISOB-KITOBI
    # -------------------------------------------------------------------------

    def create_internal_settlement(self):
        """Boshqa kompaniya bilan naqd hisob-kitob — ikkala kitobda PE.

        Расход (biz -> ular), masalan filial Sklad'ga tovar qarzini to'laydi:
          1) PE Pay     (filial): Дт Creditors[Sklad] / Кт filial kassa
          2) PE Receive (Sklad):  Дт Sklad kassa      / Кт Debtors[filial]
        Приход (ular -> biz) — teskari yo'nalish.
        """
        target = self._counterparty_company()
        target_cash, target_mop = self._other_company_cash(target)

        if self.oborot == "Расход":
            pe_ours, pe_theirs = self._move_cash(
                self.company, self.payment_account, self.source_account,
                target, target_cash, target_mop,
            )
        else:
            pe_theirs, pe_ours = self._move_cash(
                target, target_cash, target_mop,
                self.company, self.payment_account, self.source_account,
            )

        self._set_links(payment_entry=pe_ours, payment_entry_receive=pe_theirs)
        frappe.msgprint(
            _("Kompaniyalararo hisob-kitob ({0} ↔ {1}):<br>PE ({0}): {2}<br>PE ({1}): {3}").format(
                self.company, target, _pe_link(pe_ours), _pe_link(pe_theirs)
            ),
            indicator="green",
        )

    # -------------------------------------------------------------------------
    # B) SUPPLIER'GA SKLAD NOMIDAN TO'LOV
    # -------------------------------------------------------------------------

    def create_supplier_payment_via_sklad(self):
        """Filial kassasidan Sklad supplieriga to'lov.

          1) PE Pay     (filial): Дт Creditors[Sklad]    / Кт filial kassa
          2) PE Receive (Sklad):  Дт Sklad kassa         / Кт Debtors[filial]
          3) PE Pay     (Sklad):  Дт Creditors[supplier] / Кт Sklad kassa
        Sklad kassasida pul kirib-chiqadi (qoldiq o'zgarmaydi), filialning
        Sklad'ga qarzi kamayadi, Sklad'ning supplierga qarzi yopiladi.
        """
        from erpnext.accounts.party import get_party_account

        sklad = get_sklad_company()
        sklad_cash, sklad_mop = get_company_cash(sklad)

        pe_pay, pe_recv = self._move_cash(
            self.company, self.payment_account, self.source_account,
            sklad, sklad_cash, sklad_mop,
        )
        pe_supplier = self._submit_payment_entry(
            payment_type="Pay", company=sklad, mode_of_payment=sklad_mop,
            party_type="Supplier", party=self.kontragent,
            paid_from=sklad_cash, paid_to=get_party_account("Supplier", self.kontragent, sklad),
        )

        self._set_links(payment_entry=pe_pay, payment_entry_receive=pe_recv,
                        payment_entry_supplier=pe_supplier)
        frappe.msgprint(
            _("Sklad nomidan to'lov:<br>PE ({0}): {1}<br>PE ({2}): {3}<br>PE Supplier ({2}): {4}").format(
                self.company, _pe_link(pe_pay), sklad, _pe_link(pe_recv), _pe_link(pe_supplier)
            ),
            indicator="green",
        )

    # -------------------------------------------------------------------------
    # C) KOMPANIYALARARO XARAJAT
    # -------------------------------------------------------------------------

    def create_intercompany_expense(self):
        """Xarajat boshqa kompaniya kitobida — pul o'sha kassaga o'tib, chiqadi.

        Расход (to'lovchi -> xarajat kompaniyasi):
          1) PE Pay     (to'lovchi): Дт [xarajat kompaniyasi] / Кт to'lovchi kassa
          2) PE Receive (xarajat):   Дт xarajat kassasi       / Кт [to'lovchi]
          3) JE         (xarajat):   Дт xarajat hisobi        / Кт xarajat kassasi
        Приход (xarajat qaytishi) — teskari.
        """
        target = self._expense_company()
        target_cash, target_mop = self._other_company_cash(target)

        if self.oborot == "Расход":
            pe_ours, pe_theirs = self._move_cash(
                self.company, self.payment_account, self.source_account,
                target, target_cash, target_mop,
            )
            je_name = self._submit_expense_je(target, self.expense_kontragent, target_cash, expense_debit=True)
        else:
            je_name = self._submit_expense_je(target, self.expense_kontragent, target_cash, expense_debit=False)
            pe_theirs, pe_ours = self._move_cash(
                target, target_cash, target_mop,
                self.company, self.payment_account, self.source_account,
            )

        self._set_links(payment_entry=pe_ours, payment_entry_receive=pe_theirs, journal_entry=je_name)
        frappe.msgprint(
            _("Kompaniyalararo xarajat ({0} → {1}):<br>PE ({0}): {2}<br>PE ({1}): {3}<br>JE ({1}): {4}").format(
                self.company, target, _pe_link(pe_ours), _pe_link(pe_theirs), _je_link(je_name)
            ),
            indicator="green",
        )

    # -------------------------------------------------------------------------
    # D) BOSHQA KOMPANIYA ISHCHISIGA TO'LOV
    # -------------------------------------------------------------------------

    def create_cross_company_employee_payment(self):
        """Ishchi o'z kompaniyasi (Employee.company) kitobida to'lanadi.

        Расход: pul kassa kompaniyasidan ishchi kompaniyasiga o'tadi (2 PE),
                keyin ishchi kompaniyasi ishchiga to'laydi (1 PE).
        Приход: ishchi pul qaytaradi — ishchi kompaniyasi qabul qiladi (1 PE),
                keyin pul kassa kompaniyasiga o'tadi (2 PE).
        """
        from erpnext.accounts.party import get_party_account

        emp_company = self._employee_company()
        emp_cash, emp_mop = self._other_company_cash(emp_company)
        emp_account = get_party_account("Employee", self.kontragent, emp_company)

        if self.oborot == "Расход":
            pe_ours, pe_theirs = self._move_cash(
                self.company, self.payment_account, self.source_account,
                emp_company, emp_cash, emp_mop,
            )
            pe_emp = self._submit_payment_entry(
                payment_type="Pay", company=emp_company, mode_of_payment=emp_mop,
                party_type="Employee", party=self.kontragent,
                paid_from=emp_cash, paid_to=emp_account,
            )
        else:
            pe_emp = self._submit_payment_entry(
                payment_type="Receive", company=emp_company, mode_of_payment=emp_mop,
                party_type="Employee", party=self.kontragent,
                paid_from=emp_account, paid_to=emp_cash,
            )
            pe_theirs, pe_ours = self._move_cash(
                emp_company, emp_cash, emp_mop,
                self.company, self.payment_account, self.source_account,
            )

        self._set_links(payment_entry=pe_ours, payment_entry_receive=pe_theirs,
                        payment_entry_supplier=pe_emp)
        frappe.msgprint(
            _("Ishchi to'lovi ({0} → {1}):<br>PE ({0}): {2}<br>PE ({1}): {3}<br>PE Employee ({1}): {4}").format(
                self.company, emp_company, _pe_link(pe_ours), _pe_link(pe_theirs), _pe_link(pe_emp)
            ),
            indicator="green",
        )

    # -------------------------------------------------------------------------
    # HUJJAT YARATISH YORDAMCHILARI
    # -------------------------------------------------------------------------

    def _tag(self, doc):
        """PE/JE'ga BOSILADIGAN Kassa havolasi (after_migrate maydonni yaratadi)."""
        if frappe.db.has_column(doc.doctype, "custom_kassa"):
            doc.custom_kassa = self.name

    def _submit_payment_entry(self, payment_type, company, mode_of_payment,
                              party_type, party, paid_from, paid_to):
        """Payment Entry yaratib submit qiladi, nomini qaytaradi."""
        pe = frappe.new_doc("Payment Entry")
        pe.payment_type = payment_type
        pe.company = company
        pe.posting_date = self.date
        pe.mode_of_payment = mode_of_payment
        pe.party_type = party_type
        pe.party = party
        pe.paid_from = paid_from
        pe.paid_to = paid_to
        pe.paid_from_account_currency = frappe.db.get_value("Account", paid_from, "account_currency")
        pe.paid_to_account_currency = frappe.db.get_value("Account", paid_to, "account_currency")
        pe.paid_amount = self.summa
        pe.received_amount = self.summa
        pe.source_exchange_rate = 1
        pe.target_exchange_rate = 1
        pe.reference_no = self.name
        pe.reference_date = self.date
        self._tag(pe)
        if self.primechaniya:
            pe.remarks = self.primechaniya
        pe.insert(ignore_permissions=True)
        pe.submit()
        return pe.name

    def _submit_expense_je(self, company, expense_account, cash_account, expense_debit=True):
        """Xarajat Journal Entry (boshqa kompaniya kitobida).

        expense_debit=True  -> Дт xarajat / Кт kassa (Расход)
        expense_debit=False -> Дт kassa / Кт xarajat (Приход, xarajat kamayadi)
        """
        je = frappe.new_doc("Journal Entry")
        je.voucher_type = "Journal Entry"
        je.posting_date = self.date
        je.company = company
        je.user_remark = f"Kassa: {self.name} - {self.oborot} (inter-company)"
        if self.primechaniya:
            je.user_remark += f" | {self.primechaniya}"
        self._tag(je)
        exp_dr, exp_cr = (self.summa, 0) if expense_debit else (0, self.summa)
        je.append("accounts", {
            "account": expense_account,
            "debit_in_account_currency": exp_dr,
            "credit_in_account_currency": exp_cr,
        })
        je.append("accounts", {
            "account": cash_account,
            "debit_in_account_currency": exp_cr,
            "credit_in_account_currency": exp_dr,
        })
        je.insert(ignore_permissions=True)
        je.submit()
        return je.name

    def _add_transfer_entries(self, je):
        """Перемещение — hisobdan hisobga o'tkazma."""
        # Maqsad — Debit
        je.append("accounts", {
            "account": self.payment_account_2,
            "debit_in_account_currency": self.summa,
            "credit_in_account_currency": 0,
        })
        # Manba — Credit
        je.append("accounts", {
            "account": self.payment_account,
            "debit_in_account_currency": 0,
            "credit_in_account_currency": self.summa,
        })

    def _add_income_entries(self, je):
        """Приход — pul kelishi."""
        # Kassa — Debit
        je.append("accounts", {
            "account": self.payment_account,
            "debit_in_account_currency": self.summa,
            "credit_in_account_currency": 0,
        })

        # Kontragent — Credit
        if self.party_type in PARTY_TYPES_PE:
            je.append("accounts", {
                "account": self._get_party_account(),
                "party_type": self.party_type,
                "party": self.kontragent,
                "debit_in_account_currency": 0,
                "credit_in_account_currency": self.summa,
            })
        elif self.party_type == "Расходы":
            je.append("accounts", {
                "account": self.expense_kontragent,
                "debit_in_account_currency": 0,
                "credit_in_account_currency": self.summa,
            })
        elif self._is_dividend_party_type():
            je.append("accounts", {
                "account": self._get_dividend_account(),
                "debit_in_account_currency": 0,
                "credit_in_account_currency": self.summa,
            })

    def _add_expense_entries(self, je):
        """Расход — pul chiqishi."""
        # Kontragent — Debit
        if self.party_type == "Расходы":
            je.append("accounts", {
                "account": self.expense_kontragent,
                "debit_in_account_currency": self.summa,
                "credit_in_account_currency": 0,
            })
        elif self.party_type in PARTY_TYPES_PE:
            je.append("accounts", {
                "account": self._get_party_account(),
                "party_type": self.party_type,
                "party": self.kontragent,
                "debit_in_account_currency": self.summa,
                "credit_in_account_currency": 0,
            })
        elif self._is_dividend_party_type():
            je.append("accounts", {
                "account": self._get_dividend_account(),
                "debit_in_account_currency": self.summa,
                "credit_in_account_currency": 0,
            })

        # Kassa — Credit
        je.append("accounts", {
            "account": self.payment_account,
            "debit_in_account_currency": 0,
            "credit_in_account_currency": self.summa,
        })

    def _is_dividend_party_type(self):
        """Party Type «Divident ...» bilan boshlansa — divident operatsiyasi."""
        return bool(self.party_type) and self.party_type.startswith(DIVIDEND_PARTY_PREFIX)

    def _get_dividend_account(self):
        """Divident Party Type nomiga mos Equity hisobini qaytaradi.

        Masalan Party Type «Divident Aziz» -> account_name «Divident Aziz»
        bo'lgan Equity hisobi. Hisob raqami qattiq yozilmagan — admin hisobni
        istalgan raqam bilan yaratishi mumkin, faqat NOMI mos kelsin.
        """
        acc = frappe.db.get_value(
            "Account",
            {
                "company": self.company,
                "account_name": self.party_type,
                "is_group": 0,
                "root_type": "Equity",
            },
            "name",
        )
        if not acc:
            frappe.throw(
                _("'{0}' uchun '{1}' kompaniyasida shu nomli Equity (kapital) hisobi topilmadi. "
                  "Hisoblar rejasida «{0}» nomli hisob yarating.").format(
                    self.party_type, self.company
                )
            )
        return acc

    def _get_party_account(self):
        """Party uchun account olish."""
        from erpnext.accounts.party import get_party_account

        return get_party_account(self.party_type, self.kontragent, self.company)

    def cancel_accounting_documents(self):
        """Yaratilgan hujjatlarni teskari tartibda bekor qilish.

        Kompaniyalararo oqimda: JE -> PE (supplier/ishchi) -> PE (2-kompaniya)
        -> PE (kassa kompaniyasi). Hujjatlar `ignore_permissions` bilan
        yaratilgan — bekor qilish ham shunday (foydalanuvchida ikkinchi
        kompaniyaga ruxsat bo'lmasligi mumkin).
        """
        targets = [
            ("journal_entry", "Journal Entry"),
            ("payment_entry_supplier", "Payment Entry"),
            ("payment_entry_receive", "Payment Entry"),
            ("payment_entry", "Payment Entry"),
        ]
        for fieldname, doctype in targets:
            name = self.get(fieldname)
            if not name or not frappe.db.exists(doctype, name):
                continue
            doc = frappe.get_doc(doctype, name)
            if doc.docstatus == 1:
                doc.flags.ignore_permissions = True
                doc.cancel()
                frappe.msgprint(
                    _("{0} bekor qilindi: {1}").format(doctype, name),
                    indicator="orange",
                )


# =============================================================================
# WHITELISTED METHODS
# =============================================================================

@frappe.whitelist()
def get_mode_of_payment_info(mode_of_payment: str, company: str = None) -> dict:
    """Kassa (MoP) -> hisob, kompaniya va balans.

    `company` berilsa — AYNAN shu kompaniya qatori. Berilmasa va MoP bir
    nechta kompaniyada bo'lsa `ambiguous=True` va `companies` qaytadi —
    forma foydalanuvchidan kompaniyani so'raydi.
    """
    empty = {"account": "", "company": "", "balance": 0, "companies": [], "ambiguous": False}
    if not mode_of_payment:
        return empty

    info = resolve_mop_account(mode_of_payment, company or None, throw=False)
    info["balance"] = get_account_balance(info["account"]) if info["account"] else 0
    return info


@frappe.whitelist()
def get_kassa_context() -> dict:
    """Forma uchun: Sklad kompaniyasi (`Ozturk Settings`)."""
    return {"sklad_company": get_sklad_company()}


@frappe.whitelist()
def get_account_balance(account: str) -> float:
    """Account balansini olish."""
    if not account:
        return 0

    balance = frappe.db.sql("""
        SELECT SUM(debit) - SUM(credit) as balance
        FROM `tabGL Entry`
        WHERE account = %s AND is_cancelled = 0
    """, account, as_dict=True)

    return balance[0].balance if balance and balance[0].balance else 0


@frappe.whitelist()
def get_mode_of_payments_by_company(company: str, exclude_mop: str = None) -> list:
    """Berilgan company uchun Mode of Payment ro'yxatini qaytarish."""
    if not company:
        return []

    mop_list = frappe.db.sql("""
        SELECT DISTINCT mopa.parent as name
        FROM `tabMode of Payment Account` mopa
        INNER JOIN `tabMode of Payment` mop ON mop.name = mopa.parent
        WHERE mopa.company = %(company)s
            AND mopa.default_account IS NOT NULL
            AND mop.enabled = 1
    """, {"company": company}, as_dict=True)

    result = [m.name for m in mop_list]

    if exclude_mop and exclude_mop in result:
        result.remove(exclude_mop)

    return result


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_filtered_mode_of_payments(doctype, txt, searchfield, start, page_len, filters):
    """Link field uchun Mode of Payment query (target_account va DDS filtri).

    Filters:
        - company: faqat shu company'ga tegishli MoP lar
        - exclude: bu MoP ni ro'yxatdan chiqarish
    """
    company = filters.get("company", "")
    exclude = filters.get("exclude", "")

    if not company:
        return frappe.db.sql("""
            SELECT name
            FROM `tabMode of Payment`
            WHERE enabled = 1
                AND name LIKE %(txt)s
                AND name != %(exclude)s
            ORDER BY name
            LIMIT %(start)s, %(page_len)s
        """, {
            "txt": f"%{txt}%",
            "exclude": exclude or "",
            "start": start,
            "page_len": page_len,
        })

    return frappe.db.sql("""
        SELECT DISTINCT mopa.parent as name
        FROM `tabMode of Payment Account` mopa
        INNER JOIN `tabMode of Payment` mop ON mop.name = mopa.parent
        WHERE mopa.company = %(company)s
            AND mopa.default_account IS NOT NULL
            AND mop.enabled = 1
            AND mopa.parent LIKE %(txt)s
            AND mopa.parent != %(exclude)s
        ORDER BY mopa.parent
        LIMIT %(start)s, %(page_len)s
    """, {
        "company": company,
        "txt": f"%{txt}%",
        "exclude": exclude or "",
        "start": start,
        "page_len": page_len,
    })


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_filial_expense_accounts(doctype, txt, searchfield, start, page_len, filters):
    """«Xarajat kontragenti» uchun query.

    - Filial tanlangan va uning «Xarajat guruhi» sozlangan bo'lsa — faqat shu
      guruh ostidagi leaf xarajat hisoblari.
    - Aks holda — kompaniyaning barcha leaf xarajat hisoblari.
    - Kompaniya: filialniki (kompaniyalararo xarajat), bo'lmasa kassa kompaniyasi.

    (Jazira'dan farqi: filial MAJBURIY emas — tanlanmasa xarajat kassa
    kompaniyasi kitobida.)
    """
    filial = filters.get("filial")
    company = filters.get("company")

    params = {
        "txt": f"%{txt}%",
        "start": start,
        "page_len": page_len,
    }
    conds = ["is_group = 0", "root_type = 'Expense'", "name LIKE %(txt)s"]

    grp = None
    if filial:
        fc = frappe.db.get_value(
            "Kassa Filial", filial, ["company", "expense_group"], as_dict=True
        )
        if fc:
            company = fc.company or company
            if fc.expense_group:
                grp = frappe.db.get_value(
                    "Account", fc.expense_group, ["lft", "rgt"], as_dict=True
                )

    if grp:
        conds.append("lft > %(lft)s AND rgt < %(rgt)s")
        params["lft"] = grp.lft
        params["rgt"] = grp.rgt

    if not company:
        company = frappe.defaults.get_user_default("Company")
    if company:
        conds.append("company = %(company)s")
        params["company"] = company

    return frappe.db.sql(f"""
        SELECT name
        FROM `tabAccount`
        WHERE {" AND ".join(conds)}
        ORDER BY name
        LIMIT %(start)s, %(page_len)s
    """, params)
