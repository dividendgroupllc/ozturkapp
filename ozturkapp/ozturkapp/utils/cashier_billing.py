# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Hisob-kitob: xizmat haqi sozlamasi va chek tarkibini yig'ish (TZ §8, §9).

ENG MUHIM QOIDA
===============
Bu modul PUL HISOBLAMAYDI.

12% xizmat haqi ERPNext'ning `Sales Taxes and Charges Template` qatori
sifatida sozlanadi va `URY Restaurant.default_tax_template` orqali ulanadi.
Hisoblashni butunlay ERPNext'ning `calculate_taxes_and_totals()` bajaradi —
URY `get_order_invoice()` va `validate_price_list()` da bu shablonni
allaqachon chekka qo'yadi.

Ya'ni:

    URY Restaurant.default_tax_template   <- sozlama (12%)
              |
              v
    POS Invoice.taxes[]                   <- ERPNext hisoblaydi
              |
              v
    build_bill()                          <- faqat O'QIYDI va ko'rsatadi

Foizni frontend'da ham, bu yerda ham qayta hisoblamaymiz. Shu sababli
"ikkita joyda ikki xil summa" muammosi tug'ilishi MUMKIN EMAS (TZ §8).

Tekshirildi (ozturk.local, ERPNext 15.97):
    net_total 100 000 -> service charge 12 000 -> grand_total 112 000
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

#: Xizmat haqi uchun standart hisob va shablon nomlari (setup ishlatadi).
SERVICE_CHARGE_ACCOUNT_NAME = "Service Charge"
SERVICE_CHARGE_TEMPLATE_TITLE = "Restaurant Service Charge"
DEFAULT_SERVICE_CHARGE_RATE = 12.0


# ═══════════════════════════════════════════════════════════════════
#  Xizmat haqi sozlamasi
# ═══════════════════════════════════════════════════════════════════

def get_service_charge_config(restaurant: str) -> dict:
    """Restoran uchun xizmat haqi sozlamasi.

    Foiz QATTIQ YOZILMAGAN — u ERPNext soliq shablonidagi qatordan o'qiladi.
    Qaysi qator "xizmat haqi" ekanini `URY Restaurant.custom_service_charge_account`
    belgilaydi (setup avtomatik to'ldiradi).

    Returns:
        dict: enabled, rate, account, description, template
    """
    restaurant_row = frappe.db.get_value(
        "URY Restaurant",
        restaurant,
        ["default_tax_template", "custom_service_charge_account"],
        as_dict=True,
    ) or frappe._dict()

    template = restaurant_row.get("default_tax_template")
    account = restaurant_row.get("custom_service_charge_account")

    config = {
        "enabled": False,
        "rate": 0.0,
        "account": account,
        "description": "",
        "template": template,
    }
    if not template:
        return config

    row = _find_service_charge_row(template, account)
    if not row:
        return config

    config.update(
        {
            "enabled": True,
            "rate": flt(row.rate),
            "account": row.account_head,
            "description": row.description or _("Xizmat haqi"),
        }
    )
    return config


def _find_service_charge_row(template: str, account: str = None):
    """Shablondagi xizmat haqi qatorini topadi.

    Avval sozlangan hisob bo'yicha, topilmasa — nomida "service" bo'lgan
    qator bo'yicha. Ikkalasi ham bo'lmasa `None` (xizmat haqi sozlanmagan).
    """
    rows = frappe.get_all(
        "Sales Taxes and Charges",
        filters={"parent": template, "parenttype": "Sales Taxes and Charges Template"},
        fields=["account_head", "rate", "description", "charge_type", "idx"],
        order_by="idx asc",
    )
    if not rows:
        return None

    if account:
        for row in rows:
            if row.account_head == account:
                return row

    for row in rows:
        haystack = f"{row.account_head or ''} {row.description or ''}".lower()
        if "service" in haystack or "xizmat" in haystack:
            return row

    return None


# ═══════════════════════════════════════════════════════════════════
#  Oshxona holati — FAQAT O'QISH (TZ §12, §15, §16)
# ═══════════════════════════════════════════════════════════════════

def get_kitchen_state(invoice: str, progress=None) -> dict:
    """Chekka tegishli KOT'lar holati.

    Kassa bu holatni FAQAT KO'RSATADI. Hech qachon o'zgartirmaydi —
    tayyorlash jarayoni oshxonaning mas'uliyati (TZ §12).

    `preparation_started` — buyurtmani bekor qilish qoidasining asosi
    (`utils/order_cancel.py`): oshxona ishga kirishmagan bo'lsa chekni har
    qanday kassir bekor qiladi, kirishgan bo'lsa faqat menejer.

    Args:
        progress: `kitchen_status.get_order_progress()` natijasi. Berilmasa
            o'zi so'raydi — chaqiruvchi uni allaqachon hisoblagan bo'lsa
            ikkinchi so'rov qilinmaydi.
    """
    empty = {
        "kot_count": 0,
        "served_count": 0,
        "pending_count": 0,
        "preparation_started": False,
        "label": "",
    }
    if not invoice or not frappe.db.exists("DocType", "URY KOT"):
        return empty

    kots = frappe.get_all(
        "URY KOT",
        filters={"invoice": invoice, "docstatus": ["<", 2]},
        fields=["name", "order_status", "start_time_prep", "start_time_serv", "type"],
    )
    if not kots:
        return empty

    from ozturkapp.ozturkapp.utils import kitchen_status as _kitchen

    served = sum(1 for k in kots if (k.order_status or "") == "Served")

    # DIQQAT: `start_time_prep` bu yerda ISHLATILMAYDI.
    # U `URY KOT` DocType'ida `default = "Now"` — ya'ni KOT YARATILGANDA
    # to'ladi, oshpaz ishni boshlaganda emas. Bazadagi har bir KOT'da u
    # `creation` ga teng, shuning uchun unga tayangan tekshiruv "ish har
    # doim boshlangan" deb javob berardi. Yagona ishonchli manba —
    # mahsulot darajasidagi `custom_kitchen_status`.
    if progress is None:
        progress = _kitchen.get_order_progress(invoice)
    started = bool(progress.get("started"))

    pending = len(kots) - served

    if served == len(kots):
        label = _("Berildi")
    elif started:
        label = _("Tayyorlanmoqda")
    else:
        label = _("Oshxonada kutilmoqda")

    return {
        "kot_count": len(kots),
        "served_count": served,
        "pending_count": pending,
        "preparation_started": bool(started),
        "label": label,
    }


# ═══════════════════════════════════════════════════════════════════
#  Chek tarkibini yig'ish
# ═══════════════════════════════════════════════════════════════════

def build_bill(invoice, scope=None, include_kitchen: bool = True) -> dict:
    """Kassa oynasi ko'rsatadigan chek tuzilmasi.

    Args:
        invoice: `POS Invoice` hujjati yoki uning nomi.
        scope: `cashier_permissions.resolve_scope()` natijasi (valyuta uchun).
        include_kitchen: KOT holatini ham qo'shishmi.

    Barcha summalar ERPNext hisoblagan maydonlardan OLINADI, qayta
    hisoblanmaydi.
    """
    doc = invoice if hasattr(invoice, "doctype") else frappe.get_doc("POS Invoice", invoice)

    restaurant = doc.get("restaurant") or (scope or {}).get("restaurant")
    service_config = get_service_charge_config(restaurant) if restaurant else {}
    service_account = service_config.get("account")

    taxes, service_charge = [], None
    for row in doc.get("taxes") or []:
        is_service = bool(service_account and row.account_head == service_account)
        entry = {
            "description": row.description or row.account_head,
            "rate": flt(row.rate),
            "amount": flt(row.tax_amount),
            "charge_type": row.charge_type,
            "is_service_charge": is_service,
        }
        taxes.append(entry)
        if is_service and service_charge is None:
            service_charge = entry

    # Oshxona holati — mahsulot darajasida (TZ §25, §26).
    # Kassa uni FAQAT KO'RSATADI; kelajakdagi Ofitsant ilovasi esa shu
    # maydonga qarab "Bekor qilish" tugmasini o'chiradi.
    from ozturkapp.ozturkapp.utils import kitchen_status as _kitchen

    item_kitchen = _kitchen.get_item_statuses_for_invoice(doc.name) if doc.name else {}

    items = [
        {
            "name": row.name,
            "idx": row.idx,
            "item_code": row.item_code,
            "item_name": row.item_name,
            "qty": flt(row.qty),
            "uom": row.uom,
            "rate": flt(row.rate),
            "amount": flt(row.amount),
            "course": row.get("custom_course"),
            "comment": row.get("comment"),
            "kitchen": item_kitchen.get(row.item_code),
        }
        for row in doc.get("items") or []
    ]

    discount = flt(doc.get("discount_amount"))

    bill = {
        "invoice": doc.name,
        "docstatus": cint(doc.docstatus),
        "paid": cint(doc.docstatus) == 1,
        "billed": bool(cint(doc.get("invoice_printed"))),
        "cancelled": bool(cint(doc.get("custom_cancelled"))),
        # Bekor qilinganda stol bog'lami UZILADI (`utils/order_cancel.py`) —
        # aks holda URY o'sha stolga yangi zakaz olishga yo'l qo'ymasdi.
        # Ko'rsatish uchun eslab qolingan nomdan foydalanamiz.
        "table": doc.get("restaurant_table") or doc.get("custom_cancelled_table"),
        "merged_tables": doc.get("custom_merged_tables"),
        "room": doc.get("custom_restaurant_room"),
        "order_type": doc.get("order_type"),
        "customer": doc.customer,
        "customer_name": doc.get("customer_name") or doc.customer,
        "mobile_number": doc.get("mobile_number"),
        "waiter": doc.get("waiter"),
        "waiter_name": _user_label(doc.get("waiter")),
        "cashier": doc.get("cashier"),
        "cashier_name": _user_label(doc.get("cashier")),
        "pax": cint(doc.get("no_of_pax")),
        "comments": doc.get("custom_comments"),
        "order_number": doc.get("custom_ury_order_number") or doc.get("custom_ticket_number"),
        "opened_at": str(doc.creation or ""),
        "modified": str(doc.modified or ""),
        "items": items,
        "item_count": len(items),
        "total_qty": sum(item["qty"] for item in items),
        # ── Summalar: hammasi ERPNext'dan ──────────────────────────
        "subtotal": flt(doc.net_total),
        "total": flt(doc.total),
        "discount": discount,
        "taxes": taxes,
        "service_charge": service_charge,
        "service_charge_rate": flt(service_config.get("rate")),
        "total_taxes": flt(doc.total_taxes_and_charges),
        "grand_total": flt(doc.grand_total),
        "rounded_total": flt(doc.rounded_total) or flt(doc.grand_total),
        "paid_amount": flt(doc.get("paid_amount")),
        "currency": doc.currency or (scope or {}).get("currency"),
    }

    if include_kitchen:
        progress = _kitchen.get_order_progress(doc.name)
        bill["kitchen"] = get_kitchen_state(doc.name, progress)

        # Kassa oynasi «Buyurtmani bekor qilish» tugmasini SHU javobga
        # qarab chizadi. Server bir xil javobni `assert_can_cancel()` da
        # qayta qo'llaydi — tugmani chetlab o'tish hech narsa bermaydi.
        from ozturkapp.ozturkapp.utils import order_cancel

        bill["cancellation"] = order_cancel.describe(doc, progress)

    return bill


def _user_label(user: str) -> str:
    """Foydalanuvchi e-pochtasi o'rniga to'liq ism (TZ §19 — texnik atama yo'q)."""
    if not user:
        return ""
    return frappe.db.get_value("User", user, "full_name") or user


# ═══════════════════════════════════════════════════════════════════
#  To'lov usullari
# ═══════════════════════════════════════════════════════════════════

def get_payment_methods(pos_profile: str) -> list:
    """POS Profile'da sozlangan to'lov usullari (TZ §10 — dublikat yo'q).

    SO'ROV ICHIDA KESHLANADI
    ========================
    Smenani yopish oqimida bu funksiya UCH marta chaqiriladi
    (`_cash_modes()` orqali), har biri o'z so'rovlari bilan. Kesh faqat
    joriy so'rov umriga — POS Profile tahrirlangan zahoti keyingi
    so'rovda yangisi o'qiladi.
    """
    cache = getattr(frappe.local, "_ozturk_payment_methods", None)
    if cache is None:
        cache = frappe.local._ozturk_payment_methods = {}
    if pos_profile in cache:
        return cache[pos_profile]

    rows = frappe.get_all(
        "POS Payment Method",
        filters={"parent": pos_profile, "parenttype": "POS Profile"},
        fields=["mode_of_payment", "default", "allow_in_returns", "idx"],
        order_by="idx asc",
    )

    # Usul turlarini BITTA so'rovda olamiz. Ilgari har bir qator uchun
    # alohida `Mode of Payment` so'rovi ketardi.
    types = {
        row.name: row.type
        for row in frappe.get_all(
            "Mode of Payment",
            filters={"name": ["in", [r.mode_of_payment for r in rows]]},
            fields=["name", "type"],
        )
    } if rows else {}

    methods = [
        {
            "mode_of_payment": row.mode_of_payment,
            "default": bool(cint(row.default)),
            "type": types.get(row.mode_of_payment),
        }
        for row in rows
    ]
    cache[pos_profile] = methods
    return methods
