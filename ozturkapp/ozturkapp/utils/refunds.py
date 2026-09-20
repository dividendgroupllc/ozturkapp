# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""To'langan chekni qaytarish — ERPNext'ning POS qaytarish mexanizmi bilan.

MEXANIZM
========
`make_sales_return()` (ERPNext) asl chekka `return_against` bilan bog'langan,
manfiy miqdorli yangi `POS Invoice` (`is_return = 1`) yaratadi. Biz uni faqat
kerakli darajada moslaymiz va submit qilamiz; buxgalteriya (kredit-nota)
smena yopilganda ERPNext konsolidatsiyasi orqali yaratiladi — xuddi oddiy
cheklardek.

HAR DOIM MENEJER TASDIG'I
=========================
Qaytarish pulni kassadan chiqaradi. Tasdiq va sabab MAJBURIY, sozlab
o'chirib bo'lmaydi. Tasdiq izi ASL chekning tarixiga yoziladi.

QAYTARISH CHEKI QAYSI SMENAGA TUSHADI
=====================================
ERPNext smena hisobotini `POS Invoice.owner = <smenani ochgan kassir>` bilan
yig'adi. Qaytarishni menejer bajarsa, `owner` menejer bo'lib qolardi va
qaytarish hisobotga TUSHMASDI (kassadan pul chiqqan, hisobotda esa yo'q).
Shuning uchun `owner` JORIY OCHIQ SMENA egasiga o'rnatiladi — savdo cheklari
(`api/waiter.py`) ham xuddi shunday qilinadi.

TAQSIMLASH
==========
Qaytariladigan summa asl to'lov usullariga mutanosib (tiyin aniqligida)
taqsimlanadi. Asl chekdagi qaytim (change) naqd qatordan ayriladi — mijoz
aslida to'lagan summa `paid - change`. Avvalgi qaytarishlar qoldiq
sig'imdan ayriladi, shuning uchun usul bo'yicha ortiqcha qaytmaydi.
`allow_in_returns` o'chiq usulga qaytarish RAD ETILADI (jimgina boshqa
usulga o'tkazilmaydi — bu boshqa hisobdan pul chiqishi bo'lardi).

OSHXONA REALIYASI: TAOM OMBORGA QAYTMAYDI
========================================
Qaytarish chekida `update_stock = 0`. POS Profile'da `update_stock = 1`
bo'lgani uchun aks holda smena yopilganda kredit-nota tayyor taomni omborga
"qaytarib" qo'yardi — bunday taom tashlab yuboriladi. Daromad qaytadi,
tannarx (COGS) qaytmaydi.

CHOYCHAQA
=========
Choychaqa — xodimlarga tegishli, taomga emas. U faqat chek BUTUNLAY
qaytarilganda (qolgan miqdor qolmaganda) qaytariladi; qisman qaytarishda
choychaqa qatori qaytarish chekidan olib tashlanadi. (ERPNext "Actual"
qatorni to'liq manfiy qilib nusxalaydi — qisman qaytarishda bu noto'g'ri.)
"""

import json
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

import frappe
from frappe import _
from frappe.utils import cint, flt

from ozturkapp.ozturkapp.utils import cashier_billing, cashier_permissions, manager_approval, pos_closing
from ozturkapp.ozturkapp.utils.cashier_realtime import emit_order_change

QTY_PRECISION = 3

#: Qaytarish chekiga ko'chib o'tmasligi kerak bo'lgan maydonlar: stol/birlashtirish/
#: bo'lish bog'lari (URY hook'lari ularga qarab boshqa cheklarni o'zgartiradi) va
#: ofitsant/kassa holati.
RESET_ON_RETURN = (
    "restaurant_table",
    "custom_merged_tables",
    "custom_merged_pos_invoice",
    "custom_merged_total",
    "custom_split_from",
    "custom_split_group",
    "custom_bill_requested",
    "custom_bill_requested_at",
    "custom_bill_requested_by",
    "custom_ticket_number",
    "invoice_printed",
)


# ═══════════════════════════════════════════════════════════════════
#  O'qish: nima qaytarilishi mumkin
# ═══════════════════════════════════════════════════════════════════

def _returned_qty(invoice: str) -> dict:
    """`{asl qator nomi: qaytarilgan miqdor}` — barcha SUBMIT qilingan qaytarishlar."""
    rows = frappe.db.sql(
        """
        select child.pos_invoice_item as row_name, sum(abs(child.qty)) as qty
        from `tabPOS Invoice Item` child
        join `tabPOS Invoice` par on par.name = child.parent
        where par.docstatus = 1 and par.is_return = 1 and par.return_against = %s
        group by child.pos_invoice_item
        """,
        invoice,
        as_dict=True,
    )
    return {row.row_name: flt(row.qty, QTY_PRECISION) for row in rows}


def _refunded_by_mode(invoice: str) -> dict:
    """`{usul: qaytarilgan summa}` — avvalgi qaytarishlar bo'yicha."""
    rows = frappe.db.sql(
        """
        select pay.mode_of_payment, sum(abs(pay.amount)) as amount
        from `tabSales Invoice Payment` pay
        join `tabPOS Invoice` par on par.name = pay.parent and pay.parenttype = 'POS Invoice'
        where par.docstatus = 1 and par.is_return = 1 and par.return_against = %s
        group by pay.mode_of_payment
        """,
        invoice,
        as_dict=True,
    )
    return {row.mode_of_payment: flt(row.amount) for row in rows}


def net_paid_by_mode(doc) -> dict:
    """Mijoz aslida to'lagan summa usullar bo'yicha (qaytim ayirilgan).

    To'lov qatoridagi summa — qo'lga olingan (tendered) pul. Qaytim smena yopilishidagi
    AYNAN o'sha qoida bilan ayriladi (`pos_closing.net_payments`): bir nechta naqd usul
    bo'lsa qaytim qaysi usuldan ketgani yopilishdagi kutilgan summa bilan bir xil chiqadi,
    aks holda qaytarish sig'imi kassadagi naqd bilan mos kelmay qolardi.
    """
    result = {}
    for mode, amount in pos_closing.net_payments(doc):
        result[mode] = result.get(mode, 0) + amount
    return result


def get_refundable(doc) -> dict:
    """Chekning qaytariladigan holati: mahsulotlar va to'langan usullar."""
    returned = _returned_qty(doc.name)
    refunded = _refunded_by_mode(doc.name)
    methods = {m["mode_of_payment"]: m for m in cashier_billing.get_payment_methods(doc.pos_profile)}

    items = []
    for row in doc.items:
        sold = flt(row.qty, QTY_PRECISION)
        already = flt(returned.get(row.name, 0), QTY_PRECISION)
        items.append(
            {
                "name": row.name,
                "item_code": row.item_code,
                "item_name": row.item_name,
                "sold_qty": sold,
                "returned_qty": already,
                "refundable_qty": max(flt(sold - already, QTY_PRECISION), 0),
                "rate": flt(row.rate),
                "net_rate": flt(row.net_rate),
            }
        )

    paid = []
    for mode, amount in net_paid_by_mode(doc).items():
        back = flt(refunded.get(mode, 0))
        paid.append(
            {
                "mode_of_payment": mode,
                "paid": flt(amount),
                "refunded": back,
                "refundable": max(flt(amount - back), 0),
                "allow_in_returns": bool(methods.get(mode, {}).get("allow_in_returns")),
            }
        )

    return {
        "invoice": doc.name,
        "currency": doc.currency,
        "payable": flt(doc.rounded_total) or flt(doc.grand_total),
        "tip": cashier_billing.get_tip(doc),
        "is_return": bool(cint(doc.is_return)),
        "items": items,
        "paid": paid,
        "refundable": any(item["refundable_qty"] > 0 for item in items),
    }


# ═══════════════════════════════════════════════════════════════════
#  Qaytarish
# ═══════════════════════════════════════════════════════════════════

def _parse_items(items) -> dict:
    """`[{"name": <qator>, "qty": n}, ...]` -> `{qator: jami miqdor}`."""
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except ValueError:
            frappe.throw(_("Qaytariladigan mahsulotlar noto'g'ri formatda"))

    if not isinstance(items, list) or not items:
        frappe.throw(_("Qaytariladigan mahsulotlarni tanlang"), title=_("Mahsulot tanlanmagan"))

    wanted = {}
    for entry in items:
        name = (entry or {}).get("name")
        qty = flt((entry or {}).get("qty"), QTY_PRECISION)
        if not name:
            frappe.throw(_("Mahsulot qatori ko'rsatilmagan"))
        if qty <= 0:
            frappe.throw(_("Qaytariladigan miqdor noldan katta bo'lishi kerak"))
        wanted[name] = flt(wanted.get(name, 0) + qty, QTY_PRECISION)
    return wanted


def _plan(doc, wanted: dict) -> tuple:
    """Miqdorlarni QULF ostida tekshiradi. `(so'ralgan, oxirgimi)` qaytaradi."""
    returned = _returned_qty(doc.name)
    rows = {row.name: row for row in doc.items}

    unknown = set(wanted) - set(rows)
    if unknown:
        frappe.throw(
            _("Bu chekda bunday mahsulot qatori yo'q: {0}").format(", ".join(sorted(unknown))),
            title=_("Mahsulot topilmadi"),
        )

    final = True
    for name, row in rows.items():
        left = flt(flt(row.qty) - returned.get(name, 0), QTY_PRECISION)
        asked = wanted.get(name, 0)
        if asked > left:
            frappe.throw(
                _("'{0}' bo'yicha faqat {1} qaytarish mumkin (sotilgan {2}, qaytarilgan {3})").format(
                    row.item_name or row.item_code, left, flt(row.qty), returned.get(name, 0)
                ),
                title=_("Qaytariladigan miqdordan oshib ketdi"),
            )
        if flt(left - asked, QTY_PRECISION) > 0:
            final = False

    return wanted, final


def _units(value, step: Decimal) -> int:
    return int((Decimal(str(value)) / step).to_integral_value(rounding=ROUND_HALF_UP))


def allocate_refund(capacity: list, total: float, step: Decimal) -> list:
    """Summani sig'imlarga mutanosib, tiyin aniqligida taqsimlaydi.

    Args:
        capacity: `[(usul, qoldiq sig'im)]` — usullar tartibi saqlanadi.
        total: qaytariladigan summa (musbat).
        step: eng kichik pul birligi.

    Qoldiq (yaxlitlash) oxirgi sig'imi bor usulga qo'shiladi, shuning uchun
    yig'indi `total` ga ANIQ teng.
    """
    usable = [(mode, _units(amount, step)) for mode, amount in capacity if amount > 0]
    cap_units = sum(units for _mode, units in usable)
    total_units = _units(total, step)

    # Sig'im tugagan (hammasi qaytarilgan yoki yaxlitlashda nolga tushgan) —
    # bo'lishga hech narsa yo'q; jim `ZeroDivisionError`/`IndexError` o'rniga aniq xato.
    if cap_units <= 0:
        frappe.throw(
            _("Bu chek bo'yicha qaytariladigan to'lov qoldig'i yo'q"),
            title=_("Ortiqcha qaytarish"),
        )

    # Yaxlitlash tufayli sig'im bir necha tiyinga yetmay qolishi mumkin;
    # undan ortiq farq — haqiqiy ortiqcha qaytarish.
    if total_units > cap_units + len(usable):
        frappe.throw(
            _("Qaytariladigan summa ({0}) asl to'langan qoldiqdan ({1}) oshib ketdi").format(
                flt(total), flt(Decimal(cap_units) * step)
            ),
            title=_("Ortiqcha qaytarish"),
        )

    shares = []
    for mode, units in usable:
        share = (Decimal(total_units) * Decimal(units) / Decimal(cap_units)).to_integral_value(
            rounding=ROUND_FLOOR
        )
        shares.append([mode, int(share)])
    shares[-1][1] += total_units - sum(share for _mode, share in shares)

    return [(mode, flt(Decimal(share) * step)) for mode, share in shares if share > 0]


def refund(doc, scope, items, reason, approval=None) -> dict:
    """Chekni to'liq yoki qisman qaytaradi (chaqiruvchi chekni QULFLAGAN bo'lishi shart)."""
    from erpnext.accounts.doctype.pos_invoice.pos_invoice import make_sales_return

    wanted, final = _plan(doc, _parse_items(items))

    approver = manager_approval.require(
        _("Chekni qaytarish"), approval, "POS Invoice", doc.name, reason
    )

    shift = cashier_permissions.open_shift_name(scope)
    shift_user = frappe.db.get_value("POS Opening Entry", shift, "user")

    # Tasdiq olindi — hujjat qo'riqchisi (`cashier_billing.guard_invoice_changes`)
    # qaytarish chekini FAQAT shu yerdan o'tkazadi; qo'lda yaratilganini rad etadi.
    with cashier_billing.trusted_billing():
        ret = make_sales_return(doc.name)
        _shape_return(ret, doc, wanted, final, reason)
        _set_refund_payments(ret, doc)

        ret.insert()
        # Egalikni SUBMITDAN OLDIN va bazaga yozib o'zgartiramiz (modul izohiga qarang).
        frappe.db.set_value("POS Invoice", ret.name, "owner", shift_user, update_modified=False)
        ret = frappe.get_doc("POS Invoice", ret.name)
        ret.submit()

    emit_order_change(scope.branch, ret.name, "REFUND_COMPLETED")
    emit_order_change(scope.branch, doc.name, "REFUNDED", doc.get("restaurant_table"))

    return {
        "invoice": ret.name,
        "return_against": doc.name,
        "docstatus": ret.docstatus,
        "refunded": abs(flt(ret.rounded_total) or flt(ret.grand_total)),
        "payments": [
            {"mode_of_payment": row.mode_of_payment, "amount": flt(row.amount)}
            for row in ret.payments
        ],
        "final": final,
        "approved_by": approver,
    }


def _shape_return(ret, source, wanted: dict, final: bool, reason: str):
    """`make_sales_return` natijasini so'ralgan miqdorlarga moslaydi."""
    kept = []
    for row in ret.items:
        qty = wanted.get(row.pos_invoice_item)
        if not qty:
            continue
        row.qty = -qty
        row.stock_qty = flt(row.qty * flt(row.conversion_factor or 1), QTY_PRECISION)
        kept.append(row)
    ret.set("items", kept)

    for fieldname in RESET_ON_RETURN:
        if ret.meta.has_field(fieldname):
            ret.set(fieldname, None)
    if ret.meta.has_field("custom_merged_pos_invoice_details"):
        ret.set("custom_merged_pos_invoice_details", [])

    # Choychaqa faqat to'liq qaytarishda qaytadi (modul izohi).
    if not final:
        account = cashier_billing.tips_account(ret.company)
        ret.set(
            "taxes",
            [row for row in ret.taxes if not cashier_billing.is_tip_row(row, account)],
        )

    # Summa bilan qo'yilgan chegirmani foizga aylantiramiz: ERPNext to'liq
    # summani manfiy qilib nusxalaydi, qisman qaytarishda esa mutanosib bo'lishi kerak.
    if flt(source.discount_amount) and not flt(source.additional_discount_percentage):
        before = (
            flt(source.net_total) if source.apply_discount_on == "Net Total" else flt(source.grand_total)
        ) + flt(source.discount_amount)
        ret.discount_amount = 0
        ret.additional_discount_percentage = flt(source.discount_amount) / before * 100

    ret.update_stock = 0
    ret.custom_return_reason = reason
    ret.run_method("calculate_taxes_and_totals")

    if not flt(ret.rounded_total) and not flt(ret.grand_total):
        frappe.throw(_("Qaytariladigan summa nol"), title=_("Qaytarish mumkin emas"))


def _set_refund_payments(ret, source):
    """Qaytarish to'lovlari: asl usullarga mutanosib, manfiy, tiyin aniqligida."""
    total = abs(flt(ret.rounded_total) or flt(ret.grand_total))
    step = cashier_billing.money_step(ret.currency, cint(ret.precision("rounded_total")))

    refunded = _refunded_by_mode(source.name)
    capacity = [
        (mode, flt(paid) - flt(refunded.get(mode, 0)))
        for mode, paid in net_paid_by_mode(source).items()
    ]
    shares = allocate_refund(capacity, total, step)

    methods = {m["mode_of_payment"]: m for m in cashier_billing.get_payment_methods(source.pos_profile)}
    for mode, _amount in shares:
        if not methods.get(mode, {}).get("allow_in_returns"):
            frappe.throw(
                _(
                    "'{0}' usuli bilan qaytarish ruxsat etilmagan. POS Profile → To'lov "
                    "usullari jadvalida shu usul uchun «Qaytarishda ruxsat» (Allow In Returns) "
                    "belgisini yoqing."
                ).format(mode),
                title=_("Qaytarish usuli ruxsat etilmagan"),
            )

    originals = {row.mode_of_payment: row for row in source.payments}
    rate = flt(ret.conversion_rate or 1)
    ret.set("payments", [])
    for mode, amount in shares:
        original = originals[mode]
        ret.append(
            "payments",
            {
                "mode_of_payment": mode,
                "amount": -amount,
                "base_amount": -amount * rate,
                "type": original.type,
                "account": original.account,
                "default": original.default,
            },
        )

    # ERPNext `validate` to'langan summani OLDINGI saqlashdagi qiymatdan o'qiydi
    # (`set_paid_amount` undan keyin ishlaydi), `make_sales_return` esa asl
    # chekning summasini (qaytimi bilan) nusxalaydi — qo'lda to'g'rilaymiz.
    # Asl chekdagi qaytim va hisobdan chiqarish qaytarishga o'tmaydi.
    paid = -sum(amount for _mode, amount in shares)
    ret.paid_amount = paid
    ret.base_paid_amount = paid * rate
    ret.change_amount = 0
    ret.base_change_amount = 0
    ret.write_off_amount = 0
    ret.base_write_off_amount = 0
