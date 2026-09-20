# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Ofitsant ilovasi va kassa uchun UMUMIY buyurtma yordamchilari.

NEGA ALOHIDA MODUL
==================
Kassadan buyurtma qabul qilish (`api/cashier_orders.py`) ofitsant ilovasi
(`api/waiter.py`) bilan bir xil qoidalarga tayanadi: mahsulot ro'yxatini
tozalash (narx QABUL QILINMAYDI), oshxona boshlagan taomni olib tashlashni
taqiqlash, chek egasi (smenadagi kassir), menyu, mijoz qidirish. Ularni ikki
joyda yozsak, biri o'zgarganda ikkinchisi ortda qolardi — shuning uchun
ikkalasi shu yerdan oladi.

IKKINCHI BUYURTMA TIZIMI YARATILMAYDI
=====================================
`run_sync_order()` URY'ning MAVJUD `sync_order()` funksiyasini (ozturkapp
o'rami orqali) chaqiradi: POS Invoice, KOT, stol bandligi, oshxona/bar
chiptalari — hammasi ofitsant buyurtmasidagi kabi ishlaydi.
"""

import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.utils import cint, flt

from ozturkapp.ozturkapp.utils import cashier_billing, kitchen_status, table_status

#: Bitta buyurtmadagi eng ko'p qator va bir qatordagi eng ko'p miqdor.
#: Chegarasiz qiymat oshxona chiptasini va chekni yaroqsiz qiladi.
MAX_LINES = 100
MAX_QTY = 999

#: `POS Invoice.custom_comments`, `POS Invoice Item.comment`, `Customer.customer_name`,
#: `custom_client_ref` — Data maydonlari (140). Frappe uzunroq matnni inglizcha
#: xato bilan rad etadi, biz esa tushunarli xabar beramiz.
TEXT_LENGTH = 140
ADDRESS_LENGTH = 300

#: Telefon raqamidagi raqamlar soni (xalqaro chegara: E.164 — 15).
PHONE_DIGITS = (5, 15)

#: Mijoz qidiruvi bir so'rovda qaytaradigan eng ko'p qator.
MAX_SEARCH_ROWS = 100


# ═══════════════════════════════════════════════════════════════════
#  Kirish tozalash (ofitsant ilovasi, kassa va Desktop'dan kelgan matn)
# ═══════════════════════════════════════════════════════════════════

def clean_text(value, limit: int = TEXT_LENGTH, label: str = "") -> str:
    """Bir qatorli matnni tozalaydi va uzunligini tekshiradi.

    NEGA KERAK
    ==========
    Izoh, manzil va mijoz nomi chekka, oshxona chiptasiga va mijoz hisobiga
    BOSILADI. `utils/escpos.encode_text` matnni baytga o'zgartiradi va
    boshqaruv belgilarini o'tkazib yuboradi: izohga yozilgan `ESC p 0 25 250`
    printerga "g'aladonni och" buyrug'i bo'lib boradi, `GS V` — qog'ozni
    kesadi. Ofitsant roli shunday izoh yubora oladi, shuning uchun boshqaruv
    (`Cc`), chiziq/abzats ajratgichlari (`Zl`/`Zp`) va ko'rinmas format
    belgilari (`Cf`, masalan matn yo'nalishini almashtiruvchi RLO) kirishdayoq
    olib tashlanadi.
    """
    text = "" if value is None else str(value)
    kept = []
    for char in text:
        category = unicodedata.category(char)
        if category == "Cf":
            continue
        kept.append(" " if category in ("Cc", "Zl", "Zp") else char)
    text = " ".join("".join(kept).split())

    if len(text) > limit:
        frappe.throw(
            _("{0} juda uzun: {1} belgidan oshmasligi kerak (hozir {2}).").format(
                label or _("Matn"), limit, len(text)
            ),
            title=_("Matn uzun"),
        )
    return text


def parse_qty(value) -> int:
    """Miqdor: butun son (`0` — savatdan olib tashlangan qator), aks holda XATO.

    ILGARI `cint()` edi: `"abc"` va `NaN` jimgina 0 bo'lib taom buyurtmadan
    tushib qolardi, `2.5` esa 2 ga qisqarardi — ofitsant nima bo'lganini
    bilmasdi.
    """
    if value is None or value == "":
        return 0

    try:
        if isinstance(value, bool):
            raise InvalidOperation
        number = Decimal(str(value).strip())
        if not number.is_finite() or number != number.to_integral_value():
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        frappe.throw(_("Miqdor butun son bo'lishi kerak: {0}").format(frappe.as_unicode(value)[:20]),
                     title=_("Miqdor noto'g'ri"))

    qty = int(number)
    if qty < 0 or qty > MAX_QTY:
        frappe.throw(
            _("Miqdor 0 dan {0} gacha bo'lishi kerak: {1}").format(MAX_QTY, qty),
            title=_("Miqdor noto'g'ri"),
        )
    return qty


#: Bitta buyurtmadagi eng ko'p mehmon.
MAX_GUESTS = 200


def parse_pax(pax):
    """Mehmonlar soni: bo'sh (`None`) yoki 1..200. Manfiy/ulkan/matn — xato.

    `cint()` manfiy (-3) va ulkan (10**9) qiymatni chekka jimgina yozardi.
    """
    count = parse_qty(pax)
    if count > MAX_GUESTS:
        frappe.throw(
            _("Mehmonlar soni {0} tadan oshmasligi kerak").format(MAX_GUESTS),
            title=_("Mehmonlar soni noto'g'ri"),
        )
    return count or None


def clean_client_ref(client_ref):
    """Mijoz bergan takroriy-so'rov kaliti: bo'sh yoki 140 belgigacha."""
    client_ref = (client_ref or "").strip() if isinstance(client_ref, str) else client_ref
    if not client_ref:
        return None
    if not isinstance(client_ref, str) or len(client_ref) > TEXT_LENGTH:
        frappe.throw(_("So'rov kaliti (client_ref) noto'g'ri"), title=_("So'rov kaliti noto'g'ri"))
    return client_ref


def clean_phone(phone, required: bool = False):
    """Telefon: 5–15 ta raqam bo'lishi kerak ("-----" yoki "+" telefon emas)."""
    phone = (phone or "").strip() if isinstance(phone, str) or phone is None else str(phone).strip()
    if not phone:
        if required:
            frappe.throw(_("Telefon raqami kiritilishi shart"), title=_("Telefon yo'q"))
        return None

    digits = len(re.sub(r"\D", "", phone))
    if not frappe.utils.validate_phone_number(phone) or not PHONE_DIGITS[0] <= digits <= PHONE_DIGITS[1]:
        frappe.throw(_("Telefon raqami noto'g'ri formatda"), title=_("Telefon noto'g'ri"))
    return phone


# ═══════════════════════════════════════════════════════════════════
#  Mahsulot ro'yxati
# ═══════════════════════════════════════════════════════════════════

def parse_items(items) -> list:
    """Mijozdan kelgan mahsulot ro'yxatini tozalaydi.

    Narx QABUL QILINMAYDI — u serverda `Item Price` dan olinadi (TZ §12).
    Miqdor va izoh `parse_qty()` / `clean_text()` orqali tekshiriladi.
    """
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except ValueError:
            frappe.throw(_("Mahsulot ro'yxati noto'g'ri formatda"))

    if not isinstance(items, list):
        frappe.throw(_("Mahsulot ro'yxati noto'g'ri formatda"))

    if len(items) > MAX_LINES:
        frappe.throw(
            _("Bitta buyurtmada {0} tadan ortiq qator bo'lishi mumkin emas").format(MAX_LINES),
            title=_("Juda ko'p qator"),
        )

    cleaned = []
    for row in items:
        if row is None:
            continue
        if not isinstance(row, dict):
            frappe.throw(_("Mahsulot qatori noto'g'ri formatda"))

        code = row.get("item") or row.get("item_code")
        qty = parse_qty(row.get("qty"))

        if not code or qty <= 0:
            continue
        if not frappe.db.exists("Item", code):
            frappe.throw(_("Mahsulot topilmadi: {0}").format(code))

        cleaned.append(
            {
                "item": code,
                "item_name": frappe.db.get_value("Item", code, "item_name"),
                "qty": qty,
                "comment": clean_text(row.get("comment"), TEXT_LENGTH, _("Taom izohi")),
            }
        )
    return cleaned


def _active_menus(restaurant: str) -> list:
    """Restoranda buyurtma qabul qilinadigan menyular — `getRestaurantMenu()` bilan
    BIR XIL tanlov: faol menyu, yoqilgan bo'lsa zal va buyurtma turi bo'yicha menyular.

    `URY Menu.enabled` ATAYLAB tekshirilmaydi: URY uni umuman o'qimaydi, ya'ni
    u yerda "o'chiq" menyu ham ishlayveradi — biz esa ishlab turgan restoranni
    to'xtatib qo'ymasligimiz kerak.
    """
    row = frappe.db.get_value(
        "URY Restaurant",
        restaurant,
        ["active_menu", "room_wise_menu", "order_type_wise_menu"],
        as_dict=True,
    )
    if not row:
        return []

    menus = {row.active_menu}
    if cint(row.room_wise_menu):
        menus.update(frappe.get_all("Menu for Room", filters={"parent": restaurant}, pluck="menu"))
    if cint(row.order_type_wise_menu):
        menus.update(frappe.get_all("Order Type Menu", filters={"parent": restaurant}, pluck="menu"))
    return sorted(menu for menu in menus if menu)


def assert_on_menu(items: list, scope, existing: str = None):
    """Faqat restoran menyusidagi (va menyuda o'chirilmagan) taomlar buyurtma qilinadi.

    NEGA KERAK
    ==========
    Menyuda o'chirilgan («stop-list») taom ilova keshida qolgan bo'lishi
    mumkin. URY `sync_order` narx ro'yxatida narxi bor har qanday taomni
    qabul qiladi — oshxonaga tugagan taom buyurtmasi ketardi.

    `existing` (chek nomi) berilsa faqat chekdagidan KO'PROQ so'ralgan
    taomlar tekshiriladi: ofitsant ilovasi butun ro'yxatni qayta yuboradi va
    menyudan keyin o'chirilgan, lekin allaqachon buyurtma qilingan taom
    yangi buyurtmani to'sib qo'ymasligi kerak.
    """
    wanted = {}
    for row in items:
        wanted[row["item"]] = wanted.get(row["item"], 0) + cint(row["qty"])

    if existing:
        for row in frappe.get_all(
            "POS Invoice Item", filters={"parent": existing}, fields=["item_code", "qty"]
        ):
            if row.item_code in wanted:
                wanted[row.item_code] -= flt(row.qty)

    to_check = [item for item, qty in wanted.items() if qty > 0]
    if not to_check:
        return

    menus = _active_menus(scope.restaurant)
    on_menu = set(
        frappe.get_all(
            "URY Menu Item",
            filters={"parent": ["in", menus], "disabled": 0, "item": ["in", to_check]},
            pluck="item",
        )
        if menus
        else []
    )
    missing = [item for item in to_check if item not in on_menu]
    if missing:
        names = ", ".join(
            frappe.db.get_value("Item", item, "item_name") or item for item in missing
        )
        frappe.throw(
            _("Menyuda yo'q yoki vaqtincha o'chirilgan: {0}").format(names),
            title=_("Taom menyuda yo'q"),
        )


def assert_removals_allowed(invoice: str, incoming: list):
    """TZ §8 — tayyorlash boshlangan taomni kamaytirish/olib tashlash TAQIQ.

    Oshxona holati YAGONA haqiqat manbai (TZ §8/#4).
    """
    current = {}
    for row in frappe.get_all(
        "POS Invoice Item",
        filters={"parent": invoice},
        fields=["item_code", "qty"],
    ):
        current[row.item_code] = current.get(row.item_code, 0) + flt(row.qty)

    wanted = {}
    for row in incoming:
        wanted[row["item"]] = wanted.get(row["item"], 0) + flt(row["qty"])

    kitchen = kitchen_status.get_item_statuses_for_invoice(invoice)

    for item_code, old_qty in current.items():
        new_qty = wanted.get(item_code, 0)
        removing = old_qty - new_qty
        if removing <= 0:
            continue  # qo'shish yoki o'zgarishsiz — ruxsat

        state = kitchen.get(item_code)
        if not state:
            continue  # KOT yo'q — oshxona bu taomni umuman ko'rmagan

        # NECHTASI hali boshlanmaganiga qaraymiz, umumiy holatga EMAS.
        # Bitta taom ikki raundda buyurtma qilingan bo'lishi mumkin:
        # 1 dona pishmoqda, 1 dona navbatda. Umumiy holat «Kutilmoqda»
        # bo'lib ko'rinadi, lekin olib tashlash faqat NAVBATDAGISIGA
        # tegishli (`kitchen_status.get_item_statuses_for_invoice`).
        pending = flt(state.get("pending_qty") or 0)
        if removing <= pending:
            continue

        item_name = frappe.db.get_value("Item", item_code, "item_name") or item_code

        if pending <= 0:
            frappe.throw(
                _(
                    "'{0}' allaqachon oshxonada ({1}) — uni olib tashlash yoki "
                    "kamaytirish mumkin emas."
                ).format(item_name, state["label"]),
                title=_("Bekor qilib bo'lmaydi"),
            )

        frappe.throw(
            _(
                "'{0}' — {1} donasi allaqachon oshxonada. Ko'pi bilan {2} "
                "donasini olib tashlash mumkin."
            ).format(item_name, int(old_qty - pending), int(pending)),
            title=_("Bekor qilib bo'lmaydi"),
        )


# ═══════════════════════════════════════════════════════════════════
#  Chek egasi, to'lov usuli, stol
# ═══════════════════════════════════════════════════════════════════

def current_shift_user(scope):
    """Ochiq smenani kim ochgan (kassir). Smena bo'lmasa `None`."""
    from ozturkapp.ozturkapp.api.desktop_pos import _get_user_room, _open_opening_entry

    room = ""
    try:
        room = _get_user_room(scope.branch)
    except Exception:
        pass

    opening = _open_opening_entry(scope.branch, scope.pos_profile, room)
    return frappe.db.get_value("POS Opening Entry", opening, "user") if opening else None


def existing_pax(invoice) -> int:
    """Mavjud chekdagi mehmonlar soni (ofitsant uni o'zgartirmaydi)."""
    if not invoice:
        return 0
    return cint(frappe.db.get_value("POS Invoice", invoice, "no_of_pax"))


def default_mode_of_payment(pos_profile: str) -> str:
    """`sync_order` "dummy" to'lov qatori uchun usul — POS Profile'dagi standarti."""
    methods = cashier_billing.get_payment_methods(pos_profile)
    if not methods:
        frappe.throw(_("POS Profile'da to'lov usuli sozlanmagan"))
    for m in methods:
        if m["default"]:
            return m["mode_of_payment"]
    return methods[0]["mode_of_payment"]


def active_invoice_for_table(table: str, scope):
    """Stoldagi eng eski ochiq chek nomi (birlashtirilgan stollar bilan)."""
    orders = table_status.get_open_orders(scope.branch)
    mapping = table_status.map_orders_to_tables(orders)
    row = mapping.get(table)
    return row.name if row else None


def find_by_client_ref(client_ref):
    """`client_ref` bo'yicha avval yaratilgan chek nomi (oflayn qayta yuborish)."""
    if not client_ref:
        return None
    return frappe.db.get_value("POS Invoice", {"custom_client_ref": client_ref}, "name")


# ═══════════════════════════════════════════════════════════════════
#  Menyu
# ═══════════════════════════════════════════════════════════════════

def build_menu(scope, room=None, order_type=None) -> dict:
    """Menyu — URY'ning MAVJUD `getRestaurantMenu()` funksiyasidan.

    Ofitsant ilovasi ham, kassa ham AYNAN shu shakldagi javob oladi. Narx,
    rasm, kurs (course) — hammasi URY Menu'dan keladi; mijoz narxni O'ZI
    saqlamaydi va hisoblamaydi.

    Args:
        room: zal (zal bo'yicha alohida menyu yoqilgan bo'lsa).
        order_type: buyurtma turi (`Take Away`, `Delivery`). URY buni FAQAT
            hisob ochish huquqi bor foydalanuvchi (kassir) uchun hisobga
            oladi — ofitsantga ta'siri yo'q.
    """
    from ury.ury_pos.api import getRestaurantMenu

    menu = getRestaurantMenu(scope.pos_profile, room=room or None, order_type=order_type or None)

    courses = []
    seen = set()
    for item in menu.get("items", []):
        course = item.get("course")
        if course and course not in seen:
            seen.add(course)
            courses.append(course)

    return {
        "menu": menu.get("name"),
        "modified": str(menu.get("modified_time") or ""),
        "courses": courses,
        "items": [
            {
                "item": i["item"],
                "item_name": i["item_name"],
                "rate": flt(i["rate"]),
                "course": i.get("course"),
                "image": i.get("item_image"),
                "special": bool(cint(i.get("special_dish"))),
            }
            for i in menu.get("items", [])
            if not cint(i.get("disabled"))
        ],
        "currency": scope.currency,
    }


# ═══════════════════════════════════════════════════════════════════
#  Mijoz
# ═══════════════════════════════════════════════════════════════════

def search_customers(query=None, limit=20, include_phone=False) -> list:
    """Mijozlarni nomi (ixtiyoriy ravishda telefoni) bo'yicha qidiradi.

    `include_phone` FAQAT kassa uchun yoqiladi: yetkazib berishda mijozni
    telefon raqami bilan topish kerak. Ofitsant ilovasi avvalgidek faqat
    nom bo'yicha qidiradi.
    """
    filters, or_filters = {}, None
    query = clean_text(query, TEXT_LENGTH, _("Qidiruv"))
    if query:
        like = f"%{query}%"
        if include_phone:
            or_filters = {"customer_name": ["like", like], "mobile_number": ["like", like]}
        else:
            filters = {"customer_name": ["like", like]}

    return frappe.get_all(
        "Customer",
        filters=filters,
        or_filters=or_filters,
        fields=["name", "customer_name", "mobile_number"],
        limit_page_length=min(cint(limit) or 20, MAX_SEARCH_ROWS),
        order_by="modified desc",
    )


def assert_customer_usable(customer: str):
    """Chekka biriktirilishi mumkin bo'lgan mijozmi.

    Faqat `disabled` emas: muzlatilgan (`is_frozen`) mijoz va kompaniyalararo
    (`is_internal_customer`) mijoz ham rad etiladi — ikkinchisi POS chekni
    oddiy sotuv o'rniga kompaniyalararo sotuv sifatida buxgalteriyaga
    yozadi.
    """
    row = frappe.db.get_value(
        "Customer",
        customer,
        ["name", "disabled", "is_frozen", "is_internal_customer"],
        as_dict=True,
    )
    if not row:
        frappe.throw(_("Mijoz topilmadi: {0}").format(customer), frappe.DoesNotExistError)
    if cint(row.disabled) or cint(row.is_frozen):
        frappe.throw(_("Mijoz '{0}' o'chirilgan yoki muzlatilgan").format(customer))
    if cint(row.is_internal_customer):
        frappe.throw(
            _("'{0}' — ichki (kompaniyalararo) mijoz, uni kassadan biriktirib bo'lmaydi").format(
                customer
            )
        )


def create_customer(customer_name, mobile_number=None, address=None) -> dict:
    """Yangi mijoz — telefon va manzil IXTIYORIY.

    NEGA URY'NING `create_customer()` I ISHLATILMAYDI
    ================================================
    U telefonni majburiy qiladi, sukut bo'yicha `territory="India"` beradi
    (bu saytda bunday hudud yo'q) va o'rtasida `frappe.db.commit()` qiladi —
    ya'ni xato bo'lsa yarim yaratilgan mijoz qolib ketadi. Kassada yetkazib
    berish mijozi telefonsiz ham (faqat ism bilan) ochilishi kerak.

    Guruh va hudud ERPNext'ning "Selling Settings" dagi standartidan olinadi.

    `Customer` bo'yicha yozish huquqi kassir roliga berilmagan, shuning uchun
    hujjat `ignore_permissions` bilan yaratiladi — ruxsat allaqachon rol va
    `customer_attach` bayrog'i bilan tekshirilgan.
    """
    customer_name = clean_text(customer_name, TEXT_LENGTH, _("Mijoz ismi"))
    if not customer_name:
        frappe.throw(_("Mijoz ismini kiriting"))
    if re.search(r"[<>]", customer_name):
        frappe.throw(_("Mijoz ismida < yoki > belgisi bo'lishi mumkin emas"), title=_("Ism noto'g'ri"))

    mobile_number = clean_phone(mobile_number)
    address = clean_text(address, ADDRESS_LENGTH, _("Manzil")) or None

    # Tugma ikki marta bosilganda / so'rov qayta yuborilganda AYNI ism va telefon
    # bilan ikkinchi mijoz yaratilmaydi. Telefonsiz mijozlar dublikat hisoblanmaydi:
    # bir xil ismli ikki odam bo'lishi mumkin.
    if mobile_number:
        existing = frappe.db.get_value(
            "Customer",
            {"customer_name": customer_name, "mobile_number": mobile_number},
            ["name", "customer_name", "mobile_number", "customer_details"],
            as_dict=True,
        )
        if existing:
            return {
                "name": existing.name,
                "customer_name": existing.customer_name,
                "mobile_number": existing.mobile_number,
                "address": existing.customer_details or address,
            }

    customer = frappe.get_doc(
        {
            "doctype": "Customer",
            "customer_name": customer_name,
            "customer_type": "Individual",
            "customer_group": frappe.db.get_single_value("Selling Settings", "customer_group"),
            "territory": frappe.db.get_single_value("Selling Settings", "territory"),
            "mobile_number": mobile_number,
            # ERPNext'da manzil alohida `Address` hujjati; kassada esa u faqat
            # yetkazib berishda kerak va buyurtmaning o'zida saqlanadi.
            # Mijozda esa izoh sifatida eslab qolinadi.
            "customer_details": address,
        }
    ).insert(ignore_permissions=True)  # URY'ning `create_customer()` i ham shunday

    return {
        "name": customer.name,
        "customer_name": customer.customer_name,
        "mobile_number": mobile_number,
        "address": address,
    }


# ═══════════════════════════════════════════════════════════════════
#  URY `sync_order` — yagona yozish nuqtasi
# ═══════════════════════════════════════════════════════════════════

def run_sync_order(
    scope,
    *,
    items: list,
    customer: str,
    pax: int,
    comments,
    table,
    room,
    existing,
    last_modified_time,
    client_ref=None,
    order_type=None,
    waiter=None,
) -> str:
    """URY'ning `sync_order()` ini chaqiradi va chek nomini qaytaradi.

    CHEK EGASI — smenadagi KASSIR
    =============================
    ERPNext smena hisobotini `POS Invoice.owner` bo'yicha yig'adi
    (`pos_closing_entry.get_pos_invoices`: `where owner = %s`). Chek egasi
    sessiya foydalanuvchisi bo'lib qolsa, uning buyurtmasi kassirning
    Z-hisobotiga TUSHMAYDI. Shuning uchun `cashier`/`owner` — smenani
    ochgan kassir, sessiya foydalanuvchisi esa `waiter` maydonida qoladi
    (ofitsant buyurtmasidagi qoidaning aynan o'zi).

    `waiter` berilmasa — sessiya foydalanuvchisi. Mavjud buyurtmaga taom
    qo'shganda chaqiruvchi uni saqlab qolishi mumkin: buyurtmani olgan
    ofitsantning hisoboti kassir bosgan har bir tugmadan o'zgarmasligi kerak.

    `client_ref` FAQAT yangi buyurtmada beriladi: `sync_order` o'rami uni
    chekka yozadi (dublikatdan himoya). Mavjud chekni yangilashda berilmasa
    chekning eski `client_ref` i o'zgarmaydi.

    `sync_order` konfliktda `msgprint` + `{"status": "Failure"}` qaytaradi;
    bu yerda u tushunarli xatoga aylantiriladi.
    """
    shift_user = current_shift_user(scope) or frappe.session.user

    from ozturkapp.ozturkapp.overrides.ury_order import sync_order

    kwargs = {
        "items": items,
        "cashier": shift_user,
        "owner": shift_user,
        "mode_of_payment": default_mode_of_payment(scope.pos_profile),
        "customer": customer,
        "no_of_pax": cint(pax) or existing_pax(existing) or 1,
        "last_invoice": existing,
        "waiter": waiter or frappe.session.user,
        "pos_profile": scope.pos_profile,
        "last_modified_time": last_modified_time,
        "table": table,
        "invoice": existing,
        "comments": comments,
        "room": room,
        "client_ref": client_ref,
    }
    if order_type:
        kwargs["order_type"] = order_type

    result = sync_order(**kwargs)

    if isinstance(result, dict) and result.get("status") == "Failure":
        # URY konfliktda o'zining INGLIZCHA `msgprint` ini ham qo'shadi ("This order has been
        # modified. Please reload the page..."): u kassir xatosi yonida ikkinchi qator bo'lib
        # chiqib, sahifani qayta yuklashni aytadi, aslida oyna o'zi yangilanadi.
        frappe.clear_messages()
        frappe.throw(
            _("Buyurtma o'zgargan. Ekranni yangilab, qaytadan urinib ko'ring."),
            title=_("Konflikt"),
        )

    invoice_name = result.get("name") if isinstance(result, dict) else existing
    if not invoice_name:
        frappe.throw(_("Buyurtma yaratilmadi"))
    return invoice_name
