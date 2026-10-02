# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Chek chegirmasi — ERPNext'ning "Additional Discount" mexanizmi bilan.

HISOB-KITOB ERPNEXT'NIKI
========================
Bu modul chegirma summasini HISOBLAMAYDI. U faqat POS Invoice'ning
`additional_discount_percentage` maydonini to'ldiradi va `save()` qiladi:
ERPNext chegirmani mahsulot qatorlariga taqsimlaydi va jami summani
yangilaydi. Xizmat haqi chegirmadan KAMAYMAYDI — u chek chegirmasidan
OLDINGI taomlar summasidan olinadi (biznes qarori, `utils/service_charge.py`):

    taomlar 60 000, xizmat haqi 12%    -> 60 000 + 7 200 = 67 200
    10% chegirma (Net Total bo'yicha)  -> 54 000 + 7 200 = 61 200

`apply_discount_on` DOIM "Net Total" — "Grand Total" bo'lsa chegirma
choychaqa qatoriga ham tegib ketardi (choychaqa chegirmadan tashqarida).

SUMMA HAM FOIZ SIFATIDA SAQLANADI
=================================
Kassir summa kiritsa (masalan 7 777) u samarali foizga aylantiriladi va
foiz saqlanadi. Sabab: qaytarishda (`utils/refunds.py`) ERPNext chegirmani
foiz bo'yicha qayta hisoblaydi, ya'ni QISMAN qaytarishda chegirma ham
mutanosib qaytadi. Foiz 9 xonagacha saqlanadi — natijaviy summa kiritilgan
summaga aniq teng chiqadi (testda tekshirilgan).

SIYOSAT
=======
Kassir istalgan chegirmani (100% dan kichik) menejer tasdig'isiz qo'yadi —
sabab majburiy va chek tarixida qoladi.

LOG
===
Har bir chegirma (va uni olib tashlash) uch joyda qoladi:

    chek maydonlari   `custom_discount_by` / `custom_discount_at` — kim, qachon
                      (sotuv dashboardining «Chegirmalar» bo'limi shundan o'qiydi)
    chek tarixi       izoh: foiz, summa, sabab, kim
    server logi       `ozturk_cashier` (logs/ozturk_cashier.log)

100% VA UNDAN KATTA CHEGIRMA QABUL QILINMAYDI
=============================================
Nol summali chekni to'lab bo'lmaydi (to'lov qatori kerak, nol summa esa
o'tkazib yuboriladi). Bunday holat "bepul" emas, "bekor qilish" — buyurtmani
bekor qilish oqimi (`utils/order_cancel.py`) izlarini qoldiradi.

ALLAQACHON CHIQARILGAN HISOB
============================
Chegirma hisob chop etilgandan keyin ham o'zgartirilishi mumkin (mijoz
to'lov paytida so'raydi). Lekin qog'ozdagi chek eskirdi — shuning uchun
`custom_reprint_needed = 1` qo'yiladi va `build_bill()` buni
`reprint_needed` sifatida qaytaradi. Hisobni qayta chiqarish (`open_bill`,
`print_bill`) belgini o'chiradi.
"""

import math

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from ozturkapp.ozturkapp.utils import cashier_billing, cashier_permissions

AUDIT_COLUMNS = ("custom_discount_reason", "custom_discount_approved_by", "custom_reprint_needed")


def _assert_fields_ready():
    """Audit maydonlari yaratilmagan bo'lsa jimgina yo'qotmaymiz — to'xtatamiz."""
    for column in AUDIT_COLUMNS:
        if not frappe.db.has_column("POS Invoice", column):
            frappe.throw(
                _(
                    "Chegirma maydonlari sozlanmagan. Administrator «bench migrate» yoki "
                    "cashier_billing_setup.setup ni ishga tushirishi kerak."
                ),
                title=_("Chegirma sozlanmagan"),
            )


def _load_draft(invoice: str, scope):
    """Chekni QULFLAB, qoralama va bekor qilinmaganini tasdiqlab qaytaradi."""
    frappe.db.sql("select name from `tabPOS Invoice` where name = %s for update", invoice)

    row = cashier_permissions.assert_invoice_in_scope(invoice, scope, docstatus=0)
    if cint(row.custom_cancelled):
        frappe.throw(_("Bekor qilingan buyurtmaga chegirma qo'yib bo'lmaydi"))

    doc = frappe.get_doc("POS Invoice", invoice)
    if cint(doc.is_return):
        frappe.throw(_("Qaytarish chekiga chegirma qo'yib bo'lmaydi"))
    if not doc.items:
        frappe.throw(_("Buyurtmada mahsulot yo'q — chegirma qo'yib bo'lmaydi"))
    return doc


def effective_percent(doc, percent=None, amount=None) -> float:
    """Kiritilgan foiz yoki summani samarali foizga aylantiradi va tekshiradi.

    Aynan bittasi berilishi shart. Summa mahsulotlar jamiga (`doc.total`,
    chegirmasiz) nisbatan foizga aylantiriladi.
    """
    has_percent = percent not in (None, "")
    has_amount = amount not in (None, "")

    if has_percent == has_amount:
        frappe.throw(
            _("Chegirma foizda YOKI summada kiritiladi — ikkalasi birga emas."),
            title=_("Chegirma noto'g'ri"),
        )

    # `flt("nan")` -> nan: u `<= 0` va `>= 100` tekshiruvlaridan ham, kassir
    # chegarasidan ham (nan > limit — False) jimgina o'tib, bazada xato beradi.
    given = flt(percent if has_percent else amount)
    if not math.isfinite(given):
        frappe.throw(_("Chegirma raqam bo'lishi kerak"), title=_("Chegirma noto'g'ri"))

    if has_percent:
        value = given
    else:
        base = flt(doc.total)
        if base <= 0:
            frappe.throw(_("Chek summasi nol — chegirma qo'yib bo'lmaydi"))
        value = given / base * 100

    if value <= 0:
        frappe.throw(
            _("Chegirma noldan katta bo'lishi kerak. Chegirmani olib tashlash uchun «Chegirmani bekor qilish»dan foydalaning."),
            title=_("Chegirma noto'g'ri"),
        )
    if value >= 100:
        frappe.throw(
            _(
                "100% va undan katta chegirma qabul qilinmaydi: nol summali chekni "
                "to'lab bo'lmaydi. To'liq bepul bo'lsa buyurtmani bekor qiling."
            ),
            title=_("Chegirma noto'g'ri"),
        )
    return value


def apply_discount(invoice, scope, percent=None, amount=None, reason=None):
    """Chekka chegirma qo'yadi (yoki almashtiradi). `build_bill()` natijasini qaytaradi."""
    _assert_fields_ready()

    reason = (reason or "").strip()
    if not reason:
        frappe.throw(_("Chegirma sababini kiriting"), title=_("Sabab majburiy"))

    doc = _load_draft(invoice, scope)
    value = effective_percent(doc, percent, amount)

    # Choychaqa to'lov paytida qo'yiladi va mutlaq summa — chegirma o'zgarsa
    # eskirgan choychaqa qolib ketmasligi uchun olib tashlanadi.
    cashier_billing.set_tip(doc, 0)

    doc.apply_discount_on = "Net Total"
    doc.discount_amount = 0
    doc.additional_discount_percentage = value
    doc.custom_discount_reason = reason
    doc.custom_discount_approved_by = None
    _set_given_by(doc, frappe.session.user, now_datetime())
    _mark_stale_bill(doc)
    with cashier_billing.trusted_billing():
        doc.save()

    _log(doc, _("Chegirma berildi: {0}% ({1}) — sabab: {2}").format(
        flt(value, 2), frappe.format(doc.discount_amount, {"fieldtype": "Currency"}), reason,
    ))
    return cashier_billing.build_bill(doc, scope)


def remove_discount(invoice, scope):
    """Chegirmani olib tashlaydi (idempotent). `build_bill()` natijasini qaytaradi."""
    _assert_fields_ready()

    doc = _load_draft(invoice, scope)
    if not (flt(doc.additional_discount_percentage) or flt(doc.discount_amount)):
        return cashier_billing.build_bill(doc, scope)

    doc.additional_discount_percentage = 0
    doc.discount_amount = 0
    removed = flt(doc.discount_amount)
    doc.custom_discount_reason = None
    doc.custom_discount_approved_by = None
    _set_given_by(doc, None, None)
    _mark_stale_bill(doc)
    with cashier_billing.trusted_billing():
        doc.save()

    _log(doc, _("Chegirma olib tashlandi ({0})").format(
        frappe.format(removed, {"fieldtype": "Currency"}),
    ))
    return cashier_billing.build_bill(doc, scope)


def _set_given_by(doc, user, at):
    """Kim/qachon — maydonlar hali yaratilmagan saytda (migrate oldidan) jimgina o'tkaziladi:
    chegirmaning o'zi buzilmasin, «kim» esa dashboardda Version tarixidan topiladi."""
    if frappe.db.has_column("POS Invoice", "custom_discount_by"):
        doc.custom_discount_by = user
        doc.custom_discount_at = at


def _log(doc, text):
    """Chek tarixiga izoh va server logiga yozuv."""
    doc.add_comment("Comment", text)
    frappe.logger("ozturk_cashier").info(
        "%s | %s | kassir=%s", doc.name, text, frappe.session.user,
    )


def _mark_stale_bill(doc):
    """Chop etilgan chek eskirdi — kassirga qayta chiqarishni eslatamiz."""
    if cint(doc.invoice_printed):
        doc.custom_reprint_needed = 1
