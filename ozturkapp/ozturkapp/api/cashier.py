# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa oynasi — kontekst API (TZ §27).

Sahifa ochilganda BIR MARTA chaqiriladigan metodlar. Keyingi yangilanishlar
realtime orqali keladi, butun bazani qayta o'qish yo'q (TZ §25).

Bu fayl faqat "kim, qayerda, qanday sozlama bilan ishlayapti" degan savolga
javob beradi. Stol, buyurtma va hisob mantig'i alohida modullarda:

    api/table.py    — zal rejasi va stollar
    api/order.py    — faol buyurtmalar
    api/billing.py  — hisob va to'lov
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt

from ozturkapp.ozturkapp.utils import cashier_billing, cashier_permissions, table_status
from ozturkapp.ozturkapp.utils.cashier_realtime import EVENT_FLOOR, EVENT_ORDER
from ozturkapp.ozturkapp.utils.kitchen_realtime import EVENT_ITEM
from ozturkapp.ozturkapp.utils.notifications import EVENT_NOTIFY
from ozturkapp.ozturkapp.utils.table_status import STATUSES


@frappe.whitelist()
def get_cashier_context():
    """Kassa sahifasining boshlang'ich konteksti.

    Returns:
        dict: restoran, filial, xonalar, kassir, smena, valyuta, xizmat haqi,
              ruxsatlar va realtime kanal nomlari.
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()

    restaurant = frappe.db.get_value(
        "URY Restaurant",
        scope.restaurant,
        ["name", "company", "branch", "default_room", "active_menu", "default_tax_template"],
        as_dict=True,
    )

    shift = _get_shift(scope)
    service_charge = cashier_billing.get_service_charge_config(scope.restaurant)

    return {
        "restaurant": restaurant,
        "branch": scope.branch,
        "company": scope.company,
        "currency": scope.currency,
        "pos_profile": scope.pos_profile,
        "default_customer": scope.default_customer,
        "rooms": get_restaurant_rooms(),
        "cashier": {
            "user": frappe.session.user,
            "full_name": frappe.db.get_value("User", frappe.session.user, "full_name"),
        },
        "shift": shift,
        "service_charge": service_charge,
        "payment_methods": cashier_billing.get_payment_methods(scope.pos_profile),
        # Kassir QO'LDA sanaydigan usullar — smena ochish/yopishda faqat shular.
        "cash_modes": _cash_modes(scope.pos_profile),
        # Mijoz cheki uchun chop etish formati (POS Profile'dan, bo'lmasa standart).
        "print_format": frappe.db.get_value("POS Profile", scope.pos_profile, "print_format")
        or "POS Invoice",
        "permissions": {
            "can_bill": _can_bill(scope.pos_profile),
            "is_supervisor": cashier_permissions.has_supervisor_role(),
            # Kassani ochish/yopish — faqat POS Profile'ga biriktirilgan
            # kassir. Boshqa foydalanuvchiga (Administrator, menejer)
            # sahifa ochiladi va holat ko'rinadi, lekin ochish formasi
            # UMUMAN chizilmaydi.
            "can_operate_shift": cashier_permissions.can_operate_shift(scope.pos_profile),
        },
        # Ekranda "Kassani faqat {0} ochadi" deb aytish uchun.
        "shift_operators": cashier_permissions.shift_operator_names(scope.pos_profile),
        "statuses": list(STATUSES),
        "events": {
            "floor": EVENT_FLOOR,
            "order": EVENT_ORDER,
            # OSHXONA HOLATI
            # ==============
            # Oshpaz taom holatini o'zgartirganda POS Invoice'ga TEGILMAYDI,
            # ya'ni `ozturk_cashier_order` chiqmaydi. Kassa esa chek
            # panelida "🍳 Tayyorlanmoqda (1/3)" ni ko'rsatadi — bu maydon
            # shu kanalsiz qo'lda yangilanmaguncha qotib qolardi.
            "kitchen_item": EVENT_ITEM,
            # Ofitsant hisob so'raganda KO'RINADIGAN xabar.
            "notify": EVENT_NOTIFY,
        },
        "server_time": frappe.utils.now(),
        # Sozlash bo'shliqlarini kassir emas, menejer ko'rishi uchun.
        "warnings": _config_warnings(scope, restaurant, service_charge),
    }


@frappe.whitelist()
def get_restaurant_rooms():
    """Joriy filialdagi zallar + har biridagi stollar soni."""
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()

    rooms = frappe.get_all(
        "URY Room",
        filters={"branch": scope.branch},
        fields=["name", "branch", "room_type"],
        order_by="name asc",
    )

    counts = {}
    for row in frappe.get_all(
        "URY Table",
        filters={"branch": scope.branch},
        fields=["restaurant_room", "count(name) as total"],
        group_by="restaurant_room",
    ):
        counts[row.restaurant_room] = cint(row.total)

    for room in rooms:
        room["table_count"] = counts.get(room.name, 0)

    return rooms


# ═══════════════════════════════════════════════════════════════════
#  Smena (POS Opening Entry)
# ═══════════════════════════════════════════════════════════════════

def _get_shift(scope) -> dict:
    """Ochiq smena ma'lumoti.

    Smena bo'lmasa `open: False` qaytariladi — kassa sahifasi buni aniq
    ko'rsatadi, chunki smenasiz chek yaratib ham, to'lab ham bo'lmaydi
    (ERPNext `validate_pos_opening_entry` buni majburlaydi).
    """
    from ozturkapp.ozturkapp.api.desktop_pos import _get_user_room, _open_opening_entry

    room = ""
    try:
        room = _get_user_room(scope.branch)
    except Exception:
        # `URY User.room` bo'sh bo'lishi mumkin — smena qidiruvi busiz ham ishlaydi.
        pass

    opening = _open_opening_entry(scope.branch, scope.pos_profile, room)
    if not opening:
        return {"open": False, "name": None, "user": None, "opened_at": None}

    row = frappe.db.get_value(
        "POS Opening Entry",
        opening,
        ["name", "user", "period_start_date", "posting_date", "status"],
        as_dict=True,
    )
    return {
        "open": True,
        "name": row.name,
        "user": row.user,
        "user_name": frappe.db.get_value("User", row.user, "full_name") or row.user,
        "opened_at": str(row.period_start_date or row.posting_date or ""),
        "status": row.status,
    }


def _can_bill(pos_profile: str) -> bool:
    try:
        cashier_permissions.assert_can_bill(pos_profile)
        return True
    except frappe.PermissionError:
        return False


# ═══════════════════════════════════════════════════════════════════
#  Sozlash bo'shliqlari
# ═══════════════════════════════════════════════════════════════════

def _config_warnings(scope, restaurant, service_charge) -> list:
    """Kassani ishlashdan to'xtatmaydigan, lekin sozlanishi kerak bo'lgan holatlar."""
    warnings = []

    if not service_charge.get("enabled"):
        warnings.append(
            {
                "code": "NO_SERVICE_CHARGE",
                "message": _(
                    "Xizmat haqi sozlanmagan. Sozlash uchun: "
                    "bench --site {0} execute "
                    "ozturkapp.ozturkapp.setup.service_charge.setup"
                ).format(frappe.local.site),
            }
        )

    if not frappe.db.count("URY Table", {"branch": scope.branch}):
        warnings.append(
            {
                "code": "NO_TABLES",
                "message": _("Bu filialda birorta ham stol yaratilmagan."),
            }
        )

    # Tannarxsiz zaxira tovari bo'lsa SMENA YOPILMAYDI — buni smena
    # oxirida emas, BOSHIDA bilish kerak.
    from ozturkapp.ozturkapp.setup.item_costs import missing_costs

    gaps = missing_costs(scope.restaurant)
    if gaps:
        warnings.append(
            {
                "code": "NO_VALUATION_RATE",
                "message": _(
                    "{0} ta taomda tannarx (valuation rate) kiritilmagan. "
                    "Kiritilmasa kassani yopib bo'lmaydi. Masalan: {1}"
                ).format(len(gaps), ", ".join(g["item_name"] for g in gaps[:3])),
            }
        )

    if not restaurant.get("active_menu"):
        warnings.append(
            {
                "code": "NO_MENU",
                "message": _(
                    "Restoranda faol menyu tanlanmagan — URY POS buyurtma qabul qila olmaydi."
                ),
            }
        )

    return warnings


# ═══════════════════════════════════════════════════════════════════
#  Kassa smenasi — ochish va yopish
# ═══════════════════════════════════════════════════════════════════
#
# MANTIQ QAYTA YOZILMAYDI
# =======================
# Smena ochish/yopish mantig'i `api/desktop_pos.py` da allaqachon bor va
# Desktop POS (PyQt) tomonidan ishlatilib kelinmoqda. U URY'ning murakkab
# ko'p kassirli rejimini (`Sub POS Closing`, asosiy/yordamchi kassir) ham
# to'g'ri qamraydi.
#
# Shuning uchun bu yerda faqat YUPQA O'RAM: kassa roli tekshiriladi va
# o'sha funksiyalar chaqiriladi. Ikkinchi implementatsiya yaratilmaydi.


@frappe.whitelist()
def open_shift(balance_details):
    """Kassa smenasini ochish (POS Opening Entry).

    FAQAT NAQD PUL
    ==============
    Kassir smena boshida qo'lidagi NAQD pulni sanaydi. Bank/karta bo'yicha
    "boshlang'ich qoldiq" tushunchasi yo'q — u pul kassada emas, bankda
    turadi va uni kassir sanay olmaydi. Shuning uchun boshqa usullar
    kiritilmaydi va SERVERDA rad etiladi (frontend'ga ishonmaymiz).

    Bu yopilishga xalal bermaydi: ERPNext `make_closing_entry_from_opening()`
    da ochilishda bo'lmagan usulni chek uchragan zahoti `opening_amount = 0`
    bilan o'zi qo'shadi. Ya'ni karta sotuvlari solishtiruvda baribir chiqadi
    — o'zining to'g'ri boshlang'ich qoldig'i (nol) bilan.

    Args:
        balance_details: `[{"mode_of_payment": str, "opening_amount": float}]`
            — faqat naqd usullar.

    Returns:
        dict: yangi smena + yangilangan kontekst.
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_permissions.assert_shift_operator(scope.pos_profile, _("ochishni"))

    from ozturkapp.ozturkapp.api.desktop_pos import createPosOpening

    result = createPosOpening(
        pos_profile=scope.pos_profile,
        company=scope.company,
        balance_details=_opening_balance_details(balance_details, scope.pos_profile),
    )
    result["shift"] = _get_shift(scope)
    return result


def _opening_balance_details(balance_details, pos_profile: str) -> list:
    """Ochilish qatorlarini naqd usullar bilan cheklaydi va tekshiradi."""
    if isinstance(balance_details, str):
        try:
            balance_details = json.loads(balance_details)
        except ValueError:
            frappe.throw(_("Boshlang'ich summa noto'g'ri formatda"))

    if not isinstance(balance_details, list):
        frappe.throw(_("Boshlang'ich summa ro'yxat ko'rinishida bo'lishi kerak"))

    allowed = _cash_modes(pos_profile)
    if not allowed:
        frappe.throw(
            _(
                "POS Profile'da naqd to'lov usuli sozlanmagan. "
                "«Mode of Payment» turi «Cash» bo'lgan usul qo'shing."
            ),
            title=_("Naqd usul yo'q"),
        )

    entered = {}
    for row in balance_details or []:
        mode = (row or {}).get("mode_of_payment")
        if not mode:
            continue
        if mode not in allowed:
            frappe.throw(
                _("'{0}' naqd to'lov usuli emas — ochilishda kiritilmaydi").format(mode),
                title=_("Noto'g'ri to'lov usuli"),
            )
        amount = flt((row or {}).get("opening_amount"))
        if amount < 0:
            frappe.throw(_("Summa manfiy bo'lishi mumkin emas"))
        entered[mode] = amount

    missing = set(allowed) - set(entered)
    if missing:
        frappe.throw(
            _("Quyidagi usullar bo'yicha summa kiritilmagan: {0}").format(
                ", ".join(sorted(missing))
            )
        )

    return [
        {"mode_of_payment": mode, "opening_amount": entered[mode]} for mode in allowed
    ]


#: Kassani yopishdan oldin majburiy qayta sanash vaqti (soniya).
#: Kassir bu vaqt ichida qo'lidagi pulni qayta sanaydi.
CLOSING_COUNTDOWN_SECONDS = 60


def _cash_modes(pos_profile: str) -> list:
    """Kassir QO'LDA sanaydigan to'lov usullari (naqd pul).

    Bank/karta summalari terminal yozuvlaridan kelib chiqadi va kassir
    ularni kiritmaydi — ular avtomatik to'ldiriladi.
    """
    modes = []
    for method in cashier_billing.get_payment_methods(pos_profile):
        if (method.get("type") or "").strip().lower() == "cash":
            modes.append(method["mode_of_payment"])
    return modes


def _open_order_count(scope) -> int:
    """Kassirni smenani yopishdan to'xtatadigan buyurtmalar soni.

    NEGA `frappe.db.count("POS Invoice", {"docstatus": 0}) EMAS`
    ===========================================================
    Bekor qilingan chek O'CHIRILMAYDI — u `docstatus = 0` bo'lib qoladi va
    faqat `custom_cancelled = 1` bilan belgilanadi
    (`utils/order_cancel.py`). Xom `docstatus = 0` sanog'i ularni ham
    qo'shib yuboradi va kassa yopilmay qoladi:

        «Filialda 2 ta to'lanmagan buyurtma bor» — lekin kassirning
        buyurtmalar ro'yxati BO'SH, chunki u `custom_cancelled = 0`
        bo'yicha filtrlangan. Kassir nimani yopishni topa olmaydi.

    Shuning uchun sanoq ham, ro'yxat ham AYNAN bitta manbadan olinadi:
    `table_status.get_open_orders()`.
    """
    return len(table_status.get_open_orders(scope.branch))


@frappe.whitelist()
def get_shift_closing_data():
    """Kassani yopish oynasi uchun ma'lumot.

    KO'R SANOQ (blind count)
    ========================
    Kassirga UMUMIY SAVDO ham, KUTILAYOTGAN SUMMA ham ko'rsatilmaydi.
    U qo'lidagi naqd pulni bilmagan holda sanab kiritadi — shundagina
    sanoq haqiqiy nazorat vazifasini bajaradi. Farqni server hisoblaydi.

    Shuning uchun bu javobda `grand_total` va `expected_amount` YO'Q.
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()

    shift = _get_shift(scope)
    if not shift.get("open"):
        frappe.throw(_("Ochiq smena yo'q"), title=_("Smena yopiq"))

    from ozturkapp.ozturkapp.api.desktop_pos import getPosClosingData

    data = getPosClosingData(shift["name"])

    return {
        "pos_opening_entry": shift["name"],
        "opened_at": shift.get("opened_at"),
        "opened_by": shift.get("user_name"),
        "currency": scope.currency,
        # Kassirga ruxsat etilgan yagona raqam.
        "total_invoices": data.get("total_invoices", 0),
        # Sanaladigan usullar (odatda bitta — "Cash").
        "cash_modes": _cash_modes(scope.pos_profile),
        "countdown_seconds": CLOSING_COUNTDOWN_SECONDS,
        "open_orders": _open_order_count(scope),
    }


@frappe.whitelist()
def close_shift(counted_cash):
    """Kassa smenasini yopish.

    Args:
        counted_cash: `{"Cash": 1250000}` — kassir SANAGAN naqd pul.
            Faqat naqd usullar qabul qilinadi.

    Kutilayotgan summalar va naqd bo'lmagan usullar SERVERDA to'ldiriladi —
    mijozdan kelgan qiymatlarga ishonilmaydi.
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_permissions.assert_shift_operator(scope.pos_profile, _("yopishni"))

    shift = _get_shift(scope)
    if not shift.get("open"):
        frappe.throw(_("Ochiq smena yo'q"), title=_("Smena yopiq"))

    # To'lanmagan buyurtma qolgan bo'lsa smenani yopish MUMKIN EMAS —
    # aks holda o'sha buyurtmalar hisobotdan tushib qoladi.
    pending = _open_order_count(scope)
    if pending:
        frappe.throw(
            _(
                "Filialda {0} ta to'lanmagan buyurtma bor. "
                "Kassani yopishdan oldin ularni yakunlang yoki bekor qiling."
            ).format(pending),
            title=_("Yopilmagan buyurtmalar"),
        )

    # ── Tannarx tekshiruvi (ERPNext niqoblashidan OLDIN) ──────────────
    # Tannarxsiz tovar bo'lsa ERPNext konsolidatsiyada yiqiladi va xatoni
    # `except` blokida yutib, chalg'ituvchi «Could not find Reference
    # Name: POS-CLO-...» xabarini beradi. Shuning uchun sababni O'ZIMIZ,
    # tushunarli qilib aytamiz.
    from ozturkapp.ozturkapp.setup.item_costs import missing_costs

    gaps = missing_costs(scope.restaurant)
    if gaps:
        names = ", ".join(g["item_name"] for g in gaps[:5])
        more = _(" va yana {0} ta").format(len(gaps) - 5) if len(gaps) > 5 else ""
        frappe.throw(
            _(
                "Quyidagi taomlarda tannarx (valuation rate) kiritilmagan: {0}{1}.\n\n"
                "Sotuv omborni kamaytirgani uchun ERPNext tannarx provodkasini "
                "yoza olmaydi va kassa yopilmaydi. Tannarxni Item kartochkasiga "
                "kiriting yoki menejerga murojaat qiling."
            ).format(names, more),
            title=_("Tannarx kiritilmagan"),
        )

    counted = _parse_counted_cash(counted_cash, scope.pos_profile)

    # ── Solishtiruv jadvalini SERVER quradi ───────────────────────────
    from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import (
        make_closing_entry_from_opening,
    )

    opening = frappe.get_doc("POS Opening Entry", shift["name"])
    expected = make_closing_entry_from_opening(opening)

    cash_modes = set(_cash_modes(scope.pos_profile))
    reconciliation = []

    for row in expected.get("payment_reconciliation") or []:
        if row.mode_of_payment in cash_modes:
            # Naqd — kassir sanagan summa.
            closing_amount = counted.get(row.mode_of_payment, 0.0)
        else:
            # Bank/karta — terminal yozuvi bo'yicha, kassir kiritmaydi.
            closing_amount = flt(row.expected_amount)

        reconciliation.append(
            {
                "mode_of_payment": row.mode_of_payment,
                "opening_amount": flt(row.opening_amount),
                "expected_amount": flt(row.expected_amount),
                "closing_amount": flt(closing_amount),
            }
        )

    from ozturkapp.ozturkapp.api.desktop_pos import createPosClosing

    result = createPosClosing(
        pos_opening_entry=shift["name"],
        payment_reconciliation=reconciliation,
    )
    result["shift"] = _get_shift(scope)
    return result


def _parse_counted_cash(counted_cash, pos_profile: str) -> dict:
    """Kassir kiritgan naqd summani tekshiradi.

    Faqat NAQD usullar qabul qilinadi — kassir bank summasini yubora
    olmasligi kerak (TZ: "kassir faqat naqd pulni kiritadi").
    """
    if isinstance(counted_cash, str):
        try:
            counted_cash = json.loads(counted_cash)
        except ValueError:
            frappe.throw(_("Sanalgan summa noto'g'ri formatda"))

    if isinstance(counted_cash, (int, float)):
        # Bitta naqd usul bo'lsa oddiy raqam ham qabul qilinadi.
        modes = _cash_modes(pos_profile)
        if len(modes) != 1:
            frappe.throw(_("Har bir naqd usul uchun summa ko'rsating"))
        counted_cash = {modes[0]: counted_cash}

    if not isinstance(counted_cash, dict) or not counted_cash:
        frappe.throw(_("Naqd pul summasi kiritilmagan"))

    allowed = set(_cash_modes(pos_profile))
    parsed = {}

    for mode, amount in counted_cash.items():
        if mode not in allowed:
            frappe.throw(
                _("'{0}' naqd to'lov usuli emas — kassir uni kiritmaydi").format(mode),
                title=_("Noto'g'ri to'lov usuli"),
            )
        value = flt(amount)
        if value < 0:
            frappe.throw(_("Summa manfiy bo'lishi mumkin emas"))
        parsed[mode] = value

    missing = allowed - set(parsed)
    if missing:
        frappe.throw(
            _("Quyidagi usullar bo'yicha summa kiritilmagan: {0}").format(
                ", ".join(sorted(missing))
            )
        )

    return parsed
