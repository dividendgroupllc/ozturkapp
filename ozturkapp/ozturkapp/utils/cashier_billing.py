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

from contextlib import contextmanager
from decimal import ROUND_HALF_UP, Decimal

import frappe
from frappe import _
from frappe.utils import cint, flt

#: Xizmat haqi uchun standart hisob va shablon nomlari (setup ishlatadi).
SERVICE_CHARGE_ACCOUNT_NAME = "Service Charge"
SERVICE_CHARGE_TEMPLATE_TITLE = "Restaurant Service Charge"
DEFAULT_SERVICE_CHARGE_RATE = 12.0

#: Xizmat haqi OLINMAYDIGAN buyurtma turlari — ularga ofitsant xizmat qilmaydi.
SERVICE_CHARGE_FREE_ORDER_TYPES = ("Take Away", "Delivery")

#: Choychaqa (tip) soliq qatori: hisob nomi (setup yaratadi) va qator tavsifi.
TIPS_ACCOUNT_NAME = "Tips Payable"
TIPS_DESCRIPTION = "Choychaqa"

#: Bitta chekni teng bo'lib to'lashda mehmonlar soni chegarasi (xato kiritishdan himoya).
MAX_SPLIT_PARTS = 50


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


def remove_service_charge_for_takeaway(doc, method=None):
    """POS Invoice `before_validate`: olib ketish va yetkazib berishda xizmat haqi olinmaydi.

    Xizmat haqi (12%) ofitsant xizmati uchun olinadi. `Take Away` va
    `Delivery` buyurtmalariga ofitsant xizmat qilmaydi. Lekin URY shablonni
    HAR QANDAY buyurtma turiga qo'yadi (`get_order_invoice`), shuning uchun
    xizmat haqi bu buyurtmalarga ham tushib qolardi.

    `before_validate` — ERPNext jami summani hisoblashdan OLDIN, shu sabab
    qayta hisoblash kerak emas va to'lov tekshiruvlari yakuniy summa bilan ishlaydi.
    Faqat xizmat haqi qatori olib tashlanadi; shablondagi boshqa soliqlarga tegilmaydi.
    """
    if doc.get("order_type") not in SERVICE_CHARGE_FREE_ORDER_TYPES:
        return

    restaurant = doc.get("restaurant") or frappe.db.get_value(
        "URY Restaurant", {"branch": doc.get("branch")}, "name"
    )
    config = get_service_charge_config(restaurant) if restaurant else {}
    if not config.get("enabled"):
        return

    # Yangi chekda ERPNext shablon qatorlarini `validate` ichida o'zi qo'shadi
    # (`set_taxes`). Qatorlarni hozir o'zimiz qo'shib olamiz — aks holda
    # olib tashlaganimizdan keyin ular qaytib keladi.
    if doc.is_new() and not doc.get("taxes") and doc.get("taxes_and_charges"):
        doc.append_taxes_from_master()

    rows = doc.get("taxes") or []
    kept = [row for row in rows if row.account_head != config["account"]]
    if len(kept) == len(rows):
        return

    doc.set("taxes", kept)
    if not kept:
        # Shablonda boshqa qator yo'q: shablon nomini ham olib tashlaymiz,
        # aks holda bo'sh jadval `set_taxes` uchun "to'ldirilmagan" bo'lib ko'rinadi.
        doc.taxes_and_charges = None


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

    company = doc.get("company") or (scope or {}).get("company")
    tip_account = tips_account(company)

    taxes, service_charge, tip = [], None, 0.0
    for row in doc.get("taxes") or []:
        is_service = bool(service_account and row.account_head == service_account)
        is_tip = is_tip_row(row, tip_account)
        entry = {
            "description": row.description or row.account_head,
            "rate": flt(row.rate),
            "amount": flt(row.tax_amount),
            "charge_type": row.charge_type,
            "is_service_charge": is_service,
            "is_tip": is_tip,
        }
        taxes.append(entry)
        if is_service and service_charge is None:
            service_charge = entry
        if is_tip:
            tip += entry["amount"]

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
        "change_amount": flt(doc.get("change_amount")),
        "payments": [
            {
                "mode_of_payment": row.mode_of_payment,
                "amount": flt(row.amount),
            }
            for row in doc.get("payments") or []
        ],
        "currency": doc.currency or (scope or {}).get("currency"),
    }
    bill.update(_extended_keys(doc, bill, tip))

    if include_kitchen:
        progress = _kitchen.get_order_progress(doc.name)
        bill["kitchen"] = get_kitchen_state(doc.name, progress)

        # Kassa oynasi «Buyurtmani bekor qilish» tugmasini SHU javobga
        # qarab chizadi. Server bir xil javobni `assert_can_cancel()` da
        # qayta qo'llaydi — tugmani chetlab o'tish hech narsa bermaydi.
        from ozturkapp.ozturkapp.utils import order_cancel

        bill["cancellation"] = order_cancel.describe(doc, progress)

    return bill


def _extended_keys(doc, bill: dict, tip: float) -> dict:
    """Chegirma, choychaqa, yetkazib berish va qaytarish maydonlari.

    Faqat QO'SHIMCHA kalitlar — mavjud iste'molchilar (escpos, tarix, testlar)
    o'zgarmaydi. Custom maydonlar `doc.get()` bilan o'qiladi: ularni boshqa
    agent/setup yaratadi va sayt ular yaratilguncha ham ishlashi kerak.
    """
    discount = flt(bill["discount"])
    percent = flt(doc.get("additional_discount_percentage"))
    if not percent and discount and flt(doc.get("total")):
        # Foiz o'rniga summa bilan qo'yilgan chegirma (masalan URY Desktop POS).
        percent = discount / flt(doc.get("total")) * 100

    approved_by = doc.get("custom_discount_approved_by") or ""
    billed = bill["billed"]

    phone = (doc.get("custom_delivery_phone") or "").strip()
    address = (doc.get("custom_delivery_address") or "").strip()

    requested_at = doc.get("custom_bill_requested_at")

    return {
        "tip": flt(tip),
        "discount_percent": flt(percent, 3),
        "discount_reason": doc.get("custom_discount_reason") or "",
        "discount_approved_by": approved_by,
        "discount_approved_by_name": _user_label(approved_by),
        # To'lanadigan summa (choychaqa bilan) — kassir shuni yig'adi.
        "payable": flt(doc.get("rounded_total")) or flt(doc.get("grand_total")),
        # Ofitsant hisob so'ragan va kassir hali chekni chiqarmagan.
        "bill_requested": bool(cint(doc.get("custom_bill_requested"))) and not billed,
        "bill_requested_at": str(requested_at) if requested_at else None,
        "delivery": {"phone": phone, "address": address} if (phone or address) else None,
        "is_return": bool(cint(doc.get("is_return"))),
        "return_against": doc.get("return_against") or None,
        # Chek chop etilgandan keyin chegirma o'zgargan: qog'ozdagisi eskirgan.
        "reprint_needed": billed and bool(cint(doc.get("custom_reprint_needed"))),
    }


def _user_label(user: str) -> str:
    """Foydalanuvchi e-pochtasi o'rniga to'liq ism (TZ §19 — texnik atama yo'q)."""
    if not user:
        return ""
    return frappe.db.get_value("User", user, "full_name") or user


def clear_reprint_needed(invoice: str):
    """Chek qayta chop etilgach «eskirgan chek» belgisini o'chiradi.

    Chop etish navbatiga bill topshirig'i qo'yilganda chaqiriladi
    (`open_bill` ham shu yerda tozalaydi). Maydon hali yaratilmagan
    saytda hech narsa qilmaydi.
    """
    if frappe.db.has_column("POS Invoice", "custom_reprint_needed"):
        frappe.db.set_value(
            "POS Invoice", invoice, "custom_reprint_needed", 0, update_modified=False
        )


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
            "allow_in_returns": bool(cint(row.allow_in_returns)),
        }
        for row in rows
    ]
    cache[pos_profile] = methods
    return methods


def cash_modes(pos_profile: str) -> list:
    """Naqd pul usullari: `Mode of Payment.type == "Cash"` (nom bo'yicha EMAS).

    Yagona ta'rif: smena ochish/yopish (`api/cashier._cash_modes`), qaytim
    va g'aladon qoidalari shundan foydalanadi. Usul nomi hech qachon qattiq
    yozilmaydi — bu saytda yagona usul «Нахт» (turi Cash).
    """
    return [
        method["mode_of_payment"]
        for method in get_payment_methods(pos_profile)
        if (method.get("type") or "").strip().lower() == "cash"
    ]


# ═══════════════════════════════════════════════════════════════════
#  Choychaqa (tip) — ERPNext soliq qatori sifatida
# ═══════════════════════════════════════════════════════════════════
#
# Choychaqa "Actual" turidagi soliq qatori: summa qat'iy, xizmat haqi va
# QQS'ga KIRMAYDI (ular "On Net Total" — qator summasi ularga ta'sir qilmaydi)
# va "On Previous Row" bazasi ham bo'lmaydi, chunki eng oxirgi qator.
# Shu sababli grand_total/rounded_total avtomatik choychaqani o'z ichiga oladi
# va smena yopilishidagi solishtiruv (to'lovlar yig'indisi = cheklar yig'indisi)
# hech qanday o'zgarishsiz to'g'ri chiqadi. Hisob-kitob ERPNext'niki.

def tips_account(company: str):
    """Kompaniyaning choychaqa hisobi (setup yaratgan), bo'lmasa `None`."""
    if not company:
        return None

    abbr = frappe.db.get_value("Company", company, "abbr")
    name = f"{TIPS_ACCOUNT_NAME} - {abbr}"
    return name if frappe.db.exists("Account", name) else None


def is_tip_row(row, account: str = None) -> bool:
    """Soliq qatori choychaqami (`Actual` + choychaqa hisobi)."""
    return bool(account) and row.account_head == account and row.charge_type == "Actual"


def get_tip(doc) -> float:
    """Chekdagi choychaqa summasi."""
    account = tips_account(doc.get("company"))
    return sum(flt(row.tax_amount) for row in doc.get("taxes") or [] if is_tip_row(row, account))


def set_tip(doc, amount: float):
    """Choychaqa qatorini IDEMPOTENT o'rnatadi (hujjatni saqlamaydi).

    Avval mavjud choychaqa qatori(lar)i olib tashlanadi, `amount > 0` bo'lsa
    yangisi ENG OXIRGA qo'shiladi. Nol — choychaqani olib tashlash. Chaqiruvchi
    keyin `calculate_taxes_and_totals` yoki `save()` bilan jami qayta hisoblaydi.
    """
    account = tips_account(doc.get("company"))
    if not account:
        if amount <= 0:
            return
        frappe.throw(
            _(
                "Choychaqa hisobi sozlanmagan. Administrator «bench migrate» yoki "
                "cashier_billing_setup.setup ni ishga tushirishi kerak."
            ),
            title=_("Choychaqa sozlanmagan"),
        )

    doc.set(
        "taxes",
        [row for row in doc.get("taxes") or [] if not is_tip_row(row, account)],
    )
    if amount > 0:
        doc.append(
            "taxes",
            {
                "charge_type": "Actual",
                "account_head": account,
                "description": TIPS_DESCRIPTION,
                "tax_amount": amount,
            },
        )


# ═══════════════════════════════════════════════════════════════════
#  Teng bo'lib to'lash (aralash to'lov yordamchisi)
# ═══════════════════════════════════════════════════════════════════

def money_step(currency: str, precision: int) -> Decimal:
    """Eng kichik bo'linadigan pul birligi: valyutaning tanga qiymati yoki precision."""
    fraction = flt(frappe.get_cached_value("Currency", currency, "smallest_currency_fraction_value"))
    return Decimal(str(fraction)) if fraction > 0 else Decimal(1).scaleb(-precision)


def get_even_split(doc, parts: int) -> dict:
    """To'lanadigan summani `parts` ga TENG bo'ladi — yig'indi ANIQ teng.

    Qoldiq (tiyin) OXIRGI ulushga qo'shiladi, ya'ni ulushlar yig'indisi
    har doim to'lanadigan summaga tengligi kafolatlanadi. Hisob `Decimal`
    bilan — float xatosi yo'q. Hujjatga TEGILMAYDI (sof hisob).
    """
    parts = cint(parts)
    if parts < 1 or parts > MAX_SPLIT_PARTS:
        frappe.throw(
            _("Mehmonlar soni 1 dan {0} gacha bo'lishi kerak").format(MAX_SPLIT_PARTS),
            title=_("Noto'g'ri son"),
        )

    payable = flt(doc.get("rounded_total")) or flt(doc.get("grand_total"))
    if payable <= 0:
        frappe.throw(_("Bo'linadigan summa yo'q"), title=_("Chek bo'sh"))

    precision = cint(doc.precision("rounded_total"))
    step = money_step(doc.get("currency"), precision)

    units = int((Decimal(str(payable)) / step).to_integral_value(rounding=ROUND_HALF_UP))
    base = units // parts
    shares = [base] * (parts - 1) + [units - base * (parts - 1)]
    amounts = [flt(Decimal(share) * step, precision) for share in shares]

    return {
        "invoice": doc.name,
        "payable": payable,
        "parts": parts,
        "amounts": amounts,
        "currency": doc.get("currency"),
    }


# ═══════════════════════════════════════════════════════════════════
#  Hujjat darajasidagi qo'riqchi (generic REST va URY metodlariga qarshi)
# ═══════════════════════════════════════════════════════════════════
#
# NEGA KERAK
# ==========
# URY `URY Cashier` roliga POS Invoice'da write/create/submit/cancel beradi.
# Ya'ni kassir bizning API'ni ATAYLAB chetlab o'tib, `frappe.client.set_value`,
# `frappe.client.insert`, `frappe.client.cancel` yoki URY'ning o'z
# `make_invoice(additionalDiscount=...)` metodini chaqirsa: menejer tasdig'isiz
# chegirma, PIN'siz qaytarish (pul kassadan chiqadi), to'langan chekni
# bekor qilish (pul cho'ntakka, chek yo'q) mumkin bo'lardi. API'dagi tekshiruv
# faqat API orqali kelganni ushlaydi.
#
# NIMA QILINADI (faqat oddiy kassirga — menejer/Administrator Desk'da ishlashda davom etadi)
# ============================================================================
#   1. `is_return` chek — faqat `refunds.refund()` (menejer tasdig'i bilan) yaratadi;
#   2. to'langan chekni bekor qilish — yo'q (to'g'rilash uchun qaytarish bor);
#   3. `custom_discount_approved_by/reason` — faqat chegirma API'si yozadi;
#   4. chegirmaning OSHISHI — `discount` funksiyasi yoqilmagan kassada yo'q.
#
# NEGA CHEGIRMA FAQAT «O'CHIQ» HOLATDA CHEKLANADI
# ===============================================
# Chegirma ikki yo'l bilan yoqiladi: bizning veb-kassa bayrog'i
# (`custom_enable_cashier_discount`) yoki URY'ning O'Z `custom_enable_discount`
# maydoni — u Desktop POS chegirmasini boshqaradi va `additionalDiscount`
# parametri bilan tasdiqsiz chegirma beradi. Ikkalasi ham alohida maydon.
# Yoqilgan kassada chegirma chegarasini hujjat darajasida majburlash Desktop
# POS'ni buzardi (unda PIN oynasi yo'q) — bu qoldiq xavf va u hisobotda
# ko'rsatilgan.
#
# MARKER
# ======
# Bizning API kodi (`trusted_billing()`) `frappe.flags` da marker qo'yadi. Bu
# so'rov xotirasi — klient uni parametr bilan qo'ya olmaydi (`frappe.call` faqat
# funksiya imzosidagi argumentlarni o'tkazadi).

TRUSTED_FLAG = "ozturk_billing_trusted"

#: URY `split_bill` shu bayroqni qo'yadi (`ury_pos_invoice.validate_invoice`) —
#: bo'lingan chek eski chegirmani meros qiladi, bu yangi chegirma emas.
URY_SPLIT_FLAG = "ury_bill_split"

#: Chegirma "oshdi" deb hisoblash uchun eng kichik farq (foizda va valyutada).
_DISCOUNT_EPSILON_PERCENT = 1e-9
_DISCOUNT_EPSILON_AMOUNT = 0.005

AUDIT_FIELDS = ("custom_discount_approved_by", "custom_discount_reason")


@contextmanager
def trusted_billing():
    """Bizning API kodi (chegirma, qaytarish) hujjatni qo'riqchisiz saqlaydigan qismi.

    Faqat serverda, tasdiq/funksiya tekshiruvlaridan KEYIN ishlatiladi. Chiqishda
    (xatoda ham) marker albatta olib tashlanadi.
    """
    previous = frappe.flags.get(TRUSTED_FLAG)
    frappe.flags[TRUSTED_FLAG] = True
    try:
        yield
    finally:
        if previous:
            frappe.flags[TRUSTED_FLAG] = previous
        else:
            frappe.flags.pop(TRUSTED_FLAG, None)


def guard_applies() -> bool:
    """Qo'riqchi faqat ODDIY kassirga (menejer roli yo'q) va ishonchsiz yo'lda qo'llanadi."""
    if frappe.flags.get(TRUSTED_FLAG) or frappe.flags.get(URY_SPLIT_FLAG):
        return False

    from ozturkapp.ozturkapp.utils import cashier_permissions

    user = frappe.session.user
    if user == "Administrator":
        return False
    roles = set(frappe.get_roles(user))
    return "URY Cashier" in roles and not roles & set(cashier_permissions.SUPERVISOR_ROLES)


def _forbid(message: str, title: str):
    frappe.throw(message, exc=frappe.PermissionError, title=title)


def guard_invoice_changes(doc, method=None):
    """POS Invoice `validate`: qaytarish, tasdiq izi va chegirma bo'yicha qoidalar."""
    if not guard_applies():
        return

    if cint(doc.get("is_return")):
        _forbid(
            _(
                "Qaytarish chekini qo'lda rasmiylashtirib bo'lmaydi. To'langan chekni "
                "«Qaytarish» tugmasi orqali, menejer tasdig'i bilan qaytaring."
            ),
            _("Menejer tasdig'i kerak"),
        )

    before = doc.get_doc_before_save()
    _guard_audit_fields(doc, before)
    _guard_discount(doc, before)


def guard_invoice_audit(doc, method=None):
    """POS Invoice `before_update_after_submit`: to'langan chekning tasdiq izi o'zgarmaydi."""
    if guard_applies():
        _guard_audit_fields(doc, doc.get_doc_before_save())


def guard_invoice_cancel(doc, method=None):
    """POS Invoice `before_cancel`: to'langan sotuvni kassir bekor qila olmaydi."""
    if guard_applies():
        _forbid(
            _(
                "To'langan chekni kassir bekor qila olmaydi. Xato bo'lsa chekni menejer "
                "tasdig'i bilan qaytaring."
            ),
            _("Menejer tasdig'i kerak"),
        )


def _guard_audit_fields(doc, before):
    for fieldname in AUDIT_FIELDS:
        now = doc.get(fieldname) or None
        was = (before.get(fieldname) if before else None) or None
        if now != was:
            _forbid(
                _("Chegirma tasdig'i va sababi faqat chegirma oynasi orqali yoziladi."),
                _("Tasdiq izini o'zgartirib bo'lmaydi"),
            )


def _discount_allowed(pos_profile: str) -> bool:
    """Chegirma ikki yo'ldan biri bilan yoqilgan bo'lishi mumkin.

    * bizning `discount` bayrog'i (veb-kassa, `custom_enable_cashier_discount`);
    * URY'ning o'z `custom_enable_discount` maydoni — Desktop POS chegirmasi.
      Uni ham hisobga olmasak qo'riqchi Desktop POS'ni buzardi.
    """
    from ozturkapp.ozturkapp.setup import cashier_features

    if cashier_features.is_enabled(pos_profile, "discount"):
        return True

    if not pos_profile or not frappe.db.has_column("POS Profile", "custom_enable_discount"):
        return False
    return bool(cint(frappe.db.get_value("POS Profile", pos_profile, "custom_enable_discount")))


def _guard_discount(doc, before):
    percent_now = flt(doc.get("additional_discount_percentage"))
    amount_now = flt(doc.get("discount_amount"))
    percent_was = flt(before.get("additional_discount_percentage")) if before else 0.0
    amount_was = flt(before.get("discount_amount")) if before else 0.0

    # Foiz kiritilgan bo'lsa summa undan HISOBLANADI (mahsulot qo'shilsa o'zgaradi) —
    # shuning uchun foiz kirish, summa faqat foizsiz chegirmada kirish hisoblanadi.
    amount_grew = amount_now > amount_was + _DISCOUNT_EPSILON_AMOUNT
    raised = amount_grew and (
        percent_now > percent_was + _DISCOUNT_EPSILON_PERCENT or not percent_now
    )
    if raised and not _discount_allowed(doc.get("pos_profile")):
        _forbid(
            _("Chegirma bu kassa uchun yoqilmagan. Chegirmani menejer qo'yadi."),
            _("Funksiya o'chiq"),
        )


# ═══════════════════════════════════════════════════════════════════
#  Naqd to'lovdan keyin g'aladon
# ═══════════════════════════════════════════════════════════════════

def kick_drawer_after_payment(invoice: str, user: str):
    """Fon ishchisida g'aladonni ochadi (to'lov COMMIT bo'lgandan KEYIN).

    `submit_payment` buni `enqueue_after_commit=True` bilan navbatga qo'yadi:
    to'lov tranzaksiyasi tugagach ishlaydi, shuning uchun printer/g'aladon
    nosozligi to'lovni bekor qila olmaydi ham, sekinlashtira olmaydi ham.
    `kick_drawer` o'zi hech qachon xato bermaydi, lekin u boshqa agent
    tomonidan yoziladi — shuning uchun har qanday xato yutiladi va logga
    yoziladi (to'lov allaqachon qabul qilingan).
    """
    try:
        from ozturkapp.ozturkapp.utils import cashier_permissions, print_queue

        scope = cashier_permissions.resolve_scope(user)
        print_queue.kick_drawer(scope, reason="payment", invoice=invoice)
    except Exception:
        frappe.logger("ozturk_print").warning(
            "kick_drawer_after_payment: %s uchun g'aladon ochilmadi", invoice, exc_info=True
        )
