# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""
Kassa — bitta kompaniyali (single-company) versiya.

Kompaniya Mode of Payment'dan avtomatik aniqlanadi. Jazira'dagi filiallararo
(inter-company) oqimlar — Sklad orqali to'lov, filiallararo xarajat, employee'ni
boshqa filial kitobida to'lash — bu yerda YO'Q, chunki sayt bitta kompaniyada
ishlaydi.

Oqimlar:
  * Приход/Расход + Customer/Supplier/Employee/Shareholder -> Payment Entry
  * Приход/Расход + «Расходы»                              -> Journal Entry
  * Приход/Расход + «Divident ...»                         -> Journal Entry
  * Перемещение (hisobdan hisobga)                         -> Journal Entry
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


class Kassa(Document):
    """Kassa Document."""

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

    def set_company_and_accounts(self):
        """Mode of Payment'dan company va account olish."""
        if self.oborot == "Перемещение":
            if self.transfer_source_display:
                info = get_mode_of_payment_info(self.transfer_source_display)
                if not info.get("account"):
                    frappe.throw(_("'{0}' uchun hisob topilmadi.").format(self.transfer_source_display))
                self.payment_account = info["account"]
                self.company = info["company"]

            if self.target_account:
                info2 = get_mode_of_payment_info(self.target_account)
                if not info2.get("account"):
                    frappe.throw(_("'{0}' uchun hisob topilmadi.").format(self.target_account))
                self.payment_account_2 = info2["account"]
        else:
            if self.source_account:
                info = get_mode_of_payment_info(self.source_account)
                if not info.get("account"):
                    frappe.throw(_("'{0}' uchun hisob topilmadi.").format(self.source_account))
                self.payment_account = info["account"]
                self.company = info["company"]

        # Приход + Supplier/Employee/Shareholder uchun ogohlantirish
        if self.oborot == "Приход" and self.party_type in ("Supplier", "Employee", "Shareholder"):
            self._warn_prihod_payable_party()

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

    # Submit yaratadigan buxgalteriya havola maydonlari
    ACCOUNTING_LINK_FIELDS = ("payment_entry", "journal_entry")

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

        if self.oborot == "Перемещение":
            self._add_transfer_entries(je)
        elif self.oborot == "Приход":
            self._add_income_entries(je)
        elif self.oborot == "Расход":
            self._add_expense_entries(je)

        je.insert(ignore_permissions=True)
        je.submit()

        frappe.db.set_value("Kassa", self.name, "journal_entry", je.name)

        frappe.msgprint(
            _("Journal Entry yaratildi: {0}").format(
                f'<a href="/app/journal-entry/{je.name}">{je.name}</a>'
            ),
            indicator="green",
        )

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
        frappe.db.set_value("Kassa", self.name, "payment_entry", pe_name)

        frappe.msgprint(
            _("Payment Entry yaratildi: {0}").format(
                f'<a href="/app/payment-entry/{pe_name}">{pe_name}</a>'
            ),
            indicator="green",
        )

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
        """Yaratilgan hujjatlarni bekor qilish."""
        targets = [
            ("journal_entry", "Journal Entry"),
            ("payment_entry", "Payment Entry"),
        ]
        for fieldname, doctype in targets:
            name = self.get(fieldname)
            if not name:
                continue
            doc = frappe.get_doc(doctype, name)
            if doc.docstatus == 1:
                doc.cancel()
                frappe.msgprint(
                    _("{0} bekor qilindi: {1}").format(doctype, name),
                    indicator="orange",
                )


# =============================================================================
# WHITELISTED METHODS
# =============================================================================

@frappe.whitelist()
def get_mode_of_payment_info(mode_of_payment: str) -> dict:
    """Mode of Payment'dan birinchi account, company va balans olish."""
    if not mode_of_payment:
        return {"account": "", "company": "", "balance": 0}

    mopa = frappe.db.get_value(
        "Mode of Payment Account",
        {"parent": mode_of_payment, "default_account": ["is", "set"]},
        ["default_account", "company"],
        as_dict=True,
    )

    if not mopa:
        return {"account": "", "company": "", "balance": 0}

    return {
        "account": mopa.default_account,
        "company": mopa.company,
        "balance": get_account_balance(mopa.default_account),
    }


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
