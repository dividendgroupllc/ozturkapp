# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa oynasi — faol buyurtmalar (TZ §7, §11, §14).

Kassa buyurtmani O'ZI YARATGAN deb faraz QILMAYDI. Buyurtma ofitsantdan,
URY POS'dan yoki kelajakdagi integratsiyadan kelishi mumkin — shuning uchun
bu yerda faqat MAVJUD ma'lumot o'qiladi (TZ §14).

Buyurtma = `docstatus = 0` holatidagi `POS Invoice`. URY'da "URY Order"
DocType'i Single (bitta yozuvli forma) bo'lgani uchun buyurtmalar aynan
POS Invoice qoralamalarida saqlanadi.

YAGONA YOZUV AMALI — BEKOR QILISH
=================================
Kassa buyurtma YARATMAYDI va TAHRIRLAMAYDI (TZ §14). Bitta istisno bor:
ofitsant xato zakaz olib qo'ysa, kassir uni bekor qiladi. Qoida
(`utils/order_cancel.py`) — oshxona ishga kirishmagan bo'lsa har qanday
kassir, kirishgan bo'lsa faqat menejer.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from ozturkapp.ozturkapp.utils import (
    cashier_billing,
    cashier_permissions,
    kitchen_status,
    order_cancel,
    order_items,
    table_status,
)

#: Kassa tarixi bir so'rovda qaytaradigan eng ko'p chek. Chegarasiz `limit` bilan
#: bir so'rov butun filial tarixini xotiraga yuklardi.
MAX_PAID_ROWS = 500

#: Kassa tarixidagi smena tanlagichi ko'rsatadigan eng so'nggi smenalar soni.
MAX_HISTORY_SHIFTS = 60


@frappe.whitelist()
def get_active_orders(room=None, status=None, limit=100):
    """Kassir e'tiboriga muhtoj buyurtmalar ro'yxati (TZ §7).

    Args:
        room: zal bo'yicha filtr.
        status: `open` (hisob ochilmagan) yoki `billed` (hisob berilgan,
                to'lov kutilmoqda). Bo'sh bo'lsa — hammasi.
        limit: qatorlar soni.
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()

    orders = table_status.get_open_orders(scope.branch)

    if room:
        allowed = set(
            frappe.get_all(
                "URY Table",
                filters={"branch": scope.branch, "restaurant_room": room},
                pluck="name",
            )
        )
        orders = [
            order
            for order in orders
            if {order.restaurant_table}
            .union(table_status.parse_merged_with(order.custom_merged_tables))
            .intersection(allowed)
        ]

    if status == "open":
        orders = [order for order in orders if not cint(order.invoice_printed)]
    elif status == "billed":
        orders = [order for order in orders if cint(order.invoice_printed)]

    orders = orders[: max(cint(limit), 0) or 100]

    kitchen = _kitchen_states([order.name for order in orders])
    labels = _user_labels(order.waiter for order in orders)
    now = frappe.utils.now_datetime()

    return [
        {
            "invoice": order.name,
            "table": order.restaurant_table,
            "merged_tables": order.custom_merged_tables,
            "room": order.custom_restaurant_room,
            "customer": order.customer,
            "customer_name": order.customer_name or order.customer,
            "waiter": order.waiter,
            "waiter_name": labels.get(order.waiter, ""),
            "pax": cint(order.no_of_pax),
            "amount": flt(order.rounded_total) or flt(order.grand_total),
            "billed": bool(cint(order.invoice_printed)),
            "status": "billed" if cint(order.invoice_printed) else "open",
            "status_label": _("Hisob berilgan")
            if cint(order.invoice_printed)
            else _("Ochiq"),
            "order_number": order.custom_ury_order_number or order.custom_ticket_number,
            # order_type, opened_at, elapsed_minutes, bill_requested(+_at), delivery
            **table_status.order_visibility(order, now),
            "comments": order.custom_comments,
            "kitchen": kitchen.get(order.name, {}),
        }
        for order in orders
    ]


def _user_labels(users) -> dict:
    """{foydalanuvchi: to'liq ism} — BITTA so'rovda.

    Har bir qator uchun `cashier_billing._user_label()` chaqirish ro'yxat
    uzunligiga teng so'rov (N+1) berardi.
    """
    users = sorted({user for user in users if user})
    if not users:
        return {}
    names = dict(
        frappe.db.sql(
            "select name, full_name from `tabUser` where name in %s", (tuple(users),)
        )
    )
    return {user: names.get(user) or user for user in users}


def _shift_end(opening, closed_at):
    """Smena tugagan vaqt; smena hali davom etayotgan bo'lsa `None`.

    Yakuniy `POS Closing Entry` bor bo'lsa — uning `period_end_date`i (Z-hisobot
    ham, yopilish solishtiruvi ham aynan shu oynani ishlatadi). Yo'q bo'lsa-yu
    smena `Closed` bo'lsa (ko'p kassirli rejimda `Sub POS Closing` smenani yakuniy
    hujjatdan oldin `Closed` qiladi) — keyingi smena ochilguncha; keyingisi ham
    bo'lmasa oyna ochiq qoladi.
    """
    if closed_at:
        return closed_at
    if opening.status == "Open":
        return None
    return frappe.db.get_value(
        "POS Opening Entry",
        {
            "pos_profile": opening.pos_profile,
            "docstatus": 1,
            "period_start_date": [">", opening.period_start_date],
        },
        "period_start_date",
        order_by="period_start_date asc",
    )


def _shift_row(opening, closed_at, labels: dict) -> dict:
    end = _shift_end(opening, closed_at)
    return {
        "name": opening.name,
        "user": opening.user,
        "user_name": labels.get(opening.user, opening.user),
        "opened_at": str(opening.period_start_date or ""),
        "closed_at": str(end) if end else None,
        # `status` emas: yopilish hujjati bilan tasdiqlangan smena ochiq hisoblanmaydi.
        "open": opening.status == "Open" and not closed_at,
    }


def _history_shifts(scope, only=None) -> list:
    """Kassa tarixi uchun smenalar — yangisi birinchi, har birining vaqt oynasi bilan.

    Args:
        only: berilsa — faqat shu smena (cheklangan kassir uchun hozirgi ochiq smena).
    """
    filters = {"pos_profile": scope.pos_profile, "docstatus": 1}
    if only:
        filters["name"] = only
    openings = frappe.get_all(
        "POS Opening Entry",
        filters=filters,
        fields=["name", "user", "status", "pos_profile", "period_start_date"],
        order_by="period_start_date desc, creation desc",
        limit_page_length=MAX_HISTORY_SHIFTS,
    )
    if not openings:
        return []

    closings = dict(
        frappe.get_all(
            "POS Closing Entry",
            filters={"pos_opening_entry": ["in", [o.name for o in openings]], "docstatus": 1},
            fields=["pos_opening_entry", "period_end_date"],
            as_list=True,
        )
    )
    labels = _user_labels(o.user for o in openings)
    return [_shift_row(o, closings.get(o.name), labels) for o in openings]


def _shift_window(scope, shift) -> tuple:
    """Tanlangan smenaning `(boshi, oxiri)` vaqti; oxiri `None` — smena davom etyapti."""
    shift = order_items.clean_text(shift, 140, _("Smena"))
    opening = frappe.db.get_value(
        "POS Opening Entry",
        shift,
        ["name", "user", "status", "pos_profile", "docstatus", "period_start_date"],
        as_dict=True,
    )
    if not opening or opening.docstatus != 1:
        frappe.throw(_("'{0}' smenasi topilmadi").format(shift), frappe.DoesNotExistError)
    if opening.pos_profile != scope.pos_profile:
        raise cashier_permissions.CashierPermissionError(_("Bu smena boshqa kassaga tegishli"))

    closed_at = frappe.db.get_value(
        "POS Closing Entry",
        {"pos_opening_entry": opening.name, "docstatus": 1},
        "period_end_date",
    )
    return opening.period_start_date, _shift_end(opening, closed_at)


def _window_condition(start, end) -> tuple:
    """Smena oynasining SQL sharti va parametrlari: `start <= chek vaqti <= end`."""
    stamp = "timestamp(posting_date, posting_time)"
    condition, params = f"{stamp} >= %(start)s", {"start": start}
    if end:
        condition += f" AND {stamp} <= %(end)s"
        params["end"] = end
    return condition, params


def _allowed_shift(scope):
    """Shu so'rovchi tarixda ko'ra oladigan YAGONA smena.

    Oddiy kassir faqat hozirgi ochiq smenani ko'radi — oldingi (yopilgan) smenalar
    tarixini emas. Bu server tomonida majburlanadi (TZ §17): tugmani yashirish
    yetarli emas, chunki `get_paid_orders` smena va sanani argument sifatida oladi.
    Smena bo'yicha ko'rinish qoidasi Z-hisobotnikiga o'xshash: cheklovsiz faqat
    menejer (`has_supervisor_role`).

    Returns:
        `None` — cheklov yo'q (menejer); `""` — kassir, lekin ochiq smena yo'q
        (ko'rsatadigan hech narsa yo'q); aks holda ochiq smena nomi.
    """
    if cashier_permissions.has_supervisor_role():
        return None
    return cashier_permissions.open_shift_name(scope)


@frappe.whitelist()
def get_paid_order_filter_options():
    """Kassa tarixi filtrlari uchun — filialda haqiqatan uchragan stol va
    ofitsiantlar ro'yxati (bo'sh/ishlatilmagan variantlarsiz) hamda smenalar
    (`shifts`, yangisi birinchi; `open` — davom etayotgani)."""
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()

    allowed = _allowed_shift(scope)
    if allowed == "":
        return {"tables": [], "waiters": [], "shifts": []}

    # Cheklangan kassirga stol va ofitsiantlar ham FAQAT hozirgi smenadagilar:
    # oldingi smenalardagi nomlar ro'yxat orqali oshkor bo'lmasin.
    params = {"branch": scope.branch}
    window = ""
    if allowed:
        condition, window_params = _window_condition(*_shift_window(scope, allowed))
        window = f" AND {condition}"
        params.update(window_params)

    # DISTINCT bazada: filialning BARCHA to'langan cheklarini Python'ga
    # yuklab, keyin takrorlarini olib tashlash yillar davomida sekinlashardi.
    tables = frappe.db.sql_list(
        f"""
        SELECT DISTINCT restaurant_table FROM `tabPOS Invoice`
        WHERE branch = %(branch)s AND docstatus = 1 AND IFNULL(restaurant_table, '') != ''{window}
        ORDER BY restaurant_table
        """,
        params,
    )
    waiter_users = frappe.db.sql_list(
        f"""
        SELECT DISTINCT waiter FROM `tabPOS Invoice`
        WHERE branch = %(branch)s AND docstatus = 1 AND IFNULL(waiter, '') != ''{window}
        """,
        params,
    )

    labels = _user_labels(waiter_users)
    waiters = [{"value": w, "label": labels[w]} for w in sorted(waiter_users)]
    waiters.sort(key=lambda w: w["label"])

    return {"tables": tables, "waiters": waiters, "shifts": _history_shifts(scope, only=allowed)}


@frappe.whitelist()
def get_paid_orders(
    date_from=None, date_to=None, search=None, table=None, waiter=None, limit=100, shift=None
):
    """Kassa tarixi — bergiliy davrda to'langan cheklar ro'yxati.

    Ko'rish uchun (masalan qayta chop etish) — hech narsa yaratmaydi yoki
    o'zgartirmaydi.

    OLDINGI SMENALAR
    ================
    Oddiy kassir FAQAT hozirgi ochiq smenani ko'radi (ochiq smena bo'lmasa — bo'sh
    ro'yxat): boshqa `shift` yoki sana yuborsa ham natija o'zgarmaydi, boshqa
    smena so'ralsa esa `CashierPermissionError`. Oldingi smenalarni faqat
    menejer (`URY Manager`, `System Manager`) ko'radi.

    Args:
        shift: `POS Opening Entry` nomi. Berilsa cheklar smena ochilgan vaqtdan
            yopilgan vaqtigacha (ochiq smenada — hozirgacha) olinadi va sana
            filtri e'tiborga olinmaydi. Vaqt oralig'i sanadan emas, aynan
            soatgacha aniq: smena yarim tundan oshsa yoki bir kunda ikki smena
            bo'lsa ham cheklar o'z smenasida turadi.
        date_from, date_to: `YYYY-MM-DD`. Bo'sh bo'lsa — bugungi kun.
        search: chek raqami bo'yicha qidiruv (faqat raqam).
        table: aniq stol bo'yicha filtr.
        waiter: aniq ofitsiant (foydalanuvchi) bo'yicha filtr.
        limit: qatorlar soni.
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()

    # Kirish qiymatlari cheklovdan OLDIN tekshiriladi: kassir uchun sana e'tiborsiz
    # qoladi, lekin noto'g'ri sana yoki juda uzun qidiruv baribir rad etilishi kerak.
    today = frappe.utils.today()
    date_from = getdate(date_from or today)
    date_to = getdate(date_to or today)
    # Oddiy matnga keltiriladi: HTTP orqali ro'yxat kelsa SQL'ga `IN (...)` bo'lib ketmasin.
    shift = order_items.clean_text(shift, 140, _("Smena"))
    table = order_items.clean_text(table, 140, _("Stol"))
    waiter = order_items.clean_text(waiter, 140, _("Ofitsiant"))
    search = order_items.clean_text(search, 60, _("Qidiruv"))

    allowed = _allowed_shift(scope)
    if allowed is not None:
        if not allowed:
            return []
        if shift and shift != allowed:
            raise cashier_permissions.CashierPermissionError(
                _("Oldingi smenalar tarixini faqat menejer ko'ra oladi")
            )
        # Sana ham e'tiborga olinmaydi: cheklov faqat smena oynasi bilan.
        shift = allowed

    # Shartlar — doimiy matnlar, qiymatlar esa `params` orqali (SQL'ga qo'shilmaydi).
    conditions = ["branch = %(branch)s", "docstatus = 1"]
    params = {
        "branch": scope.branch,
        "limit": min(max(cint(limit), 0) or 100, MAX_PAID_ROWS),
    }

    if shift:
        condition, window_params = _window_condition(*_shift_window(scope, shift))
        conditions.append(condition)
        params.update(window_params)
    else:
        conditions.append("posting_date between %(date_from)s and %(date_to)s")
        params["date_from"] = date_from
        params["date_to"] = date_to

    if table:
        conditions.append("restaurant_table = %(table)s")
        params["table"] = table
    if waiter:
        conditions.append("waiter = %(waiter)s")
        params["waiter"] = waiter

    if search:
        conditions.append(
            "(name like %(like)s or restaurant_table like %(like)s or customer_name like %(like)s)"
        )
        params["like"] = f"%{search}%"

    where = " AND ".join(conditions)
    rows = frappe.db.sql(
        f"""
        SELECT name, restaurant_table, custom_merged_tables, customer_name,
               waiter, cashier, posting_date, posting_time,
               rounded_total, grand_total, order_type,
               is_return, return_against
        FROM `tabPOS Invoice`
        WHERE {where}
        ORDER BY posting_date DESC, posting_time DESC
        LIMIT %(limit)s
        """,
        params,
        as_dict=True,
    )
    if not rows:
        return []

    labels = _user_labels([r.waiter for r in rows] + [r.cashier for r in rows])
    payments = frappe.get_all(
        "Sales Invoice Payment",
        filters={"parent": ["in", [r.name for r in rows]], "parenttype": "POS Invoice"},
        fields=["parent", "mode_of_payment", "amount"],
    )
    payments_by_invoice = {}
    for p in payments:
        payments_by_invoice.setdefault(p.parent, []).append(
            {"mode_of_payment": p.mode_of_payment, "amount": flt(p.amount)}
        )

    return [
        {
            "invoice": r.name,
            "table": r.restaurant_table,
            "merged_tables": r.custom_merged_tables,
            "customer_name": r.customer_name,
            "waiter_name": labels.get(r.waiter, ""),
            "cashier_name": labels.get(r.cashier, ""),
            "order_type": r.order_type,
            "date": str(r.posting_date or ""),
            "time": str(r.posting_time or "")[:8],
            "amount": flt(r.rounded_total) or flt(r.grand_total),
            # Qaytarish cheki manfiy summa bilan shu ro'yxatda turadi — frontend
            # uni «QAYTARISH» deb ajratib ko'rsatishi va asl chekka bog'lashi uchun.
            "is_return": bool(cint(r.is_return)),
            "return_against": r.return_against or None,
            "payments": payments_by_invoice.get(r.name, []),
        }
        for r in rows
    ]


@frappe.whitelist()
def get_order_counts(room=None):
    """Yuqoridagi ro'yxat uchun sanoqlar — badge'lar bir xil to'plamdan olinsin."""
    orders = get_active_orders(room=room, limit=10000)
    return {
        "all": len(orders),
        "open": sum(1 for order in orders if not order["billed"]),
        "billed": sum(1 for order in orders if order["billed"]),
        "amount": sum(order["amount"] for order in orders),
    }


@frappe.whitelist()
def get_table_order(table):
    """Stoldagi faol buyurtmaning to'liq hisobi (mahsulotlar bilan)."""
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_permissions.assert_table_in_scope(table, scope)

    from ozturkapp.ozturkapp.api.table import _resolve_table_state

    state = _resolve_table_state(table, scope)
    if not state.get("order"):
        return None

    invoice = frappe.get_doc("POS Invoice", state["order"]["name"])
    return cashier_billing.build_bill(invoice, scope)


@frappe.whitelist()
def get_order_bill_preview(order):
    """Bitta buyurtmaning hisob ko'rinishi — FAQAT O'QISH (TZ §3, §4, §5).

    Bu metod HECH NARSA YARATMAYDI va HECH NARSANI SUBMIT QILMAYDI. U chekni
    ochib, ERPNext allaqachon hisoblab qo'ygan summalarni qaytaradi xolos.
    Chaqirilishi hujjat holatini o'zgartirmaydi (TZ §5, §12/#10).

    Ma'lumot HAR SAFAR bazadan yangidan o'qiladi — frontend keshiga
    ishonilmaydi (TZ §9).

    Args:
        order: `POS Invoice` nomi.

    Returns:
        dict: order, table, waiter, customer, pax, items, subtotal,
              taxes, service_charge, grand_total, currency.
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_permissions.assert_invoice_in_scope(order, scope)

    doc = frappe.get_doc("POS Invoice", order)
    bill = cashier_billing.build_bill(doc, scope)

    # TZ §4 dagi nomlar bilan moslik uchun qo'shimcha kalitlar.
    bill["order"] = doc.name
    bill["service_charge_amount"] = (
        flt(bill["service_charge"]["amount"]) if bill.get("service_charge") else 0.0
    )
    return bill


# ═══════════════════════════════════════════════════════════════════
#  Bekor qilish — kassaning yagona yozuv amali
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def cancel_order(order, reason):
    """Ofitsant xato olgan buyurtmani bekor qilish.

    KIM QILA OLADI
    ==============
        Oshxona hali BOSHLAMAGAN  ->  har qanday kassir
        Oshxona BOSHLAB YUBORGAN  ->  faqat menejer

    Qoidaning o'zi `utils/order_cancel.py` da — kassa oynasi ham,
    kelajakdagi boshqa mijoz ham AYNAN o'sha funksiyaga murojaat qiladi,
    o'z tekshiruvini yozmaydi.

    Chek O'CHIRILMAYDI: `custom_cancelled = 1` qo'yiladi, sabab va kim
    bekor qilgani yoziladi. Shu bilan birga oshxona chiptasi yopiladi va
    boshqa ochiq cheki qolmagan stol bo'shatiladi.

    Args:
        order: `POS Invoice` nomi (`docstatus = 0` bo'lishi shart).
        reason: bekor qilish sababi — majburiy, hisobotga tushadi.

    Returns:
        dict: invoice, cancelled_items, freed_tables, kitchen_started
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    row = cashier_permissions.assert_invoice_in_scope(order, scope, docstatus=0)

    return order_cancel.cancel_invoice(row, reason, scope)


def _kitchen_states(invoices: list) -> dict:
    """Bir nechta chek uchun KOT holatini BITTA so'rovda yig'ish (TZ §25).

    Har bir buyurtma uchun alohida `get_kitchen_state()` chaqirish ro'yxat
    uzun bo'lganda N+1 so'rovga aylanardi.
    """
    if not invoices or not frappe.db.exists("DocType", "URY KOT"):
        return {}

    rows = frappe.get_all(
        "URY KOT",
        filters={"invoice": ["in", invoices], "docstatus": ["<", 2]},
        fields=["invoice", "order_status"],
    )

    grouped = {}
    for row in rows:
        grouped.setdefault(row.invoice, []).append(row)

    started_invoices = _started_invoices(list(grouped))

    result = {}
    for invoice, kots in grouped.items():
        served = sum(1 for k in kots if (k.order_status or "") == "Served")
        started = invoice in started_invoices

        if served == len(kots):
            label = _("Berildi")
        elif started:
            label = _("Tayyorlanmoqda")
        else:
            label = _("Oshxonada kutilmoqda")

        result[invoice] = {
            "kot_count": len(kots),
            "served_count": served,
            "pending_count": len(kots) - served,
            "preparation_started": bool(started),
            "label": label,
        }
    return result


def _started_invoices(invoices: list) -> set:
    """Oshxona ISHNI BOSHLAGAN cheklar to'plami — bitta so'rovda.

    `kitchen_status.get_order_progress()` bilan bir xil qoida, faqat
    ro'yxat uchun: har bir chek uchun alohida chaqirish N+1 so'rovga
    aylanardi.

    `URY KOT.start_time_prep` ATAYLAB ishlatilmaydi — u `default = "Now"`
    bilan e'lon qilingan va KOT yaratilganda to'ladi, ya'ni har doim
    to'lgan bo'ladi. Ish boshlanganini faqat mahsulot darajasidagi
    `custom_kitchen_status` bildiradi.
    """
    if not invoices:
        return set()

    rows = frappe.db.sql(
        """
        SELECT DISTINCT k.invoice
        FROM `tabURY KOT Items` ki
        INNER JOIN `tabURY KOT` k ON k.name = ki.parent
        WHERE k.invoice IN %(invoices)s AND k.docstatus = 1
          AND k.type IN %(types)s
          AND IFNULL(ki.custom_kitchen_status, %(pending)s)
              NOT IN (%(pending)s, %(cancelled)s)
        """,
        {
            "invoices": tuple(invoices),
            "types": kitchen_status.COOKING_KOT_TYPES,
            "pending": kitchen_status.PENDING,
            "cancelled": kitchen_status.CANCELLED,
        },
        as_dict=True,
    )
    return {row.invoice for row in rows}
