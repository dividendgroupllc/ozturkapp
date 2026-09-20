# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa oynasi — kassadan buyurtma qabul qilish va mijoz biriktirish.

IKKINCHI BUYURTMA TIZIMI YARATILMAYDI
=====================================
Kassir buyurtmasi ofitsant buyurtmasi bilan BIR XIL yo'ldan o'tadi: URY'ning
`sync_order()` (ozturkapp o'rami orqali). U POS Invoice, KOT, stol bandligini
yaratadi; oshxona va bar chiptalari ham shundan chiqadi. Desktop POS ham
olib ketish/yetkazib berishni AYNAN shunday yaratadi (`order_type` bilan,
stolsiz) — biz uning isbotlangan oqimini takrorlaymiz.

    dine-in        stol majburiy                 order_type URY'ning o'zi beradi
    Take Away      stolsiz
    Delivery       stolsiz + telefon va manzil   (`custom_delivery_*`)

CHEK EGASI
==========
Smenadagi KASSIR chek egasi (`owner`/`cashier`), sessiya foydalanuvchisi esa
`waiter` maydonida — ofitsant buyurtmasidagi qoidaning o'zi (ERPNext smena
hisobotini `POS Invoice.owner` bo'yicha yig'adi). Mavjud buyurtmaga taom
qo'shganda yoki olib tashlaganda buyurtmani olgan ofitsant `waiter` da qoladi.

HAR BIR YOZUV AMALI
===================
    rol -> ko'lam -> funksiya bayrog'i -> smena ochiqmi -> chek qatori qulfi
    -> yozish -> realtime

Funksiya bayroqlari (`setup/cashier_features.py`): taom qo'shish/olib tashlash
va buyurtma ochish `cashier_orders`, mijoz `customer_attach`.
"""

import json
import uuid

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint

from ozturkapp.ozturkapp.setup import cashier_features
from ozturkapp.ozturkapp.setup.cashier_orders_setup import (
    DELIVERY_ADDRESS_FIELD,
    DELIVERY_PHONE_FIELD,
)
from ozturkapp.ozturkapp.utils import (
    cashier_billing,
    cashier_permissions,
    order_cancel,
    order_items,
    order_transfer,
    table_status,
)
from ozturkapp.ozturkapp.utils.cashier_realtime import emit_floor_change, emit_order_change

DINE_IN = "Dine In"
TAKE_AWAY = "Take Away"
DELIVERY = "Delivery"

#: Kassadan ochiladigan buyurtma turlari. Aggregators/Phone In bu yerda YO'Q.
ORDER_TYPES = (DINE_IN, TAKE_AWAY, DELIVERY)


# ═══════════════════════════════════════════════════════════════════
#  Umumiy ruxsat zanjiri
# ═══════════════════════════════════════════════════════════════════

def _begin(feature: str, shift: bool = True):
    """Rol -> ko'lam -> funksiya bayrog'i (-> smena). `scope` qaytaradi."""
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_features.assert_enabled(scope.pos_profile, feature)
    if shift:
        cashier_permissions.assert_shift_open(scope)
    return scope


def _lock_invoice(invoice: str, scope):
    """Chekni qulflaydi va holatini QULFDAN KEYIN qayta o'qiydi (TZ §24).

    Faqat to'lanmagan, bekor qilinmagan chek ustida ishlashga ruxsat.
    """
    cashier_permissions.assert_invoice_in_scope(invoice, scope, docstatus=0)

    # Qulflovchi o'qish: REPEATABLE READ da `FOR UPDATE` dan keyingi oddiy
    # `SELECT` eski suratni qaytaradi (`order_transfer._lock_order` izohi).
    doc = frappe.get_doc("POS Invoice", invoice, for_update=True)
    if doc.docstatus != 0:
        frappe.throw(
            _("Bu buyurtma allaqachon to'langan yoki yopilgan."),
            title=_("Chek holati mos emas"),
        )
    if cint(doc.get("custom_cancelled")):
        frappe.throw(_("Bekor qilingan buyurtmani o'zgartirib bo'lmaydi."))
    return doc


def _assert_not_billed(doc):
    if cint(doc.get("invoice_printed")):
        frappe.throw(
            _("Hisob allaqachon chiqarilgan — buyurtmani o'zgartirib bo'lmaydi."),
            title=_("Buyurtma yopilgan"),
        )


def _order_payload(invoice: str, scope) -> dict:
    """Kassir ko'radigan chek + qayta yuborish uchun optimistik qulf.

    `last_modified_time` — keyingi `add_items()` da AYNAN shu qiymat qaytariladi
    (URY'ning optimistik qulfi: ikki kassir/ofitsant bir buyurtmani bir
    vaqtda o'zgartira olmaydi).
    """
    doc = frappe.get_doc("POS Invoice", invoice)
    bill = cashier_billing.build_bill(doc, scope)

    bill["can_edit"] = (
        cint(doc.docstatus) == 0
        and not cint(doc.get("invoice_printed"))
        and not cint(doc.get("custom_cancelled"))
    )
    bill["last_modified_time"] = str(doc.modified)
    return bill


def _emit(scope, doc, reason: str):
    emit_order_change(scope.branch, doc.name, reason, doc.get("restaurant_table"))
    tables = table_status.tables_of(doc)
    if tables:
        emit_floor_change(scope.branch, tables, reason, doc.name)


# ═══════════════════════════════════════════════════════════════════
#  Menyu
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_menu(order_type=None, room=None):
    """Kassir uchun menyu — ofitsant menyusi bilan BIR XIL shakl.

    Ikkalasi ham `utils/order_items.build_menu()` dan oladi. `order_type`
    (`Take Away`/`Delivery`) buyurtma turiga alohida menyu yoqilgan
    restoranlarda kerak; `room` — zal bo'yicha menyu uchun.
    """
    scope = _begin("cashier_orders", shift=False)

    if order_type and order_type not in ORDER_TYPES:
        frappe.throw(_("Noma'lum buyurtma turi: {0}").format(order_type))

    return order_items.build_menu(scope, room=room, order_type=order_type)


# ═══════════════════════════════════════════════════════════════════
#  Buyurtma ochish
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def create_order(
    order_type,
    items,
    table=None,
    customer=None,
    pax=None,
    comments=None,
    delivery=None,
    client_ref=None,
):
    """Kassadan yangi buyurtma: stol bo'yicha, olib ketish yoki yetkazib berish.

    Args:
        order_type: `Dine In` (stol majburiy), `Take Away` yoki `Delivery`
            (ikkalasi ham stolsiz).
        items: `[{"item": "BURGER", "qty": 2, "comment": "Piyozsiz"}]`.
            Narx QABUL QILINMAYDI — u `Item Price` dan olinadi.
        customer: ixtiyoriy. Standart mijozdan boshqasi uchun `customer_attach`
            funksiyasi yoqilgan bo'lishi kerak.
        delivery: `{"phone": ..., "address": ...}` — `Delivery` uchun ikkalasi
            ham MAJBURIY; boshqa turlarda saqlanmaydi.
        client_ref: mijoz bergan UUID. Tarmoq uzilib qayta yuborilsa dublikat
            chek yaratilmaydi — avval yaratilgan buyurtma qaytariladi.

    Returns:
        dict: `build_bill()` (`delivery`, `order_type`, `bill_requested`, ...)
        + `can_edit`, `last_modified_time`.
    """
    scope = _begin("cashier_orders")
    order_type = _clean_order_type(order_type)
    client_ref = order_items.clean_client_ref(client_ref)
    pax = order_items.parse_pax(pax)

    # ── Oflayn qayta yuborish ─────────────────────────────────────────
    # Hech narsa o'zgartirilmaydi — avval yaratilgan buyurtma qaytariladi.
    already = order_items.find_by_client_ref(client_ref)
    if already:
        cashier_permissions.assert_invoice_in_scope(already, scope)
        frappe.logger("ozturk_cashier").info(
            "create_order: takroriy so'rov (client_ref=%s) -> %s", client_ref, already
        )
        return _order_payload(already, scope)

    items = order_items.parse_items(items)
    if not items:
        frappe.throw(_("Kamida bitta taom tanlanishi kerak"))
    order_items.assert_on_menu(items, scope)
    comments = order_items.clean_text(comments, label=_("Buyurtma izohi")) or None

    customer = _resolve_customer(customer, scope)
    delivery_info = _clean_delivery(delivery) if order_type == DELIVERY else None

    if order_type == DINE_IN:
        if not table:
            frappe.throw(_("Stol tanlanishi kerak"), title=_("Stol yo'q"))
        table_row = cashier_permissions.assert_table_in_scope(table, scope)

        # Qulf: ikki kassir (yoki ofitsant) bir bo'sh stolga bir vaqtda
        # buyurtma ochib yubormasin (TZ §24).
        frappe.db.sql("select name from `tabURY Table` where name = %s for update", table)

        busy = order_items.active_invoice_for_table(table, scope)
        if busy:
            frappe.throw(
                _("{0} stolida ochiq buyurtma bor ({1}) — unga taom qo'shing.").format(
                    table, busy
                ),
                title=_("Stol band"),
            )
        order_transfer.claim_table(table, scope.branch)
        order_transfer.release_stale_cluster(table, scope.branch)
        room, sync_type = table_row.restaurant_room, None
    else:
        if table:
            frappe.throw(
                _("Olib ketish va yetkazib berish buyurtmasi stolga bog'lanmaydi."),
                title=_("Stol ko'rsatilmasin"),
            )
        table, room, sync_type = None, None, order_type

    invoice = order_items.run_sync_order(
        scope,
        items=items,
        customer=customer,
        pax=pax,
        comments=comments,
        table=table,
        room=room,
        existing=None,
        last_modified_time=None,
        client_ref=client_ref or str(uuid.uuid4()),
        order_type=sync_type,
    )

    if delivery_info:
        _store_delivery(invoice, delivery_info)

    doc = frappe.get_doc("POS Invoice", invoice)
    _emit(scope, doc, "ORDER_CREATED")
    return _order_payload(invoice, scope)


def _store_delivery(invoice: str, delivery_info: dict):
    """Yetkazib berish telefoni va manzilini chekka yozadi.

    `update_modified=False`: `sync_order` qaytargan `modified` o'zgarmasin —
    mijoz uni keyingi `add_items()` da optimistik qulf sifatida qaytaradi.
    """
    frappe.db.set_value(
        "POS Invoice",
        invoice,
        {
            DELIVERY_PHONE_FIELD: delivery_info["phone"],
            DELIVERY_ADDRESS_FIELD: delivery_info["address"],
        },
        update_modified=False,
    )


def _clean_order_type(order_type) -> str:
    order_type = (order_type or "").strip()
    if order_type not in ORDER_TYPES:
        frappe.throw(
            _("Buyurtma turi noto'g'ri: '{0}'. Ruxsat etilgan: {1}").format(
                order_type, ", ".join(ORDER_TYPES)
            ),
            title=_("Buyurtma turi noto'g'ri"),
        )
    return order_type


def _clean_delivery(delivery) -> dict:
    """Yetkazib berish: telefon va manzil ikkalasi ham MAJBURIY."""
    if isinstance(delivery, str):
        try:
            delivery = json.loads(delivery)
        except ValueError:
            frappe.throw(_("Yetkazib berish ma'lumoti noto'g'ri formatda"))

    delivery = delivery if isinstance(delivery, dict) else {}
    phone = (str(delivery.get("phone") or "")).strip()
    address = order_items.clean_text(
        delivery.get("address"), order_items.ADDRESS_LENGTH, _("Yetkazib berish manzili")
    )

    if not phone or not address:
        frappe.throw(
            _("Yetkazib berish uchun telefon raqami va manzil kiritilishi shart."),
            title=_("Yetkazib berish ma'lumoti yo'q"),
        )

    return {"phone": order_items.clean_phone(phone), "address": address}


def _resolve_customer(customer, scope) -> str:
    """Buyurtma mijozi: berilmasa — POS Profile'dagi standart mijoz.

    Standartdan BOSHQA mijozni biriktirish `customer_attach` funksiyasiga
    tegishli — u o'chiq bo'lsa kassir buyurtma ochish bilan uni chetlab
    o'ta olmaydi.
    """
    if customer and not isinstance(customer, str):
        frappe.throw(_("Mijoz noto'g'ri ko'rsatilgan"), title=_("Noto'g'ri so'rov"))

    if customer and customer != scope.default_customer:
        cashier_features.assert_enabled(scope.pos_profile, "customer_attach")
        order_items.assert_customer_usable(customer)
        return customer

    if not scope.default_customer:
        frappe.throw(_("Mijoz ko'rsatilmagan va POS Profile'da standart mijoz yo'q"))
    return scope.default_customer


# ═══════════════════════════════════════════════════════════════════
#  Mavjud buyurtmaga taom qo'shish / olib tashlash
# ═══════════════════════════════════════════════════════════════════
#
# URY POS ro'yxatning TO'LIQ YANGI NUSXASINI yuboradi va `sync_order`
# eski/yangisini solishtirib KOT yaratadi (`ury_kot_generate.kot_execute`).
# Bu solishtirish bir taom chekda BIR NECHA qatorda bo'lsa adashadi: yangi
# taom oshxonaga umuman ketmaydi yoki noto'g'ri miqdor bekor bo'ladi. Shu
# sababli o'zgartirilayotgan taomning barcha qatorlari BITTAGA jamlanadi
# (`_collapse`); tegilmagan taomlar o'z qatorlarida qoladi.

def _lines(doc) -> list:
    return [
        {
            "item": row.item_code,
            "item_name": row.item_name,
            "qty": row.qty,
            "comment": row.get("comment") or "",
        }
        for row in doc.items
    ]


def _collapse(lines: list, item: str) -> list:
    """`item` ning barcha qatorlarini birinchisi o'rnida BITTA qatorga jamlaydi."""
    merged, first = [], None
    for line in lines:
        if line["item"] != item:
            merged.append(line)
        elif first is None:
            first = dict(line)
            merged.append(first)
        else:
            first["qty"] += line["qty"]
    return merged


@frappe.whitelist()
def add_items(invoice, items, last_modified_time):
    """Hisobi hali chiqarilmagan buyurtmaga taom qo'shadi.

    Args:
        items: qo'shiladigan taomlar (faqat YANGILARI, butun ro'yxat emas).
        last_modified_time: buyurtma yuklanganda olingan `last_modified_time`
            — optimistik qulf. Boshqa kimdir buyurtmani o'zgartirgan bo'lsa
            xato beradi va ekranni yangilash kerak bo'ladi.

    Yangi taomlar oshxona/barga YANGI KOT bo'lib boradi (`sync_order` oqimi).
    """
    scope = _begin("cashier_orders")
    doc = _lock_invoice(invoice, scope)
    _assert_not_billed(doc)

    new_items = order_items.parse_items(items)
    if not new_items:
        frappe.throw(_("Kamida bitta taom tanlanishi kerak"))
    order_items.assert_on_menu(new_items, scope)

    if not last_modified_time:
        frappe.throw(
            _("Buyurtmani yangilash uchun uning joriy holati kerak. Ekranni yangilang."),
            title=_("Eskirgan ma'lumot"),
        )

    lines = _lines(doc)
    for new in new_items:
        lines = _collapse(lines, new["item"])
        existing = next((line for line in lines if line["item"] == new["item"]), None)
        if existing:
            existing["qty"] += new["qty"]
            existing["comment"] = new["comment"] or existing["comment"]
        else:
            lines.append(new)

    _push(doc, scope, lines, last_modified_time)
    _emit(scope, doc, "ITEMS_ADDED")
    return _order_payload(invoice, scope)


@frappe.whitelist()
def remove_item(invoice, item_row, qty=None, reason=None):
    """Buyurtmadan taomni olib tashlaydi yoki miqdorini kamaytiradi.

    IKKI SHART BIRGA
    ================
    1. POS Profile'dagi URY bayrog'i «Allow Cashier To Edit And Remove Table
       Order Items» (`remove_items`) yoqilgan bo'lishi kerak.
    2. Oshxona qoidasi (`order_items.assert_removals_allowed`, ofitsant
       bilan bir manba): faqat hali BOSHLANMAGAN (Kutilmoqda) porsiya
       olib tashlanadi. Oshxona boshlab yuborgan porsiyani FAQAT menejer,
       SABAB bilan olib tashlay oladi — bu `order_cancel` qoidasining o'zi:
       oshpazga «TO'XTATING» kartasi boradi.

    Oshxonada olib tashlangan taomning chiptasi QOLMAYDI: URY bekor-KOT
    yaratadi va `order_cancel.apply_item_cancellation` navbatdagi qatorlarni
    yopadi; URY solishtirishi adashsa, `close_surplus_kitchen_rows` ortiqcha
    navbatdagi porsiyalarni baribir yopadi.

    Args:
        item_row: `POS Invoice Item` qatorining `name` i (`build_bill()` dagi
            `items[].name`).
        qty: NECHTASINI olib tashlash (qatordagi miqdordan oshmasin).
            Berilmasa — qator butunlay.
        reason: sabab. Oshxona boshlagan porsiyani olib tashlashda majburiy.
    """
    scope = _begin("cashier_orders")

    if not cint(frappe.db.get_value("POS Profile", scope.pos_profile, "remove_items")):
        raise cashier_permissions.CashierPermissionError(
            _(
                "Kassirga buyurtmadan taom olib tashlashga ruxsat berilmagan. "
                "POS Profile'da «Allow Cashier To Edit And Remove Table Order "
                "Items» yoqilishi kerak."
            )
        )

    doc = _lock_invoice(invoice, scope)
    _assert_not_billed(doc)

    line = next((row for row in doc.items if row.name == item_row), None)
    if not line:
        frappe.throw(_("Buyurtmada bunday qator yo'q"), frappe.DoesNotExistError)

    removing = order_items.parse_qty(qty) if qty not in (None, "") else int(line.qty)
    if removing <= 0 or removing > line.qty:
        frappe.throw(
            _("Olib tashlanadigan miqdor 1 dan {0} gacha bo'lishi kerak").format(int(line.qty)),
            title=_("Miqdor noto'g'ri"),
        )

    lines = _collapse(_lines(doc), line.item_code)
    kept = next(entry for entry in lines if entry["item"] == line.item_code)
    kept["qty"] -= removing
    lines = [entry for entry in lines if entry["qty"] > 0]

    if not lines:
        frappe.throw(
            _("Buyurtmada kamida bitta taom qolishi kerak — butun buyurtmani bekor qiling."),
            title=_("Oxirgi taom"),
        )

    forced = _kitchen_allows_or_forced(invoice, lines, reason)

    _push(doc, scope, lines, str(doc.modified))
    order_cancel.close_surplus_kitchen_rows(invoice, line.item_code)

    _audit_removal(doc, line, removing, reason, forced)
    _emit(scope, doc, "ITEM_REMOVED")
    return _order_payload(invoice, scope)


def _kitchen_allows_or_forced(invoice: str, lines: list, reason) -> bool:
    """Oshxona qoidasini qo'llaydi. Menejer majburan olib tashlasa `True`.

    Oshpaz boshlagan porsiyani oddiy kassir olib tashlay olmaydi (ofitsantdagi
    xato matni bilan). Menejer olib tashlay oladi, lekin sabab yozishi shart.
    """
    try:
        order_items.assert_removals_allowed(invoice, lines)
    except frappe.ValidationError:
        if not cashier_permissions.has_supervisor_role():
            raise

        frappe.clear_last_message()
        if len((reason or "").strip()) < order_cancel.MIN_REASON_LENGTH:
            frappe.throw(
                _(
                    "Oshxona ishni boshlab yuborgan taomni olib tashlash sababi "
                    "yozilishi shart — u hisobotga tushadi."
                ),
                title=_("Sabab ko'rsatilmagan"),
            )
        return True
    return False


def _audit_removal(doc, line, removing: int, reason, forced: bool):
    """Kim, nimani, nega olib tashlagani — chekning izohlar tarixida."""
    text = _("{0} — {1} ta olib tashlandi ({2})").format(
        line.item_name or line.item_code,
        removing,
        (reason or "").strip() or _("sabab ko'rsatilmagan"),
    )
    if forced:
        text += " — " + _("oshxona boshlagan porsiya, menejer qarori")
    doc.add_comment("Comment", text)

    frappe.logger("ozturk_cashier").info(
        "Taom olib tashlandi: %s | %s x%s | kassir=%s | menejer_qarori=%s | sabab=%s",
        doc.name, line.item_code, removing, frappe.session.user, forced, reason,
    )


def _push(doc, scope, lines: list, last_modified_time: str):
    """To'liq yangi ro'yxatni URY `sync_order` ga yuboradi.

    Buyurtmani olgan ofitsant, mehmonlar soni, umumiy izoh va buyurtma
    turi O'ZGARMAYDI. Buyurtma turi stolsiz cheklarda narx ro'yxatini
    (buyurtma turiga alohida menyu) tanlaydi — uni yo'qotsak narxlar
    boshqa menyudan olinardi.
    """
    order_items.run_sync_order(
        scope,
        items=lines,
        customer=doc.customer,
        pax=None,
        comments=doc.get("custom_comments"),
        table=doc.restaurant_table,
        room=doc.get("custom_restaurant_room"),
        existing=doc.name,
        last_modified_time=last_modified_time,
        order_type=doc.order_type,
        waiter=doc.get("waiter"),
    )


# ═══════════════════════════════════════════════════════════════════
#  Mijoz (feature: customer_attach)
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def search_customers(query=None, limit=20):
    """Mijozni nomi yoki telefoni bo'yicha qidirish."""
    _begin("customer_attach", shift=False)
    return order_items.search_customers(query, limit, include_phone=True)


@frappe.whitelist()
@rate_limit(limit=60, seconds=3600)
def create_customer(customer_name, mobile_number=None, address=None):
    """Yangi mijoz. Telefon va manzil ixtiyoriy.

    `Customer` `ignore_permissions` bilan yaratiladi, shuning uchun soatiga
    60 tadan ortig'i rad etiladi (bazani to'ldirib yuborishdan himoya).
    """
    _begin("customer_attach", shift=False)
    return order_items.create_customer(customer_name, mobile_number, address)


@frappe.whitelist()
def set_customer(invoice, customer):
    """To'lanmagan chekka mijozni biriktiradi.

    Hisob allaqachon chiqarilgan bo'lsa ham mumkin — lekin mijoz chekda
    ko'rinadi, shuning uchun chek `custom_reprint_needed = 1` deb belgilanadi
    (hisob bilan bir xil qoida; maydonni hisob moduli yaratadi, bo'lmasa
    belgilanmaydi) va kassir chekni QAYTA chiqarishi kerak.
    """
    scope = _begin("customer_attach")
    order_items.assert_customer_usable(customer)
    doc = _lock_invoice(invoice, scope)

    if doc.customer != customer:
        doc.customer = customer
        doc.mobile_number = frappe.db.get_value("Customer", customer, "mobile_number")
        if cint(doc.get("invoice_printed")) and frappe.db.has_column(
            "POS Invoice", "custom_reprint_needed"
        ):
            doc.custom_reprint_needed = 1
        doc.save()

        frappe.logger("ozturk_cashier").info(
            "Mijoz biriktirildi: %s | %s | kassir=%s", invoice, customer, frappe.session.user
        )
        _emit(scope, doc, "CUSTOMER_SET")

    return _order_payload(invoice, scope)
