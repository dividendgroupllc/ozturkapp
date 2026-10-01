# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Expense Allocation — markaziy kompaniya ma'muriy xarajatlarini filiallarga taqsimlash.

Nima uchun kerak:
    «O'zturk Sklad» tovarni filiallarga TAN NARXDA beradi (Branch Stock
    Transfer) — u hech narsa topmaydi. Lekin uning ma'muriy xarajatlari
    (ish haqi, ijara, kommunal ...) bor. Ular oy oxirida filiallarga
    "qayta yoziladi" — shunda har filialning P&L'i haqiqiy natijani ko'rsatadi,
    Sklad esa nolga yaqin chiqadi.

Yozuvlar (har oy uchun BITTA tasdiqlangan hujjat — takroriy taqsimot yo'q):

    Manba (Sklad) kitobida — bitta JE:
        Дт Debtors              [Customer: filialning ichki mijozi]  — har filial ulushi
        Кт asl xarajat hisoblari (ulush nisbatida)                   — jami
           (yoki `source_contra_account` to'ldirilgan bo'lsa — shu bitta hisob)

    Har bir filial kitobida — bitta JE:
        Дт filialning MOS xarajat hisoblari (nomi bo'yicha) yoki bitta hisob
        Кт Creditors            [Supplier: Skladning ichki ta'minotchisi]

    Qarz party bilan yozilgani uchun Akt sverka/Payment Entry bilan yopiladi.

Jazira (`jazira_app` → «Jazira Expense Allocation») dan ko'chirilgan. Farqlar:
  * hisob nomlari/raqamlari («52002», kompaniyalar ro'yxati) kodga yozilmagan —
    hammasi hujjatda sozlanadi (Sklad hisoblar rejasi keyin qayta nomlanadi);
  * hovuz faqat Journal Entry emas, BARCHA GL yozuvlaridan olinadi (Kassa,
    Purchase Invoice ...), taqsimot JE'larining o'zi esa chiqarib tashlanadi;
  * Jazira'dagi «tranzit kassa» va foyda bazasi yo'q — baza: belgilangan %
    yoki filial tushumi ulushi;
  * submit paytida hovuz qayta hisoblanadi va SAQLANGAN oldindan ko'rish
    bilan solishtiriladi — farq bo'lsa submit to'xtaydi ("ko'rganing = yozilgan").
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, formatdate, get_first_day, get_last_day, get_link_to_form, getdate

REMARK_PREFIX = "Expense Allocation:"
# Journal Entry'dagi havola — taqsimot JE'larini hovuzdan chiqarish va
# "Connections"da ko'rsatish uchun (setup/expense_allocation_setup.py yaratadi).
JE_LINK_FIELD = "custom_expense_allocation"

BASIS_FIXED = "Belgilangan foiz"
BASIS_REVENUE = "Tushum ulushi"
MODE_MATCH = "Mos hisob (nomi bo'yicha)"
MODE_SINGLE = "Bitta hisob"

# Hovuzga kirmasligi kerak bo'lgan hisob turlari (tannarx/ombor yozuvlari) —
# standart guruhni tanlashda shular bor guruh o'tkazib yuboriladi.
STOCK_ACCOUNT_TYPES = (
    "Cost of Goods Sold",
    "Stock Adjustment",
    "Expenses Included In Valuation",
    "Expenses Included In Asset Valuation",
)

PRECISION = 2


class ExpenseAllocation(Document):
    # ------------------------------------------------------------------
    # Hayot sikli
    # ------------------------------------------------------------------
    def validate(self):
        self._set_period()
        self._validate_source()
        if self.docstatus == 0:
            self._set_defaults()
        self._validate_accounts()
        self._validate_branches()
        if self.docstatus == 0:
            self._validate_unique_period()
            self._apply(self._compute())
        self._set_status()
        self.title = f"{self.source_company} — {formatdate(self.from_date, 'MM.yyyy')}"

    def before_submit(self):
        self._validate_unique_period()

        # Foydalanuvchi oldindan ko'rishda ko'rgan raqamlar AYNAN yozilishi
        # kerak. Saqlangandan keyin davrga yangi xarajat tushgan bo'lsa —
        # jimgina boshqa summani yozmaymiz, qayta saqlashni so'raymiz.
        fresh = self._compute()
        stored = {row.account: flt(row.net_amount, PRECISION) for row in self.expenses}
        fresh_pool = {row["account"]: row["net_amount"] for row in fresh["pool"]}
        stored_amounts = [flt(row.allocated_amount, PRECISION) for row in self.branches]
        if stored != fresh_pool or stored_amounts != fresh["amounts"]:
            frappe.throw(
                _(
                    "Saqlangandan keyin davr xarajatlari (yoki tushum) o'zgargan. "
                    "Hujjatni qayta saqlang, oldindan ko'rishni tekshiring va keyin tasdiqlang."
                )
            )

        if flt(self.allocated_total) <= 0:
            frappe.throw(_("Filiallarga ajratiladigan summa yo'q — davrda xarajat topilmadi"))

        self._validate_posting_setup()

    def on_submit(self):
        plan = self.build_journal_entries()
        created = []

        if not (self.source_journal_entry and frappe.db.exists("Journal Entry", self.source_journal_entry)):
            name = self._post(plan["source"])
            self.db_set("source_journal_entry", name)
            created.append(name)

        for entry in plan["branches"]:
            row = self.get("branches", {"name": entry["row_name"]})[0]
            if row.journal_entry and frappe.db.exists("Journal Entry", row.journal_entry):
                continue
            name = self._post(entry)
            row.db_set("journal_entry", name, update_modified=False)
            created.append(name)

        self.db_set("status", "Submitted")
        frappe.msgprint(
            _("Xarajat taqsimoti: {0} ta Journal Entry yaratildi").format(len(created)),
            indicator="green",
            alert=True,
        )

    def on_cancel(self):
        # JE'lar shu hujjatga havola qiladi — avval ularni bekor qilamiz,
        # Frappe'ning "bog'langan hujjat bor" tekshiruvi ulardan keyin ishlaydi.
        self.ignore_linked_doctypes = ("Journal Entry", "GL Entry", "Payment Ledger Entry")
        names = [row.journal_entry for row in self.branches if row.journal_entry]
        if self.source_journal_entry:
            names.append(self.source_journal_entry)
        for name in names:
            if not frappe.db.exists("Journal Entry", name):
                continue
            je = frappe.get_doc("Journal Entry", name)
            if je.docstatus == 1:
                je.flags.ignore_permissions = True
                je.cancel()
        self.db_set("status", "Cancelled")

    # ------------------------------------------------------------------
    # Davr, manba, standart qiymatlar
    # ------------------------------------------------------------------
    def _set_period(self):
        if not self.from_date:
            frappe.throw(_("Oyni tanlang"))
        # Taqsimot faqat TO'LIQ oy uchun — oyning istalgan kuni tanlansa ham
        # davr 1-kundan oxirgi kungacha bo'ladi.
        self.from_date = get_first_day(getdate(self.from_date))
        self.to_date = get_last_day(self.from_date)
        self.posting_date = self.to_date

    def _validate_source(self):
        if not self.source_company:
            frappe.throw(_("Xarajat manba kompaniyasi majburiy"))
        if frappe.db.get_value("Company", self.source_company, "is_group"):
            # Guruh kompaniyasida (O'zturk) operatsiya yuritilmaydi.
            frappe.throw(
                _("'{0}' — guruh kompaniyasi, xarajat manbasi bo'la olmaydi").format(self.source_company)
            )
        self.currency = frappe.db.get_value("Company", self.source_company, "default_currency")

    def _set_defaults(self):
        company = self.source_company
        if not self.source_receivable_account:
            self.source_receivable_account = get_default_party_account(company, "Receivable")
        if not self.source_cost_center:
            self.source_cost_center = get_default_cost_center(company)
        self.source_supplier = get_internal_supplier(company)

        if not self.accounts:
            for account in get_default_expense_groups(company):
                self.append("accounts", {"account": account, "exclude": 0})
            for account in get_default_excluded_accounts(company):
                self.append("accounts", {"account": account, "exclude": 1})

        if not self.branches:
            for branch in get_sibling_companies(company):
                self.append("branches", {"company": branch})

        for row in self.branches:
            if not row.company:
                continue
            row.customer = get_internal_customer(row.company)
            if not row.payable_account:
                row.payable_account = get_default_party_account(row.company, "Payable")
            if not row.cost_center:
                row.cost_center = get_default_cost_center(row.company)
            if not row.expense_account:
                row.expense_account = get_default_branch_expense_account(
                    row.company, [r.account for r in self.accounts if not r.exclude]
                )

        if self.allocation_basis == BASIS_FIXED and self.branches and not any(
            flt(row.percent) for row in self.branches
        ):
            # Foiz kiritilmagan — teng bo'lamiz (bitta filial bo'lsa 100%).
            share = flt(100.0 / len(self.branches), 6)
            for row in self.branches:
                row.percent = share
            self.branches[-1].percent = flt(100 - share * (len(self.branches) - 1), 6)

    # ------------------------------------------------------------------
    # Tekshiruvlar
    # ------------------------------------------------------------------
    def _validate_accounts(self):
        if not self.accounts:
            frappe.throw(_("Taqsimlanadigan xarajat hisoblari (yoki guruhlari) tanlanmagan"))
        seen = set()
        for row in self.accounts:
            data = frappe.db.get_value(
                "Account", row.account, ["company", "root_type", "is_group"], as_dict=True
            )
            if not data:
                frappe.throw(_("{0}-qator: hisob topilmadi: {1}").format(row.idx, row.account))
            if data.company != self.source_company:
                frappe.throw(
                    _("{0}-qator: '{1}' hisobi '{2}' kompaniyasiga tegishli emas").format(
                        row.idx, row.account, self.source_company
                    )
                )
            if data.root_type != "Expense":
                frappe.throw(_("{0}-qator: '{1}' xarajat (Expense) hisobi emas").format(row.idx, row.account))
            if row.account in seen:
                frappe.throw(_("Hisob takrorlangan: {0}").format(row.account))
            seen.add(row.account)
            row.is_group = data.is_group
        if not any(not row.exclude for row in self.accounts):
            frappe.throw(_("Kamida bitta hisob «Chiqarib tashlash»siz bo'lishi kerak"))

    def _validate_branches(self):
        if not self.branches:
            frappe.throw(_("Kamida bitta filial kompaniyasi kerak"))
        seen = set()
        for row in self.branches:
            if row.company == self.source_company:
                frappe.throw(_("Manba kompaniya filiallar ro'yxatida bo'lishi mumkin emas"))
            if row.company in seen:
                frappe.throw(_("Filial takrorlangan: {0}").format(row.company))
            seen.add(row.company)
            company = frappe.db.get_value(
                "Company", row.company, ["is_group", "default_currency"], as_dict=True
            )
            if not company:
                frappe.throw(_("Kompaniya topilmadi: {0}").format(row.company))
            if company.is_group:
                frappe.throw(_("'{0}' — guruh kompaniyasi, filial bo'la olmaydi").format(row.company))
            if company.default_currency != self.currency:
                frappe.throw(
                    _("'{0}' valyutasi ({1}) manba valyutasidan ({2}) farq qiladi").format(
                        row.company, company.default_currency, self.currency
                    )
                )
            if flt(row.percent) < 0:
                frappe.throw(_("'{0}': ulush manfiy bo'lishi mumkin emas").format(row.company))
            if row.expense_account:
                validate_account(row.expense_account, row.company, _("Filial xarajat hisobi"), root_type="Expense")
            if row.payable_account:
                validate_account(row.payable_account, row.company, _("Kreditorlik hisobi"), account_type="Payable")
            if row.cost_center:
                validate_cost_center(row.cost_center, row.company)

        if self.allocation_basis == BASIS_FIXED:
            total = flt(sum(flt(row.percent) for row in self.branches), 6)
            if total <= 0:
                frappe.throw(_("Filiallar ulushi (%) kiritilmagan"))
            if total > 100.0001:
                frappe.throw(_("Filiallar ulushi jami 100% dan oshib ketdi: {0}%").format(total))

        if self.source_receivable_account:
            validate_account(
                self.source_receivable_account, self.source_company, _("Debitorlik hisobi"), account_type="Receivable"
            )
        if self.source_contra_account:
            validate_account(self.source_contra_account, self.source_company, _("Manba kredit hisobi"))
        if self.source_cost_center:
            validate_cost_center(self.source_cost_center, self.source_company)

    def _validate_unique_period(self):
        existing = frappe.db.sql(
            """
            SELECT name FROM `tabExpense Allocation`
            WHERE name != %(name)s AND docstatus = 1 AND source_company = %(company)s
              AND from_date <= %(to_date)s AND to_date >= %(from_date)s
            LIMIT 1
            """,
            {"name": self.name or "", "company": self.source_company,
             "from_date": self.from_date, "to_date": self.to_date},
        )
        if existing:
            frappe.throw(
                _("'{0}' uchun bu oy allaqachon taqsimlangan: {1}. Qayta taqsimlash uchun avval uni bekor qiling.").format(
                    self.source_company, get_link_to_form("Expense Allocation", existing[0][0])
                )
            )

    def _validate_posting_setup(self):
        """JE yozishdan oldin barcha kerakli hisob/kontragentlar borligini tekshirish."""
        missing = []
        if not self.source_receivable_account:
            missing.append(_("Debitorlik hisobi (manba)"))
        if not self.source_cost_center and not self.source_contra_account:
            missing.append(_("Manba Cost Center"))
        if not self.source_supplier:
            missing.append(_("'{0}' ning ichki ta'minotchisi (Supplier: is_internal_supplier, represents_company)").format(
                self.source_company))
        for row in self.branches:
            if flt(row.allocated_amount) <= 0:
                continue
            if not row.customer:
                missing.append(_("'{0}' ning ichki mijozi (Customer: is_internal_customer, represents_company)").format(
                    row.company))
            if not row.payable_account:
                missing.append(_("'{0}': kreditorlik hisobi").format(row.company))
            if not row.cost_center:
                missing.append(_("'{0}': Cost Center").format(row.company))
        if missing:
            frappe.throw(_("Quyidagilar to'ldirilmagan:") + "<br>• " + "<br>• ".join(missing))

        # Filialda hisob topilmasa, fallback hisob shart.
        plan = self.build_journal_entries()
        for entry in plan["branches"]:
            if any(not line["account"] for line in entry["accounts"]):
                frappe.throw(
                    _("'{0}': mos xarajat hisobi topilmadi va «Filial xarajat hisobi» to'ldirilmagan").format(
                        entry["company"]
                    )
                )

    # ------------------------------------------------------------------
    # Hisoblash
    # ------------------------------------------------------------------
    def _compute(self):
        """Hovuz va filial ulushlarini hisoblaydi (hujjatni o'zgartirmaydi)."""
        pool = self.get_pool()
        total = flt(sum(row["net_amount"] for row in pool), PRECISION)

        if self.allocation_basis == BASIS_REVENUE:
            revenues = [max(get_revenue(row.company, self.from_date, self.to_date), 0) for row in self.branches]
            revenue_total = sum(revenues)
            percents = [flt(r / revenue_total * 100, 6) if revenue_total else 0 for r in revenues]
        else:
            revenues = [None] * len(self.branches)
            percents = [flt(row.percent, 6) for row in self.branches]

        amounts = [flt(total * p / 100, PRECISION) if total > 0 else 0 for p in percents]
        # 100% taqsimlanayotgan bo'lsa yaxlitlash qoldig'i oxirgi musbat
        # ulushli filialga — jami hovuzga aynan teng bo'lsin.
        if total > 0 and abs(sum(percents) - 100) < 0.0001:
            positive = [i for i, p in enumerate(percents) if p > 0]
            if positive:
                last = positive[-1]
                amounts[last] = flt(total - sum(a for i, a in enumerate(amounts) if i != last), PRECISION)

        return {"pool": pool, "total": total, "percents": percents, "revenues": revenues, "amounts": amounts}

    def _apply(self, result):
        self.set("expenses", [])
        for row, item_split in zip(result["pool"], self._split_by_account(result["pool"], result["amounts"])["by_account"]):
            self.append("expenses", {
                "account": row["account"],
                "account_name": row["account_name"],
                "net_amount": row["net_amount"],
                "allocated_amount": item_split,
                "remaining_amount": flt(row["net_amount"] - item_split, PRECISION),
                "currency": self.currency,
            })

        for row, pct, revenue, amount in zip(self.branches, result["percents"], result["revenues"], result["amounts"]):
            if self.allocation_basis == BASIS_REVENUE:
                row.percent = pct
                row.revenue_amount = flt(revenue, PRECISION)
            else:
                row.revenue_amount = 0
            row.allocated_amount = amount

        self.total_expense_amount = result["total"]
        self.total_percent = flt(sum(flt(r.percent) for r in self.branches), 6)
        self.allocated_total = flt(sum(result["amounts"]), PRECISION)
        self.remaining_amount = flt(self.total_expense_amount - self.allocated_total, PRECISION)

    def get_pool(self):
        """Manba kompaniyaning davrdagi taqsimlanadigan xarajatlari (hisob bo'yicha jami).

        BARCHA GL yozuvlari olinadi (JE, Kassa, Purchase Invoice ...), faqat
        taqsimot JE'larining o'zi va Period Closing Voucher chiqariladi.
        """
        include = [r.account for r in self.accounts if not r.exclude]
        exclude = [r.account for r in self.accounts if r.exclude]
        include_sql, params = _range_condition("acc", include, "inc")
        exclude_sql, ex_params = _range_condition("acc", exclude, "exc")
        params.update(ex_params)
        params.update({"company": self.source_company, "from_date": self.from_date, "to_date": self.to_date,
                       "remark_like": f"{REMARK_PREFIX}%"})

        je_filter = "IFNULL(je.user_remark, '') NOT LIKE %(remark_like)s"
        if frappe.db.has_column("Journal Entry", JE_LINK_FIELD):
            je_filter += f" AND IFNULL(je.`{JE_LINK_FIELD}`, '') = ''"

        rows = frappe.db.sql(
            f"""
            SELECT gle.account, acc.account_name,
                   SUM(gle.debit - gle.credit) AS net_amount
            FROM `tabGL Entry` gle
            INNER JOIN `tabAccount` acc ON acc.name = gle.account
            LEFT JOIN `tabJournal Entry` je
                ON gle.voucher_type = 'Journal Entry' AND je.name = gle.voucher_no
            WHERE gle.company = %(company)s
              AND gle.is_cancelled = 0
              AND gle.posting_date BETWEEN %(from_date)s AND %(to_date)s
              AND gle.voucher_type != 'Period Closing Voucher'
              AND acc.root_type = 'Expense'
              AND ({include_sql})
              {"AND NOT (" + exclude_sql + ")" if exclude else ""}
              AND (je.name IS NULL OR ({je_filter}))
            GROUP BY gle.account, acc.account_name, acc.lft
            HAVING ABS(SUM(gle.debit - gle.credit)) > 0.005
            ORDER BY acc.lft
            """,
            params,
            as_dict=True,
        )
        return [
            {"account": r.account, "account_name": r.account_name, "net_amount": flt(r.net_amount, PRECISION)}
            for r in rows
        ]

    def _split_by_account(self, pool, amounts):
        """Har filial ulushini hovuz hisoblari nisbatida bo'ladi.

        Qaytaradi: {"per_branch": [[summa hisob bo'yicha] ...], "by_account": [jami filiallarga]}
        Har filial qatori yig'indisi aynan uning ulushiga teng.
        """
        weights = [row["net_amount"] for row in pool]
        per_branch = [split_amount(amount, weights) for amount in amounts]
        by_account = [
            flt(sum(split[i] for split in per_branch), PRECISION) for i in range(len(pool))
        ]
        return {"per_branch": per_branch, "by_account": by_account}

    def _set_status(self):
        if self.docstatus == 1:
            self.status = "Submitted"
        elif self.docstatus == 2:
            self.status = "Cancelled"
        else:
            self.status = "Calculated" if flt(self.allocated_total) > 0 else "Draft"

    # ------------------------------------------------------------------
    # Journal Entry rejasi — oldindan ko'rish va yozish UCHUN BITTA manba
    # ------------------------------------------------------------------
    def build_journal_entries(self):
        pool = [{"account": r.account, "account_name": r.account_name, "net_amount": flt(r.net_amount)}
                for r in self.expenses]
        amounts = [flt(r.allocated_amount, PRECISION) for r in self.branches]
        split = self._split_by_account(pool, amounts)

        # --- Manba kitobi ---
        source_lines = []
        for row, amount in zip(self.branches, amounts):
            if amount <= 0:
                continue
            source_lines.append(_line(self.source_receivable_account, amount, 0,
                                      party_type="Customer", party=row.customer, note=row.company))
        allocated = flt(sum(a for a in amounts if a > 0), PRECISION)
        if self.source_contra_account:
            source_lines.append(_line(self.source_contra_account, 0, allocated, cost_center=self.source_cost_center))
        else:
            for item, value in zip(pool, split["by_account"]):
                if abs(value) > 0.005:
                    source_lines.append(_signed_line(item["account"], -value, cost_center=self.source_cost_center))

        source = {"company": self.source_company, "label": _("filiallarga qayta yozish"), "accounts": source_lines}

        # --- Filial kitoblari ---
        branches = []
        for row, amount, per_account in zip(self.branches, amounts, split["per_branch"]):
            if amount <= 0:
                continue
            debits = {}
            if self.branch_account_mode == MODE_SINGLE:
                debits[row.expense_account] = amount
            else:
                for item, value in zip(pool, per_account):
                    target = find_matching_account(row.company, item["account"]) or row.expense_account
                    debits[target] = flt(debits.get(target, 0) + value, PRECISION)
            lines = [
                _signed_line(account, value, cost_center=row.cost_center)
                for account, value in debits.items()
                if abs(value) > 0.005
            ]
            lines.append(_line(row.payable_account, 0, amount, party_type="Supplier", party=self.source_supplier))
            branches.append({"company": row.company, "row_name": row.name,
                             "label": _("{0} xarajat ulushi").format(self.source_company), "accounts": lines})

        return {"source": source, "branches": branches}

    def _post(self, entry):
        je = frappe.new_doc("Journal Entry")
        je.voucher_type = "Journal Entry"
        je.company = entry["company"]
        je.posting_date = self.posting_date
        je.user_remark = f"{REMARK_PREFIX} {self.name} | {self.from_date} - {self.to_date} | {entry['label']}"
        if frappe.db.has_column("Journal Entry", JE_LINK_FIELD):
            je.set(JE_LINK_FIELD, self.name)
        for line in entry["accounts"]:
            row = {
                "account": line["account"],
                "debit_in_account_currency": line["debit"],
                "credit_in_account_currency": line["credit"],
                "user_remark": je.user_remark + (f" | {line['note']}" if line.get("note") else ""),
            }
            if line.get("party"):
                row.update({"party_type": line["party_type"], "party": line["party"]})
            if line.get("cost_center"):
                row["cost_center"] = line["cost_center"]
            je.append("accounts", row)
        je.flags.ignore_permissions = True
        je.insert()
        je.submit()
        return je.name

    @frappe.whitelist()
    def get_preview(self):
        """Yoziladigan JE'lar (manba + har filial) — formada jadval bo'lib chiqadi."""
        plan = self.build_journal_entries() if self.expenses else {"source": None, "branches": []}
        warnings = []
        if self.docstatus == 0:
            drafts = frappe.db.sql(
                """
                SELECT COUNT(DISTINCT je.name) FROM `tabJournal Entry` je
                INNER JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
                INNER JOIN `tabAccount` acc ON acc.name = jea.account
                WHERE je.company = %s AND je.docstatus = 0 AND acc.root_type = 'Expense'
                  AND je.posting_date BETWEEN %s AND %s
                """,
                (self.source_company, self.from_date, self.to_date),
            )[0][0]
            if drafts:
                warnings.append(
                    _("Manba kompaniyada shu oy uchun {0} ta tasdiqlanmagan (Draft) xarajat JE bor — ular hovuzga kirmaydi").format(drafts)
                )
            if flt(self.remaining_amount) > 0.005:
                warnings.append(
                    _("Xarajatning {0} qismi manbada qoladi (filiallar ulushi jami {1}%)").format(
                        frappe.format_value(self.remaining_amount, {"fieldtype": "Currency", "options": "currency"}, self),
                        flt(self.total_percent, 2),
                    )
                )
        for entry in plan["branches"]:
            for line in entry["accounts"]:
                if not line["account"]:
                    warnings.append(_("'{0}': mos hisob topilmadi — «Filial xarajat hisobi»ni to'ldiring").format(entry["company"]))
                    break
        return {"source": plan["source"], "branches": plan["branches"], "warnings": warnings}


# ----------------------------------------------------------------------
# Yordamchilar
# ----------------------------------------------------------------------
def _line(account, debit, credit, party_type=None, party=None, cost_center=None, note=None):
    return {"account": account, "debit": flt(debit, PRECISION), "credit": flt(credit, PRECISION),
            "party_type": party_type, "party": party, "cost_center": cost_center, "note": note}


def _signed_line(account, value, cost_center=None):
    """Musbat — debet, manfiy — kredit (manfiy qoldiqli xarajat hisoblari uchun)."""
    value = flt(value, PRECISION)
    return _line(account, value if value > 0 else 0, -value if value < 0 else 0, cost_center=cost_center)


def split_amount(amount, weights):
    """`amount` ni `weights` nisbatida bo'lish; yig'indi aynan `amount`.

    Yaxlitlash qoldig'i eng katta (mutlaq) og'irlikli qatorga qo'shiladi.
    """
    total = sum(weights)
    if not weights:
        return []
    if not total:
        return [0.0] * len(weights)
    parts = [flt(amount * w / total, PRECISION) for w in weights]
    diff = flt(amount - sum(parts), PRECISION)
    if diff:
        idx = max(range(len(weights)), key=lambda i: abs(weights[i]))
        parts[idx] = flt(parts[idx] + diff, PRECISION)
    return parts


def _range_condition(alias, accounts, prefix):
    """Hisob yoki guruh ro'yxatini `lft/rgt` oralig'i shartiga aylantiradi."""
    if not accounts:
        return "1=0", {}
    clauses, params = [], {}
    for i, account in enumerate(accounts):
        lft, rgt = frappe.db.get_value("Account", account, ["lft", "rgt"])
        params[f"{prefix}_l{i}"], params[f"{prefix}_r{i}"] = lft, rgt
        clauses.append(f"({alias}.lft >= %({prefix}_l{i})s AND {alias}.rgt <= %({prefix}_r{i})s)")
    return " OR ".join(clauses), params


def get_revenue(company, from_date, to_date):
    """Davrdagi daromad (Income) — kredit minus debet."""
    return flt(frappe.db.sql(
        """
        SELECT COALESCE(SUM(gle.credit - gle.debit), 0)
        FROM `tabGL Entry` gle INNER JOIN `tabAccount` acc ON acc.name = gle.account
        WHERE gle.company = %s AND gle.is_cancelled = 0 AND acc.root_type = 'Income'
          AND gle.voucher_type != 'Period Closing Voucher'
          AND gle.posting_date BETWEEN %s AND %s
        """,
        (company, from_date, to_date),
    )[0][0], PRECISION)


def find_matching_account(company, source_account):
    """Filialda manba hisobi bilan BIR XIL NOMLI xarajat hisobini topadi.

    Raqam emas, nom bo'yicha — Sklad hisoblar rejasi filiallarnikiga moslab
    qayta nomlanadi, raqamlar esa farq qilishi mumkin.
    """
    account_name = frappe.db.get_value("Account", source_account, "account_name")
    if not account_name:
        return None
    return frappe.db.get_value(
        "Account",
        {"company": company, "account_name": account_name, "is_group": 0, "root_type": "Expense", "disabled": 0},
        "name",
    )


def validate_account(account, company, label, root_type=None, account_type=None):
    data = frappe.db.get_value(
        "Account", account, ["company", "is_group", "root_type", "account_type"], as_dict=True
    )
    if not data:
        frappe.throw(_("{0} topilmadi: {1}").format(label, account))
    if data.is_group:
        frappe.throw(_("{0} guruh hisob bo'lishi mumkin emas: {1}").format(label, account))
    if data.company != company:
        frappe.throw(_("{0} '{1}' {2} kompaniyasiga tegishli emas").format(label, account, company))
    if root_type and data.root_type != root_type:
        frappe.throw(_("{0} '{1}' {2} turida bo'lishi kerak").format(label, account, root_type))
    if account_type and data.account_type != account_type:
        frappe.throw(
            _("{0} '{1}' {2} turida bo'lishi kerak (qarz party bilan yoziladi)").format(label, account, account_type)
        )


def validate_cost_center(cost_center, company):
    data = frappe.db.get_value("Cost Center", cost_center, ["company", "is_group"], as_dict=True)
    if not data or data.company != company or data.is_group:
        frappe.throw(_("Cost Center '{0}' {1} kompaniyasining guruh bo'lmagan markazi emas").format(cost_center, company))


def get_internal_customer(company):
    """Kompaniyani ifodalovchi ichki mijoz — manba kitobida "filial qarzdor"."""
    return frappe.db.get_value("Customer", {"represents_company": company, "is_internal_customer": 1}, "name")


def get_internal_supplier(company):
    """Kompaniyani ifodalovchi ichki ta'minotchi — filial kitobida "Skladga qarzmiz"."""
    return frappe.db.get_value("Supplier", {"represents_company": company, "is_internal_supplier": 1}, "name")


def get_default_party_account(company, account_type):
    field = "default_receivable_account" if account_type == "Receivable" else "default_payable_account"
    account = frappe.db.get_value("Company", company, field)
    if account and frappe.db.get_value("Account", account, "account_type") == account_type:
        return account
    return frappe.db.get_value(
        "Account", {"company": company, "account_type": account_type, "is_group": 0, "disabled": 0},
        "name", order_by="lft asc",
    )


def get_default_cost_center(company):
    return frappe.db.get_value("Company", company, "cost_center") or frappe.db.get_value(
        "Cost Center", {"company": company, "is_group": 0}, "name", order_by="lft asc"
    )


def get_default_expense_groups(company):
    """Standart taqsimlanadigan guruhlar — nomga bog'lanmasdan topiladi.

    Expense ildizining bevosita guruhlaridan tannarx/ombor turidagi hisobi
    YO'Q bo'lganlari olinadi (standart rejada bu «Indirect Expenses»).
    """
    root = frappe.db.get_value(
        "Account", {"company": company, "root_type": "Expense", "is_group": 1,
                    "parent_account": ["is", "not set"]}, "name"
    ) or frappe.db.get_value(
        "Account", {"company": company, "root_type": "Expense", "is_group": 1}, "name", order_by="lft asc"
    )
    if not root:
        return []
    groups = []
    for group in frappe.get_all("Account", filters={"company": company, "parent_account": root, "is_group": 1},
                                fields=["name", "lft", "rgt"], order_by="lft asc"):
        has_stock = frappe.db.exists("Account", {
            "company": company, "lft": [">", group.lft], "rgt": ["<", group.rgt],
            "account_type": ["in", STOCK_ACCOUNT_TYPES],
        })
        if not has_stock:
            groups.append(group.name)
    return groups


def get_default_excluded_accounts(company):
    """Yaxlitlash va kurs farqi — ma'muriy xarajat emas, standart chiqariladi."""
    accounts = frappe.db.get_value("Company", company, ["round_off_account", "exchange_gain_loss_account"]) or []
    return [a for a in accounts if a and frappe.db.get_value("Account", a, "root_type") == "Expense"]


def get_default_branch_expense_account(company, source_accounts):
    """Filial uchun zaxira xarajat hisobi: manba guruhiga MOS nomli guruhning
    birinchi oddiy (turi yo'q) hisobi."""
    for account in source_accounts:
        data = frappe.db.get_value("Account", account, ["account_name", "is_group"], as_dict=True)
        if not data:
            continue
        match = frappe.db.get_value(
            "Account", {"company": company, "account_name": data.account_name, "root_type": "Expense"},
            ["name", "is_group", "lft", "rgt"], as_dict=True,
        )
        if not match:
            continue
        if not match.is_group:
            return match.name
        leaf = frappe.db.get_value(
            "Account",
            {"company": company, "lft": [">", match.lft], "rgt": ["<", match.rgt], "is_group": 0,
             "account_type": ["in", ["", None]], "disabled": 0},
            "name", order_by="lft asc",
        )
        if leaf:
            return leaf
    return None


def get_sibling_companies(company):
    """Manba bilan bir guruhdagi boshqa ishchi kompaniyalar (standart filiallar)."""
    parent = frappe.db.get_value("Company", company, "parent_company")
    if not parent:
        return []
    return frappe.get_all(
        "Company",
        filters={"parent_company": parent, "is_group": 0, "name": ["!=", company]},
        pluck="name", order_by="name asc",
    )
