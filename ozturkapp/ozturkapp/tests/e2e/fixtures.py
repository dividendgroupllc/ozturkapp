# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""E2E ma'lumotlari: HAMMASI shim tranzaksiyasi ichida yaratiladi va oxirida qaytariladi.

Bu funksiyalar `Shim.ops` orqali haydovchidan chaqiriladi; har biri alohida "so'rov"
(virtual commit bilan) sifatida ishlaydi. Hech qanday DDL yo'q: faqat qator yozuvlari.

Sayt ma'lumotiga o'xshash narsalar (nomlar KODGA yozilmaydi):
  - filial / POS Profile / restoran   — saytdagi yagona POS Profile'dan;
  - naqd usul                          — profildagi birinchi naqd usul (`Нахт`);
  - narxlar                            — `URY Menu Item.rate` dan `Item Price` sifatida.
"""

import frappe
from frappe.utils import flt

from ozturkapp.ozturkapp.setup import cashier_features
from ozturkapp.ozturkapp.utils import cashier_billing, cashier_permissions, manager_approval

CASHIER = "e2e-cashier@example.com"
MANAGER = "e2e-manager@example.com"
WAITER = "e2e-waiter@example.com"
PIN = "4321"
CARD = "Test Karta"
CASH2 = "Test Naqd 2"
PRICED_ITEMS = 200


def _user(email, roles, pin=None):
    doc = frappe.get_doc(
        {
            "doctype": "User",
            "email": email,
            "first_name": email.split("@")[0],
            "send_welcome_email": 0,
            "enabled": 1,
            "roles": [{"role": role} for role in roles],
        }
    )
    if pin:
        doc.custom_pos_pin = pin
    doc.flags.no_welcome_mail = True
    doc.insert(ignore_permissions=True)


def _profile():
    branch = frappe.get_all("Branch", pluck="name", limit=1)[0]
    name = frappe.db.get_value("POS Profile", {"branch": branch, "disabled": 0}, "name")
    return branch, frappe.get_doc("POS Profile", name)


def _payment_mode(name, mode_type, account, company):
    if not frappe.db.exists("Mode of Payment", name):
        frappe.get_doc(
            {
                "doctype": "Mode of Payment",
                "mode_of_payment": name,
                "type": mode_type,
                "enabled": 1,
                "accounts": [{"company": company, "default_account": account}],
            }
        ).insert(ignore_permissions=True)


def setup_base():
    """Foydalanuvchilar, to'lov usullari, narxlar, printerlar, stollar, bayroqlar."""
    branch, profile = _profile()
    company = profile.company

    # Saytdagi HAQIQIY ochiq smena tranzaksiya ichida yopiladi: "Kassa yopiq" ekrani sinalsin.
    frappe.db.sql(
        "update `tabPOS Opening Entry` set status = 'Closed' where pos_profile = %s and status = 'Open'",
        profile.name,
    )

    _user(CASHIER, ["URY Cashier"])
    _user(MANAGER, ["URY Manager", "URY Cashier"], pin=PIN)
    _user(WAITER, ["URY Captain"])

    branch_doc = frappe.get_doc("Branch", branch)
    for email in (CASHIER, MANAGER, WAITER):
        frappe.get_doc(
            {"doctype": "URY User", "parent": branch, "parenttype": "Branch", "parentfield": "user",
             "user": email, "idx": len(branch_doc.get("user")) + 1}
        ).insert(ignore_permissions=True)
    for email in (CASHIER, MANAGER):
        frappe.get_doc(
            {"doctype": "POS Profile User", "parent": profile.name, "parenttype": "POS Profile",
             "parentfield": "applicable_for_users", "user": email, "default": 0}
        ).insert(ignore_permissions=True)

    cash_mode = next((row for row in profile.payments if row.default), profile.payments[0])
    cash_account = frappe.db.get_value(
        "Mode of Payment Account", {"parent": cash_mode.mode_of_payment, "company": company},
        "default_account",
    )
    # Kassa vaqt o'tib boshqa to'lov usullari (click, Payme, ...) qo'shadi — sinov ularga bog'liq
    # bo'lmasin: profilda asosiy naqd usul + sinov usullari qoladi (tranzaksiya ichida, rollback bo'ladi).
    frappe.db.delete(
        "POS Payment Method",
        {"parent": profile.name, "mode_of_payment": ["!=", cash_mode.mode_of_payment]},
    )
    frappe.local._ozturk_payment_methods = {}
    _payment_mode(CARD, "Bank", cash_account, company)
    _payment_mode(CASH2, "Cash", cash_account, company)
    for idx, (mode, allow) in enumerate(((CASH2, 0), (CARD, 1)), start=90):
        frappe.get_doc(
            {"doctype": "POS Payment Method", "parent": profile.name, "parenttype": "POS Profile",
             "parentfield": "payments", "mode_of_payment": mode, "default": 0,
             "allow_in_returns": allow, "idx": idx}
        ).insert(ignore_permissions=True)

    _price_menu(profile)
    _printers(branch)
    _tables(branch, profile.restaurant)

    for key in cashier_features.FEATURES:
        _set_flag(profile.name, cashier_features.FEATURES[key]["fieldname"], 1)
    frappe.db.set_value("POS Profile", profile.name, "remove_items", 1, update_modified=False)
    _set_setting(profile.name, "max_cashier_discount_percent", 10)
    _set_setting(profile.name, "cash_payout_approval_limit", 100000)

    manager_approval.reset_attempts(MANAGER)
    manager_approval.reset_requester(CASHIER)
    frappe.local._ozturk_scope_cache = {}
    return {"branch": branch, "profile": profile.name, "cash_mode": cash_mode.mode_of_payment}


def _price_menu(profile):
    menu = frappe.db.get_value("URY Restaurant", profile.restaurant, "active_menu")
    rows = frappe.get_all(
        "URY Menu Item", filters={"parent": menu, "disabled": 0},
        fields=["item", "rate"], order_by="item_name asc", limit=PRICED_ITEMS,
    )
    # URY chekning narx ro'yxatini MENYUGA bog'langan `Price List.restaurant_menu` dan oladi.
    price_list = frappe.db.get_value("Price List", {"restaurant_menu": menu, "enabled": 1})
    # Dev bazada narxlar allaqachon kiritilgan bo'lishi mumkin (menejer ularni qo'lda qo'yadi) —
    # ERPNext bir xil (taom, narxnoma) juftini ikki marta qabul qilmaydi. Mavjud narx ishlatiladi.
    priced = set(
        frappe.get_all(
            "Item Price",
            filters={"price_list": price_list, "item_code": ["in", [row.item for row in rows]]},
            pluck="item_code",
        )
    )
    for row in rows:
        if row.item in priced:
            continue
        frappe.get_doc(
            {"doctype": "Item Price", "item_code": row.item, "price_list": price_list,
             "price_list_rate": row.rate, "selling": 1, "currency": profile.currency}
        ).insert(ignore_permissions=True)


def _printers(branch):
    units = frappe.get_all("URY Production Unit", filters={"branch": branch}, pluck="name")
    specs = [("E2E Kassa", "Kassa", None)] + [(f"E2E {unit}", "Oshxona", unit) for unit in units]
    for name, role, unit in specs:
        frappe.get_doc(
            {"doctype": "Ozturk Printer", "printer_name": name, "enabled": 1, "branch": branch,
             "role": role, "production_unit": unit, "ip_address": "127.0.0.1", "port": 9100,
             "paper_width": "80", "codepage": "cp866", "cut_paper": 1}
        ).insert(ignore_permissions=True)


def _tables(branch, restaurant):
    room = frappe.db.get_value("URY Table", {"branch": branch}, "restaurant_room")
    for idx in range(1, 4):
        doc = frappe.get_doc(
            {"doctype": "URY Table", "restaurant": restaurant, "restaurant_room": room, "branch": branch,
             "no_of_seats": 4, "layout_x": 40 + idx * 120, "layout_y": 300, "layout_width": 100,
             "layout_height": 80, "table_shape": "Rectangle"}
        )
        doc.__newname = f"E2E-T{idx}"
        doc.insert(ignore_permissions=True)


def _set_flag(profile, fieldname, value):
    frappe.db.set_value("POS Profile", profile, fieldname, 1 if value else 0, update_modified=False)


def _set_setting(profile, key, value):
    frappe.db.set_value(
        "POS Profile", profile, cashier_features.SETTINGS[key]["fieldname"], value, update_modified=False
    )


def set_features(**flags):
    """`{"discount": 0, "tips": 1, ...}` — bayroqlarni almashtiradi (profil nomi avtomatik)."""
    _branch, profile = _profile()
    for key, enabled in flags.items():
        if key == "remove_items":
            frappe.db.set_value("POS Profile", profile.name, "remove_items", 1 if enabled else 0,
                                update_modified=False)
        else:
            _set_flag(profile.name, cashier_features.FEATURES[key]["fieldname"], enabled)
    frappe.local._ozturk_scope_cache = {}
    return cashier_features.get_features(profile.name)


def set_settings(**settings):
    _branch, profile = _profile()
    for key, value in settings.items():
        _set_setting(profile.name, key, value)
    return cashier_features.get_settings(profile.name)


def set_allow_in_returns(mode, allowed):
    _branch, profile = _profile()
    frappe.db.set_value("POS Payment Method", {"parent": profile.name, "mode_of_payment": mode},
                        "allow_in_returns", 1 if allowed else 0)
    frappe.local._ozturk_payment_methods = {}


def close_real_shift_state():
    """Ochiq smena bormi (xato tashxisi uchun)."""
    scope = cashier_permissions.resolve_scope(CASHIER)
    return {"open_shift": cashier_permissions.open_shift_name(scope), "cash_modes": cashier_billing.cash_modes(scope.pos_profile)}


def request_bill_as_waiter(invoice):
    """Ofitsant "hisob so'radi" — haqiqiy `waiter.request_bill` (rol WAITER)."""
    from ozturkapp.ozturkapp.api import waiter

    frappe.set_user(WAITER)
    return waiter.request_bill(invoice)


def invoice_truth(name):
    """Chekning DB'dagi haqiqati: jami, soliq/chegirma/choychaqa qatorlari, to'lovlar."""
    doc = frappe.get_doc("POS Invoice", name)
    meta = doc.as_dict()
    return {
        "name": doc.name,
        "docstatus": doc.docstatus,
        "status": doc.status,
        "table": doc.get("restaurant_table"),
        "merged": doc.get("custom_merged_tables"),
        "order_type": doc.get("order_type"),
        "customer": doc.customer,
        "net_total": flt(doc.net_total),
        "total": flt(doc.total),
        "grand_total": flt(doc.grand_total),
        "rounded_total": flt(doc.rounded_total),
        "discount_amount": flt(doc.discount_amount),
        "discount_percent": flt(doc.additional_discount_percentage),
        "paid_amount": flt(doc.paid_amount),
        "change_amount": flt(doc.change_amount),
        "printed": doc.get("invoice_printed"),
        "is_return": doc.is_return,
        "return_against": doc.return_against,
        "cancelled": doc.get("custom_cancelled"),
        "bill_requested": doc.get("custom_bill_requested"),
        "reprint_needed": meta.get("custom_reprint_needed"),
        "items": [{"item": i.item_code, "qty": flt(i.qty), "rate": flt(i.rate), "amount": flt(i.amount)}
                  for i in doc.items],
        "taxes": [{"desc": t.description, "account": t.account_head, "amount": flt(t.tax_amount),
                   "type": t.charge_type} for t in doc.taxes],
        "payments": [{"mode": p.mode_of_payment, "amount": flt(p.amount)} for p in doc.payments],
    }


def reset_shift():
    """Joriy ochiq smenani (tranzaksiya ichida) yopiq deb belgilaydi — yangi smena sinovi uchun."""
    _branch, profile = _profile()
    frappe.db.sql(
        "update `tabPOS Opening Entry` set status = 'Closed' where pos_profile = %s and status = 'Open'",
        profile.name,
    )
    frappe.local._ozturk_scope_cache = {}


OPS = {
    "reset_shift": reset_shift,
    "setup_base": setup_base,
    "features": set_features,
    "settings": set_settings,
    "allow_in_returns": set_allow_in_returns,
    "shift_state": close_real_shift_state,
    "waiter_request_bill": request_bill_as_waiter,
    "invoice": invoice_truth,
}
