# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""«Aksiya» chegirmasi ichimliklarga tegmaydi.

BIZNES QOIDASI (2026-10-02)
===========================
Kassir sababi «Aksiya» bo'lgan chegirma qo'ysa (odatda 50%), u faqat
ichimlik BO'LMAGAN taomlarga qo'llanadi; «Напитки» guruhidagi mahsulotlar
to'liq narxda qoladi. Boshqa sabablar (Doimiy mijoz, Xodim, Shikoyat, ...)
avvalgidek butun chekka::

    taom 100 000 + ichimlik 20 000, «Aksiya» 50%
        chegirma = 50% × 100 000 = 50 000   (ilgari 60 000 edi)

QANDAY
======
Chekda foiz (`additional_discount_percentage`, masalan 50) SAQLANADI — kassa
ekrani va chek «50%» ko'rsatadi. Hisoblash paytida esa (`overrides/
pos_invoice_totals`) ERPNext'ga foiz o'rniga tayyor summa beriladi:
foiz × chegirmaga tushadigan taomlar summasi. Keyin chegirma mahsulot
qatorlariga faqat shu taomlar bo'yicha taqsimlanadi — ichimlik qatorining
sof summasi (daromad) to'liq qoladi.

Mahsulot qo'shilsa/olib tashlansa summa har hisoblashda qayta topiladi.
Qaytarish cheki asl chekning sababiga qaraydi: qaytarilgan ichimlik to'liq,
taom esa chegirmali narxda qaytadi.

FAQAT QORALAMA (va to'lov paytidagi submit) — to'langan cheklarga tegilmaydi.
"""

from contextlib import contextmanager

import frappe
from frappe.utils import cint, flt

#: Shu sabab bilan berilgan chegirma ...
REASON = "Aksiya"
#: ... shu guruhlardagi mahsulotlarga tegmaydi.
EXCLUDED_GROUPS = frozenset({"Напитки"})


def _reason(doc) -> str:
    reason = doc.get("custom_discount_reason")
    if not reason and cint(doc.get("is_return")) and doc.get("return_against"):
        reason = frappe.db.get_value(doc.doctype, doc.return_against, "custom_discount_reason")
    return (reason or "").strip()


def applies(doc) -> bool:
    # Qoralama yoki AYNAN HOZIR to'lanayotgan chek (submit paytida ERPNext
    # jami summani docstatus=1 bilan yana bir bor hisoblaydi).
    editable = cint(doc.docstatus) == 0 or doc.get("_action") == "submit"
    return (
        editable
        and flt(doc.get("additional_discount_percentage")) > 0
        and doc.get("apply_discount_on") == "Net Total"
        and _reason(doc).lower() == REASON.lower()
    )


def is_excluded(row) -> bool:
    group = row.get("item_group") or frappe.get_cached_value("Item", row.item_code, "item_group")
    return group in EXCLUDED_GROUPS


def eligible_total(doc) -> float:
    """Chegirmaga tushadigan taomlar summasi (chek chegirmasidan oldin)."""
    return sum(flt(row.amount) for row in doc.get("items") or [] if not is_excluded(row))


@contextmanager
def fixed_amount(doc):
    """Ichida ERPNext foiz o'rniga tayyor summani qo'llaydi; chiqishda foiz qaytadi.

    `yield` qiymati — qoida ishladimi (True bo'lsa qayta hisoblash kerak).
    """
    if not applies(doc):
        yield False
        return
    percent = flt(doc.additional_discount_percentage)
    doc.additional_discount_percentage = 0
    doc.discount_amount = flt(eligible_total(doc) * percent / 100, doc.precision("discount_amount"))
    try:
        yield True
    finally:
        doc.additional_discount_percentage = percent


def redistribute(doc):
    """Chegirmani mahsulot qatorlariga FAQAT chegirmaga tushadigan taomlar bo'yicha taqsimlaydi.

    Jami (`net_total`) o'zgarmaydi — faqat qatorlar orasidagi bo'linish.
    """
    if not applies(doc):
        return
    rows = [row for row in doc.items if not is_excluded(row)]
    base = sum(flt(row.amount) for row in rows)
    discount = flt(doc.discount_amount)
    rate = flt(doc.conversion_rate) or 1
    left = discount
    eligible = {id(row) for row in rows}
    for row in doc.items:
        share = 0.0
        if id(row) in eligible and base:
            share = left if row is rows[-1] else flt(discount * flt(row.amount) / base,
                                                     row.precision("distributed_discount_amount"))
            left -= share
        row.distributed_discount_amount = share
        row.net_amount = flt(flt(row.amount) - share, row.precision("net_amount"))
        row.net_rate = flt(row.net_amount / row.qty, row.precision("net_rate")) if flt(row.qty) else 0
        row.base_net_amount = flt(row.net_amount * rate, row.precision("base_net_amount"))
        row.base_net_rate = flt(row.net_rate * rate, row.precision("base_net_rate"))
