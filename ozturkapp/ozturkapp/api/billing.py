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

import frappe
from frappe import _
from frappe.utils import cint, flt

from ozturkapp.ozturkapp.setup import bill_split_setup
from ozturkapp.ozturkapp.utils import cashier_billing, cashier_permissions, table_status
from ozturkapp.ozturkapp.utils.cashier_realtime import emit_floor_change, emit_order_change


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
def submit_payment(invoice, payments):
    """To'lovni qabul qilish va chekni submit qilish (TZ §22).

    Args:
        invoice: `POS Invoice` nomi.
        payments: `[{"mode_of_payment": "Cash", "amount": 112000}, ...]`

    Muvaffaqiyatli bo'lsa — chek submit bo'ladi, GL yozuvlari yaratiladi va
    stol biznes qoidalariga ko'ra bo'shatiladi. Xato bo'lsa — tranzaksiya
    orqaga qaytadi va stol BAND bo'lib qoladi (TZ §23).
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_permissions.assert_can_bill(scope.pos_profile)
    cashier_permissions.assert_shift_open(scope)

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
    if not doc.items:
        frappe.throw(_("Buyurtmada mahsulot yo'q — to'lov qilib bo'lmaydi"))

    rows = _validate_payments(payments, doc, scope)

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

    return {
        "invoice": doc.name,
        "docstatus": doc.docstatus,
        "paid_amount": flt(doc.paid_amount),
        "change_amount": flt(doc.change_amount),
        "grand_total": flt(doc.grand_total),
        "rounded_total": flt(doc.rounded_total),
        "table": row.restaurant_table,
        "table_status": _table_status_after_payment(row, scope),
    }


# ═══════════════════════════════════════════════════════════════════
#  Server tomonidagi to'lov tekshiruvi (TZ §17)
# ═══════════════════════════════════════════════════════════════════

def _validate_payments(payments, doc, scope) -> list:
    """To'lov qatorlarini tekshiradi. Frontend'ga ISHONMAYMIZ."""
    if isinstance(payments, str):
        try:
            payments = json.loads(payments)
        except ValueError:
            frappe.throw(_("To'lov ma'lumoti noto'g'ri formatda"))

    if not isinstance(payments, list) or not payments:
        frappe.throw(_("Kamida bitta to'lov usuli tanlanishi kerak"))

    allowed = {
        method["mode_of_payment"]
        for method in cashier_billing.get_payment_methods(scope.pos_profile)
    }

    cleaned, total = [], 0.0
    for entry in payments:
        mode = (entry or {}).get("mode_of_payment")
        amount = flt((entry or {}).get("amount"))

        if mode not in allowed:
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

    if not cleaned:
        frappe.throw(_("To'lov summasi kiritilmagan"))

    payable = flt(doc.rounded_total) or flt(doc.grand_total)
    precision = doc.precision("rounded_total")

    if flt(total, precision) < flt(payable, precision):
        frappe.throw(
            _("To'lov summasi yetarli emas: {0} kiritildi, {1} kerak").format(
                frappe.format_value(total, {"fieldtype": "Currency"}, doc),
                frappe.format_value(payable, {"fieldtype": "Currency"}, doc),
            ),
            title=_("To'lov to'liq emas"),
        )

    # Ortiqcha to'lov (qaytim) faqat qaytim hisobi sozlangan bo'lsa mumkin —
    # aks holda ERPNext submit paytida tushunarsiz xato beradi.
    if flt(total, precision) > flt(payable, precision):
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
