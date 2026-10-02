# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Xizmat haqi chek chegirmasidan OLDINGI taomlar summasidan olinadi.

BIZNES QOIDASI (2026-10-02)
===========================
Kassir bergan chek chegirmasi faqat TAOMGA tegadi, xizmat haqiga emas::

    xizmat haqi = foiz × taomlar jami (`doc.total`)

`doc.total` — mahsulot qatorlari summasi: qator chegirmasidan KEYIN, lekin
chek chegirmasidan (`additional_discount_percentage` / `discount_amount`)
OLDIN. Masalan::

    taomlar 322 000, chegirma 50%  ->  taom 161 000 + xizmat 12% × 322 000 = 38 640
                                   ->  jami 199 640
    chegirmasiz                    ->  o'zgarishsiz: xizmat 12% × jami

NEGA "Actual" QATOR
===================
ERPNext "On Net Total" qatorini chegirmadan KEYINGI `net_total` dan hisoblaydi
va buni sozlab bo'lmaydi. Shuning uchun qoralama chekda xizmat haqi qatori
"Actual" turiga o'tkaziladi va summasi shu yerda qo'yiladi. ERPNext "Actual"
summani o'zgartirmaydi (faqat mahsulotlarga mutanosib taqsimlaydi), "Net Total"
chegirmasi esa "Actual" qatorlarga tegmaydi — natija aynan yuqoridagi qoida.

Foiz va hisob baribir soliq shablonidan (`cashier_billing.get_service_charge_config`)
o'qiladi — kodda qattiq yozilmagan, Desk'da shablon foizini o'zgartirish yetarli.
"Actual" qatorda ERPNext `rate` ni o'chiradi; foiz ko'rinishi (chek, ekran)
sozlamadan va qator tavsifidan ("Xizmat haqi 12%") olinadi.

QAYERDA ISHLAYDI
================
`overrides/pos_invoice.OzturkPOSInvoice.calculate_taxes_and_totals()`. ERPNext
`validate`, URY (`sync_order`, `split_bill`, `make_invoice`) va bizning kod
(choychaqa, chegirma, qaytarish) jami summani shu metod orqali hisoblaydi —
ya'ni mahsulot qo'shilsa/olib tashlansa, chegirma qo'yilsa/olinsa xizmat haqi
har safar qayta hisoblanadi. Bitta joy — "ikki joyda ikki xil summa" yo'q.

FAQAT QORALAMA (`docstatus = 0`)
================================
To'langan cheklarga TEGILMAYDI, ma'lumot ko'chirilmaydi. Eski cheklar
"On Net Total" qatori bilan qoladi; smena konsolidatsiyasi (ERPNext
`merge_pos_invoice_into`) har qanday qatorni "Actual" qilib
`tax_amount_after_discount_amount` bo'yicha yig'adi — eski va yangi cheklar
bir xil hisobda aralash qo'shiladi.

QAYTARISH
=========
Qaytarish cheki asl chekning xizmat haqini MUTANOSIB qaytaradi::

    -asl xizmat haqi × (qaytarilgan taomlar / asl taomlar)

va oxirgi qaytarish qoldiqni tiyinigacha yopadi. Asl chek eski qoida bilan
("On Net Total") to'langan bo'lsa qaytarishga tegilmaydi — ERPNext uni eski
qoida bo'yicha (o'sha chek qanday hisoblangan bo'lsa) mutanosib qaytaradi.

CHEGIRMA QAYERGA QO'YILADI
==========================
Chegirma "Grand Total" ga qo'yilgan bo'lsa (POS Profile standarti; URY
`make_invoice(additionalDiscount=...)`), foiz xizmat haqi va choychaqani ham
o'z ichiga olgan summadan hisoblanardi. Xizmat haqi qatori bor qoralamada
chegirma doim "Net Total" ga o'tkaziladi (`utils/discounts.py` ham shunday
qiladi) — chegirma faqat taomdan.
"""

import frappe
from frappe.utils import cint, flt

from ozturkapp.ozturkapp.utils import cashier_billing

ACTUAL = "Actual"


def apply(doc) -> bool:
    """Qoralama POS Invoice'dagi xizmat haqi qatorini yangi qoidaga keltiradi.

    Hujjatni SAQLAMAYDI va jami summani hisoblamaydi. Qator summasi yoki turi
    o'zgargan bo'lsa `True` qaytaradi — chaqiruvchi jami summani qayta
    hisoblashi kerak (`OzturkPOSInvoice.calculate_taxes_and_totals`).

    `doc.total` oldingi hisoblashdan olinadi, shuning uchun bu funksiya
    `calculate_taxes_and_totals()` dan KEYIN chaqiriladi.
    """
    if cint(doc.get("docstatus")) != 0 or not doc.get("items") or not doc.get("taxes"):
        return False

    config = _config(doc)
    account = config.get("account")
    if not config.get("enabled") or not account:
        return False

    row = next((tax for tax in doc.taxes if tax.account_head == account), None)
    if not row or row.get("row_id"):
        # Xizmat haqi yo'q (olib ketish) yoki u boshqa qatorga bog'langan —
        # nostandart sozlama, unga tegmaymiz.
        return False

    precision = row.precision("tax_amount")
    changed = False

    if cint(doc.get("is_return")):
        amount = _return_amount(doc, row, precision)
        if amount is None:
            return False
    else:
        amount = flt(flt(config.get("rate")) * flt(doc.total) / 100, precision)
        changed = _discount_on_net_total(doc)
        if config.get("description") and row.description != config["description"]:
            row.description = config["description"]

    if row.charge_type != ACTUAL or flt(row.tax_amount, precision) != amount:
        row.charge_type = ACTUAL
        row.tax_amount = amount
        changed = True
    return changed


def _config(doc) -> dict:
    restaurant = doc.get("restaurant") or frappe.db.get_value(
        "URY Restaurant", {"branch": doc.get("branch")}, "name"
    )
    return cashier_billing.get_service_charge_config(restaurant) if restaurant else {}


def _discount_on_net_total(doc) -> bool:
    """Chek chegirmasi bor bo'lsa — faqat taomdan ("Net Total"). Modul izohiga qarang."""
    has_discount = flt(doc.get("additional_discount_percentage")) or flt(doc.get("discount_amount"))
    if not has_discount or doc.get("apply_discount_on") == "Net Total":
        return False
    if cint(doc.get("is_cash_or_non_trade_discount")):
        return False
    doc.apply_discount_on = "Net Total"
    return True


def _return_amount(doc, row, precision):
    """Qaytarish chekidagi xizmat haqi: asl chekdan mutanosib (manfiy).

    Asl chek yangi qoida ("Actual") bilan to'lanmagan bo'lsa `None` — qatorga tegilmaydi.
    """
    source = doc.get("return_against")
    if not source:
        return None

    source_rows = frappe.get_all(
        "Sales Taxes and Charges",
        filters={"parenttype": "POS Invoice", "parent": source, "account_head": row.account_head},
        fields=["charge_type", "tax_amount"],
    )
    if not source_rows or any(r.charge_type != ACTUAL for r in source_rows):
        return None

    source_total = flt(frappe.db.get_value("POS Invoice", source, "total"))
    if not source_total:
        return None
    source_service = sum(flt(r.tax_amount) for r in source_rows)

    # Avvalgi (submit qilingan) qaytarishlar: qolgan taom va qolgan xizmat haqi.
    previous = frappe.db.sql(
        """
        SELECT COALESCE(SUM(pi.total), 0) AS total,
               COALESCE(SUM((
                   SELECT SUM(t.tax_amount) FROM `tabSales Taxes and Charges` t
                   WHERE t.parenttype = 'POS Invoice' AND t.parent = pi.name
                     AND t.account_head = %(account)s
               )), 0) AS service
        FROM `tabPOS Invoice` pi
        WHERE pi.return_against = %(source)s AND pi.is_return = 1
          AND pi.docstatus = 1 AND pi.name != %(name)s
        """,
        {"source": source, "account": row.account_head, "name": doc.name or ""},
        as_dict=True,
    )[0]

    returned_total = -(flt(previous.total) + flt(doc.total))
    if returned_total >= source_total - 0.005:
        # Oxirgi qaytarish: qoldiqni aniq yopadi (yaxlitlash farqi qolmaydi).
        return flt(-(source_service + flt(previous.service)), precision)
    return flt(source_service * flt(doc.total) / source_total, precision)
