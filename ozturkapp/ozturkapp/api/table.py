# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa oynasi — zal rejasi va stol amallari (TZ §4, §5, §21, §23, §24).

Stol modeli YARATILMAYDI. `URY Table` yagona manba bo'lib qoladi, biz faqat
uning `layout_x/y/width/height`, `table_shape`, `merged_with` maydonlarini
o'qiymiz (TZ §29).

KONKURENSIYA (TZ §24)
=====================
Stolni band qilish — kritik amal. Ikki kassir bir vaqtda bitta stolni
ochmasligi uchun `SELECT ... FOR UPDATE` bilan qator qulflanadi. Faqat
frontend darajasidagi bloklash ishonchsiz.
"""

import re

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from ozturkapp.ozturkapp.setup import cashier_features
from ozturkapp.ozturkapp.utils import (
    cashier_billing,
    cashier_permissions,
    order_items,
    order_transfer,
    table_status,
)
from ozturkapp.ozturkapp.utils.cashier_realtime import emit_floor_change



# ═══════════════════════════════════════════════════════════════════
#  O'qish
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_floor_plan(room=None):
    """Zal rejasining to'liq holati.

    Sahifa ochilganda bir marta, keyin realtime signali kelganda chaqiriladi.
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()

    state = table_status.build_floor_state(scope.branch, room or None)
    state["room"] = room or None
    state["branch"] = scope.branch
    return state


@frappe.whitelist()
def get_table_status(table):
    """Bitta stolning joriy holati — kritik amaldan oldin qayta tekshirish uchun."""
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_permissions.assert_table_in_scope(table, scope)

    return _resolve_table_state(table, scope)


@frappe.whitelist()
def get_table_detail(table):
    """Stol tanlanganda o'ng paneldagi to'liq ma'lumot (TZ §21).

    OCCUPIED  -> buyurtma, mahsulotlar, oraliq summa, xizmat haqi, jami
    RESERVED  -> bron ma'lumoti
    AVAILABLE -> stol ma'lumoti va ochish imkoniyati
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    row = cashier_permissions.assert_table_in_scope(table, scope)

    state = _resolve_table_state(table, scope)

    detail = {
        "table": table,
        "status": state["status"],
        "cluster": state["cluster"],
        "is_merged": len(state["cluster"]) > 1,
        "room": row.restaurant_room,
        "seats": cint(frappe.db.get_value("URY Table", table, "no_of_seats")),
        "shape": frappe.db.get_value("URY Table", table, "table_shape"),
        "occupied_flag": cint(row.occupied),
        "bill": None,
        "reservation": state.get("reservation"),
        "other_orders": [],
        # Ochiq buyurtma bo'lmasa — bir xil shakldagi bo'sh qiymatlar.
        **{
            key: (state.get("order") or {}).get(key, empty)
            for key, empty in table_status.NO_ORDER_VISIBILITY.items()
        },
        # Ma'lumot nomuvofiqligi bo'lsa — kassir buni KO'RISHI kerak,
        # jim bo'sh hisob ko'rsatilmasligi kerak (TZ §8).
        "issue": None,
    }

    if state.get("order"):
        invoice_name = state["order"]["name"]
        try:
            invoice = frappe.get_doc("POS Invoice", invoice_name)
        except frappe.DoesNotExistError:
            # Poyga: zal rejasi yuklangandan keyin chek o'chirilgan.
            detail["issue"] = _order_issue(
                "ORDER_NOT_FOUND",
                _("Bu stolning buyurtmasi topilmadi. Ekranni yangilang."),
                table=table,
                invoice=invoice_name,
            )
            return detail

        detail["bill"] = cashier_billing.build_bill(invoice, scope)

        # Hisob bo'lingan bo'lsa — qolgan cheklar ham ko'rinsin (TZ §23).
        detail["other_orders"] = [
            {
                "invoice": order["name"],
                "amount": order["amount"],
                "billed": order["billed"],
            }
            for order in state.get("all_orders", [])
            if order["name"] != invoice_name
        ]

    elif cint(row.occupied):
        # `URY Table.occupied = 1`, lekin birorta ochiq chek yo'q — "orphan"
        # holat. Buyurtma AVTOMATIK YARATILMAYDI (TZ §8), faqat xabar beriladi.
        detail["issue"] = _order_issue(
            "STALE_OCCUPIED_FLAG",
            _(
                "Stol band deb belgilangan, lekin unga tegishli faol buyurtma "
                "topilmadi. Menejer stolni bo'shatishi kerak."
            ),
            table=table,
            cluster=state["cluster"],
        )

    return detail


def _order_issue(code: str, message: str, **context) -> dict:
    """Nomuvofiqlikni qaytaradi va uni Error Log'ga yozadi (TZ §8).

    Kassirga tushunarli matn, dasturchiga esa nosozlikni topish uchun
    yetarli kontekst kerak — shuning uchun ikkalasi ham.
    """
    frappe.log_error(
        title=f"Kassa: {code}",
        message=frappe.as_json(
            {
                "code": code,
                "user": frappe.session.user,
                "context": context,
            }
        ),
    )
    frappe.logger("ozturk_cashier").warning("%s | %s", code, context)

    return {"code": code, "message": message, "context": context}


def _resolve_table_state(table: str, scope) -> dict:
    """Bitta stol uchun holatni klaster va ochiq cheklar asosida hisoblash."""
    cluster_source = frappe.get_all(
        "URY Table", filters={"branch": scope.branch}, fields=["name", "merged_with"]
    )
    clusters = table_status.build_clusters([dict(row) for row in cluster_source])
    cluster = clusters.get(table, [table])

    orders = table_status.get_open_orders(scope.branch)
    order_map = table_status.map_orders_to_tables(orders)
    reservations = table_status.get_reservation_provider()(scope.branch, [table])

    status = table_status.derive_status(table, cluster, order_map, reservations)

    # Klasterdagi ISTALGAN a'zoning buyurtmasi shu stolga tegishli.
    primary = None
    for member in cluster:
        if order_map.get(member):
            primary = order_map[member]
            break

    all_orders = [
        table_status._thin_order(order)
        for order in orders
        if {order.restaurant_table}.union(
            table_status.parse_merged_with(order.custom_merged_tables)
        ).intersection(cluster)
    ]

    return {
        "table": table,
        "status": status,
        "cluster": cluster,
        "order": table_status._thin_order(primary) if primary else None,
        "all_orders": all_orders,
        "reservation": table_status._thin_reservation(reservations[table])
        if reservations.get(table)
        else None,
    }


# ═══════════════════════════════════════════════════════════════════
#  Yozish
# ═══════════════════════════════════════════════════════════════════

#: Zal rejasi koordinatasining chegarasi (piksel). `NaN`/`inf` ustunga yozilsa
#: butun zal rejasi buzilardi.
MAX_LAYOUT = 100000


def _layout_number(value) -> float:
    number = flt(value)
    if number != number or abs(number) > MAX_LAYOUT:
        frappe.throw(_("Stol koordinatasi noto'g'ri"), title=_("Joylashuv noto'g'ri"))
    return number


@frappe.whitelist()
def update_table_layout(table, x, y, width=None, height=None):
    """Zal rejasida stolni surib qo'yish (drag-and-drop) — joyini saqlaydi.

    Har qanday kassir o'zgartira oladi — bu sotuvga taalluqli emas, faqat
    zal rejasining ko'rinishi, shuning uchun menejer huquqi talab qilinmaydi.

    `width`/`height` ixtiyoriy: birinchi marta ko'chirilayotgan (hali
    hech qachon joylashtirilmagan) stol uchun ham yuboriladi — aks holda
    to'rttala `layout_*` maydon nolga teng qolib, stol yana avtomatik
    to'rga qaytib ketardi (`table_status.is_positioned`).
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_permissions.assert_table_in_scope(table, scope)

    values = {"layout_x": _layout_number(x), "layout_y": _layout_number(y)}
    if width is not None:
        values["layout_width"] = _layout_number(width) or table_status.DEFAULT_TABLE_WIDTH
    if height is not None:
        values["layout_height"] = _layout_number(height) or table_status.DEFAULT_TABLE_HEIGHT

    frappe.db.set_value("URY Table", table, values, update_modified=False)

    emit_floor_change(scope.branch, [table], "TABLE_LAYOUT_UPDATED")

    return {"table": table, **values}


# ═══════════════════════════════════════════════════════════════════
#  Bron — kassir bo'sh stolni bron qiladi / bronni yechadi
# ═══════════════════════════════════════════════════════════════════
#
# BIZNES QOIDASI
# ==============
# Kassir stolni QO'LDA BAND QILA OLMAYDI. Stol faqat BUYURTMA orqali band
# bo'ladi (ofitsant ilovasi yoki URY POS) va buyurtma yopilganda avtomatik
# bo'shaydi. Kassirning stolga ta'siri faqat bron bilan cheklangan.
#
# Shuning uchun ilgari mavjud bo'lgan `seat_table()` (qo'lda band qilish)
# OLIB TASHLANDI — u yuqoridagi qoidani buzardi.


#: Tugash vaqti ko'rsatilmagan bronning standart davomiyligi.
DEFAULT_RESERVATION_SECONDS = 2 * 3600


_TIME_PATTERN = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d{1,6})?)?$")

def _parse_time(value, label: str) -> str:
    """`9:00`, `09:00`, `9:00:00` -> `HH:MM:SS`; boshqasi tushunarli xato.

    NEGA O'ZIMIZ TEKSHIRAMIZ
    ========================
    Frappe'ning `get_time()` i `"abc"` va `"25:99"` da `dateutil` xatosini
    (500) tashlaydi — kassir "server xatosi" ko'rardi.
    """
    match = _TIME_PATTERN.match(str(value).strip()) if value is not None else None
    hour, minute, second = (int(part or 0) for part in match.groups()) if match else (99, 99, 99)
    if hour > 23 or minute > 59 or second > 59:
        frappe.throw(
            _("{0} noto'g'ri: «{1}». Format: 19:30").format(label, value),
            title=_("Vaqt noto'g'ri"),
        )
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def _seconds(value: str) -> int:
    hour, minute, second = (int(part) for part in value.split(":"))
    return hour * 3600 + minute * 60 + second


def _default_end(from_time: str):
    """Bron tugash vaqti berilmaganda: boshlanishdan 2 soat keyin (kun oxirigacha).

    NEGA KERAK
    ==========
    `URY Table Reservation.to_time` bo'sh qoldirilsa Frappe uni HOZIRGI vaqt
    bilan to'ldiradi. Boshlanish vaqti hozirdan keyin bo'lsa (masalan soat
    19:00 ga bron, hozir 14:00), `validate_times()` "tugash boshlanishdan
    keyin bo'lishi kerak" deb rad etardi — ya'ni bugungi kechki bronni
    yozib bo'lmasdi. Aniq davomiylik `validate_overlap()` uchun ham to'g'ri
    oraliq beradi.

    Bron BIR KUN ichida (yarim tundan o'tmaydi): `23:59:59` da boshlangan
    bronga tugash vaqti topilmaydi.
    """
    seconds = _seconds(from_time)
    end = min(seconds + DEFAULT_RESERVATION_SECONDS, 24 * 3600 - 1)
    if end <= seconds:
        frappe.throw(
            _("Bron kun oxirida boshlana olmaydi (23:59:59). Ertangi kun uchun bron qiling."),
            title=_("Vaqt noto'g'ri"),
        )
    return f"{end // 3600:02d}:{end % 3600 // 60:02d}:{end % 60:02d}"


def _guests(guests, pax, table) -> int:
    """Mehmonlar soni: `guests` ustun; bo'sh bo'lsa stol sig'imi.

    `cint()` manfiy (-5) va ulkan (10**9) qiymatni jimgina bronga yozardi.
    """
    value = guests if guests not in (None, "") else pax
    return order_items.parse_pax(value) or _default_pax(table)


def _default_pax(table) -> int:
    """Kassir mehmonlar sonini kiritmaydi — stol sig'imini olamiz.

    `pax` DocType darajasida majburiy, shuning uchun bo'sh qoldirib
    bo'lmaydi. Stol o'rinlari soni eng mantiqiy taxmin.
    """
    return cint(frappe.db.get_value("URY Table", table, "no_of_seats")) or 2


@frappe.whitelist()
def reserve_table(
    table,
    customer_name,
    phone=None,
    pax=None,
    from_time=None,
    to_time=None,
    reservation_date=None,
    notes=None,
    guests=None,
):
    """Stolni bron qiladi.

    Bron stolni BAND QILMAYDI — u faqat RESERVED holatini beradi. Mehmon
    kelib buyurtma bergandagina stol OCCUPIED bo'ladi.

    Args:
        guests: mehmonlar soni (`pax` bilan bir xil ma'noda — yangi mijozlar
            shu nomni yuboradi). Ikkalasi ham berilsa `guests` ustun.
        reservation_date: bron sanasi, standart — bugun. Kelajakdagi sana
            uchun stolning HOZIRGI holati (band/bron) tekshirilmaydi: u
            o'sha kunga tegishli emas. Ikki bron to'qnashuvi esa baribir
            `URY Table Reservation.validate_overlap` da ushlanadi.
        from_time: bron vaqti. Bugungi bron uchun standart — hozir;
            kelajakdagi sana uchun MAJBURIY.
        to_time: tugash vaqti. Berilmasa — boshlanishdan 2 soat keyin.
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    row = cashier_permissions.assert_table_in_scope(table, scope)

    customer_name = order_items.clean_text(customer_name, label=_("Mehmon ismi"))
    if not customer_name:
        frappe.throw(_("Mehmon ismini kiriting"))
    phone = order_items.clean_phone(phone)
    notes = order_items.clean_text(notes, order_items.ADDRESS_LENGTH, _("Bron izohi")) or None

    date = getdate(reservation_date or nowdate())
    if date < getdate(nowdate()):
        frappe.throw(_("Bron sanasi o'tib ketgan"), title=_("Sana noto'g'ri"))

    is_today = date == getdate(nowdate())
    if not is_today and not from_time:
        frappe.throw(_("Kelajakdagi bron uchun vaqtni kiriting"), title=_("Vaqt yo'q"))

    start = _parse_time(from_time or frappe.utils.nowtime(), _("Boshlanish vaqti"))
    end = _parse_time(to_time, _("Tugash vaqti")) if to_time else _default_end(start)
    if _seconds(end) <= _seconds(start):
        frappe.throw(
            _(
                "Tugash vaqti boshlanish vaqtidan keyin bo'lishi kerak. Bron bir kun "
                "ichida — kun oxirigacha bo'lsa 23:59 ni kiriting."
            ),
            title=_("Vaqt noto'g'ri"),
        )
    guest_count = _guests(guests, pax, table)

    # Qulf: ikki kassir bir stolni bir vaqtda bron qilmasin (TZ §24).
    frappe.db.sql("select name from `tabURY Table` where name = %s for update", table)

    # Bugun stolda ochiq buyurtma bo'lsa bron qilinmaydi. Boshqa BRON esa
    # to'sqinlik EMAS: bir stolga bir kunda vaqti to'qnashmaydigan bir necha
    # bron mumkin — to'qnashuvni `URY Table Reservation.validate_overlap` ushlaydi.
    if is_today and _resolve_table_state(table, scope)["status"] == table_status.OCCUPIED:
        frappe.throw(
            _("{0} stolida faol buyurtma bor — bron qilib bo'lmaydi.").format(table),
            title=_("Stol band"),
        )

    reservation = frappe.get_doc(
        {
            "doctype": "URY Table Reservation",
            "table": table,
            "restaurant": row.restaurant,
            "branch": scope.branch,
            "room": row.restaurant_room,
            "customer_name": customer_name,
            "phone": phone,
            "pax": guest_count,
            "reservation_date": date,
            "from_time": start,
            "to_time": end,
            "status": "Confirmed",
            "notes": notes,
        }
    ).insert()

    emit_floor_change(scope.branch, [table], "RESERVATION_CREATED")

    return {
        "table": table,
        "reservation": reservation.name,
        # Kelajakdagi bron stolning HOZIRGI holatini o'zgartirmaydi.
        "status": table_status.RESERVED
        if is_today
        else _resolve_table_state(table, scope)["status"],
        "date": str(date),
    }


@frappe.whitelist()
def get_reservations(date=None):
    """Bir kunlik bronlar ro'yxati (standart — bugun), vaqt bo'yicha tartiblangan.

    Bekor qilingan bronlar chiqmaydi — kassir ular bilan hech narsa qilmaydi.
    Qolgan holatlar (`Pending`, `Confirmed`, `Seated`, `No Show`, `Completed`)
    ko'rsatiladi: kun davomida kim kelgani, kim kelmagani ko'rinib turadi.
    Filial bo'yicha BITTA so'rov.
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()

    day = getdate(date or nowdate())

    rows = frappe.get_all(
        "URY Table Reservation",
        filters={
            "branch": scope.branch,
            "reservation_date": day,
            "status": ["!=", "Cancelled"],
            "docstatus": ["<", 2],
        },
        fields=[
            "name", "table", "room", "customer", "customer_name", "phone", "pax",
            "reservation_date", "from_time", "to_time", "status", "notes",
            "seated_invoice",
        ],
        order_by="from_time asc, creation asc",
    )

    return [
        {
            "name": row.name,
            "table": row.table,
            "room": row.room,
            "customer": row.customer,
            "guest": row.customer_name or row.customer,
            "phone": row.phone,
            "pax": cint(row.pax),
            "date": str(row.reservation_date or ""),
            "from_time": str(row.from_time or ""),
            "to_time": str(row.to_time or ""),
            "status": row.status,
            "notes": row.notes,
            "seated_invoice": row.seated_invoice,
        }
        for row in rows
    ]


@frappe.whitelist()
def cancel_reservation(table=None, reservation=None, reason=None):
    """Bronni bekor qiladi — stol yana bo'sh bo'ladi."""
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()

    if not reservation:
        if not table:
            frappe.throw(_("Stol yoki bron ko'rsatilishi kerak"))
        cashier_permissions.assert_table_in_scope(table, scope)
        state = _resolve_table_state(table, scope)
        reservation = (state.get("reservation") or {}).get("name")

    if not reservation:
        frappe.throw(_("Bu stolda faol bron yo'q"), title=_("Bron topilmadi"))

    row = frappe.db.get_value(
        "URY Table Reservation",
        reservation,
        ["branch", "table", "status", "notes"],
        as_dict=True,
    )
    if not row:
        frappe.throw(_("Bron topilmadi"), frappe.DoesNotExistError)
    if row.branch != scope.branch:
        raise cashier_permissions.CashierPermissionError(
            _("Bu bron boshqa filialga tegishli")
        )

    # Mehmon o'tirgan (`Seated`), kelmagan (`No Show`) yoki yakunlangan bron
    # "bekor qilinmaydi": aks holda hisobot tarixi buziladi.
    if row.status not in ("Pending", "Confirmed"):
        frappe.throw(
            _("Bu bron allaqachon yopilgan (holati: {0}) — uni bekor qilib bo'lmaydi.").format(
                row.status
            ),
            title=_("Bron yopilgan"),
        )

    reason = order_items.clean_text(reason, order_items.ADDRESS_LENGTH, _("Sabab"))
    notes = row.notes or ""
    if reason:
        notes = f"{notes}\n{_('Bekor qilindi')}: {reason}".strip()

    frappe.db.set_value(
        "URY Table Reservation",
        reservation,
        {"status": "Cancelled", "notes": notes or None},
    )

    frappe.logger("ozturk_cashier").info(
        "Bron bekor qilindi: %s | stol=%s | kassir=%s | sabab=%s",
        reservation, row.table, frappe.session.user, reason,
    )

    emit_floor_change(scope.branch, [row.table], "RESERVATION_CANCELLED")

    return {"table": row.table, "reservation": reservation, "status": table_status.AVAILABLE}


@frappe.whitelist()
def release_table(table, reason):
    """Stolni QO'LDA bo'shatish — faqat menejer (TZ §23).

    Ochiq chek qolgan bo'lsa RAD ETILADI: aks holda buyurtma "yo'qolib"
    qoladi va kassa hisobotlari buziladi.
    """
    cashier_permissions.require_cashier()
    cashier_permissions.require_supervisor(_("stolni bo'shatish"))
    scope = cashier_permissions.resolve_scope()
    cashier_permissions.assert_table_in_scope(table, scope)

    reason = order_items.clean_text(reason, order_items.ADDRESS_LENGTH, _("Sabab"))
    if not reason:
        frappe.throw(_("Bo'shatish sababi ko'rsatilishi shart"))

    state = _resolve_table_state(table, scope)
    if state["all_orders"]:
        frappe.throw(
            _("{0} stolida {1} ta to'lanmagan buyurtma bor — avval ularni hal qiling.").format(
                table, len(state["all_orders"])
            ),
            title=_("Stol bo'shatilmadi"),
        )

    # `merged_with` ham tozalanadi: qolib ketgan birlashtirish keyingi buyurtmani
    # sherik stolni ham o'ziga tortishiga olib keladi (`order_transfer.release_stale_cluster`).
    for member in state["cluster"]:
        frappe.db.set_value(
            "URY Table",
            member,
            {"occupied": 0, "latest_invoice_time": None, "merged_with": None},
            update_modified=False,
        )

    frappe.logger("ozturk_cashier").info(
        "Stol bo'shatildi: %s | kassir=%s | sabab=%s", table, frappe.session.user, reason
    )

    emit_floor_change(scope.branch, state["cluster"], "TABLE_RELEASED")
    return {"table": table, "status": table_status.AVAILABLE}


# ═══════════════════════════════════════════════════════════════════
#  Stol ko'chirish / birlashtirish (feature: table_transfer)
# ═══════════════════════════════════════════════════════════════════
#
# Barcha mantiq `utils/order_transfer.py` da (qulf, tekshiruv, KOT, savepoint).
# Bu yerda faqat ruxsat zanjiri: rol -> ko'lam -> funksiya bayrog'i -> smena.
#
# Kassir buyurtmani ko'chira oladi, lekin stolni QO'LDA band qila olmaydi —
# yuqoridagi "BIZNES QOIDASI" o'zgarmaydi: ko'chirish faqat MAVJUD buyurtmani
# bo'sh stolga olib o'tadi.


def _prepare_table_operation(invoice):
    """Uchala amal uchun bir xil ruxsat zanjiri. `scope` qaytaradi."""
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_features.assert_enabled(scope.pos_profile, "table_transfer")
    cashier_permissions.assert_shift_open(scope)
    cashier_permissions.assert_invoice_in_scope(invoice, scope, docstatus=0)
    return scope


@frappe.whitelist()
def transfer_table(invoice, to_table):
    """Buyurtmani boshqa BO'SH stolga ko'chiradi.

    Chek, oshxona chiptalari va stol bandligi birga o'tadi; eski stol
    bo'shaydi (unda boshqa ochiq chek qolmagan bo'lsa). Hisob allaqachon
    chiqarilgan bo'lsa ham ko'chiriladi — javobda `billed = True`.

    Returns:
        dict: invoice, from_table, to_table, room, billed, freed_tables,
              kots_updated, reservation
    """
    scope = _prepare_table_operation(invoice)
    return order_transfer.transfer(invoice, to_table, scope)


@frappe.whitelist()
def merge_tables(invoice, tables):
    """Buyurtma stoliga bir zaldagi BO'SH stollarni qo'shadi (bitta hisob).

    Args:
        tables: qo'shiladigan stollar — ro'yxat yoki JSON qatori.

    Returns:
        dict: invoice, table, merged_tables, cluster, billed, reservations
    """
    scope = _prepare_table_operation(invoice)
    return order_transfer.merge(invoice, tables, scope)


@frappe.whitelist()
def unmerge_table(invoice, table):
    """Birlashtirilgan stollardan bittasini ajratib, bo'shatadi.

    Returns:
        dict: invoice, table, released_table, merged_tables, cluster, billed
    """
    scope = _prepare_table_operation(invoice)
    return order_transfer.unmerge(invoice, table, scope)
