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
Bu funksiya ERPNext bilan AYNAN bir xil natija berishi shart (quyidagi
ikkita ONGLI farqdan tashqari): filtrlar, vaqt chegarasi (`>=` / `<=`),
soliqlarni `(account_head, rate)` bo'yicha jamlash va to'lovlarni
`mode_of_payment` bo'yicha jamlash. Har qanday boshqa farq Z-hisobotni
jimgina buzadi. `tests/test_cashier.py` da natija
ERPNext'ning o'z funksiyasi bilan maydonma-maydon solishtiriladi.

IKKITA ONGLI FARQ (naqd pulning KUTILAYOTGAN summasi)
=====================================================
Kassadagi naqd pul uch narsadan kelib chiqadi: sotuv, qaytim va kassa
harakati. ERPNext faqat birinchisini hisoblaydi — natijada kutilgan
summa haqiqiy g'aladondan chetga chiqadi. Shuning uchun:

  1. QAYTIM AYIRILADI. POS Invoice'da naqd to'lov qatori MIJOZ BERGAN
     summani saqlaydi (390 000), qaytim (2 000) esa alohida maydonda.
     ERPNext yopilishda 390 000 ni qo'shadi, holbuki g'aladonda 388 000
     qoladi — kassir to'g'ri sanasa ham har smenada qaytim yig'indisicha
     «kamomad» chiqadi. Bu yerda qaytim naqd qatordan ayiriladi
     (GL yozuvi ham xuddi shunday: qaytim hisobi kreditlanadi). Qaytimsiz
     cheklarda natija ERPNext bilan bir xil.
  2. KASSA HARAKATI QO'SHILADI. `Ozturk Cash Movement` (kirim +, chiqim -)
     naqd usulning kutilgan summasiga qo'shiladi. Movement bekor qilingan
     (docstatus 2) bo'lsa hisobga olinmaydi.

Ikkala yo'l ham (kassa sahifasi `close_shift` va Desktop POS
`createPosClosing`) shu funksiyadan o'qiydi, ya'ni ular doim bir xil raqam
ko'radi. ERPNext'ning O'Z Desk oqimi ("Get Invoices" tugmasi) o'zgarmagan.
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

        for mode, amount in net_payments(invoice):
            existing = [row for row in payments if row.mode_of_payment == mode]
            if existing:
                existing[0].expected_amount += flt(amount)
            else:
                payments.append(
                    frappe._dict(
                        {
                            "mode_of_payment": mode,
                            "opening_amount": 0,
                            "expected_amount": amount,
                        }
                    )
                )

    apply_cash_movements(payments, opening_entry.name)

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
        SELECT name, customer, posting_date, grand_total, net_total, total_qty, change_amount,
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
    taxes = bulk_children("Sales Taxes and Charges", names, ["account_head", "rate", "tax_amount"])
    payments = bulk_children("Sales Invoice Payment", names, ["mode_of_payment", "amount", "type"])

    for row in rows:
        row.taxes = taxes.get(row.name, [])
        row.payments = payments.get(row.name, [])

    return rows


def bulk_children(doctype: str, parents: list, fields: list) -> dict:
    """`{parent: [qatorlar]}` — POS Invoice bola jadvalini bitta so'rovda oladi."""
    grouped = {}
    for row in frappe.get_all(
        doctype,
        filters={"parent": ["in", parents], "parenttype": "POS Invoice"},
        fields=["parent", "idx", *fields],
        order_by="parent asc, idx asc",
    ):
        grouped.setdefault(row.parent, []).append(row)
    return grouped


def net_payments(invoice) -> list:
    """Chekning to'lov qatorlari `[(usul, summa)]` — NAQD qatordan qaytim ayirilgan.

    `invoice.payments[*].type` — `Sales Invoice Payment.type` (Mode of Payment
    turi, saqlashda ko'chiriladi). Qaytim faqat NAQD qatordan ayiriladi:
    ERPNext ham qaytimni faqat naqd to'lovda yozadi. Bir nechta naqd qator
    bo'lsa qaytim ketma-ket ayiriladi va hech qachon qator summasidan oshmaydi.
    Qaytimsiz chekda (va qaytarish cheklarida) natija qatorlarning o'zi.
    """
    change = flt(invoice.get("change_amount"))
    rows = []
    for payment in invoice.payments:
        amount = flt(payment.amount)
        if change > 0 and payment.get("type") == "Cash" and amount > 0:
            given = min(change, amount)
            amount -= given
            change -= given
        rows.append((payment.mode_of_payment, amount))
    return rows


def get_cash_movements(opening_name: str) -> list:
    """Smenaning tasdiqlangan (docstatus 1) kassa harakatlari, vaqt bo'yicha.

    Jadval yaratilmagan saytda (kod yangilandi, `bench migrate` hali
    ishlamagan) bo'sh ro'yxat qaytadi: yopish oqimi hech qachon shu sababdan
    yiqilmasligi kerak.
    """
    if not frappe.db.table_exists("Ozturk Cash Movement"):
        return []

    return frappe.get_all(
        "Ozturk Cash Movement",
        filters={"pos_opening_entry": opening_name, "docstatus": 1},
        fields=[
            "name", "kind", "category", "amount", "mode_of_payment", "reason", "user",
            "approved_by", "posting_datetime", "journal_entry",
        ],
        order_by="posting_datetime asc, creation asc",
    )


def apply_cash_movements(payments: list, opening_name: str):
    """Kassa harakatlarini naqd usulning kutilgan summasiga qo'shadi.

    Yopish `cash_movements` funksiyasi yoqilganligiga QARAMAYDI: allaqachon
    yozilgan harakat — pul haqiqatan g'aladonga kirgan/undan chiqqan
    degani, bayroq keyin o'chirilsa ham hisobdan tushib qolmasligi kerak.
    """
    for move in get_cash_movements(opening_name):
        amount = flt(move.amount) if move.kind == "In" else -flt(move.amount)
        existing = [row for row in payments if row.mode_of_payment == move.mode_of_payment]
        if existing:
            existing[0].expected_amount += amount
        else:
            payments.append(
                frappe._dict(
                    {
                        "mode_of_payment": move.mode_of_payment,
                        "opening_amount": 0,
                        "expected_amount": amount,
                    }
                )
            )


# ═══════════════════════════════════════════════════════════════════
#  POS Closing Entry qo'riqchisi (generic REST'ga qarshi)
# ═══════════════════════════════════════════════════════════════════
#
# URY `URY Cashier` roliga POS Closing Entry'da create/submit/cancel bergan, ERPNext
# `validate` da esa faqat smena ochiqligini, dublikat va yaroqsiz cheklarni tekshiradi —
# `expected_amount`, `difference` va `pos_transactions` TO'LIQLIGINI emas. Shu sababli
# kassir `frappe.client.insert` bilan yopilishni qo'lda yozib:
#   * kutilgan summani sanagan summaga tenglashtira olardi (kamomad ko'rinmaydi);
#   * ba'zi cheklarni ro'yxatdan tashlay olardi (ular konsolidatsiya qilinmaydi, keyingi
#     yopilish ham ularni olmaydi — buxgalteriyaga hech qachon tushmaydi);
#   * yopilishni bekor qilib, farqni ko'rgach QAYTA sanay olardi (ko'r sanoq buziladi).
# Bizning yo'llar (`close_shift`, Desktop POS) yopilishni AYNAN shu modulning
# `make_closing_entry_from_opening()` idan quradi, ya'ni ular bu tekshiruvdan o'zgarishsiz o'tadi.

#: Kutilgan summa solishtiruvida yo'l qo'yiladigan farq (valyuta tiyini).
CLOSING_TOLERANCE = 0.01


def guard_closing_entry(doc, method=None):
    """POS Closing Entry `validate`: oddiy kassir kutilgan summa va cheklar ro'yxatini o'zi yoza olmaydi."""
    from ozturkapp.ozturkapp.utils import cashier_billing

    if not cashier_billing.guard_applies():
        return
    if not doc.get("pos_opening_entry") or not frappe.db.exists("POS Opening Entry", doc.pos_opening_entry):
        return   # yaroqsiz smenani ERPNext o'zi rad etadi

    truth = make_closing_entry_from_opening(frappe.get_doc("POS Opening Entry", doc.pos_opening_entry))
    want = {row.mode_of_payment: flt(row.expected_amount) for row in truth.payment_reconciliation}
    got = {row.mode_of_payment: flt(row.expected_amount) for row in doc.get("payment_reconciliation") or []}
    drift = any(
        abs(want.get(mode, 0) - got.get(mode, 0)) > CLOSING_TOLERANCE for mode in want.keys() | got.keys()
    )
    same_invoices = {row.pos_invoice for row in truth.pos_transactions} == {
        row.pos_invoice for row in doc.get("pos_transactions") or []
    }
    if drift or not same_invoices:
        frappe.throw(
            frappe._(
                "Yopish ma'lumoti smenaning haqiqiy holatiga mos emas (kutilgan summa yoki cheklar "
                "ro'yxati). Kassani «Kassani yopish» tugmasi orqali yoping."
            ),
            title=frappe._("Yopish rad etildi"),
        )


def guard_closing_cancel(doc, method=None):
    """POS Closing Entry `before_cancel`: kassir yopilishni bekor qilib qayta sana olmaydi."""
    from ozturkapp.ozturkapp.utils import cashier_billing

    if cashier_billing.guard_applies():
        frappe.throw(
            frappe._("Kassa yopilishini faqat menejer bekor qila oladi."),
            exc=frappe.PermissionError,
            title=frappe._("Menejer tasdig'i kerak"),
        )
