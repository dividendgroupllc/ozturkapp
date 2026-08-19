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
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, time_diff_in_seconds

from ozturkapp.ozturkapp.utils import cashier_billing, cashier_permissions, table_status


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

    orders = orders[: cint(limit) or 100]

    kitchen = _kitchen_states([order.name for order in orders])
    now = frappe.utils.now_datetime()

    return [
        {
            "invoice": order.name,
            "table": order.restaurant_table,
            "merged_tables": order.custom_merged_tables,
            "room": order.custom_restaurant_room,
            "order_type": order.order_type,
            "customer": order.customer,
            "customer_name": order.customer_name or order.customer,
            "waiter": order.waiter,
            "waiter_name": cashier_billing._user_label(order.waiter),
            "pax": cint(order.no_of_pax),
            "amount": flt(order.rounded_total) or flt(order.grand_total),
            "billed": bool(cint(order.invoice_printed)),
            "status": "billed" if cint(order.invoice_printed) else "open",
            "status_label": _("Hisob berilgan")
            if cint(order.invoice_printed)
            else _("Ochiq"),
            "order_number": order.custom_ury_order_number or order.custom_ticket_number,
            "opened_at": str(order.creation or ""),
            "elapsed_minutes": int(
                max(0, time_diff_in_seconds(now, order.creation)) // 60
            ),
            "comments": order.custom_comments,
            "kitchen": kitchen.get(order.name, {}),
        }
        for order in orders
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
        fields=["invoice", "order_status", "start_time_prep"],
    )

    grouped = {}
    for row in rows:
        grouped.setdefault(row.invoice, []).append(row)

    result = {}
    for invoice, kots in grouped.items():
        served = sum(1 for k in kots if (k.order_status or "") == "Served")
        started = any(k.start_time_prep or (k.order_status or "") == "Served" for k in kots)

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
