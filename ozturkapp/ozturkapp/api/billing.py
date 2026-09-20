# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa oynasi — hisob va to'lov (TZ §9, §17, §22, §23, §24).

BUXGALTERIYA YARATILMAYDI
=========================
To'lov ERPNext'ning o'z mexanizmi bilan amalga oshiriladi::

    POS Invoice.payments[] -> .save() -> .submit() -> GL Entry

Hujjat API'si orqali ketganimiz uchun URY'ning barcha hook'lari
(`before_submit`, `on_submit`, KOT yopilishi, stol bo'shatilishi) odatdagidek
ishlaydi (TZ §32 — mavjud POS buzilmaydi).

NEGA `ury...make_invoice` CHAQIRILMAYDI
======================================
Upstream `make_invoice()` da ikkita muammo bor:

  1. `restaurant = get_restaurant_and_menu_name(table)` — bu funksiya
     UCH ELEMENTLI kortej qaytaradi (`branch, menu, restaurant`), lekin
     natija to'g'ridan-to'g'ri `invoice.restaurant` (Link maydon) ga
     yoziladi. Ya'ni Link maydonga kortej tushadi.
  2. U `get_order_invoice()` orqali menyu talab qiladi va restoranda faol
     menyu bo'lmasa `throw` qiladi — to'lovga menyuning aloqasi yo'q.

Shuning uchun to'lov shu yerda, ERPNext'ning AYNAN o'sha mexanizmi bilan
bajariladi. URY manbasiga tegilmaydi (TZ §32).

TO'LOVGACHA HISOB CHIQARISH SHART
================================
`ury/hooks/ury_pos_invoice.py:validate_invoice_print` — stolga bog'langan
chek `invoice_printed = 0` bo'lsa submit'ga YO'L QO'YMAYDI. Bu TZ §22 dagi
"7. Hisob/to'lovni ochish -> 9. To'lovni tasdiqlash" ketma-ketligining
server tomonidagi kafolati.
"""

import json
import math

import frappe
from frappe import _
from frappe.utils import cint, flt

from ozturkapp.ozturkapp.setup import bill_split_setup, cashier_features
from ozturkapp.ozturkapp.utils import (
    cashier_billing,
    cashier_permissions,
    discounts,
    money,
    refunds,
    table_status,
)
from ozturkapp.ozturkapp.utils.cashier_realtime import emit_floor_change, emit_order_change
from ozturkapp.ozturkapp.utils.deadlock import retry_on_deadlock

#: Bitta to'lov qatori uchun eng katta summa. Bazadagi Currency ustuni (21,9) dan
#: kichik bo'lishi kerak: `1e30` kabi qiymat `DataError` (500) bilan yiqilardi.
MAX_PAYMENT_AMOUNT = 10 ** 11


@frappe.whitelist()
def get_bill(invoice):
    """Chek tarkibi — mahsulotlar, oraliq summa, xizmat haqi, jami (TZ §22)."""
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_permissions.assert_invoice_in_scope(invoice, scope)

    doc = frappe.get_doc("POS Invoice", invoice)
    return cashier_billing.build_bill(doc, scope)


@frappe.whitelist()
def get_payment_modes():
    """POS Profile'dagi to'lov usullari + qaytim hisobi sozlanganmi."""
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()

    return {
        "methods": cashier_billing.get_payment_methods(scope.pos_profile),
        "currency": scope.currency,
        "change_account": frappe.db.get_value(
            "POS Profile", scope.pos_profile, "account_for_change_amount"
        ),
    }


@frappe.whitelist()
def open_bill(invoice):
    """Hisobni ochish — `invoice_printed = 1`.

    Bu to'lovdan OLDINGI majburiy qadam (yuqoridagi izohga qarang). Stol
    SHU BOSQICHDA BO'SHATILMAYDI: TZ §23 ga ko'ra stolni faqat muvaffaqiyatli
    to'lov bo'shatadi. (URY'da `release_tables_after_print()` degan metod bor,
    lekin biz uni ATAYLAB chaqirmaymiz.)
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_permissions.assert_can_bill(scope.pos_profile)
    cashier_permissions.assert_shift_open(scope)

    row = cashier_permissions.assert_invoice_in_scope(invoice, scope, docstatus=0)

    if cint(row.custom_cancelled):
        frappe.throw(_("Bekor qilingan buyurtma uchun hisob ochib bo'lmaydi"))

    if not frappe.db.count("POS Invoice Item", {"parent": invoice}):
        frappe.throw(
            _("Buyurtmada mahsulot yo'q — avval taomlarni qo'shing."),
            title=_("Bo'sh buyurtma"),
        )

    if cint(row.invoice_printed):
        # Idempotent: ikkinchi bosishda xato bermaymiz.
        return get_bill(invoice)

    frappe.db.set_value("POS Invoice", invoice, "invoice_printed", 1)
    cashier_billing.clear_reprint_needed(invoice)

    emit_order_change(scope.branch, invoice, "BILL_OPENED", row.restaurant_table)
    emit_floor_change(
        scope.branch, _tables_of(row), "BILL_OPENED", invoice
    )

    return get_bill(invoice)


@frappe.whitelist()
def split_bill(invoice, items_to_move, customer=None):
    """Hisobni taqsimlash — tanlangan mahsulotlarni yangi, bog'liq chekka ko'chiradi.

    HAQIQIY ISH URY'NIKI
    =====================
    Mahsulotni ko'chirish, soliq/xizmat haqini ikkala chekda ham qayta
    hisoblash — bularning barchasini `ury.ury.doctype.ury_order.ury_order
    .split_bill()` bajaradi (u allaqachon mavjud va sinalgan). Bu yerda
    FAQAT kassa ko'lami (filial/POS Profile), smena va POS Profile'dagi
    yoqish/o'chirish bayrog'i tekshiriladi (TZ §17 — frontend'ga ishonmaymiz).

    Args:
        invoice: manba `POS Invoice` nomi (`docstatus = 0` bo'lishi shart).
        items_to_move: `[{"name": <POS Invoice Item qatori>, "qty": <son>}, ...]`
            — har bir qatordan necha dona ko'chirilishi. To'liq miqdor
            ko'chirilsa qator butunlay manba chekdan chiqadi.
        customer: ixtiyoriy — yangi chekka boshqa mijoz biriktirish.

    Returns:
        dict: source_invoice, new_invoice, source_bill, new_bill (ikkalasi
            ham to'liq `build_bill()` ko'rinishida — frontend qayta
            so'ramasdan darhol ko'rsata oladi).
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_permissions.assert_can_bill(scope.pos_profile)
    cashier_permissions.assert_shift_open(scope)

    row = cashier_permissions.assert_invoice_in_scope(invoice, scope, docstatus=0)

    if cint(row.custom_cancelled):
        frappe.throw(_("Bekor qilingan buyurtma uchun hisobni bo'lib bo'lmaydi"))

    if not bill_split_setup.is_enabled(scope.pos_profile):
        frappe.throw(
            _(
                "Hisobni taqsimlash bu kassa uchun yoqilmagan. "
                "POS Profile sozlamalaridan yoqing."
            ),
            title=_("Ruxsat yo'q"),
        )

    from ury.ury.doctype.ury_order.ury_order import split_bill as ury_split_bill

    result = ury_split_bill(invoice, items_to_move, customer=customer)
    new_invoice = result["new_invoice"]

    # URY `split_bill()` xizmat haqini manba chekning `taxes_and_charges`
    # (shablon havolasi) maydonidan ko'chiradi — lekin bu saytda soliq
    # qatori chekka TO'G'RIDAN-TO'G'RI (shablon havolasisiz) qo'shiladi,
    # ya'ni o'sha maydon har doim bo'sh. Natijada yangi chek xizmat
    # haqisiz qolib ketardi. Shuning uchun ikkalasida ham qo'lda tekshirib,
    # yetishmasa to'ldiramiz.
    _ensure_service_charge(invoice, scope.restaurant)
    _ensure_service_charge(new_invoice, scope.restaurant)

    emit_order_change(scope.branch, invoice, "BILL_SPLIT", row.restaurant_table)
    emit_order_change(scope.branch, new_invoice, "BILL_SPLIT", row.restaurant_table)
    emit_floor_change(scope.branch, _tables_of(row), "BILL_SPLIT", invoice)

    return {
        "source_invoice": result["source_invoice"],
        "new_invoice": new_invoice,
        "source_bill": get_bill(result["source_invoice"]),
        "new_bill": get_bill(new_invoice),
    }


@frappe.whitelist()
@retry_on_deadlock
def submit_payment(invoice, payments, tip=0):
    """To'lovni qabul qilish va chekni submit qilish (TZ §22).

    Args:
        invoice: `POS Invoice` nomi.
        payments: `[{"mode_of_payment": "<usul>", "amount": 112000}, ...]`
        tip: choychaqa summasi (faqat `tips` yoqilgan bo'lsa; 0 — choychaqasiz).

    To'lovning o'zi menejer tasdig'ini talab QILMAYDI (chegirma va qaytarish
    talab qiladi), shuning uchun `approval` parametri yo'q.

    Muvaffaqiyatli bo'lsa — chek submit bo'ladi, GL yozuvlari yaratiladi va
    stol biznes qoidalariga ko'ra bo'shatiladi. Xato bo'lsa — tranzaksiya
    orqaga qaytadi va stol BAND bo'lib qoladi (TZ §23).

    QOIDALAR
    ========
    * Bir necha to'lov qatori faqat `split_payment` yoqilgan bo'lsa.
    * Naqd bo'lmagan usul qoldiq summadan OSHMAYDI — faqat naqd ortiqcha
      to'lab qaytim beradi (naqd = `Mode of Payment.type == "Cash"`).
    * Choychaqa chek qoralamasiga soliq qatori sifatida yoziladi va jami
      qayta hisoblanadi — bir tranzaksiyada, chek qulfi ostida.
    * Naqd to'lovdan keyin (`cash_drawer` yoqilgan bo'lsa) g'aladon ochish
      COMMITDAN KEYIN fon ishchisida bajariladi — to'lovga ta'sir qilmaydi.
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_permissions.assert_can_bill(scope.pos_profile)
    cashier_permissions.assert_shift_open(scope)

    tip = flt(tip)
    if not math.isfinite(tip):
        frappe.throw(_("Choychaqa raqam bo'lishi kerak"), title=_("Choychaqa noto'g'ri"))
    if tip < 0:
        frappe.throw(_("Choychaqa manfiy bo'lishi mumkin emas"), title=_("Choychaqa noto'g'ri"))
    if tip > 0:
        cashier_features.assert_enabled(scope.pos_profile, "tips")
    tips_enabled = cashier_features.is_enabled(scope.pos_profile, "tips")
    split_allowed = cashier_features.is_enabled(scope.pos_profile, "split_payment")

    # ── Atomiylik: chek qatorini qulflaymiz (TZ §24) ──────────────────
    # Ikki kassir bir vaqtda "To'lash" bosganda ikkinchisi shu yerda kutadi
    # va keyingi tekshiruvda chek allaqachon to'langanini ko'radi.
    frappe.db.sql(
        "select name from `tabPOS Invoice` where name = %s for update", invoice
    )

    row = cashier_permissions.assert_invoice_in_scope(invoice, scope)
    if row.docstatus == 1:
        frappe.throw(_("Bu buyurtma allaqachon to'langan"), title=_("Takroriy to'lov"))
    if row.docstatus == 2:
        frappe.throw(_("Bu buyurtma bekor qilingan"))
    if cint(row.custom_cancelled):
        frappe.throw(_("Bu buyurtma bekor qilingan"))

    if row.restaurant_table and not cint(row.invoice_printed):
        frappe.throw(
            _("Avval hisobni oching (chekni chiqaring), keyin to'lovni qabul qiling."),
            title=_("Hisob ochilmagan"),
        )

    doc = frappe.get_doc("POS Invoice", invoice)
    if cint(doc.is_return):
        # Qaytarish chekining puli `refund_invoice` orqali (menejer tasdig'i bilan) chiqadi.
        frappe.throw(_("Qaytarish chekini sotuv sifatida to'lab bo'lmaydi"))
    if not doc.items:
        frappe.throw(_("Buyurtmada mahsulot yo'q — to'lov qilib bo'lmaydi"))

    # Choychaqa to'lanadigan summaga kiradi — to'lov tekshiruvidan OLDIN.
    # Funksiya o'chiq bo'lsa mavjud qatorlarga tegilmaydi (avvalgi xulq).
    if tips_enabled and (tip > 0 or cashier_billing.get_tip(doc) > 0):
        _apply_tip(doc, tip)

    rows = _validate_payments(payments, doc, scope, allow_split=split_allowed)

    doc.set("payments", [])
    for entry in rows:
        doc.append(
            "payments",
            {"mode_of_payment": entry["mode_of_payment"], "amount": entry["amount"]},
        )

    doc.save()
    doc.submit()

    emit_order_change(scope.branch, invoice, "PAYMENT_COMPLETED", row.restaurant_table)
    emit_floor_change(scope.branch, _tables_of(row), "PAYMENT_COMPLETED", invoice)

    if _has_cash(rows, scope) and cashier_features.is_enabled(scope.pos_profile, "cash_drawer"):
        _enqueue_drawer_kick(invoice)

    return {
        "invoice": doc.name,
        "docstatus": doc.docstatus,
        "paid_amount": flt(doc.paid_amount),
        "change_amount": flt(doc.change_amount),
        "grand_total": flt(doc.grand_total),
        "rounded_total": flt(doc.rounded_total),
        "tip": cashier_billing.get_tip(doc),
        "payments": rows,
        "table": row.restaurant_table,
        "table_status": _table_status_after_payment(row, scope),
    }


@frappe.whitelist()
def apply_discount(invoice, percent=None, amount=None, reason=None, approval=None):
    """Chekka chegirma qo'yish (foiz YOKI summa, sabab majburiy).

    Kassir chegarasidan oshsa `ApprovalRequired` — frontend PIN oynasini
    ochib, shu so'rovni `approval` bilan qayta yuboradi. Qoralama chek
    bo'lishi shart; `build_bill()` natijasini qaytaradi.
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_permissions.assert_can_bill(scope.pos_profile)
    cashier_permissions.assert_shift_open(scope)
    cashier_features.assert_enabled(scope.pos_profile, "discount")

    bill = discounts.apply_discount(invoice, scope, percent, amount, reason, approval)

    emit_order_change(scope.branch, invoice, "DISCOUNT_APPLIED", bill.get("table"))
    return bill


@frappe.whitelist()
def remove_discount(invoice):
    """Chegirmani olib tashlash (idempotent). `build_bill()` natijasini qaytaradi."""
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_permissions.assert_can_bill(scope.pos_profile)
    cashier_permissions.assert_shift_open(scope)
    cashier_features.assert_enabled(scope.pos_profile, "discount")

    bill = discounts.remove_discount(invoice, scope)

    emit_order_change(scope.branch, invoice, "DISCOUNT_REMOVED", bill.get("table"))
    return bill


@frappe.whitelist()
def get_even_split(invoice, parts):
    """To'lanadigan summani `parts` ta teng ulushga bo'ladi (aralash to'lov yordamchisi).

    Ulushlar yig'indisi to'lanadigan summaga ANIQ teng (qoldiq oxirgisiga).
    Sof hisob — chekka tegmaydi. Choychaqa ulushga KIRMAYDI: u to'lov
    paytida `submit_payment(tip=...)` bilan qo'shiladi.
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_features.assert_enabled(scope.pos_profile, "split_payment")
    cashier_permissions.assert_invoice_in_scope(invoice, scope, docstatus=0)

    return cashier_billing.get_even_split(frappe.get_doc("POS Invoice", invoice), parts)


@frappe.whitelist()
def get_refundable(invoice):
    """To'langan chek bo'yicha qaytarish mumkin bo'lgan miqdorlar va to'lov usullari."""
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_features.assert_enabled(scope.pos_profile, "refunds")
    cashier_permissions.assert_invoice_in_scope(invoice, scope, docstatus=1)

    return refunds.get_refundable(frappe.get_doc("POS Invoice", invoice))


@frappe.whitelist()
def refund_invoice(invoice, items, reason, approval=None):
    """To'langan chekni to'liq yoki qisman qaytarish — HAR DOIM menejer tasdig'i bilan.

    Args:
        invoice: asl (submit qilingan, qaytarish bo'lmagan) `POS Invoice`.
        items: `[{"name": <asl mahsulot qatori>, "qty": <son>}, ...]`
            (qatorlar `get_refundable()` dan).
        reason: sabab (majburiy).
        approval: `{"user", "pin"}` — menejerning o'zi kassada bo'lsa shart emas.

    Qaytarish cheki JORIY OCHIQ SMENAga tushadi (smena hisobotida manfiy
    sotuv bo'lib ko'rinadi). Mahsulot omborga QAYTMAYDI (`utils/refunds.py`).
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_permissions.assert_can_bill(scope.pos_profile)
    cashier_permissions.assert_shift_open(scope)
    cashier_features.assert_enabled(scope.pos_profile, "refunds")

    reason = (reason or "").strip()
    if not reason:
        frappe.throw(_("Qaytarish sababini kiriting"), title=_("Sabab majburiy"))

    # Ikki bir vaqtdagi qaytarish shu yerda navbatga turadi: ikkinchisi
    # birinchisining submit'ini ko'rib, qolgan miqdorni to'g'ri hisoblaydi.
    frappe.db.sql("select name from `tabPOS Invoice` where name = %s for update", invoice)

    row = cashier_permissions.assert_invoice_in_scope(invoice, scope, docstatus=1)
    if cint(row.custom_cancelled):
        frappe.throw(_("Bekor qilingan buyurtmani qaytarib bo'lmaydi"))

    doc = frappe.get_doc("POS Invoice", invoice)
    if cint(doc.is_return):
        frappe.throw(
            _("Bu chek o'zi qaytarish cheki — uni qayta qaytarib bo'lmaydi"),
            title=_("Qaytarish mumkin emas"),
        )

    return refunds.refund(doc, scope, items, reason, approval)


# ═══════════════════════════════════════════════════════════════════
#  Server tomonidagi to'lov tekshiruvi (TZ §17)
# ═══════════════════════════════════════════════════════════════════

def _apply_tip(doc, tip: float):
    """Choychaqa qatorini o'rnatib, jami summani qayta hisoblaydi (saqlamaydi).

    Yuqori chegara: chegirmadan keyingi mahsulotlar summasi (xizmat haqisiz),
    ya'ni 100%. Real choychaqa 5–20% bo'ladi; chekning o'zidan katta summa
    deyarli har doim ortiqcha nol kiritilgan xato.
    """
    precision = doc.precision("net_total")
    if flt(tip, precision) > flt(doc.net_total, precision):
        frappe.throw(
            _("Choychaqa chek summasidan ({0}) oshmasligi kerak").format(
                money.format_amount(doc.net_total)
            ),
            title=_("Choychaqa juda katta"),
        )

    cashier_billing.set_tip(doc, flt(tip, doc.precision("grand_total")))
    doc.run_method("calculate_taxes_and_totals")


def _parse_payments(payments) -> list:
    if isinstance(payments, str):
        try:
            payments = json.loads(payments)
        except ValueError:
            frappe.throw(_("To'lov ma'lumoti noto'g'ri formatda"))

    if not isinstance(payments, list) or not payments:
        frappe.throw(_("Kamida bitta to'lov usuli tanlanishi kerak"))
    return payments


def _payment_amount(value, precision: int) -> float:
    """Qator summasini son sifatida oladi: cheklangan va valyuta aniqligiga yaxlitlangan.

    `nan`/`inf`/`1e30` bazaga yetib xato bermasin, 3 xonali summa (`67200.004`)
    `paid_amount` ga axlat bo'lib yozilmasin.
    """
    amount = flt(value)
    if not math.isfinite(amount) or abs(amount) > MAX_PAYMENT_AMOUNT:
        frappe.throw(_("To'lov summasi noto'g'ri"), title=_("To'lov summasi noto'g'ri"))
    return flt(amount, precision)


def _validate_payments(payments, doc, scope, allow_split: bool = False) -> list:
    """To'lov qatorlarini tekshiradi. Frontend'ga ISHONMAYMIZ.

    `allow_split` — `split_payment` yoqilganmi. Standart `False`: aniqlanmagan
    chaqiruv aralash to'lovga yo'l ochib qo'ymasin.
    """
    payments = _parse_payments(payments)

    allowed = {
        method["mode_of_payment"]
        for method in cashier_billing.get_payment_methods(scope.pos_profile)
    }
    cash = set(cashier_billing.cash_modes(scope.pos_profile))
    precision = doc.precision("rounded_total")

    cleaned, total, cash_total = [], 0.0, 0.0
    for entry in payments:
        if not isinstance(entry, dict):
            frappe.throw(_("To'lov qatori noto'g'ri formatda"), title=_("To'lov usuli noto'g'ri"))

        mode = entry.get("mode_of_payment")
        amount = _payment_amount(entry.get("amount"), precision)

        if not isinstance(mode, str) or mode not in allowed:
            frappe.throw(
                _("'{0}' to'lov usuli bu kassa uchun ruxsat etilmagan").format(mode),
                title=_("To'lov usuli noto'g'ri"),
            )
        if amount < 0:
            frappe.throw(_("To'lov summasi manfiy bo'lishi mumkin emas"))
        if amount == 0:
            continue

        cleaned.append({"mode_of_payment": mode, "amount": amount})
        total += amount
        if mode in cash:
            cash_total += amount

    if not cleaned:
        frappe.throw(_("To'lov summasi kiritilmagan"))

    if len(cleaned) > 1 and not allow_split:
        frappe.throw(
            _(
                "Aralash to'lov bu kassa uchun yoqilmagan — chek faqat bitta "
                "usul bilan to'lanadi. Yoqish: POS Profile → «Aralash to'lovga ruxsat berish»."
            ),
            title=_("Aralash to'lov yoqilmagan"),
        )

    payable = flt(doc.rounded_total) or flt(doc.grand_total)

    if flt(total, precision) < flt(payable, precision):
        frappe.throw(
            _("To'lov summasi yetarli emas: {0} kiritildi, {1} kerak").format(
                money.format_amount(total),
                money.format_amount(payable),
            ),
            title=_("To'lov to'liq emas"),
        )

    # Ortiqcha to'lov (qaytim) — FAQAT naqd. Naqd bo'lmagan usul (karta,
    # o'tkazma) qoldiq summadan oshmaydi: kartadan ortiqcha yechilgan pulni
    # kassir qaytara olmaydi. Qaytim faqat naqd qatordan beriladi.
    if flt(total, precision) > flt(payable, precision):
        non_cash = total - cash_total

        if flt(non_cash, precision) > flt(payable, precision):
            frappe.throw(
                _(
                    "Naqd bo'lmagan usullar summasi ({0}) to'lanadigan summadan ({1}) oshib "
                    "ketdi. Faqat naqd pul ortiqcha qabul qilinadi (qaytim beriladi)."
                ).format(
                    money.format_amount(non_cash),
                    money.format_amount(payable),
                ),
                title=_("Ortiqcha to'lov mumkin emas"),
            )
        if cash_total and flt(non_cash, precision) >= flt(payable, precision):
            frappe.throw(
                _(
                    "Naqd bo'lmagan usullar summani to'liq qoplaydi — ortiqcha naqd "
                    "qatorini olib tashlang."
                ),
                title=_("Ortiqcha to'lov mumkin emas"),
            )

        # Qaytim faqat qaytim hisobi sozlangan bo'lsa mumkin —
        # aks holda ERPNext submit paytida tushunarsiz xato beradi.
        change_account = frappe.db.get_value(
            "POS Profile", scope.pos_profile, "account_for_change_amount"
        )
        if not change_account:
            frappe.throw(
                _(
                    "Ortiqcha to'lov qabul qilinmadi: POS Profile'da qaytim hisobi "
                    "(Account for Change Amount) sozlanmagan. Aniq summani kiriting."
                ),
                title=_("Qaytim sozlanmagan"),
            )

    return cleaned


def _has_cash(rows: list, scope) -> bool:
    cash = set(cashier_billing.cash_modes(scope.pos_profile))
    return any(entry["mode_of_payment"] in cash for entry in rows)


def _enqueue_drawer_kick(invoice: str):
    """G'aladonni ochishni to'lov COMMITIDAN KEYIN fon ishchisiga topshiradi.

    Nega to'g'ridan-to'g'ri chaqiruv emas: `kick_drawer` bosma navbatiga
    yozadi va boshqa agent tomonidan yoziladi — uning sekinligi yoki xatosi
    to'lov so'rovini sekinlashtirmasligi/yiqitmasligi kerak, DB xatosi esa
    to'lov tranzaksiyasini ifloslantirishi mumkin. `enqueue_after_commit`
    tufayli to'lov bekor bo'lsa g'aladon ham ochilmaydi.
    """
    try:
        frappe.enqueue(
            "ozturkapp.ozturkapp.utils.cashier_billing.kick_drawer_after_payment",
            queue="short",
            enqueue_after_commit=True,
            invoice=invoice,
            user=frappe.session.user,
        )
    except Exception:
        frappe.logger("ozturk_print").warning(
            "g'aladon topshirig'i navbatga qo'yilmadi: %s", invoice, exc_info=True
        )


# ═══════════════════════════════════════════════════════════════════
#  Yordamchilar
# ═══════════════════════════════════════════════════════════════════

def _ensure_service_charge(invoice_name: str, restaurant: str):
    """Split'dan keyingi chekda xizmat haqi yo'q bo'lib qolmasligini ta'minlaydi.

    Faqat qoralama (`docstatus = 0`) chekka tegadi va faqat xizmat haqi
    qatori haqiqatan ham YO'Q bo'lsa qo'shadi — allaqachon bor bo'lsa
    hech narsa qilmaydi (idempotent).
    """
    config = cashier_billing.get_service_charge_config(restaurant)
    if not config.get("enabled"):
        return

    doc = frappe.get_doc("POS Invoice", invoice_name)
    if doc.docstatus != 0:
        return
    if any(t.account_head == config["account"] for t in doc.taxes):
        return

    doc.append(
        "taxes",
        {
            "charge_type": "On Net Total",
            "account_head": config["account"],
            "description": config.get("description") or _("Xizmat haqi"),
            "rate": config["rate"],
        },
    )
    doc.save(ignore_permissions=True)


def _tables_of(row) -> list:
    tables = []
    if row.restaurant_table:
        tables.append(row.restaurant_table)
    tables.extend(table_status.parse_merged_with(row.custom_merged_tables))
    return list(dict.fromkeys(tables))


def _table_status_after_payment(row, scope) -> str:
    """To'lovdan keyin stol qanday holatda qolganini qaytaradi.

    Hisob bo'lingan bo'lsa stol BAND bo'lib qolishi mumkin — kassir buni
    darhol ko'rishi kerak (TZ §23).
    """
    if not row.restaurant_table:
        return ""

    from ozturkapp.ozturkapp.api.table import _resolve_table_state

    return _resolve_table_state(row.restaurant_table, scope)["status"]
