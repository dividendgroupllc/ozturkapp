# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa yopish solishtiruvini TEZ qurish.

MUAMMO
======
ERPNext'ning `make_closing_entry_from_opening()` ichida
`get_pos_invoices()` bor va u har bir chek uchun TO'LIQ hujjatni
yuklaydi::

    erpnext/accounts/doctype/pos_closing_entry/pos_closing_entry.py
        data = [frappe.get_doc("POS Invoice", d.name).as_dict() for d in data]

Bitta `get_doc` = 11 ta so'rov (chekning o'zi + 10 ta bola jadval:
Pricing Rule Detail, Packed Item, Timesheet, Payment Schedule,
Sales Team, ...). Yopish oqimida bu funksiya UCH marta ishlaydi, ya'ni
narx chek soniga chiziqli va uch baravar:

    8 chek   ->  ~90 ms  (×3 = 270 ms)
    96 chek  -> ~1010 ms (×3 = 3.0 s)
    200 chek -> ~1935 ms (×3 = 5.8 s)

Holbuki solishtiruvga chekdan atigi bir necha maydon kerak:
`grand_total`, `net_total`, `total_qty`, `customer`, `posting_date`,
soliq qatorlari va to'lov qatorlari.

YECHIM
======
Xuddi shu ma'lumot UCHTA bulk so'rov bilan olinadi. O'lchangan natija:
96 chekda 1010 ms -> 4 ms.

NEGA ERPNext FAYLI TAHRIRLANMAYDI
=================================
`apps/erpnext` upstream — `bench update` da har qanday tahrir yo'qoladi.
Shuning uchun bu yerda O'Z nusxamiz bor va uni faqat ozturkapp oqimlari
ishlatadi. ERPNext'ning O'Z Desk oqimi (POS Closing Entry formasidagi
"Get Invoices") avvalgidek ishlaydi.

DIQQAT — PUL HISOBI
===================
Bu funksiya ERPNext bilan AYNAN bir xil natija berishi shart: filtrlar,
vaqt chegarasi (`>=` / `<=`), soliqlarni `(account_head, rate)` bo'yicha
jamlash va to'lovlarni `mode_of_payment` bo'yicha jamlash. Har qanday
farq Z-hisobotni jimgina buzadi. `tests/test_cashier.py` da natija
ERPNext'ning o'z funksiyasi bilan maydonma-maydon solishtiriladi.
"""

import frappe
from frappe.utils import flt, get_datetime


def make_closing_entry_from_opening(opening_entry):
    """ERPNext'nikining o'rnini bosuvchi, lekin bulk o'qiydigan versiya."""
    closing_entry = frappe.new_doc("POS Closing Entry")
    closing_entry.pos_opening_entry = opening_entry.name
    closing_entry.period_start_date = opening_entry.period_start_date
    closing_entry.period_end_date = frappe.utils.get_datetime()
    closing_entry.pos_profile = opening_entry.pos_profile
    closing_entry.user = opening_entry.user
    closing_entry.company = opening_entry.company
    closing_entry.grand_total = 0
    closing_entry.net_total = 0
    closing_entry.total_quantity = 0

    invoices = get_pos_invoices(
        closing_entry.period_start_date,
        closing_entry.period_end_date,
        closing_entry.pos_profile,
        closing_entry.user,
    )

    pos_transactions, taxes = [], []

    # Ochilishda sanalgan naqd — solishtiruvning boshlang'ich nuqtasi.
    payments = [
        frappe._dict(
            {
                "mode_of_payment": detail.mode_of_payment,
                "opening_amount": detail.opening_amount,
                "expected_amount": detail.opening_amount,
            }
        )
        for detail in opening_entry.balance_details
    ]

    for invoice in invoices:
        pos_transactions.append(
            frappe._dict(
                {
                    "pos_invoice": invoice.name,
                    "posting_date": invoice.posting_date,
                    "grand_total": invoice.grand_total,
                    "customer": invoice.customer,
                }
            )
        )
        closing_entry.grand_total += flt(invoice.grand_total)
        closing_entry.net_total += flt(invoice.net_total)
        closing_entry.total_quantity += flt(invoice.total_qty)

        for tax in invoice.taxes:
            existing = [
                row
                for row in taxes
                if row.account_head == tax.account_head and row.rate == tax.rate
            ]
            if existing:
                existing[0].amount += flt(tax.tax_amount)
            else:
                taxes.append(
                    frappe._dict(
                        {
                            "account_head": tax.account_head,
                            "rate": tax.rate,
                            "amount": tax.tax_amount,
                        }
                    )
                )

        for payment in invoice.payments:
            existing = [
                row for row in payments if row.mode_of_payment == payment.mode_of_payment
            ]
            if existing:
                existing[0].expected_amount += flt(payment.amount)
            else:
                payments.append(
                    frappe._dict(
                        {
                            "mode_of_payment": payment.mode_of_payment,
                            "opening_amount": 0,
                            "expected_amount": payment.amount,
                        }
                    )
                )

    closing_entry.set("pos_transactions", pos_transactions)
    closing_entry.set("payment_reconciliation", payments)
    closing_entry.set("taxes", taxes)

    return closing_entry


def get_pos_invoices(start, end, pos_profile, user):
    """Smena oynasidagi konsolidatsiya qilinmagan cheklar — UCHTA so'rovda.

    Filtrlar ERPNext'nikiga AYNAN mos:
        owner = <smenani ochgan foydalanuvchi>
        docstatus = 1
        pos_profile = <smena profili>
        ifnull(consolidated_invoice, '') = ''
        start <= timestamp(posting_date, posting_time) <= end

    Vaqt chegarasi ATAYLAB Python'da qoldirilgan — ERPNext ham shunday
    qiladi va `get_datetime()` bilan solishtiradi. SQL'ga ko'chirilsa
    chegara qiymatlari (mikrosoniya, NULL `posting_time`) boshqacha
    ishlashi mumkin edi.
    """
    rows = frappe.db.sql(
        """
        SELECT name, customer, posting_date, grand_total, net_total, total_qty,
               timestamp(posting_date, posting_time) AS `timestamp`
        FROM `tabPOS Invoice`
        WHERE owner = %(user)s AND docstatus = 1 AND pos_profile = %(profile)s
          AND ifnull(consolidated_invoice, '') = ''
        ORDER BY `timestamp`
        """,
        {"user": user, "profile": pos_profile},
        as_dict=True,
    )

    start, end = get_datetime(start), get_datetime(end)
    rows = [row for row in rows if start <= get_datetime(row.timestamp) <= end]
    if not rows:
        return []

    names = [row.name for row in rows]
    taxes = _children("Sales Taxes and Charges", names, ["account_head", "rate", "tax_amount"])
    payments = _children("Sales Invoice Payment", names, ["mode_of_payment", "amount"])

    for row in rows:
        row.taxes = taxes.get(row.name, [])
        row.payments = payments.get(row.name, [])

    return rows


def _children(doctype: str, parents: list, fields: list) -> dict:
    """`{parent: [qatorlar]}` — bola jadvalni bitta so'rovda oladi."""
    grouped = {}
    for row in frappe.get_all(
        doctype,
        filters={"parent": ["in", parents], "parenttype": "POS Invoice"},
        fields=["parent", "idx", *fields],
        order_by="parent asc, idx asc",
    ):
        grouped.setdefault(row.parent, []).append(row)
    return grouped
