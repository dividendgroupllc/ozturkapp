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

import frappe
from frappe import _
from frappe.utils import cint

from ozturkapp.ozturkapp.utils import cashier_billing, cashier_permissions, table_status
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
):
    """Bo'sh stolni bron qiladi.

    Bron stolni BAND QILMAYDI — u faqat RESERVED holatini beradi. Mehmon
    kelib buyurtma bergandagina stol OCCUPIED bo'ladi.
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    row = cashier_permissions.assert_table_in_scope(table, scope)

    if not (customer_name or "").strip():
        frappe.throw(_("Mehmon ismini kiriting"))

    # Qulf: ikki kassir bir stolni bir vaqtda bron qilmasin (TZ §24).
    frappe.db.sql("select name from `tabURY Table` where name = %s for update", table)

    state = _resolve_table_state(table, scope)
    if state["status"] == table_status.OCCUPIED:
        frappe.throw(
            _("{0} stolida faol buyurtma bor — bron qilib bo'lmaydi.").format(table),
            title=_("Stol band"),
        )
    if state["status"] == table_status.RESERVED:
        frappe.throw(
            _("{0} stoli allaqachon bron qilingan.").format(table),
            title=_("Bron mavjud"),
        )

    reservation = frappe.get_doc(
        {
            "doctype": "URY Table Reservation",
            "table": table,
            "restaurant": row.restaurant,
            "branch": scope.branch,
            "room": row.restaurant_room,
            "customer_name": customer_name.strip(),
            "phone": (phone or "").strip() or None,
            "pax": cint(pax) or _default_pax(table),
            "reservation_date": reservation_date or frappe.utils.nowdate(),
            "from_time": from_time or frappe.utils.nowtime(),
            "to_time": to_time or None,
            "status": "Confirmed",
            "notes": notes,
        }
    ).insert()

    emit_floor_change(scope.branch, [table], "RESERVATION_CREATED")

    return {
        "table": table,
        "reservation": reservation.name,
        "status": table_status.RESERVED,
    }


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
        "URY Table Reservation", reservation, ["branch", "table", "status"], as_dict=True
    )
    if not row:
        frappe.throw(_("Bron topilmadi"), frappe.DoesNotExistError)
    if row.branch != scope.branch:
        raise cashier_permissions.CashierPermissionError(
            _("Bu bron boshqa filialga tegishli")
        )

    frappe.db.set_value(
        "URY Table Reservation",
        reservation,
        {"status": "Cancelled", "notes": reason or None},
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

    if not (reason or "").strip():
        frappe.throw(_("Bo'shatish sababi ko'rsatilishi shart"))

    state = _resolve_table_state(table, scope)
    if state["all_orders"]:
        frappe.throw(
            _("{0} stolida {1} ta to'lanmagan buyurtma bor — avval ularni hal qiling.").format(
                table, len(state["all_orders"])
            ),
            title=_("Stol bo'shatilmadi"),
        )

    for member in state["cluster"]:
        frappe.db.set_value(
            "URY Table",
            member,
            {"occupied": 0, "latest_invoice_time": None},
            update_modified=False,
        )

    frappe.logger("ozturk_cashier").info(
        "Stol bo'shatildi: %s | kassir=%s | sabab=%s", table, frappe.session.user, reason
    )

    emit_floor_change(scope.branch, state["cluster"], "TABLE_RELEASED")
    return {"table": table, "status": table_status.AVAILABLE}
