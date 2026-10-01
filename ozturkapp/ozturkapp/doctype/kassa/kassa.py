# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""
Kassa — naqd pul harakati (Приход / Расход / Перемещение), bitta kompaniyali.

Öztürk BITTA kompaniya sifatida ishlaydi («O'zturk Maksim Gorkiy»). Jazira'dagi
kompaniyalararo oqimlar (Sklad orqali to'lov, filiallararo xarajat, boshqa
kompaniya ishchisi, ichki kontragent bilan hisob-kitob) bu yerda YO'Q — ular
qo'shilib, keyin olib tashlangan (`Ozturk Settings` ham o'chirilgan, patch:
`patches/v1_0/remove_intercompany_kassa`).

Oqimlar:
  * Приход/Расход + Customer/Supplier/Employee/Shareholder -> Payment Entry
  * Приход/Расход + «Расходы»                              -> Journal Entry
  * Приход/Расход + «Divident ...»                         -> Journal Entry
  * Перемещение (hisobdan hisobga, bitta kompaniya ichida) -> Journal Entry

Kompaniyani aniqlash (saqlangan tuzatish). Avval Mode of Payment'ning
TASODIFIY birinchi qatori olinardi (`get_value` tartibsiz). Saytda guruh
kompaniyasi («O'zturk») ham bor va ba'zi kassalarda (masalan «click») bir
nechta kompaniya qatori bor — kassa goh bir, goh boshqa kompaniya kitobiga
yozilardi. Endi `resolve_mop_account`: qatorlar `idx` bo'yicha, guruh
kompaniyalari e'tiborga olinmaydi, baribir bir nechta qolsa — «Kompaniya»
maydonidagi qator AYNAN olinadi.

Yaratilgan PE/JE `custom_kassa` maydoni orqali Kassa'ga bog'lanadi
(`setup/kassa_setup.py: ensure_kassa_link_fields`) — Kassa formasining
«Connections» bo'limi shu maydondan o'qiydi.
"""

import frappe
from frappe import _
from frappe.model.document import Document

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
    ACCOUNTING_LINK_FIELDS = ("payment_entry", "journal_entry")

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
        self.validate_transfer()
        self.validate_expense_kontragent()
        self.clear_irrelevant_fields()

    def validate_summa(self):
        """Summa > 0 bo'lishi kerak."""
        if self.summa <= 0:
            frappe.throw(_("Summa 0 dan katta bo'lishi kerak"))

    # -------------------------------------------------------------------------
    # KOMPANIYA VA HISOBLAR
    # -------------------------------------------------------------------------

    def set_company_and_accounts(self):
        """Kompaniya + kassa hisobini aniqlash (deterministik).

        * Kassa (MoP) bitta (guruh bo'lmagan) kompaniyada hisobga ega ->
          kompaniya shu (forma standart kompaniyani oldindan qo'yadi, lekin
          bitta kompaniyali kassada ikkinchi variant yo'q — kassa ustun).
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

        # Перемещение maqsadi — faqat shu kompaniya qatori (JE bitta kompaniyada)
        if self.oborot == "Перемещение" and self.target_account and self.company:
            self.payment_account_2 = resolve_mop_account(self.target_account, self.company)["account"]

        # Приход + Supplier/Employee/Shareholder uchun ogohlantirish
        if self.oborot == "Приход" and self.party_type in ("Supplier", "Employee", "Shareholder"):
            self._warn_prihod_payable_party()

    def _resolve_kassa(self, mop):
        """Bitta kompaniyali kassa -> o'sha; bir nechta -> `self.company` qatori."""
        info = resolve_mop_account(mop, throw=False)
        if info["account"]:
            return info
        # Hisob umuman yo'q yoki kompaniya tanlanmagan — tushunarli xato bilan
        return resolve_mop_account(mop, self.company if info["companies"] else None)

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
        # Ikki xil kassa bitta hisobga bog'langan bo'lishi mumkin — JE Дт/Кт
        # bir hisobda bo'lib, ERPNext tushunarsiz xato berardi.
        if self.payment_account and self.payment_account == self.payment_account_2:
            frappe.throw(_("Ikkala kassa bir xil hisobga ({0}) bog'langan — o'tkazmaning ma'nosi yo'q.").format(
                self.payment_account))

    def validate_expense_kontragent(self):
        """FAQAT «Расходы» uchun tekshiruv.

        - Xarajat hisobi «Expense» root turida bo'lishi kerak.
        - Amortizatsiya (Depreciation) hisobi kassadan to'lanmaydi -> taqiqlanadi.
        - Hisob kassa kompaniyasiga tegishli bo'lishi kerak.
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

        if self.company and account_data.company != self.company:
            frappe.throw(
                _("Xarajat hisobi '{0}' kompaniyasiga tegishli bo'lishi kerak.").format(self.company)
            )

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
        else:
            self.transfer_source_display = None
            self.transfer_source_balance = 0
            self.target_account = None
            self.target_balance = 0
            self.payment_account_2 = None

            if self.party_type != "Расходы":
                self.filial = None

    # =========================================================================
    # ACCOUNTING (Payment Entry / Journal Entry)
    # =========================================================================

    def on_submit(self):
        """Submit bo'lganda mos buxgalteriya hujjatini yaratish."""
        # Himoya: duplicate/amend orqali nusxalangan eski havolalarni tozalaymiz.
        self._reset_accounting_links()

        if self.oborot in ("Приход", "Расход") and self.party_type in PARTY_TYPES_PE:
            self.create_payment_entry()
        else:
            self.create_journal_entry()

    def on_cancel(self):
        """Cancel bo'lganda yaratilgan hujjatni (PE yoki JE) bekor qilish."""
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
        """Yaratilgan hujjatni DB'ga va xotiradagi hujjatga yozish."""
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
    # HUJJAT YARATISH YORDAMCHILARI
    # -------------------------------------------------------------------------

    def _tag(self, doc):
        """PE/JE'ga BOSILADIGAN Kassa havolasi (after_migrate maydonni yaratadi).

        `reference_no`/`user_remark` oddiy matn — bosib o'tib bo'lmaydi va
        hisobot qaysi kassa ekanini ishonchli topa olmaydi.
        """
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
        """Yaratilgan hujjatni (JE yoki PE) bekor qilish.

        Hujjat `ignore_permissions` bilan yaratilgan — bekor qilish ham shunday
        (kassirda PE/JE'ni bekor qilish ruxsati bo'lmasligi mumkin).
        """
        targets = [
            ("journal_entry", "Journal Entry"),
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
# MODE OF PAYMENT -> (kompaniya, hisob)
# =============================================================================

def is_group_company(company):
    """Guruh kompaniyasi (masalan «O'zturk») — o'zi savdo/kassa qilmaydi."""
    return bool(company and frappe.get_cached_value("Company", company, "is_group"))


def get_mop_accounts(mode_of_payment):
    """MoP'ning hisob qatorlari, `idx` tartibida (deterministik).

    Qaytaradi: [{"company", "account"}, ...] — faqat hisobi ko'rsatilganlar.
    """
    if not mode_of_payment:
        return []
    return frappe.get_all(
        "Mode of Payment Account",
        filters={
            "parent": mode_of_payment,
            "parenttype": "Mode of Payment",
            "default_account": ["is", "set"],
        },
        fields=["company", "default_account as account"],
        order_by="idx asc",
    )


def resolve_mop_account(mode_of_payment, company=None, throw=True):
    """MoP + kompaniya -> {"company", "account", "companies", "ambiguous"}.

    ESKI XATO. Avval birinchi tasodifiy qator olinardi (`get_value` tartibsiz),
    kompaniya ham shu qatordan chiqarilardi — bir nechta kompaniya qatori bor
    kassa goh bir, goh boshqa kompaniya kitobiga yozilardi.

    Endi:
      * kompaniya berilgan  -> AYNAN shu kompaniya qatori (yo'q bo'lsa xato);
      * berilmagan, bitta qator -> o'sha qator;
      * berilmagan, bir nechta -> guruh kompaniyalari chiqarib tashlanadi
        (ular kassa yuritmaydi); baribir bir nechta qolsa — kompaniya so'raladi.
    """
    rows = get_mop_accounts(mode_of_payment)
    companies = [r.company for r in rows]
    result = {"company": "", "account": "", "companies": companies, "ambiguous": False}

    if company:
        row = next((r for r in rows if r.company == company), None)
        if not row:
            if throw:
                frappe.throw(
                    _("'{0}' kassasi '{1}' kompaniyasi uchun hisobga ega emas. "
                      "Mode of Payment «Accounts» jadvalini tekshiring yoki boshqa kassani tanlang.").format(
                        mode_of_payment, company
                    ),
                    title=_("Kassa hisobi topilmadi"),
                )
            return result
        result.update(company=row.company, account=row.account)
        return result

    candidates = rows
    if len(candidates) > 1:
        candidates = [r for r in rows if not is_group_company(r.company)]

    if len(candidates) == 1:
        result.update(company=candidates[0].company, account=candidates[0].account)
        return result

    if not candidates:
        if throw:
            frappe.throw(_("'{0}' uchun hisob (Account) bog'lanmagan. "
                           "Mode of Payment sozlamalarini tekshiring.").format(mode_of_payment))
        return result

    result["ambiguous"] = True
    if throw:
        frappe.throw(
            _("'{0}' kassasi bir nechta kompaniyada hisobga ega ({1}). "
              "Avval «Kompaniya» maydonini tanlang.").format(
                mode_of_payment, ", ".join(c.company for c in candidates)
            ),
            title=_("Kompaniya tanlanmagan"),
        )
    return result


# =============================================================================
# WHITELISTED METHODS
# =============================================================================

@frappe.whitelist()
def get_mode_of_payment_info(mode_of_payment: str, company: str = None) -> dict:
    """Kassa (MoP) -> hisob, kompaniya va balans.

    `company` berilsa — AYNAN shu kompaniya qatori. Berilmasa va MoP bir
    nechta (guruh bo'lmagan) kompaniyada bo'lsa `ambiguous=True` va
    `companies` qaytadi — forma foydalanuvchidan kompaniyani so'raydi.
    """
    empty = {"account": "", "company": "", "balance": 0, "companies": [], "ambiguous": False}
    if not mode_of_payment:
        return empty

    info = resolve_mop_account(mode_of_payment, company or None, throw=False)
    info["balance"] = get_account_balance(info["account"]) if info["account"] else 0
    return info


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

    (Jazira'dan farqi: bitta kompaniya bo'lgani uchun filial MAJBURIY emas.)
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
