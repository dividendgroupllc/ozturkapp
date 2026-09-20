# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa harakati API'si — smena davomida g'aladonga pul kiritish/undan chiqarish.

    get_cash_movements()           -> joriy smenaning harakatlari
    create_cash_movement(...)      -> yangi harakat (Journal Entry bilan)

Qoidalar `Ozturk Cash Movement` hujjatida (`doctype/ozturk_cash_movement`);
bu yerda faqat kim, qachon va qanday tasdiq bilan yoza olishi.

MENEJER TASDIG'I
================
    Chiqim (Out)  — summa `cash_payout_approval_limit` dan KATTA bo'lsa
                    (limit 0 — har bir chiqim tasdiqlanadi);
    Kirim  (In)   — DOIM (limit yo'q).

Nega kirim ham? Kirim kutilgan summani OSHIRADI. Kassir pul yetmayotganini
bilsa, sohta «kassaga qo'shish» yozib kamomadni yopa oladi. Amalda kassaga
mayda pulni baribir menejer olib keladi, shuning uchun uning PIN-kodi
tabiiy tasdiq. Menejer o'zi kassada ishlasa PIN so'ralmaydi.

BALANS TEKSHIRUVI YO'Q (ataylab)
================================
Chiqim "g'aladondagi puldan oshmasin" degan tekshiruv QO'YILMAGAN. Uning
uchun kutilgan summa kerak, xato xabari esa uni kassirga oshkor qilardi —
ko'r sanoq buziladi. Himoya boshqa yerda: chiqimning har biri (limitdan
yuqorisi) menejer tasdig'idan o'tadi, ortiqcha chiqim esa smena oxirida
kamomad bo'lib chiqadi.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, flt, now_datetime

from ozturkapp.ozturkapp.doctype.ozturk_cash_movement.ozturk_cash_movement import (
    CATEGORIES,
    KIND_OUT,
)
from ozturkapp.ozturkapp.setup import cashier_features
from ozturkapp.ozturkapp.utils import (
    cashier_billing,
    cashier_permissions,
    manager_approval,
    money,
    shift_report,
)

#: Bir xil harakat shuncha soniya ichida qaytarilsa — takror (ikki marta bosish
#: yoki tarmoq qayta yuborishi) deb rad etiladi. Tasodifiy emas: bir xil summa,
#: tur va sabab bilan ketma-ket ikki chiqim amalda uchramaydi.
DUPLICATE_WINDOW_SECONDS = 10


@frappe.whitelist()
def get_cash_movements():
    """Joriy smenaning kassa harakatlari va yangi harakat formasi uchun tanlovlar.

    Returns:
        dict: `pos_opening_entry` (smena yopiq bo'lsa `None`), `count`,
        `total_in`, `total_out`, `items`, `categories` ({"In": [...], "Out": [...]}),
        `cash_modes`.

    Bu javob kutilgan summani OCHMAYDI: kirim/chiqim jami sotuvsiz ma'nosiz.
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_features.assert_enabled(scope.pos_profile, "cash_movements")

    shift = cashier_permissions.open_shift_name(scope)
    summary = shift_report.movements_summary(shift) if shift else _empty_summary()
    return {
        "pos_opening_entry": shift or None,
        **summary,
        "categories": {kind: list(names) for kind, names in CATEGORIES.items()},
        "cash_modes": cashier_billing.cash_modes(scope.pos_profile),
    }


@frappe.whitelist(methods=["POST"])
def create_cash_movement(kind, amount, category, reason, approval=None, mode_of_payment=None):
    """Kassaga pul kiritish yoki undan chiqarish (Journal Entry bilan).

    Args:
        kind: "In" (kirim) yoki "Out" (chiqim).
        amount: summa, 0 dan katta.
        category: kirim — «Kassaga qo'shish» | «Boshqa»; chiqim — «Xarajat» |
            «Inkassatsiya» | «Boshqa».
        reason: sabab (kamida 3 belgi) — hisobotga va Journal Entry izohiga tushadi.
        approval: `{"user", "pin"}` (dict yoki JSON) — menejer tasdig'i
            (yuqoridagi qoidaga qarang). Kerak bo'lsa `ApprovalRequired`.
        mode_of_payment: naqd usul. Berilmasa profildagi birinchi naqd usul.

    Returns:
        dict: `get_cash_movements()` natijasi + `name`, `journal_entry`, `approved_by`.
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_features.assert_enabled(scope.pos_profile, "cash_movements")
    cashier_permissions.assert_shift_open(scope)

    shift = cashier_permissions.open_shift_name(scope)
    kind = str(kind or "").strip().capitalize()
    amount = flt(amount)

    doc = frappe.get_doc(
        {
            "doctype": "Ozturk Cash Movement",
            "pos_opening_entry": shift,
            "branch": scope.branch,
            "kind": kind,
            "category": category,
            "amount": amount,
            "mode_of_payment": _resolve_mode(scope.pos_profile, mode_of_payment),
            "reason": reason,
            "user": frappe.session.user,
            "posting_datetime": now_datetime(),
        }
    )
    # Qoidalar buzilgan bo'lsa PIN so'ramaymiz — kassir avval tuzatsin.
    doc.validate()

    _lock_open_shift(shift)
    _assert_not_duplicate(doc, shift)

    approver = _approve(doc, scope, shift, approval)
    if approver:
        doc.approved_by = approver

    doc.insert(ignore_permissions=True)
    doc.submit()

    return {
        **get_cash_movements(),
        "name": doc.name,
        "journal_entry": doc.journal_entry,
        "approved_by": doc.approved_by,
    }


def _lock_open_shift(shift: str):
    """Smena qatorini qulflaydi va u HALI OCHIQ ekanini qulf ichida qayta tekshiradi.

    Ikki maqsad: parallel so'rovlar navbatga turadi (takrorni ikkinchisi ko'radi)
    va smena shu orada yopilgan bo'lsa harakat yopilgan smenaga yozilmaydi —
    oddiy o'qish tranzaksiya boshidagi eski holatni ko'rishi mumkin, qulfli
    o'qish esa oxirgi tasdiqlangan holatni.
    """
    row = frappe.db.sql(
        "select status, docstatus from `tabPOS Opening Entry` where name = %s for update",
        shift,
        as_dict=True,
    )
    if not row or row[0].docstatus != 1 or row[0].status != "Open":
        frappe.throw(_("Kassa smenasi yopilgan"), title=_("Smena yopiq"))


def _approve(doc, scope, shift: str, approval):
    """Kerak bo'lsa menejer tasdig'ini oladi; tasdiqlagan foydalanuvchini qaytaradi."""
    limit = flt(cashier_features.get_settings(scope.pos_profile)["cash_payout_approval_limit"])
    if doc.kind == KIND_OUT and flt(doc.amount) <= limit:
        return None

    label = _("Kassadan chiqarish") if doc.kind == KIND_OUT else _("Kassaga kiritish")
    return manager_approval.require(
        f"{label} {money.format_amount(doc.amount)}",
        approval,
        reference_doctype="POS Opening Entry",
        reference_name=shift,
        details=f"{doc.category}: {doc.reason}",
    )


def _assert_not_duplicate(doc, shift: str):
    filters = {
        "pos_opening_entry": shift,
        "user": frappe.session.user,
        "kind": doc.kind,
        "category": doc.category,
        "amount": flt(doc.amount),
        "reason": doc.reason,
        "docstatus": 1,
        "creation": [">", add_to_date(now_datetime(), seconds=-DUPLICATE_WINDOW_SECONDS)],
    }
    if frappe.db.exists("Ozturk Cash Movement", filters):
        frappe.throw(
            _("Xuddi shu harakat hozirgina qayd etilgan — takror yozilmadi."),
            title=_("Takroriy harakat"),
        )


def _resolve_mode(pos_profile: str, requested) -> str:
    modes = cashier_billing.cash_modes(pos_profile)
    if not modes:
        frappe.throw(
            _("POS Profile'da naqd to'lov usuli sozlanmagan."), title=_("Naqd usul yo'q")
        )
    if requested:
        if requested not in modes:
            frappe.throw(
                _("'{0}' naqd to'lov usuli emas").format(requested),
                title=_("Noto'g'ri to'lov usuli"),
            )
        return requested
    return modes[0]


def _empty_summary() -> dict:
    return {"count": 0, "total_in": 0.0, "total_out": 0.0, "items": []}
