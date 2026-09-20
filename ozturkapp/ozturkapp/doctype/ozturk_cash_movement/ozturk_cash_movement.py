# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa harakati — smena davomida g'aladonga naqd pul kirishi yoki chiqishi.

Sotuv bo'lmagan har qanday naqd pul harakati: xarajat (muz sotib olish),
inkassatsiya (kassadagi pulni seyfga olish), kassaga sarmoya (mayda pul
qo'shish). Bu yozuvlarsiz smena oxirida kutilgan summa haqiqiy g'aladondan
chetga chiqadi.

NEGA SUBMIT QILINADIGAN (is_submittable)
========================================
Pul harakati tahrirlanmasligi kerak: xato bo'lsa hujjat BEKOR QILINADI
(menejer, smena ochiq bo'lsa) va yangisi yoziladi — ERPNext buxgalteriya
hujjatlaridagi (Journal Entry, Kassa) xuddi shu qoida. Oddiy (submit'siz)
hujjatni kassir yoki menejer jimgina o'zgartirib, yopilgan smena
hisobotini buzishi mumkin edi. Submit — bir vaqtning o'zida "hisobga olindi"
belgisi: kutilgan summa FAQAT `docstatus = 1` harakatlardan hisoblanadi
(`utils/pos_closing.py`), bekor qilingani chiqib ketadi.

BUXGALTERIYA
============
Submit'da ERPNext Journal Entry (`Cash Entry`) yoziladi:

    Chiqim (Out):  Dr  qarshi hisob        Cr  naqd usul hisobi
    Kirim  (In):   Dr  naqd usul hisobi    Cr  qarshi hisob

Naqd usul hisobi — `Mode of Payment Account.default_account` (sotuv
tushumi tushadigan AYNAN o'sha hisob, shuning uchun kassa qoldig'i
buxgalteriyada ham to'g'ri chiqadi). Qarshi hisob — Kompaniyadagi
«Kassa harakati hisobi» (`Company.custom_cash_movement_account`): standart
bo'yicha `setup/cashier_shift_setup.py` yaratadigan «Kassa harakati - <abbr>»
oraliq hisobi; buxgalter uni istalgan hisob bilan almashtira oladi.
Hisob RUNTIME'da yaratilmaydi — hisoblar rejasiga kassirning so'rovi
ichida jimgina hisob qo'shilmaydi.

Bekor qilinsa Journal Entry ham bekor qilinadi. Yopilgan smenaning
harakati bekor QILINMAYDI — u yopish hisobotiga kirgan; tuzatish uchun
keyingi smenada teskari harakat yoziladi.
"""

import math

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

KIND_IN = "In"
KIND_OUT = "Out"

#: Yo'nalish -> ruxsat etilgan turlar. «Inkassatsiya» va «Xarajat» faqat
#: chiqim, «Kassaga qo'shish» faqat kirim bo'lishi mantiqan shart.
CATEGORIES = {
    KIND_IN: ("Kassaga qo'shish", "Boshqa"),
    KIND_OUT: ("Xarajat", "Inkassatsiya", "Boshqa"),
}

#: Sabab shundan qisqa bo'lsa qabul qilinmaydi (`utils/order_cancel.py` bilan bir xil).
MIN_REASON_LENGTH = 3

#: Qarshi hisob saqlanadigan `Company` custom field (`setup/cashier_shift_setup.py`).
COMPANY_ACCOUNT_FIELD = "custom_cash_movement_account"

#: Bitta harakatning eng katta summasi (Currency ustuni (21,9) dan past): `1e30` kabi
#: qiymat bazada `DataError` bilan yiqilmasin, tushunarli xato bilan to'xtasin.
MAX_AMOUNT = 10 ** 11


class OzturkCashMovement(Document):
    def validate(self):
        self.validate_amount()
        self.validate_kind_and_category()
        self.validate_reason()
        shift = self.validate_shift()
        self.validate_mode_of_payment(shift.pos_profile)
        self.resolve_accounts()

    def validate_amount(self):
        amount = flt(self.amount)
        # `nan`/`inf` ham `> 0` tekshiruvidan o'tib ketmasin.
        if not (math.isfinite(amount) and 0 < amount <= MAX_AMOUNT):
            frappe.throw(_("Summa 0 dan katta bo'lishi kerak"), title=_("Summa noto'g'ri"))

        # Valyuta aniqligiga yaxlitlaymiz: `0.004` kabi summa Journal Entry'da nolga
        # yaxlitlanib, kutilgan naqdga esa tiyin-tiyin qo'shilib qolmasin.
        self.amount = flt(amount, self.precision("amount"))
        if not self.amount > 0:
            frappe.throw(_("Summa 0 dan katta bo'lishi kerak"), title=_("Summa noto'g'ri"))

    def validate_kind_and_category(self):
        if self.kind not in CATEGORIES:
            frappe.throw(_("Yo'nalish «In» yoki «Out» bo'lishi kerak"))
        if self.category not in CATEGORIES[self.kind]:
            frappe.throw(
                _("«{0}» yo'nalishi uchun tur quyidagilardan biri bo'lishi kerak: {1}").format(
                    self.kind, ", ".join(CATEGORIES[self.kind])
                ),
                title=_("Tur noto'g'ri"),
            )

    def validate_reason(self):
        self.reason = (self.reason or "").strip()
        if len(self.reason) < MIN_REASON_LENGTH:
            frappe.throw(
                _("Sabab yozilishi shart — u smena hisobotiga tushadi."),
                title=_("Sabab ko'rsatilmagan"),
            )

    def validate_shift(self):
        """Harakat faqat OCHIQ smenaga yoziladi; kompaniya va filial shundan olinadi."""
        shift = frappe.db.get_value(
            "POS Opening Entry",
            self.pos_opening_entry,
            ["docstatus", "status", "pos_profile", "company", "branch"],
            as_dict=True,
        )
        if not shift or shift.docstatus != 1 or shift.status != "Open":
            frappe.throw(
                _("Kassa harakati faqat ochiq smenaga yoziladi: {0}").format(self.pos_opening_entry),
                title=_("Smena yopiq"),
            )
        self.company = shift.company
        self.branch = shift.branch or self.branch
        return shift

    def validate_mode_of_payment(self, pos_profile: str):
        mode = frappe.db.get_value(
            "Mode of Payment", self.mode_of_payment, ["type", "enabled"], as_dict=True
        )
        if not mode or mode.type != "Cash":
            frappe.throw(
                _("'{0}' naqd to'lov usuli emas — kassa harakati faqat naqd pul bilan").format(
                    self.mode_of_payment
                ),
                title=_("Noto'g'ri to'lov usuli"),
            )
        if not frappe.db.exists(
            "POS Payment Method",
            {"parent": pos_profile, "parenttype": "POS Profile", "mode_of_payment": self.mode_of_payment},
        ):
            frappe.throw(
                _("'{0}' usuli '{1}' kassasida yo'q").format(self.mode_of_payment, pos_profile),
                title=_("Noto'g'ri to'lov usuli"),
            )

    def resolve_accounts(self):
        self.cash_account = frappe.db.get_value(
            "Mode of Payment Account",
            {"parent": self.mode_of_payment, "company": self.company},
            "default_account",
        )
        if not self.cash_account:
            frappe.throw(
                _("'{0}' usuli uchun '{1}' kompaniyasida hisob (Account) sozlanmagan").format(
                    self.mode_of_payment, self.company
                ),
                title=_("Kassa hisobi yo'q"),
            )

        self.counter_account = counter_account(self.company)
        if self.counter_account == self.cash_account:
            frappe.throw(
                _("Qarshi hisob kassa hisobining o'zi bo'lishi mumkin emas: {0}").format(
                    self.cash_account
                ),
                title=_("Qarshi hisob noto'g'ri"),
            )

    # ── Buxgalteriya ────────────────────────────────────────────────

    def on_submit(self):
        journal_entry = self.make_journal_entry()
        self.db_set("journal_entry", journal_entry.name, update_modified=False)

    def make_journal_entry(self):
        amount = flt(self.amount)
        is_out = self.kind == KIND_OUT

        pos_profile = frappe.db.get_value("POS Opening Entry", self.pos_opening_entry, "pos_profile")
        cost_center = frappe.db.get_value("POS Profile", pos_profile, "cost_center") or (
            frappe.db.get_value("Company", self.company, "cost_center")
        )

        journal_entry = frappe.new_doc("Journal Entry")
        journal_entry.voucher_type = "Cash Entry"
        journal_entry.company = self.company
        journal_entry.posting_date = getdate(self.posting_datetime)
        journal_entry.user_remark = (
            f"Kassa harakati {self.name}: {'chiqim' if is_out else 'kirim'} | "
            f"{self.category} | {self.reason} | Kassir: {self.user}"
            + (f" | Tasdiqlagan: {self.approved_by}" if self.approved_by else "")
        )
        journal_entry.append(
            "accounts",
            {
                "account": self.cash_account,
                "debit_in_account_currency": 0 if is_out else amount,
                "credit_in_account_currency": amount if is_out else 0,
            },
        )
        journal_entry.append(
            "accounts",
            {
                "account": self.counter_account,
                "cost_center": cost_center,
                "debit_in_account_currency": amount if is_out else 0,
                "credit_in_account_currency": 0 if is_out else amount,
            },
        )
        journal_entry.flags.ignore_permissions = True
        journal_entry.insert()
        journal_entry.submit()
        return journal_entry

    def before_cancel(self):
        status = frappe.db.get_value("POS Opening Entry", self.pos_opening_entry, "status")
        if status != "Open":
            frappe.throw(
                _(
                    "Smena yopilgan — kassa harakatini bekor qilib bo'lmaydi, chunki u "
                    "yopish hisobotiga kirgan. Tuzatish uchun ochiq smenada teskari "
                    "harakat kiriting."
                ),
                title=_("Smena yopilgan"),
            )

    def on_cancel(self):
        if not self.journal_entry:
            return
        journal_entry = frappe.get_doc("Journal Entry", self.journal_entry)
        if journal_entry.docstatus == 1:
            journal_entry.flags.ignore_permissions = True
            journal_entry.cancel()


def counter_account(company: str) -> str:
    """Kompaniyaning «Kassa harakati hisobi» — sozlanmagan bo'lsa tushunarli xato."""
    account = (
        frappe.db.get_value("Company", company, COMPANY_ACCOUNT_FIELD)
        if frappe.db.has_column("Company", COMPANY_ACCOUNT_FIELD)
        else None
    )
    if not account:
        frappe.throw(
            _(
                "'{0}' kompaniyasida «Kassa harakati hisobi» tanlanmagan. Sozlash uchun: "
                "bench --site {1} execute "
                "ozturkapp.ozturkapp.setup.cashier_shift_setup.setup"
            ).format(company, frappe.local.site),
            title=_("Qarshi hisob sozlanmagan"),
        )

    info = frappe.db.get_value(
        "Account", account, ["company", "is_group", "disabled"], as_dict=True
    )
    if not info or info.company != company or info.is_group or info.disabled:
        frappe.throw(
            _(
                "«Kassa harakati hisobi» ({0}) yaroqsiz: u '{1}' kompaniyasining guruh "
                "bo'lmagan, faol hisobi bo'lishi kerak."
            ).format(account, company),
            title=_("Qarshi hisob noto'g'ri"),
        )
    return account
