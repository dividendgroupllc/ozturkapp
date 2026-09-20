# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Smena hisoboti: X (oraliq) va Z (yopilgan smena) uchun MA'LUMOT.

    build_report("X", scope)   -> hozir ochiq smena bo'yicha
    build_report("Z", scope)   -> oxirgi YOPILGAN smena bo'yicha

Bu modul faqat yig'adi; qog'ozga chiqarish `utils/escpos.build_shift_report`
da, navbatga qo'yish `utils/print_queue.enqueue_shift_report` da.

MANBA — YOPISH BILAN AYNAN BIR XIL
==================================
X hisobot cheklarni `pos_closing.make_closing_entry_from_opening()` dan oladi,
ya'ni smena YOPILGANDA hisoblanadigan raqamning o'zi (qaytim va kassa
harakatlari bilan). Z hisobot esa yopilgan `POS Closing Entry` yozuvidan
o'qiydi — chunki yopilgach cheklar konsolidatsiya qilinadi va X yo'lidagi
filtr ularni endi topmaydi. Shu sababli hisobot bilan yopilish orasida farq
bo'lishi mumkin emas.

KO'R SANOQ (BLIND COUNT) — ENG MUHIM QOIDA
=========================================
Kassir naqd pulni KUTILGAN summani bilmay sanaydi (`api/cashier.py`).
Hisobot buni buzmasligi kerak, shuning uchun quyidagilar FAQAT menejerga
(`has_supervisor_role`) beriladi:

    - kutilgan naqd summa va farq (ortiqcha/kamomad);
    - savdo jami: yalpi savdo, chegirma, xizmat haqi, choychaqa, soliqlar,
      qaytarishlar, sof savdo;
    - NAQD usul bo'yicha summalar.

Nega savdo jami ham? `yalpi - naqd bo'lmagan usullar = naqd sotuv`, ya'ni
jamini ko'rgan kassir kutilgan summani bir amalda hisoblab oladi. Kassirga
qoladi: cheklar SONI, bekor qilingan buyurtmalar, kassa harakatlari
(o'zi kiritgani), naqd bo'lmagan usullar summasi (terminal bilan
solishtirish uchun) va Z da o'zi sanagan summa.

Menejerga ham smena YOPILGUNCHA (X hisobotda) faqat boshqa odam ochgan
smena ko'rinadi. Smenani o'zi ochgan (yopadigan) menejer uchun xuddi
kassirdagidek yopilishdan keyingina — aks holda ko'r sanoq uning
smenasida ishlamay qolardi. `restricted` kaliti yashirilganini bildiradi;
yashirilgan qiymatlar `None` (kalit o'zi qoladi, frontend tarmoqlanmaydi).
"""

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from ozturkapp.ozturkapp.utils import cashier_billing, cashier_permissions, pos_closing
from ozturkapp.ozturkapp.utils.print_queue import JOB_DRAWER

KIND_X = "X"
KIND_Z = "Z"

#: Hisobotda ko'rsatiladigan savdosiz g'aladon ochilishlari soni (sanog'i esa to'liq).
MAX_DRAWER_ROWS = 20

#: `sales` bloki kalitlari — kassir uchun hammasi `None`.
SALES_KEYS = (
    "gross_sales", "discounts", "service_charge", "tips", "other_taxes",
    "rounding", "sales_total", "returns_total", "net_total",
)


def may_see_cash_figures(shift_user: str, closed: bool) -> bool:
    """Kutilgan summa / farq / savdo jami shu so'rovchiga ko'rsatilishi mumkinmi.

    Args:
        shift_user: smenani ochgan foydalanuvchi (`POS Opening Entry.user`).
        closed: smena yopilganmi (Z).
    """
    if not cashier_permissions.has_supervisor_role():
        return False
    return bool(closed) or frappe.session.user != shift_user


def build_report(kind: str, scope, closing: str | None = None) -> dict:
    """X yoki Z hisobot ma'lumoti.

    Args:
        kind: "X" (ochiq smena) yoki "Z" (yopilgan smena).
        scope: `cashier_permissions.resolve_scope()` natijasi.
        closing: FAQAT ichki chaqiruv uchun — Z hisobot qaysi `POS Closing
            Entry` bo'yicha (`close_shift` hozirgina yopgan hujjat).
            Berilmasa oxirgisi olinadi va kassir faqat O'Z smenasini ko'radi.
    """
    kind = _normalize_kind(kind)
    shift = _open_shift(scope) if kind == KIND_X else _closed_shift(scope, closing)
    return _assemble(kind, scope, shift)


def _normalize_kind(kind) -> str:
    kind = str(kind or "").strip().upper()
    if kind not in (KIND_X, KIND_Z):
        frappe.throw(_("Hisobot turi «X» yoki «Z» bo'lishi kerak"), title=_("Hisobot turi noto'g'ri"))
    return kind


# ═══════════════════════════════════════════════════════════════════
#  Smenani topish
# ═══════════════════════════════════════════════════════════════════

def _open_shift(scope) -> frappe._dict:
    name = cashier_permissions.open_shift_name(scope)
    if not name:
        frappe.throw(_("Ochiq smena yo'q"), title=_("Smena yopiq"))

    opening = frappe.get_doc("POS Opening Entry", name)
    closing = pos_closing.make_closing_entry_from_opening(opening)
    return frappe._dict(
        opening=opening,
        closing=None,
        period_end=closing.period_end_date,
        invoices=[row.pos_invoice for row in closing.get("pos_transactions") or []],
        reconciliation=[
            frappe._dict(
                mode_of_payment=row.mode_of_payment,
                opening_amount=flt(row.opening_amount),
                expected_amount=flt(row.expected_amount),
                closing_amount=None,
                difference=None,
            )
            for row in closing.get("payment_reconciliation") or []
        ],
    )


def _closed_shift(scope, closing_name: str | None) -> frappe._dict:
    fields = ["name", "pos_opening_entry", "pos_profile", "user", "period_end_date"]
    if closing_name:
        row = frappe.db.get_value(
            "POS Closing Entry", {"name": closing_name, "docstatus": 1}, fields, as_dict=True
        )
    else:
        filters = {"pos_profile": scope.pos_profile, "docstatus": 1}
        if not cashier_permissions.has_supervisor_role():
            filters["user"] = frappe.session.user
        row = frappe.db.get_value(
            "POS Closing Entry", filters, fields, as_dict=True, order_by="creation desc"
        )

    if not row:
        frappe.throw(_("Yopilgan smena topilmadi"), title=_("Z-hisobot yo'q"))
    if row.pos_profile != scope.pos_profile:
        raise cashier_permissions.CashierPermissionError(
            _("Bu smena boshqa kassaga tegishli")
        )

    return frappe._dict(
        opening=frappe.get_doc("POS Opening Entry", row.pos_opening_entry),
        closing=row.name,
        period_end=row.period_end_date,
        invoices=frappe.get_all(
            "POS Invoice Reference",
            filters={"parent": row.name, "parenttype": "POS Closing Entry"},
            pluck="pos_invoice",
        ),
        reconciliation=[
            frappe._dict(
                mode_of_payment=detail.mode_of_payment,
                opening_amount=flt(detail.opening_amount),
                expected_amount=flt(detail.expected_amount),
                closing_amount=flt(detail.closing_amount),
                difference=flt(detail.difference),
            )
            for detail in frappe.get_all(
                "POS Closing Entry Detail",
                filters={"parent": row.name, "parenttype": "POS Closing Entry"},
                fields=[
                    "mode_of_payment", "opening_amount", "expected_amount",
                    "closing_amount", "difference",
                ],
                order_by="idx asc",
            )
        ],
    )


# ═══════════════════════════════════════════════════════════════════
#  Yig'ish
# ═══════════════════════════════════════════════════════════════════

def _assemble(kind: str, scope, shift) -> dict:
    opening = shift.opening
    closed = kind == KIND_Z
    full = may_see_cash_figures(opening.user, closed)

    invoices = _load_invoices(shift.invoices)
    service_account = (
        cashier_billing.get_service_charge_config(scope.restaurant).get("account")
        if scope.restaurant else None
    )
    sales, counts = _sales(invoices, service_account, cashier_billing.tips_account(opening.company))
    modes = _mode_types(invoices, shift.reconciliation)
    payments = _payments(invoices, shift.reconciliation, modes)
    cash = _cash(shift.reconciliation, modes, closed)
    movements = movements_summary(opening.name)

    drawer = _drawer_openings(opening.branch or scope.branch, opening.period_start_date, shift.period_end)
    counts["cancelled_orders"] = _cancelled_orders(
        opening.branch or scope.branch, opening.period_start_date, shift.period_end
    )
    counts["drawer_no_sale"] = len(drawer)

    if not full:
        sales = dict.fromkeys(SALES_KEYS)
        for row in payments:
            if row["is_cash"]:
                row["sales_amount"] = row["refund_amount"] = row["net_amount"] = None
        cash["expected"] = cash["difference"] = None

    return {
        "kind": kind,
        "restricted": not full,
        "restaurant": scope.restaurant,
        "company": opening.company,
        "branch": opening.branch or scope.branch,
        "pos_profile": opening.pos_profile,
        "currency": scope.currency,
        "pos_opening_entry": opening.name,
        "pos_closing_entry": shift.closing,
        "cashier": {"user": opening.user, "full_name": _user_label(opening.user)},
        "period_start": str(opening.period_start_date or ""),
        "period_end": str(shift.period_end or ""),
        "generated_at": str(now_datetime()),
        "printed_by": _user_label(frappe.session.user),
        "counts": counts,
        "sales": sales,
        "payments": payments,
        "cash_movements": movements,
        "cash": cash,
        "drawer_openings": drawer[:MAX_DRAWER_ROWS],
    }


def _load_invoices(names: list) -> list:
    """Cheklar + soliq + to'lov qatorlari — jami 3 ta so'rov (chek boshiga emas)."""
    if not names:
        return []

    invoices = frappe.get_all(
        "POS Invoice",
        filters={"name": ["in", names]},
        fields=[
            "name", "is_return", "total", "discount_amount", "grand_total",
            "rounded_total", "change_amount",
        ],
        order_by="creation asc",
    )
    taxes = pos_closing.bulk_children(
        "Sales Taxes and Charges", names, ["account_head", "charge_type", "tax_amount"]
    )
    payments = pos_closing.bulk_children(
        "Sales Invoice Payment", names, ["mode_of_payment", "amount", "type"]
    )
    for invoice in invoices:
        invoice.taxes = taxes.get(invoice.name, [])
        invoice.payments = payments.get(invoice.name, [])
    return invoices


def _sales(invoices: list, service_account, tip_account) -> tuple:
    """Savdo jami va cheklar soni. Qaytarish cheklari alohida sanaladi.

    Choychaqa qatori `cashier_billing.is_tip_row()` bilan aniqlanadi — u
    chekni yozadigan kod ham ishlatadigan YAGONA ta'rif.
    """
    totals = dict.fromkeys(SALES_KEYS, 0.0)
    counts = {"invoices": 0, "returns": 0}

    for invoice in invoices:
        payable = flt(invoice.rounded_total) or flt(invoice.grand_total)

        if invoice.is_return:
            counts["returns"] += 1
            totals["returns_total"] += abs(payable)
            continue

        counts["invoices"] += 1
        totals["gross_sales"] += flt(invoice.total)
        totals["discounts"] += flt(invoice.discount_amount)
        totals["sales_total"] += payable
        totals["rounding"] += payable - flt(invoice.grand_total)

        for tax in invoice.taxes:
            amount = flt(tax.tax_amount)
            if cashier_billing.is_tip_row(tax, tip_account):
                totals["tips"] += amount
            elif service_account and tax.account_head == service_account:
                totals["service_charge"] += amount
            else:
                totals["other_taxes"] += amount

    totals["net_total"] = totals["sales_total"] - totals["returns_total"]
    return {key: flt(value, 2) for key, value in totals.items()}, counts


def _mode_types(invoices: list, reconciliation: list) -> dict:
    """`{usul: turi}` — qaysi usul NAQD ekanini bitta so'rovda aniqlaydi."""
    modes = {row.mode_of_payment for row in reconciliation}
    for invoice in invoices:
        modes.update(payment.mode_of_payment for payment in invoice.payments)
    if not modes:
        return {}
    return {
        row.name: row.type
        for row in frappe.get_all(
            "Mode of Payment", filters={"name": ["in", list(modes)]}, fields=["name", "type"]
        )
    }


def _payments(invoices: list, reconciliation: list, types: dict) -> list:
    """To'lov usullari bo'yicha son va summa (naqdda qaytim ayirilgan)."""
    per_mode = {}

    def bucket(mode):
        return per_mode.setdefault(
            mode,
            {"sales_count": 0, "sales_amount": 0.0, "refund_count": 0, "refund_amount": 0.0},
        )

    # Naqd usullar (ochilishda sanalgan) sotuvsiz ham hisobotda turadi.
    for row in reconciliation:
        bucket(row.mode_of_payment)

    for invoice in invoices:
        counted = set()
        for mode, amount in pos_closing.net_payments(invoice):
            row = bucket(mode)
            if invoice.is_return:
                row["refund_amount"] += abs(amount)
                if mode not in counted:
                    row["refund_count"] += 1
            else:
                row["sales_amount"] += amount
                if mode not in counted:
                    row["sales_count"] += 1
            counted.add(mode)

    return [
        {
            "mode_of_payment": mode,
            "is_cash": types.get(mode) == "Cash",
            "sales_count": row["sales_count"],
            "sales_amount": flt(row["sales_amount"], 2),
            "refund_count": row["refund_count"],
            "refund_amount": flt(row["refund_amount"], 2),
            "net_amount": flt(row["sales_amount"] - row["refund_amount"], 2),
        }
        for mode, row in per_mode.items()
    ]


def _cash(reconciliation: list, types: dict, closed: bool) -> dict:
    """Naqd pul bloki. `expected`/`difference` ko'r sanoq uchun keyin yashiriladi."""
    rows = [row for row in reconciliation if types.get(row.mode_of_payment) == "Cash"]
    return {
        "opening": flt(sum(row.opening_amount for row in rows), 2),
        "counted": flt(sum(row.closing_amount for row in rows), 2) if closed else None,
        "expected": flt(sum(row.expected_amount for row in rows), 2),
        "difference": flt(sum(row.difference for row in rows), 2) if closed else None,
    }


def movements_summary(opening_name: str) -> dict:
    """Smenaning kassa harakatlari: soni, jami kirim/chiqim va qatorlar."""
    rows = pos_closing.get_cash_movements(opening_name)
    labels = {
        user: _user_label(user)
        for user in {u for row in rows for u in (row.user, row.approved_by) if u}
    }
    return {
        "count": len(rows),
        "total_in": flt(sum(flt(row.amount) for row in rows if row.kind == "In"), 2),
        "total_out": flt(sum(flt(row.amount) for row in rows if row.kind == "Out"), 2),
        "items": [
            {
                "name": row.name,
                "kind": row.kind,
                "category": row.category,
                "amount": flt(row.amount),
                "mode_of_payment": row.mode_of_payment,
                "reason": row.reason,
                "user": row.user,
                "user_name": labels[row.user],
                "approved_by": row.approved_by,
                # Menejerning ismi — ekranda foydalanuvchi ID emas, ism ko'rinsin.
                "approved_by_name": labels.get(row.approved_by) if row.approved_by else None,
                "posting_datetime": str(row.posting_datetime or ""),
            }
            for row in rows
        ],
    }


def _drawer_openings(branch: str, start, end) -> list:
    """Chekka bog'lanmagan (savdosiz) g'aladon ochilishlari.

    Naqd to'lovdan keyingi ochilish chek nomini saqlaydi (`ref_name`) —
    u yerda ro'yxatga TUSHMAYDI. Qolgani qo'lda ochilgan.
    """
    rows = frappe.get_all(
        "Ozturk Print Job",
        filters=[
            ["branch", "=", branch],
            ["job_type", "=", JOB_DRAWER],
            ["creation", "between", [start, end]],
            ["ref_name", "is", "not set"],
        ],
        fields=["name", "owner", "creation", "reason"],
        order_by="creation asc",
    )
    labels = {row.owner: _user_label(row.owner) for row in rows}
    return [
        {
            "job": row.name,
            "time": str(row.creation),
            "user": row.owner,
            "user_name": labels[row.owner],
            "reason": row.reason,
        }
        for row in rows
    ]


def _cancelled_orders(branch: str, start, end) -> int:
    """Smena davomida bekor qilingan buyurtmalar soni.

    Bekor qilingan chek `docstatus = 0` bo'lib qoladi va `custom_cancelled = 1`
    bilan belgilanadi (`utils/order_cancel.py`); bekor qilish vaqti alohida
    maydonda saqlanmaydi — `modified` (bekor qilishda yangilanadi) ishlatiladi.
    """
    return frappe.db.count(
        "POS Invoice",
        {
            "branch": branch,
            "docstatus": 0,
            "custom_cancelled": 1,
            "modified": ["between", [start, end]],
        },
    )


def _user_label(user) -> str:
    return (frappe.db.get_value("User", user, "full_name") if user else "") or user or ""
