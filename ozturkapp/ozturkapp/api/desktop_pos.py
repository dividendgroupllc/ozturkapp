"""URY Desktop POS — server API kengaytmasi.

Ushbu modul Desktop POS (PyQt6) ilovasi chaqiradigan, upstream
`ury.ury_pos.api` da mavjud bo'lmagan whitelisted metodlarni beradi.

╔══════════════════════════════════════════════════════════════════════╗
║  DIQQAT — kod `ozturkapp` da, endpoint nomi esa `ury...`             ║
║                                                                      ║
║  POS mijozi metodlarni `ury.ury_pos.api.<metod>` yo'li bilan         ║
║  chaqiradi. Ular shu faylga `hooks.override_whitelisted_methods`     ║
║  orqali yo'naltiriladi (`frappe/handler.py:67` — satr `get_attr`     ║
║  dan OLDIN almashtiriladi, shuning uchun upstream'da mavjud          ║
║  bo'lmagan nomni ham yo'naltirish mumkin).                           ║
║                                                                      ║
║  Sabab: `ury` upstream repo, unga push qila olmaymiz. Kod shu        ║
║  yerda tursa, `ury` toza qoladi va yangilanishlari muammosiz         ║
║  tortiladi; POS mijozini esa qayta yig'ish shart emas.               ║
║                                                                      ║
║  YANGI METOD QO'SHSANGIZ — `ozturkapp/hooks.py` dagi                 ║
║  `DESKTOP_POS_METHODS` ro'yxatiga ham qo'shing, aks holda POS        ║
║  «has no attribute» (HTTP 417) xatosini oladi.                       ║
║                                                                      ║
║  Deploy'dan keyin `bench clear-cache` MAJBURIY — hook'lar keshda.    ║
╚══════════════════════════════════════════════════════════════════════╝

Guruhlar:
    1. Kassa smenasi   — checkPosOpening, createPosOpening,
                         getPosClosingData, createPosClosing
    2. Kutilayotgan    — getPendingOrders, getPendingOrderCounts,
       buyurtmalar       getPendingOrderDetail, cancelPendingOrder
    3. Stol / xona     — getTables, getRoomsForBranch, freeTable,
                         cleanupOrphanTables
    4. Sozlamalar      — get_pos_cashiers, get_printer_config,
                         save_pos_quick_items
    5. Menyu tartibi   — saveMenuCourseOrder, saveMenuItemOrder
    6. Profil          — getPosProfile (upstream ustiga qo'shimcha maydonlar)
"""

import json
import math

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_datetime, now_datetime, nowdate

__all__ = [
    "checkPosOpening",
    "createPosOpening",
    "getPosClosingData",
    "createPosClosing",
    "getPendingOrders",
    "getPendingOrderCounts",
    "getPendingOrderDetail",
    "cancelPendingOrder",
    "getTables",
    "getRoomsForBranch",
    "freeTable",
    "cleanupOrphanTables",
    "get_pos_cashiers",
    "get_printer_config",
    "save_pos_quick_items",
    "saveMenuCourseOrder",
    "saveMenuItemOrder",
    "getPosProfile",
    # ── Dinamik narxlash ─────────────────────────────
    "getPricingVersion",
    "getPricingSnapshot",
    "getPricingSettings",
    "getPriceHistory",
    "getPricingAlerts",
    "verifyCartPrices",
    "recalcPricing",
    "savePricingSettings",
    "setBasePrices",
    "setItemPriceLock",
    "revertPricingToBase",
    "seedPricingDemo",
    "simulatePricing",
]


# ═══════════════════════════════════════════════════════════════════
#  Ichki yordamchilar
# ═══════════════════════════════════════════════════════════════════

#: Desktop POS `order_type` chip'lari uchun ishlatiladigan qiymatlar.
#: `Dastavka` va `Dastavka Saboy` ikkalasi ham serverda `Delivery`.
ORDER_TYPES = ("Dine In", "Take Away", "Delivery", "Phone In", "Aggregators")

#: Mijoz chekining standart sozlamalari — POS Profile bo'sh qoldirsa.
DEFAULT_PRINTER_DRIVER = "ESC/POS"
DEFAULT_PRINTER_WIDTH = 80
DEFAULT_PRINTER_CODEPAGE = "CP1251"

#: Stol necha soat band tursa "orphan" hisoblanadi (ochiq invoice'siz).
ORPHAN_TABLE_HOURS = 12

#: Stolni qo'lda bo'shatish / buyurtmani bekor qilish huquqi bo'lmagan rollar.
RESTRICTED_ROLES = ("Ofitsant", "Waiter")


def _loads(value, default):
    """HTTP orqali kelgan JSON qiymatni Python obyektiga aylantirish.

    Desktop POS ba'zi argumentlarni `json.dumps` bilan (string), ba'zilarini
    esa to'g'ridan-to'g'ri JSON body ichida (list/dict) yuboradi — ikkala
    holatni ham qo'llab-quvvatlaymiz.
    """
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _get_branch() -> str:
    """Joriy foydalanuvchi biriktirilgan filial (upstream `getBranch`)."""
    from ury.ury_pos import api as _api

    return _api.getBranch()


def _get_pos_profile_name(branch: str = None) -> str:
    """Filialga tegishli POS Profile nomi."""
    branch = branch or _get_branch()
    profile = frappe.db.exists("POS Profile", {"branch": branch, "disabled": 0})
    if not profile:
        profile = frappe.db.exists("POS Profile", {"branch": branch})
    if not profile:
        frappe.throw(_("'{0}' filiali uchun POS Profile topilmadi").format(branch))
    return profile


def _get_user_room(branch: str = None) -> str:
    """Foydalanuvchiga biriktirilgan xona (`URY User.room`), bo'lmasa ''."""
    return (
        frappe.db.get_value(
            "URY User",
            {"user": frappe.session.user, "parenttype": "Branch"},
            "room",
        )
        or ""
    )


def _assert_not_restricted(role: str, action: str):
    """Ofitsant rolidagi xodimga taqiqlangan amallarni bloklash."""
    if (role or "").strip() in RESTRICTED_ROLES:
        frappe.throw(_("{0} uchun ruxsat yo'q: {1}").format(role, action))


def _open_opening_entry(branch: str, profile: str, room: str = ""):
    """Filial uchun ochiq POS Opening Entry hujjatini topish.

    Multiple-cashier rejimida bitta smenani bir nechta kassir bo'lishadi,
    shuning uchun avval joriy foydalanuvchi ochgan smena, keyin esa shu
    xona/filialdagi istalgan ochiq smena qidiriladi.
    """
    base = {"status": "Open", "docstatus": 1, "pos_profile": profile}

    own = frappe.db.get_value("POS Opening Entry", dict(base, user=frappe.session.user), "name")
    if own:
        return own

    if room:
        shared = frappe.db.sql(
            """
            SELECT DISTINCT poe.name
            FROM `tabPOS Opening Entry` poe
            INNER JOIN `tabMultiple Rooms` mr ON mr.parent = poe.name
            WHERE poe.status = 'Open' AND poe.docstatus = 1
              AND poe.pos_profile = %s AND mr.room = %s
            ORDER BY poe.creation DESC
            LIMIT 1
            """,
            (profile, room),
            as_dict=True,
        )
        if shared:
            return shared[0].name

    return frappe.db.get_value("POS Opening Entry", base, "name")


def _assert_not_finalized(opening):
    """Smena yakuniy `POS Closing Entry` bilan yopilmaganini tekshirish.

    `POS Opening Entry.status` mezon sifatida yaramaydi: ko'p kassirli
    rejimda `Sub POS Closing` submit qilinishi bilan URY hook'i smenani
    darhol `Closed` qilib qo'yadi, biroq asosiy kassir hali yakuniy
    hujjatni yopmagan bo'ladi.
    """
    finalized = frappe.db.exists(
        "POS Closing Entry", {"pos_opening_entry": opening.name, "docstatus": ["<", 2]}
    )
    if finalized:
        frappe.throw(
            _("'{0}' smenasi allaqachon '{1}' bilan yopilgan").format(opening.name, finalized)
        )
    if opening.docstatus != 1:
        frappe.throw(_("'{0}' smenasi tasdiqlanmagan").format(opening.name))


def _pending_filters(only_mine=0, mine_cashier_name="", order_type=None) -> dict:
    """Draft POS Invoice'lar uchun umumiy filtr to'plami.

    `getPendingOrders` va `getPendingOrderCounts` bir xil to'plamdan
    foydalanishi shart — aks holda ro'yxat va badge raqami mos kelmaydi.
    """
    branch = _get_branch()
    filters = {
        "docstatus": 0,
        "branch": branch,
        "custom_cancelled": 0,
    }
    if cint(only_mine) and mine_cashier_name:
        filters["custom_active_cashier"] = mine_cashier_name
    if order_type:
        filters["order_type"] = order_type
    return filters


# ═══════════════════════════════════════════════════════════════════
#  1. Kassa smenasi
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def checkPosOpening():
    """Joriy kassir uchun ochiq kassa smenasi bor-yo'qligini tekshirish.

    Returns:
        dict: `{"status": "open"|"closed", "opening_entry": str,
                "pos_profile": str, "company": str}`
    """
    branch = _get_branch()
    profile = _get_pos_profile_name(branch)
    room = _get_user_room(branch)

    entry = _open_opening_entry(branch, profile, room)
    if not entry:
        return {"status": "closed", "opening_entry": "", "pos_profile": profile}

    company = frappe.db.get_value("POS Opening Entry", entry, "company")
    return {
        "status": "open",
        "opening_entry": entry,
        "pos_profile": profile,
        "company": company,
    }


@frappe.whitelist()
def createPosOpening(pos_profile, company, balance_details):
    """Kassa smenasini ochish — POS Opening Entry yaratib submit qilish.

    Args:
        pos_profile: POS Profile nomi.
        company: Kompaniya nomi.
        balance_details: `[{"mode_of_payment": str, "opening_amount": float}]`
            (JSON string yoki list).

    Returns:
        dict: `{"name": str, "status": "open"}`
    """
    branch = _get_branch()
    room = _get_user_room(branch)
    details = _loads(balance_details, [])

    existing = _open_opening_entry(branch, pos_profile, room)
    if existing:
        # Idempotent: smena allaqachon ochiq bo'lsa dublikat yaratmaymiz.
        return {"name": existing, "status": "open", "already_open": True}

    doc = frappe.new_doc("POS Opening Entry")
    doc.period_start_date = now_datetime()
    doc.posting_date = nowdate()
    doc.user = frappe.session.user
    doc.pos_profile = pos_profile
    doc.company = company
    doc.branch = branch

    if room:
        doc.custom_room = room
        doc.append("custom_rooms", {"room": room})

    restaurant = frappe.db.get_value("POS Profile", pos_profile, "restaurant")
    if restaurant:
        doc.restaurant = restaurant

    for row in details:
        mode = (row or {}).get("mode_of_payment")
        if not mode:
            continue
        doc.append(
            "balance_details",
            {"mode_of_payment": mode, "opening_amount": flt((row or {}).get("opening_amount"))},
        )

    if not doc.balance_details:
        frappe.throw(_("Kamida bitta to'lov turi ko'rsatilishi kerak"))

    doc.insert(ignore_permissions=True)
    doc.submit()

    return {"name": doc.name, "status": "open"}


@frappe.whitelist()
def getPosClosingData(pos_opening_entry):
    """Kassani yopish oynasi uchun kutilayotgan summalarni hisoblash.

    ERPNext'ning `make_closing_entry_from_opening` yordamchisidan
    foydalanadi — soliq va to'lov turlari bo'yicha jamlash mantig'i
    standart hisobot bilan bir xil bo'lishi uchun.

    Returns:
        dict: `{"total_invoices": int, "grand_total": float,
                "reconciliation": [{"mode_of_payment", "opening_amount",
                                    "expected_amount"}]}`
    """
    from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import (
        make_closing_entry_from_opening,
    )

    opening = frappe.get_doc("POS Opening Entry", pos_opening_entry)
    _assert_not_finalized(opening)

    closing = make_closing_entry_from_opening(opening)

    return {
        "pos_opening_entry": pos_opening_entry,
        "pos_profile": opening.pos_profile,
        "company": opening.company,
        "period_start_date": str(opening.period_start_date or ""),
        "total_invoices": len(closing.get("pos_transactions") or []),
        "grand_total": flt(closing.grand_total),
        "net_total": flt(closing.net_total),
        "total_quantity": flt(closing.total_quantity),
        "reconciliation": [
            {
                "mode_of_payment": p.mode_of_payment,
                "opening_amount": flt(p.opening_amount),
                "expected_amount": flt(p.expected_amount),
            }
            for p in (closing.get("payment_reconciliation") or [])
        ],
    }


def _is_main_cashier(pos_profile: str, user: str = None) -> bool:
    """Foydalanuvchi shu profilning asosiy kassiri (`custom_main_cashier`) mi."""
    user = user or frappe.session.user
    doc = frappe.get_doc("POS Profile", pos_profile)
    for row in doc.applicable_for_users:
        if row.user == user:
            return bool(cint(row.get("custom_main_cashier")))
    return False


def _create_sub_closing(opening, entered: dict):
    """Joriy kassir uchun `Sub POS Closing` yaratib submit qilish.

    URY multiple-cashier oqimida har bir kassir smenadagi o'z ulushini
    Sub POS Closing bilan yopadi; asosiy kassir esa yakuniy POS Closing
    Entry'ni yaratadi va `calculate_closing_amount` hook'i sub summalarni
    unga qo'shib hisoblaydi.

    Agar joriy kassir uchun submit qilingan Sub POS Closing allaqachon
    mavjud bo'lsa, qaytadan yaratilmaydi (idempotent).
    """
    user = frappe.session.user
    existing = frappe.db.get_value(
        "Sub POS Closing",
        {"pos_opening_entry": opening.name, "user": user, "docstatus": 1},
        "name",
    )
    if existing:
        return frappe.get_doc("Sub POS Closing", existing)

    invoices = frappe.get_all(
        "POS Invoice",
        filters={
            "docstatus": 1,
            "pos_profile": opening.pos_profile,
            "owner": user,
            "consolidated_invoice": ["in", ["", None]],
            "posting_date": [">=", opening.period_start_date],
        },
        fields=["name", "posting_date", "grand_total", "net_total", "total_qty"],
    )

    sub = frappe.new_doc("Sub POS Closing")
    sub.pos_opening_entry = opening.name
    sub.pos_profile = opening.pos_profile
    sub.company = opening.company
    sub.user = user
    sub.period_start_date = opening.period_start_date
    sub.period_end_date = now_datetime()
    sub.posting_date = nowdate()
    sub.grand_total = sum(flt(i.grand_total) for i in invoices)
    sub.net_total = sum(flt(i.net_total) for i in invoices)
    sub.total_quantity = sum(flt(i.total_qty) for i in invoices)

    for inv in invoices:
        sub.append(
            "pos_transactions",
            {
                "pos_invoice": inv.name,
                "posting_date": inv.posting_date,
                "grand_total": flt(inv.grand_total),
            },
        )

    # To'lov turlari bo'yicha kutilgan summalar — kassirning o'z cheklaridan.
    expected = {}
    for inv in invoices:
        for p in frappe.get_all(
            "Sales Invoice Payment",
            filters={"parent": inv.name, "parenttype": "POS Invoice"},
            fields=["mode_of_payment", "amount"],
        ):
            expected[p.mode_of_payment] = expected.get(p.mode_of_payment, 0) + flt(p.amount)

    for row in opening.balance_details:
        exp = flt(expected.pop(row.mode_of_payment, 0))
        closing_amount = entered.get(row.mode_of_payment, exp)
        sub.append(
            "payment_reconciliation",
            {
                "mode_of_payment": row.mode_of_payment,
                "opening_amount": flt(row.opening_amount),
                "expected_amount": exp,
                "closing_amount": closing_amount,
                "difference": closing_amount - exp,
            },
        )
    for mode, exp in expected.items():
        closing_amount = entered.get(mode, flt(exp))
        sub.append(
            "payment_reconciliation",
            {
                "mode_of_payment": mode,
                "opening_amount": 0,
                "expected_amount": flt(exp),
                "closing_amount": closing_amount,
                "difference": closing_amount - flt(exp),
            },
        )

    sub.insert(ignore_permissions=True)
    sub.submit()
    return sub


@frappe.whitelist()
def createPosClosing(pos_opening_entry, payment_reconciliation):
    """Kassani yopish — POS Closing Entry yaratib submit qilish.

    Multiple-cashier rejimida avval joriy kassir uchun `Sub POS Closing`
    yaratiladi. Yordamchi kassir shu bilan tugatadi; asosiy kassir esa
    qo'shimcha ravishda yakuniy `POS Closing Entry` ni ham yopadi.

    Args:
        pos_opening_entry: Yopilayotgan smena nomi.
        payment_reconciliation: `[{"mode_of_payment", "opening_amount",
            "expected_amount", "closing_amount"}]` (JSON string yoki list).

    Returns:
        dict: `{"name": str, "z_report_data": {...}}` — z_report_data
        Desktop POS'dagi Z-hisobot chekini chop etish uchun.
    """
    from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import (
        make_closing_entry_from_opening,
    )

    rows = _loads(payment_reconciliation, [])
    opening = frappe.get_doc("POS Opening Entry", pos_opening_entry)
    _assert_not_finalized(opening)

    # Kassir kiritgan haqiqiy summalar (mode_of_payment → closing_amount).
    entered = {
        (r or {}).get("mode_of_payment"): flt((r or {}).get("closing_amount"))
        for r in rows
        if (r or {}).get("mode_of_payment")
    }

    multiple_cashier = cint(
        frappe.db.get_value("POS Profile", opening.pos_profile, "custom_enable_multiple_cashier")
    )

    is_main = _is_main_cashier(opening.pos_profile) if multiple_cashier else True

    if multiple_cashier and not is_main:
        # Yordamchi kassir smenani `Sub POS Closing` bilan yopadi — URY
        # yakuniy `POS Closing Entry` ni faqat asosiy kassirga ruxsat etadi
        # (`The Main Cashier cannot close a Sub POS Closing entry`).
        sub = _create_sub_closing(opening, entered)
        frappe.db.set_value(
            "POS Opening Entry", opening.name, "custom_sub_pos_close_entry", sub.name
        )
        payments = [
            {
                "mode_of_payment": p.mode_of_payment,
                "opening_amount": flt(p.opening_amount),
                "expected_amount": flt(p.expected_amount),
                "closing_amount": flt(p.closing_amount),
                "difference": flt(p.difference),
            }
            for p in sub.payment_reconciliation
        ]
        return {
            "name": sub.name,
            "type": "sub",
            "status": "sub_closed",
            "z_report_data": {
                "pos_opening_entry": pos_opening_entry,
                "pos_closing_entry": sub.name,
                "period_start_date": str(opening.period_start_date or ""),
                "period_end_date": str(sub.period_end_date or ""),
                "total_sales": flt(sub.grand_total),
                "total_invoices": len(sub.pos_transactions),
                "expected_cash": flt(payments[0]["expected_amount"]) if payments else 0.0,
                "actual_cash": flt(payments[0]["closing_amount"]) if payments else 0.0,
                "cash_diff": flt(payments[0]["difference"]) if payments else 0.0,
                "payments": payments,
            },
        }

    if multiple_cashier:
        # `calculate_closing_amount` hook'i yakuniy hujjatni submit qilishda
        # kamida bitta submit qilingan Sub POS Closing bo'lishini talab
        # qiladi — aks holda tushunarsiz ingliz xatosi chiqadi.
        has_sub = frappe.db.count(
            "Sub POS Closing",
            {"docstatus": 1, "period_start_date": [">=", opening.period_start_date]},
        )
        if not has_sub:
            frappe.throw(
                _(
                    "Ko'p kassirli rejimda yakuniy kassani yopish uchun kamida bitta "
                    "yordamchi kassir o'z smenasini yopgan bo'lishi kerak.\n\n"
                    "Agar '{0}' profilida bitta kassir ishlasa, POS Profile'dagi "
                    "'Enable Multiple Cashier' bayrog'ini o'chiring."
                ).format(opening.pos_profile),
                title=_("Yordamchi kassir smenasi topilmadi"),
            )

    if opening.status != "Open":
        # `SubPOSClosing.on_submit` smenani darhol `Closed` qilib qo'yadi,
        # ERPNext esa yakuniy `POS Closing Entry` uchun uni `Open` holatda
        # ko'rishni talab qiladi (`Selected POS Opening Entry should be
        # open`). Yakuniy hujjat hali yaratilmagani yuqorida tekshirilgan,
        # shuning uchun statusni yopish muddatiga tiklaymiz — ERPNext'ning
        # o'z `on_submit` mantig'i uni qaytadan `Closed` qiladi.
        frappe.db.set_value("POS Opening Entry", opening.name, "status", "Open")
        opening.reload()

    closing = make_closing_entry_from_opening(opening)
    closing.posting_date = nowdate()
    closing.period_end_date = now_datetime()

    for row in closing.payment_reconciliation:
        amount = entered.get(row.mode_of_payment, flt(row.expected_amount))
        if multiple_cashier:
            # Sub POS Closing summalari `calculate_closing_amount` hook'ida
            # `custom_closing_amount` ustiga qo'shiladi — ikki marta
            # hisoblanmasligi uchun bu yerda faqat custom maydon to'ldiriladi.
            row.custom_closing_amount = 0
        else:
            row.closing_amount = amount
            row.difference = amount - flt(row.expected_amount)

    closing.insert(ignore_permissions=True)
    closing.submit()

    payments = [
        {
            "mode_of_payment": p.mode_of_payment,
            "opening_amount": flt(p.opening_amount),
            "expected_amount": flt(p.expected_amount),
            "closing_amount": flt(p.closing_amount),
            "difference": flt(p.closing_amount) - flt(p.expected_amount),
        }
        for p in closing.payment_reconciliation
    ]
    cash = next(
        (
            p
            for p in payments
            if p["mode_of_payment"].lower().strip()
            in {"cash", "naqd", "naqd pul", "наличные", "нахт", "cash in hand"}
        ),
        payments[0] if payments else None,
    )

    return {
        "name": closing.name,
        "status": "closed",
        "z_report_data": {
            "pos_opening_entry": pos_opening_entry,
            "pos_closing_entry": closing.name,
            "period_start_date": str(opening.period_start_date or ""),
            "period_end_date": str(closing.period_end_date or ""),
            "total_sales": flt(closing.grand_total),
            "total_invoices": len(closing.get("pos_transactions") or []),
            "expected_cash": flt(cash["expected_amount"]) if cash else 0.0,
            "actual_cash": flt(cash["closing_amount"]) if cash else 0.0,
            "cash_diff": flt(cash["difference"]) if cash else 0.0,
            "payments": payments,
        },
    }


# ═══════════════════════════════════════════════════════════════════
#  2. Kutilayotgan (Draft) buyurtmalar
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def getPendingOrders(only_mine=0, mine_cashier_name="", limit=100, limit_start=0, order_type=None):
    """To'lov kutayotgan Draft POS Invoice'lar ro'yxati.

    Args:
        only_mine: 1 bo'lsa faqat `mine_cashier_name` kassirining buyurtmalari.
        mine_cashier_name: Aktiv kassir ismi (`custom_active_cashier`).
        limit / limit_start: Sahifalash.
        order_type: Filtr (`Dine In`, `Take Away`, `Delivery`, ...).

    Returns:
        list[dict]: Desktop POS jadvali kutadigan ustunlar bilan.
    """
    filters = _pending_filters(only_mine, mine_cashier_name, order_type)

    rows = frappe.get_all(
        "POS Invoice",
        filters=filters,
        fields=[
            "name",
            "customer",
            "grand_total",
            "rounded_total",
            "order_type",
            "posting_date",
            "posting_time",
            "restaurant_table",
            "custom_restaurant_room as room",
            "custom_ticket_number",
            "custom_active_cashier",
            "custom_active_cashier_role",
            "custom_comments",
            "custom_ury_order_number",
            "owner",
            "modified",
        ],
        order_by="creation desc",
        limit_page_length=cint(limit) or 100,
        limit_start=cint(limit_start) or 0,
    )

    for r in rows:
        r["posting_time"] = str(r.get("posting_time") or "")
        r["posting_date"] = str(r.get("posting_date") or "")
    return rows


@frappe.whitelist()
def getPendingOrderCounts(only_mine=0, mine_cashier_name=""):
    """Draft buyurtmalar sanog'i — umumiy va buyurtma turi bo'yicha.

    Returns:
        dict: `{"all": int, "Dine In": int, "Take Away": int,
                "Delivery": int, ...}` — Desktop POS'dagi chip badge'lari.
    """
    filters = _pending_filters(only_mine, mine_cashier_name)

    counts = {ot: 0 for ot in ORDER_TYPES}
    counts["all"] = 0

    grouped = frappe.get_all(
        "POS Invoice",
        filters=filters,
        fields=["order_type", "count(name) as cnt"],
        group_by="order_type",
    )
    for row in grouped:
        cnt = cint(row.get("cnt"))
        counts["all"] += cnt
        ot = row.get("order_type")
        if ot:
            counts[ot] = counts.get(ot, 0) + cnt

    return counts


@frappe.whitelist()
def getPendingOrderDetail(invoice):
    """Bitta Draft buyurtmaning to'liq tarkibi — to'lov oynasi uchun.

    Returns:
        dict: Invoice sarlavhasi + `items` ro'yxati.
    """
    doc = frappe.get_doc("POS Invoice", invoice)
    if doc.docstatus != 0:
        frappe.throw(_("'{0}' allaqachon to'langan yoki bekor qilingan").format(invoice))

    return {
        "name": doc.name,
        "customer": doc.customer,
        "grand_total": flt(doc.grand_total),
        "rounded_total": flt(doc.rounded_total),
        "net_total": flt(doc.net_total),
        "order_type": doc.order_type,
        "restaurant_table": doc.get("restaurant_table") or "",
        "room": doc.get("custom_restaurant_room") or "",
        "custom_ticket_number": doc.get("custom_ticket_number") or "",
        "custom_comments": doc.get("custom_comments") or "",
        "custom_active_cashier": doc.get("custom_active_cashier") or "",
        "custom_active_cashier_role": doc.get("custom_active_cashier_role") or "",
        "posting_date": str(doc.posting_date or ""),
        "posting_time": str(doc.posting_time or ""),
        "items": [
            {
                "item_code": it.item_code,
                "item_name": it.item_name,
                "rate": flt(it.rate),
                "qty": flt(it.qty),
                "amount": flt(it.amount),
                "uom": it.uom,
            }
            for it in doc.items
        ],
    }


@frappe.whitelist()
def cancelPendingOrder(invoice, reason, cashier="", active_cashier="", active_cashier_role="Kassir"):
    """Draft buyurtmani bekor qilish va band stolni bo'shatish.

    Hujjat o'chirilmaydi — audit uchun `custom_cancelled` bayrog'i,
    sabab va bekor qilgan kassir yoziladi.

    Returns:
        dict: `{"status": "ok", "invoice": str}`
    """
    _assert_not_restricted(active_cashier_role, _("buyurtmani bekor qilish"))

    if not (reason or "").strip():
        frappe.throw(_("Bekor qilish sababi ko'rsatilishi shart"))

    doc = frappe.get_doc("POS Invoice", invoice)
    if doc.docstatus != 0:
        frappe.throw(_("Faqat to'lanmagan buyurtmani bekor qilish mumkin"))

    table = doc.get("restaurant_table")

    doc.db_set(
        {
            "custom_cancelled": 1,
            "cancel_reason": reason,
            "custom_cancel_by": active_cashier or cashier or frappe.session.user,
        },
        update_modified=True,
    )

    if table:
        _free_table_doc(table)

    frappe.publish_realtime("pending_order_cancelled", {"invoice": invoice, "table": table or ""})
    return {"status": "ok", "invoice": invoice}


# ═══════════════════════════════════════════════════════════════════
#  3. Stollar va xonalar
# ═══════════════════════════════════════════════════════════════════

def _free_table_doc(table: str) -> bool:
    """URY Table'ni bo'sh holatga o'tkazish. Stol topilmasa `False`."""
    if not table or not frappe.db.exists("URY Table", table):
        return False
    frappe.db.set_value("URY Table", table, "occupied", 0)
    frappe.publish_realtime("table_freed", {"table": table})
    return True


@frappe.whitelist()
def getTables(branch=None):
    """Filialdagi stollar ro'yxati (Desktop POS stol tanlash oynasi)."""
    branch = branch or _get_branch()
    return frappe.get_all(
        "URY Table",
        filters={"branch": branch},
        fields=[
            "name",
            "restaurant_room",
            "no_of_seats",
            "minimum_seating",
            "occupied",
            "is_take_away",
            "table_shape",
            "branch",
        ],
        order_by="restaurant_room asc, name asc",
    )


@frappe.whitelist()
def getRoomsForBranch(branch=None):
    """Filialdagi xonalar ro'yxati."""
    branch = branch or _get_branch()
    return frappe.get_all(
        "URY Room",
        filters={"branch": branch},
        fields=["name", "branch", "room_type"],
        order_by="name asc",
    )


@frappe.whitelist()
def freeTable(table, reason, active_cashier="", active_cashier_role="Kassir"):
    """Stolni qo'lda bo'shatish.

    Stolda to'lanmagan Draft invoice qolgan bo'lsa bo'shatishga yo'l
    qo'yilmaydi — aks holda buyurtma "yo'qolib" qoladi.

    Returns:
        dict: `{"status": "ok", "table": str}`
    """
    _assert_not_restricted(active_cashier_role, _("stolni bo'shatish"))

    if not (reason or "").strip():
        frappe.throw(_("Stolni bo'shatish sababi ko'rsatilishi shart"))

    pending = frappe.db.count(
        "POS Invoice",
        {"restaurant_table": table, "docstatus": 0, "custom_cancelled": 0},
    )
    if pending:
        frappe.throw(
            _("'{0}' stolida {1} ta to'lanmagan buyurtma bor — avval ularni hal qiling").format(
                table, pending
            )
        )

    if not _free_table_doc(table):
        frappe.throw(_("'{0}' stoli topilmadi").format(table))

    frappe.logger("ury_desktop_pos").info(
        "Stol bo'shatildi: %s | kassir=%s | sabab=%s",
        table,
        active_cashier or frappe.session.user,
        reason,
    )
    return {"status": "ok", "table": table}


@frappe.whitelist()
def cleanupOrphanTables():
    """Egasiz band stollarni avtomatik bo'shatish.

    "Orphan" — `occupied = 1`, lekin unga bog'langan to'lanmagan Draft
    invoice yo'q va oxirgi faollikdan `ORPHAN_TABLE_HOURS` soat o'tgan.

    Returns:
        dict: `{"freed_count": int, "tables": [str]}`
    """
    branch = _get_branch()
    occupied = frappe.get_all(
        "URY Table",
        filters={"branch": branch, "occupied": 1},
        fields=["name", "modified"],
    )
    if not occupied:
        return {"freed_count": 0, "tables": []}

    busy = {
        r.restaurant_table
        for r in frappe.get_all(
            "POS Invoice",
            filters={"docstatus": 0, "custom_cancelled": 0, "branch": branch},
            fields=["restaurant_table"],
        )
        if r.restaurant_table
    }

    cutoff = get_datetime(add_to_date(now_datetime(), hours=-ORPHAN_TABLE_HOURS))
    freed = []
    for table in occupied:
        if table.name in busy:
            continue
        if get_datetime(table.modified) > cutoff:
            continue
        if _free_table_doc(table.name):
            freed.append(table.name)

    if freed:
        frappe.logger("ury_desktop_pos").info("Orphan stollar bo'shatildi: %s", ", ".join(freed))

    return {"freed_count": len(freed), "tables": freed}


# ═══════════════════════════════════════════════════════════════════
#  4. Sozlamalar — kassirlar, printer, tezkor tovarlar
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_pos_cashiers(pos_profile=None):
    """POS Profile'ga biriktirilgan kassirlar va ularning PIN kodlari.

    PIN `POS Profile User.custom_pin` maydonida saqlanadi. PIN bo'sh
    bo'lsa bo'sh qator qaytariladi — Desktop POS bunday holatda o'zining
    lokal cache'idagi PIN'ni saqlab qoladi.

    Returns:
        list[dict]: `[{"name", "full_name", "user", "pin", "main_cashier"}]`
    """
    profile = pos_profile or _get_pos_profile_name()
    doc = frappe.get_doc("POS Profile", profile)

    cashiers = []
    for row in doc.applicable_for_users:
        full_name = frappe.db.get_value("User", row.user, "full_name") or row.user
        cashiers.append(
            {
                "name": full_name,
                "full_name": full_name,
                "user": row.user,
                "pin": (row.get("custom_pin") or "").strip(),
                "main_cashier": cint(row.get("custom_main_cashier")),
            }
        )
    return cashiers


def _printer_device_name(printer_settings) -> str:
    """`URY Printer Settings` jadvalidan chek printeri qurilma nomini olish."""
    for row in printer_settings or []:
        if not row.printer:
            continue
        device = frappe.db.get_value("Network Printer Settings", row.printer, "printer_name")
        if device:
            return device
    return ""


@frappe.whitelist()
def get_printer_config(pos_profile=None):
    """Desktop POS uchun printer konfiguratsiyasi.

    Mijoz cheki printeri POS Profile'ning `customer_qz_printer_*`
    maydonlaridan, oshxona/bar printerlari esa shu profilga bog'langan
    `URY Production Unit` hujjatlaridan yig'iladi.

    Returns:
        dict: `{"print_enabled": bool, "customer_printer": {...},
                "production_units": [{"name", "printer_name",
                                      "item_groups": [...]}]}`
    """
    profile = pos_profile or _get_pos_profile_name()
    doc = frappe.get_doc("POS Profile", profile)

    customer_name = (doc.get("customer_qz_printer_name") or "").strip()
    customer_printer = {
        "name": customer_name,
        "driver": (doc.get("customer_qz_printer_driver") or DEFAULT_PRINTER_DRIVER),
        "width_mm": cint(doc.get("customer_qz_printer_width")) or DEFAULT_PRINTER_WIDTH,
        "codepage": (doc.get("customer_qz_printer_codepage") or DEFAULT_PRINTER_CODEPAGE),
    }

    units = []
    for unit_name in frappe.get_all(
        "URY Production Unit", filters={"pos_profile": profile}, pluck="name"
    ):
        unit = frappe.get_doc("URY Production Unit", unit_name)
        units.append(
            {
                "name": unit.production or unit.name,
                "printer_name": _printer_device_name(unit.printer_settings),
                "driver": DEFAULT_PRINTER_DRIVER,
                "width_mm": DEFAULT_PRINTER_WIDTH,
                "codepage": DEFAULT_PRINTER_CODEPAGE,
                "item_groups": [g.item_group for g in unit.item_groups if g.item_group],
            }
        )

    return {
        "print_enabled": bool(customer_name or any(u["printer_name"] for u in units)),
        "customer_printer": customer_printer,
        "production_units": units,
    }


@frappe.whitelist()
def save_pos_quick_items(pos_profile, items, slots_count=3):
    """Tezkor sotuv tugmalarini POS Profile'ga saqlash.

    Desktop POS `[{"item_code": str, "slot_idx": int}]` yuboradi (faqat
    to'ldirilgan slotlar). Server `POS Profile.custom_quick_items` da
    `{slot_idx: item_code}` ko'rinishida saqlaydi — slot raqami yo'qolmasligi
    uchun. `getPosProfile` uni qaytarishda POS kutgan slot massiviga
    aylantiradi.

    Returns:
        dict: `{"status": "ok", "saved_count": int, "slots_count": int}`
    """
    slots = max(3, min(cint(slots_count) or 3, 4))

    mapping = {}
    for pos, row in enumerate(_loads(items, []), start=1):
        if isinstance(row, str):
            code, idx = row, pos
        elif isinstance(row, dict):
            code = row.get("item_code") or row.get("item")
            idx = cint(row.get("slot_idx") or pos)
        else:
            continue
        if code and 1 <= idx <= slots:
            mapping[str(idx)] = code

    frappe.db.set_value(
        "POS Profile",
        pos_profile,
        {
            "custom_quick_items": json.dumps(mapping, ensure_ascii=False),
            "custom_quick_slots_count": slots,
        },
        update_modified=True,
    )
    return {"status": "ok", "saved_count": len(mapping), "slots_count": slots}


def _quick_items_for_pos(pos_profile: str, slots: int) -> list:
    """Saqlangan tezkor tovarlarni Desktop POS kutgan ko'rinishga keltirish.

    POS `config["quick_items"]` ni slot massivi sifatida o'qiydi:
    `[{item_code, item_name, price, currency} | None, ...]` — indeks slot
    raqamiga mos keladi, bo'sh slot `None` bo'ladi.
    """
    raw = _loads(frappe.db.get_value("POS Profile", pos_profile, "custom_quick_items"), {})

    # Eski yozuvlar oddiy ro'yxat bo'lishi mumkin — slot tartibi sifatida o'qiymiz.
    if isinstance(raw, list):
        raw = {str(i): v for i, v in enumerate(raw, start=1) if isinstance(v, str)}
    if not isinstance(raw, dict):
        return [None] * slots

    currency = frappe.db.get_value("POS Profile", pos_profile, "currency") or "UZS"
    menu = _get_menu_for_profile(pos_profile)

    result = []
    for idx in range(1, slots + 1):
        code = raw.get(str(idx))
        if not code or not frappe.db.exists("Item", code):
            result.append(None)
            continue
        rate = 0
        if menu:
            rate = (
                frappe.db.get_value(
                    "URY Menu Item", {"parent": menu, "item": code}, "rate"
                )
                or 0
            )
        result.append(
            {
                "item_code": code,
                "item_name": frappe.db.get_value("Item", code, "item_name") or code,
                "price": flt(rate),
                "currency": currency,
            }
        )
    return result


# ═══════════════════════════════════════════════════════════════════
#  5. Menyu tartibi (admin drag-drop)
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def saveMenuCourseOrder(orders):
    """Kategoriyalar tartibini saqlash (`URY Menu Course.custom_serving_priority`).

    Args:
        orders: `[{"name": str, "priority": int}]` yoki `[str, ...]`
            (ro'yxatdagi tartib priority sifatida ishlatiladi).

    Returns:
        dict: `{"status": "ok", "updated": int}`
    """
    rows = _loads(orders, [])
    updated = 0

    for idx, row in enumerate(rows, start=1):
        if isinstance(row, str):
            course, priority = row, idx
        else:
            course = (row or {}).get("name") or (row or {}).get("course")
            priority = cint((row or {}).get("priority") or idx)

        if not course or not frappe.db.exists("URY Menu Course", course):
            continue
        frappe.db.set_value(
            "URY Menu Course", course, "custom_serving_priority", priority, update_modified=True
        )
        updated += 1

    return {"status": "ok", "updated": updated}


@frappe.whitelist()
def saveMenuItemOrder(pos_profile, items):
    """Tovarlar tartibini saqlash (`URY Menu Item.idx`).

    Menyu POS Profile → URY Restaurant → URY Menu zanjiri orqali topiladi.

    Args:
        items: `[{"item": str, "idx": int}]` yoki `[str, ...]`.

    Returns:
        dict: `{"status": "ok", "updated": int}`
    """
    rows = _loads(items, [])
    menu = _get_menu_for_profile(pos_profile)
    if not menu:
        frappe.throw(_("'{0}' uchun URY Menu topilmadi").format(pos_profile))

    order_map = {}
    for pos, row in enumerate(rows, start=1):
        if isinstance(row, str):
            order_map[row] = pos
        else:
            code = (row or {}).get("item") or (row or {}).get("item_code")
            if code:
                order_map[code] = cint((row or {}).get("idx") or pos)

    if not order_map:
        return {"status": "ok", "updated": 0}

    updated = 0
    for child in frappe.get_all(
        "URY Menu Item", filters={"parent": menu, "parenttype": "URY Menu"}, fields=["name", "item"]
    ):
        if child.item in order_map:
            frappe.db.set_value("URY Menu Item", child.name, "idx", order_map[child.item])
            updated += 1

    return {"status": "ok", "updated": updated, "menu": menu}


def _get_menu_for_profile(pos_profile: str) -> str:
    """POS Profile uchun aktiv URY Menu nomini topish.

    `URY Menu` filialga bog'langan (`branch`), shuning uchun profil →
    filial → menyu zanjiri bo'yicha qidiriladi. Filialda yoqilgan menyu
    bo'lmasa, o'sha filialning istalgan menyusi olinadi.
    """
    branch = frappe.db.get_value("POS Profile", pos_profile, "branch")
    if not branch:
        return ""
    return (
        frappe.db.get_value("URY Menu", {"branch": branch, "enabled": 1}, "name")
        or frappe.db.get_value("URY Menu", {"branch": branch}, "name")
        or ""
    )


# ═══════════════════════════════════════════════════════════════════
#  6. getPosProfile — upstream javobini Desktop POS uchun boyitish
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def getPosProfile():
    """POS Profile ma'lumotlari — Desktop POS uchun kengaytirilgan.

    Upstream `ury.ury_pos.api.getPosProfile` javobini saqlab qoladi va
    ustiga Desktop POS `_sync_pos_profile` kutadigan maydonlarni qo'shadi
    (to'lov turlari, ko'rinish bayroqlari, buyurtma turlari, brend nomi,
    chek pastki matni, tezkor tovarlar, kassirlar).
    """
    from ury.ury_pos import api as _api

    data = dict(_api.getPosProfile())
    profile_name = data.get("pos_profile")
    if not profile_name:
        return data

    doc = frappe.get_doc("POS Profile", profile_name)
    slots = max(3, min(cint(doc.get("custom_quick_slots_count")) or 3, 4))

    data.update(
        {
            "currency": doc.currency,
            "default_customer": doc.customer or "",
            "payment_methods": [p.mode_of_payment for p in doc.payments if p.mode_of_payment],
            "brand_name": (doc.get("custom_company_brand_name") or doc.company or ""),
            "receipt_footer": doc.get("custom_receipt_footer") or "",
            "order_number_type": doc.get("custom_order_number_type") or "Stiker",
            "item_columns": cint(doc.get("custom_item_columns")),
            "quick_slots_count": slots,
            "quick_items": _quick_items_for_pos(profile_name, slots),
            # Ko'rinish bayroqlari
            "show_comment": cint(doc.get("custom_show_comment")),
            "show_ticket": cint(doc.get("custom_show_ticket")),
            "show_customer": cint(doc.get("custom_show_customer")),
            "show_history": cint(doc.get("custom_show_history")),
            "show_shifts": cint(doc.get("custom_show_shifts")),
            # Buyurtma turlari
            "order_type_dine_in": cint(doc.get("custom_order_type_dine_in")),
            "order_type_take_away": cint(doc.get("custom_order_type_take_away")),
            "order_type_delivery": cint(doc.get("custom_order_type_delivery")),
            "order_type_delivery_saboy": cint(doc.get("custom_order_type_delivery_saboy")),
            "daily_pos_close": cint(doc.get("custom_daily_pos_close")),
            "cashiers": get_pos_cashiers(profile_name),
        }
    )
    return data


# ═══════════════════════════════════════════════════════════════════
#  7. Dinamik narxlash — "Bozor narxlari" oynasi uchun
#
#  Hisoblash engine `ozturkapp.ozturkapp.api.dynamic_pricing` da; bu yerda faqat
#  Desktop POS chaqiradigan whitelisted qobiqlar turadi.
# ═══════════════════════════════════════════════════════════════════

def _pricing_branch(pos_profile: str) -> str:
    """POS Profile → filial. Narx sozlamasi filial darajasida saqlanadi."""
    branch = frappe.db.get_value("POS Profile", pos_profile, "branch") if pos_profile else None
    return branch or _get_branch()


def _require_pricing_admin(pos_profile: str):
    """Narxni o'zgartirish huquqini tekshirish.

    Mijozdagi PIN — bu faqat UX to'sig'i (`config.json` dagi tuzsiz SHA-256
    xavfsizlik chegarasi emas), shuning uchun server o'zi qayta tekshiradi.
    Barcha kassalar bitta Frappe foydalanuvchisi ostida ishlagani uchun
    rol yetarli bo'lmasa `custom_main_cashier` bayrog'i ishlatiladi.
    """
    roles = set(frappe.get_roles())
    if {"System Manager", "URY Manager"} & roles:
        return
    if pos_profile and _is_main_cashier(pos_profile):
        return
    frappe.throw(
        _("Narxlarni o'zgartirish uchun ruxsat yo'q — faqat asosiy kassir yoki menejer"),
        frappe.PermissionError,
    )


def _last_price_changes(branch: str) -> dict:
    """Har bir tovar uchun oxirgi narx o'zgarishi.

    Oynadagi "eski narx" (chizib tashlangan) aynan shu — narx oxirgi marta
    o'zgarishidan OLDINGI qiymat. Alohida maydonda saqlamaymiz, chunki bitta
    guruhlangan so'rov yetarli.
    """
    rows = frappe.db.sql(
        """
        SELECT l.item, l.old_rate, l.new_rate, l.creation
        FROM `tabURY Price Change Log` l
        INNER JOIN (
            SELECT item, MAX(creation) AS mc
            FROM `tabURY Price Change Log`
            WHERE branch = %(branch)s AND entry_type = 'change'
            GROUP BY item
        ) t ON t.item = l.item AND t.mc = l.creation
        WHERE l.branch = %(branch)s AND l.entry_type = 'change'
        """,
        {"branch": branch},
        as_dict=True,
    )
    return {r["item"]: r for r in rows}


@frappe.whitelist()
def getPricingVersion(pos_profile=None):
    """Arzon poll — POS har 60 soniyada shuni so'raydi.

    Versiya o'zgarmagan bo'lsa POS to'liq snapshot tortmaydi.
    """
    from ozturkapp.ozturkapp.api import dynamic_pricing as dp

    branch = _pricing_branch(pos_profile)
    cfg = dp.get_branch_settings(branch)
    row = frappe.db.get_value(
        "Branch", branch,
        ["custom_pricing_version", "custom_pricing_last_run", "custom_pricing_next_run"],
        as_dict=True,
    ) or {}
    menu = dp.get_menu_for_branch(branch)

    return {
        "branch": branch,
        "menu": menu,
        "pricing_version": cint(row.get("custom_pricing_version")),
        "enabled": cint(cfg["enabled"]),
        "dry_run": cint(cfg["dry_run"]),
        "interval_minutes": cint(cfg["interval_minutes"]),
        "last_run": row.get("custom_pricing_last_run"),
        "next_run": row.get("custom_pricing_next_run"),
        "server_time": now_datetime(),
        "item_count": frappe.db.count(
            "URY Menu Item", {"parent": menu, "parenttype": "URY Menu", "disabled": 0}
        ) if menu else 0,
    }


@frappe.whitelist()
def getPricingSnapshot(pos_profile=None, since_version=0, history_days=14, include_history=1):
    """Bozor taxtasi uchun to'liq holat.

    `since_version` joriy versiyaga teng bo'lsa hech nima yuborilmaydi —
    POS keshidagi ma'lumot yangi.
    """
    from ozturkapp.ozturkapp.api import dynamic_pricing as dp

    branch = _pricing_branch(pos_profile)
    cfg = dp.get_branch_settings(branch)
    version = cint(frappe.db.get_value("Branch", branch, "custom_pricing_version"))

    if cint(since_version) and cint(since_version) == version:
        return {"pricing_version": version, "unchanged": True}

    menu = dp.get_menu_for_branch(branch)
    if not menu:
        return {"pricing_version": version, "items": [], "history": [],
                "error": "no_menu", "branch": branch}

    rows = frappe.get_all(
        "URY Menu Item",
        filters={"parent": menu, "parenttype": "URY Menu", "disabled": 0},
        fields=[
            "item", "item_name", "course", "rate", "idx",
            "custom_base_rate", "custom_shadow_rate", "custom_cost_rate",
            "custom_cost_source", "custom_demand_score", "custom_trend",
            "custom_pricing_status", "custom_price_locked", "custom_price_updated_at",
        ],
        order_by="idx asc, item_name asc",
    )

    last_changes = _last_price_changes(branch)
    demand = dp.get_demand(branch, cfg)
    images = {
        r["name"]: r["image"]
        for r in frappe.get_all("Item", filters={"name": ["in", [r.item for r in rows]]},
                                fields=["name", "image"])
    } if rows else {}

    items = []
    for row in rows:
        base = flt(row.custom_base_rate) or flt(row.rate)
        change = last_changes.get(row.item) or {}
        stats = demand.get(row.item) or {}
        prev = flt(change.get("old_rate")) if change else 0.0
        # Oxirgi o'zgarish shu narxga olib kelgan bo'lsa — eski narx haqiqiy.
        # Aks holda (keyin qo'lda tahrir bo'lgan) eski narxni ko'rsatmaymiz.
        if prev and abs(flt(change.get("new_rate")) - flt(row.rate)) > 0.005:
            prev = 0.0

        items.append({
            "item": row.item,
            "item_name": row.item_name,
            "course": row.course,
            "image": images.get(row.item),
            "idx": cint(row.idx),
            "rate": flt(row.rate),
            "prev_rate": prev,
            "base_rate": base,
            "shadow_rate": flt(row.custom_shadow_rate),
            "min_rate": base * (1 - flt(cfg["max_down_pct"]) / 100.0),
            "max_rate": base * (1 + flt(cfg["max_up_pct"]) / 100.0),
            "cost_rate": flt(row.custom_cost_rate),
            "cost_source": row.custom_cost_source or "none",
            "demand_score": flt(row.custom_demand_score),
            "trend": row.custom_trend or "flat",
            "status": row.custom_pricing_status or "auto",
            "locked": cint(row.custom_price_locked),
            "qty_recent": flt(stats.get("qty_recent")),
            "qty_base": flt(stats.get("qty_base")),
            "changed_at": row.custom_price_updated_at,
        })

    history = []
    if cint(include_history):
        history = frappe.db.sql(
            """
            SELECT item, posting_date, new_rate AS rate, base_rate,
                   demand_score, qty_recent AS qty
            FROM `tabURY Price Change Log`
            WHERE branch = %(branch)s
              AND entry_type IN ('snapshot', 'change')
              AND posting_date >= %(from_date)s
            ORDER BY item ASC, posting_date ASC
            """,
            {"branch": branch,
             "from_date": add_to_date(nowdate(), days=-cint(history_days) or -14)},
            as_dict=True,
        )

    return {
        "pricing_version": version,
        "branch": branch,
        "menu": menu,
        "enabled": cint(cfg["enabled"]),
        "dry_run": cint(cfg["dry_run"]),
        "currency": frappe.db.get_value("POS Profile", pos_profile, "currency") or "UZS",
        "generated_at": now_datetime(),
        "bounds": {
            "max_up_pct": flt(cfg["max_up_pct"]),
            "max_down_pct": flt(cfg["max_down_pct"]),
            "max_step_pct_per_cycle": flt(cfg["max_step_pct_per_cycle"]),
            "max_step_pct_per_day": flt(cfg["max_step_pct_per_day"]),
            "rounding_step": cint(cfg["rounding_step"]),
            "min_price_for_dynamic": flt(cfg["min_price_for_dynamic"]),
        },
        "items": items,
        "history": history,
    }


@frappe.whitelist()
def getPricingSettings(pos_profile=None):
    from ozturkapp.ozturkapp.api import dynamic_pricing as dp

    branch = _pricing_branch(pos_profile)
    return {"branch": branch, "settings": dp.get_branch_settings(branch),
            "defaults": dp.DEFAULTS}


@frappe.whitelist()
def getPriceHistory(pos_profile=None, item=None, days=30):
    """Bitta tovar (yoki barchasi) uchun narx tarixi — batafsil oyna uchun."""
    branch = _pricing_branch(pos_profile)
    filters = {"branch": branch, "posting_date": [">=", add_to_date(nowdate(), days=-cint(days))]}
    if item:
        filters["item"] = item
    return frappe.get_all(
        "URY Price Change Log",
        filters=filters,
        fields=["item", "posting_date", "entry_type", "old_rate", "new_rate",
                "base_rate", "cost_rate", "demand_score", "qty_recent", "qty_base",
                "reason", "applied", "creation"],
        order_by="creation asc",
        limit=5000,
    )


@frappe.whitelist()
def getPricingAlerts(pos_profile=None):
    """Admin uchun e'tibor talab qiladigan holatlar."""
    from ozturkapp.ozturkapp.api import dynamic_pricing as dp

    branch = _pricing_branch(pos_profile)
    menu = dp.get_menu_for_branch(branch)
    if not menu:
        return {"cost_violation": [], "no_base": [], "unknown_cost_count": 0}

    base_filters = {"parent": menu, "parenttype": "URY Menu", "disabled": 0}

    violations = frappe.get_all(
        "URY Menu Item",
        filters=dict(base_filters, custom_pricing_status="cost_violation"),
        fields=["item", "item_name", "rate", "custom_cost_rate", "custom_base_rate"],
        limit=200,
    )
    for row in violations:
        # Tannarxni qoplaydigan bazaviy narx taklifi — admin shuni qo'yishi kerak.
        row["suggested_base"] = flt(row.custom_cost_rate) * 1.25

    return {
        "branch": branch,
        "cost_violation": violations,
        "no_base": frappe.get_all(
            "URY Menu Item", filters=dict(base_filters, custom_base_rate=0),
            fields=["item", "item_name", "rate"], limit=200,
        ),
        "locked": frappe.db.count("URY Menu Item", dict(base_filters, custom_price_locked=1)),
        "unknown_cost_count": frappe.db.count(
            "URY Menu Item", dict(base_filters, custom_cost_rate=0)
        ),
    }


@frappe.whitelist()
def verifyCartPrices(pos_profile=None, items=None):
    """Savatdagi narxlar serverdagi bilan mos kelishini tekshirish.

    Bu chaqiruv `sync_order` dan OLDIN qilinadi va "ko'rsatilgan narx ≠
    hisoblangan narx" poygasini yopadi: `ury_order.sync_order` invoys qatorini
    POS yuborgan `rate` dan emas, `Item Price` dan narxlaydi. Narx POS keshiga
    yetib bormasdan o'zgargan bo'lsa, kassir chekni bir summada chop etib,
    invoys boshqa summada yozilardi.
    """
    from ozturkapp.ozturkapp.api import dynamic_pricing as dp

    branch = _pricing_branch(pos_profile)
    cfg = dp.get_branch_settings(branch)
    menu = dp.get_menu_for_branch(branch)
    price_list = dp.get_price_list_for_menu(menu) if menu else ""

    rows = _loads(items, [])
    mismatches = []
    for row in rows:
        code = (row or {}).get("item") or (row or {}).get("item_code")
        if not code:
            continue
        sent = flt((row or {}).get("rate") or (row or {}).get("price"))
        server = None
        if price_list:
            server = frappe.db.get_value(
                "Item Price", {"item_code": code, "price_list": price_list}, "price_list_rate"
            )
        if server is None and menu:
            server = frappe.db.get_value(
                "URY Menu Item", {"parent": menu, "item": code}, "rate"
            )
        if server is None:
            continue
        if abs(flt(server) - sent) > 0.005:
            mismatches.append({"item": code, "sent_rate": sent, "server_rate": flt(server)})

    return {
        "ok": not mismatches,
        "policy": cfg.get("price_mismatch_policy") or "warn",
        "pricing_version": cint(frappe.db.get_value("Branch", branch, "custom_pricing_version")),
        "mismatches": mismatches,
    }


# ── Admin amallari ───────────────────────────────────────────────

@frappe.whitelist()
def recalcPricing(pos_profile=None, dry_run=None, active_cashier=None):
    """Narxlarni qo'lda qayta hisoblash."""
    from ozturkapp.ozturkapp.api import dynamic_pricing as dp

    _require_pricing_admin(pos_profile)
    branch = _pricing_branch(pos_profile)

    if dry_run is not None:
        cfg = dp.get_branch_settings(branch)
        cfg["dry_run"] = cint(dry_run)
        dp.save_branch_settings(branch, cfg)
        frappe.db.commit()

    return dp.run_for_branch(branch, mode="manual",
                             triggered_by=active_cashier or frappe.session.user, force=True)


@frappe.whitelist()
def savePricingSettings(pos_profile=None, settings=None, active_cashier=None, keep_shadow=0):
    """Sozlamani saqlash.

    `dry_run` 1 → 0 ga o'tganda soya narx majburan sotuv narxiga tenglashtiriladi.
    Aks holda dry-run davomida to'plangan butun siljish bir zumda qo'llanib,
    narx keskin sakrab ketardi.
    """
    from ozturkapp.ozturkapp.api import dynamic_pricing as dp

    _require_pricing_admin(pos_profile)
    branch = _pricing_branch(pos_profile)

    old = dp.get_branch_settings(branch)
    new = dp.merge_settings(dict(old, **(_loads(settings, {}) or {})))
    dp.save_branch_settings(branch, new)

    resynced = 0
    if cint(old.get("dry_run")) and not cint(new.get("dry_run")) and not cint(keep_shadow):
        resynced = dp.resync_shadow(branch)

    frappe.db.commit()
    return {"status": "ok", "branch": branch, "settings": new, "shadow_resynced": resynced}


@frappe.whitelist()
def setBasePrices(pos_profile=None, items=None, active_cashier=None):
    """Bazaviy narxni qo'lda belgilash.

    `items` bo'sh bo'lsa — barcha tovarlarning joriy narxi bazaviy deb olinadi
    (koridorni hozirgi holatga qayta markazlashtirish).
    """
    from ozturkapp.ozturkapp.api import dynamic_pricing as dp

    _require_pricing_admin(pos_profile)
    branch = _pricing_branch(pos_profile)
    menu = dp.get_menu_for_branch(branch)
    if not menu:
        frappe.throw(_("'{0}' filiali uchun menyu topilmadi").format(branch))

    rows = _loads(items, [])
    updated = 0

    if not rows:
        frappe.db.sql(
            """UPDATE `tabURY Menu Item`
               SET custom_base_rate = rate, custom_shadow_rate = rate,
                   custom_last_engine_rate = rate
               WHERE parent = %s AND parenttype = 'URY Menu'""",
            menu,
        )
        updated = frappe.db.count("URY Menu Item", {"parent": menu, "parenttype": "URY Menu"})
    else:
        for row in rows:
            code = (row or {}).get("item") or (row or {}).get("item_code")
            base = flt((row or {}).get("base_rate"))
            if not code or base <= 0:
                continue
            name = frappe.db.get_value("URY Menu Item", {"parent": menu, "item": code}, "name")
            if not name:
                continue
            frappe.db.set_value("URY Menu Item", name, {
                "custom_base_rate": base,
                "custom_shadow_rate": base,
            }, update_modified=False)
            updated += 1

    frappe.db.commit()
    return {"status": "ok", "updated": updated, "branch": branch}


@frappe.whitelist()
def setItemPriceLock(pos_profile=None, item=None, locked=1, active_cashier=None):
    """Bitta tovar narxini qulflash / qulfni ochish."""
    from ozturkapp.ozturkapp.api import dynamic_pricing as dp

    _require_pricing_admin(pos_profile)
    branch = _pricing_branch(pos_profile)
    menu = dp.get_menu_for_branch(branch)
    name = frappe.db.get_value("URY Menu Item", {"parent": menu, "item": item}, "name")
    if not name:
        frappe.throw(_("'{0}' menyuda topilmadi").format(item))

    frappe.db.set_value("URY Menu Item", name, "custom_price_locked", cint(locked),
                        update_modified=False)
    frappe.db.commit()
    return {"status": "ok", "item": item, "locked": cint(locked)}


@frappe.whitelist()
def revertPricingToBase(pos_profile=None, active_cashier=None):
    """PANIK TUGMA — barcha narxlarni bazaviy narxga qaytarish."""
    from ozturkapp.ozturkapp.api import dynamic_pricing as dp

    _require_pricing_admin(pos_profile)
    return dp.revert_to_base(_pricing_branch(pos_profile),
                             triggered_by=active_cashier or frappe.session.user)


# ── Dev yordamchilari ────────────────────────────────────────────
#
# Dev serverda atigi bitta yakunlangan POS Invoice bor, ya'ni real ma'lumot
# bilan sinash imkonsiz. Soxta POS Invoice yaratish esa GL va zaxirani buzadi —
# shuning uchun talab bitta JSON hujjatda simulyatsiya qilinadi.

def _assert_developer_mode():
    if not frappe.conf.get("developer_mode"):
        frappe.throw(_("Bu metod faqat developer_mode da ishlaydi"), frappe.PermissionError)
    frappe.only_for("System Manager")


@frappe.whitelist()
def seedPricingDemo(pos_profile=None, days=45, seed=42):
    """Sinov uchun real ko'rinishdagi sotuv tarixini yaratish.

    Har bir tovarga xarakter beriladi: log-normal bazaviy talab, hafta kuni
    koeffitsienti, bir qism tovarlarda o'suvchi/tushuvchi trend, bir nechtasida
    oxirgi haftada keskin sakrash, ~30% da esa umuman sotuv yo'q. Shu tarzda
    engine'ning barcha yo'llari (o'sish, tushish, o'rtaga qaytish) tekshiriladi.
    """
    import random

    from ozturkapp.ozturkapp.api import dynamic_pricing as dp

    _assert_developer_mode()
    branch = _pricing_branch(pos_profile)
    menu = dp.get_menu_for_branch(branch)
    if not menu:
        frappe.throw(_("'{0}' filiali uchun menyu topilmadi").format(branch))

    days = cint(days) or 45
    rng = random.Random(cint(seed) or 42)
    dow_factor = [1.0, 0.9, 0.95, 1.05, 1.3, 1.6, 1.4]

    items = frappe.get_all("URY Menu Item",
                           filters={"parent": menu, "parenttype": "URY Menu", "disabled": 0},
                           pluck="item")

    demand = {}
    for pos, item_code in enumerate(items):
        if rng.random() < 0.30:              # sust tovarlar — o'rtaga qaytishni sinash uchun
            demand[item_code] = {}
            continue

        lam = math.exp(rng.gauss(1.1, 0.9))  # log-normal: ko'pchilik kam, bir nechtasi ko'p
        trend = rng.choice([0.0, 0.0, 0.0, 0.015, -0.015])
        viral = pos % 90 == 0                 # ~5 ta tovarda oxirgi haftada portlash

        by_date = {}
        for back in range(days):
            day = add_to_date(nowdate(), days=-back)
            weekday = get_datetime(day).weekday()
            rate = lam * dow_factor[weekday] * ((1.0 + trend) ** (days - back))
            if viral and back < 7:
                rate *= 3.0
            qty = sum(1 for _ in range(int(rate * 2) + 1) if rng.random() < 0.5)
            if qty:
                by_date[str(day)] = qty
        demand[item_code] = by_date

    for stale in frappe.get_all("URY Price Run", filters={"branch": branch, "mode": "seed"},
                                pluck="name"):
        frappe.delete_doc("URY Price Run", stale, force=True, ignore_permissions=True)

    frappe.get_doc({
        "doctype": "URY Price Run",
        "run_key": "{0}|seed|{1}".format(branch, frappe.generate_hash(length=8)),
        "branch": branch, "menu": menu, "mode": "seed",
        "started_at": now_datetime(), "finished_at": now_datetime(),
        "evaluated": len(items),
        "settings_snapshot": json.dumps({"demand": demand}),
    }).insert(ignore_permissions=True)

    cfg = dp.get_branch_settings(branch)
    cfg["demand_source"] = "synthetic"
    dp.save_branch_settings(branch, cfg)
    frappe.db.commit()

    sold = sum(1 for v in demand.values() if v)
    return {"status": "ok", "branch": branch, "items": len(items),
            "items_with_sales": sold, "days": days,
            "note": "demand_source='synthetic' yoqildi — real ma'lumotga qaytarish uchun 'pos_invoice' qiling"}


@frappe.whitelist()
def simulatePricing(pos_profile=None, cycles=240, interval_minutes=None):
    """Narx traektoriyasini xotirada hisoblash — hech nima yozilmaydi.

    465 ta real tovar × 240 sikl (10 kun) bir necha soniyada tugaydi, chunki
    `compute_next` sof funksiya.
    """
    from ozturkapp.ozturkapp.api import dynamic_pricing as dp

    _assert_developer_mode()
    branch = _pricing_branch(pos_profile)
    cfg = dp.get_branch_settings(branch)
    menu = dp.get_menu_for_branch(branch)
    if not menu:
        frappe.throw(_("'{0}' filiali uchun menyu topilmadi").format(branch))

    interval = cint(interval_minutes) or cint(cfg["interval_minutes"])
    cycles = cint(cycles) or 240

    rows = frappe.get_all(
        "URY Menu Item",
        filters={"parent": menu, "parenttype": "URY Menu", "disabled": 0},
        fields=["item", "item_name", "course", "rate", "creation",
                "custom_base_rate", "custom_cost_rate"],
    )
    demand = dp.get_demand(branch, cfg)
    medians = dp.course_median_velocities(rows, demand, cfg)

    import time as _time
    start = _time.time()
    out = []

    for row in rows:
        base = flt(row.custom_base_rate) or flt(row.rate)
        state = {
            "rate": flt(row.rate), "base_rate": base, "shadow_rate": flt(row.rate),
            "last_engine_rate": flt(row.rate), "cost_rate": flt(row.custom_cost_rate),
            "locked": 0, "excluded": False, "trend": "flat",
            "created_ts": _time.mktime(get_datetime(row.creation).timetuple()) if row.creation else None,
            "price_updated_ts": None, "shadow_updated_ts": None,
        }
        stats = dict(demand.get(row.item) or {})
        stats["course_median_velocity"] = medians.get(row.course or "", 0.0)

        changes = 0
        for i in range(cycles):
            now = start + i * interval * 60
            decision = dp.compute_next(state, stats, cfg, now)
            state["base_rate"] = decision["base_rate"]
            state["shadow_rate"] = decision["shadow_rate"]
            state["shadow_updated_ts"] = now
            if decision["changed"]:
                state["rate"] = decision["applied_rate"]
                state["last_engine_rate"] = decision["last_engine_rate"]
                state["price_updated_ts"] = now
                changes += 1

        out.append({
            "item": row.item, "item_name": row.item_name,
            "start_rate": flt(row.rate), "end_rate": state["rate"],
            "base_rate": base, "cost_rate": flt(row.custom_cost_rate),
            "pct": (state["rate"] / flt(row.rate) - 1) * 100 if flt(row.rate) else 0.0,
            "changes": changes, "status": decision["status"],
            "demand_score": decision["demand_score"],
            "qty_recent": flt(stats.get("qty_recent")), "qty_base": flt(stats.get("qty_base")),
        })

    up = sum(1 for r in out if r["end_rate"] > r["start_rate"])
    down = sum(1 for r in out if r["end_rate"] < r["start_rate"])
    return {
        "branch": branch, "cycles": cycles, "interval_minutes": interval,
        "simulated_days": round(cycles * interval / 1440.0, 1),
        "items": len(out), "up": up, "down": down, "flat": len(out) - up - down,
        "duration_ms": int((_time.time() - start) * 1000),
        "results": sorted(out, key=lambda r: -abs(r["pct"]))[:100],
    }
