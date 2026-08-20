# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Ofitsant mobil ilovasi uchun REST API (Flutter mijoz).

ENG MUHIM: BUYURTMA URY'NING MAVJUD OQIMIDAN O'TADI
===================================================
`submit_order()` ikkinchi buyurtma tizimini YARATMAYDI. U URY'ning
`sync_order()` funksiyasini chaqiradi — o'sha funksiya:

    1. `POS Invoice` (draft) yaratadi/yangilaydi
    2. narxlarni `Item Price` / `Price List` dan oladi
    3. `kot_execute()` orqali KOT yaratadi (TZ §7.6)
    4. `URY Table.occupied = 1` qiladi

Ya'ni mobil ilovadan olingan buyurtma AYNAN kassir/Desktop POS
yaratganidek tizimga tushadi va Kassa oynasida ham, Oshxona KDS'ida ham
darhol ko'rinadi.

OFLAYN (TZ §11)
===============
`client_ref` — ilova mahalliy yaratgan UUID. `ozturkapp.overrides.ury_order`
o'rami shu bo'yicha idempotentlikni ta'minlaydi: aloqa uzilib qayta
yuborilsa DUBLIKAT chek yaratilmaydi.

BEKOR QILISH QOIDASI (TZ §8)
============================
Ofitsant faqat `Pending` holatidagi mahsulotni olib tashlashi/kamaytirishi
mumkin. Tayyorlash boshlangan bo'lsa — RAD ETILADI. Qoida serverda,
`utils/kitchen_status.py` dagi yagona manbadan tekshiriladi.

PUL HISOBLANMAYDI (TZ §8/#5)
============================
12% xizmat haqi ERPNext soliq shablonidan keladi. Ilova faqat KO'RSATADI.
"""

import json
import uuid

import frappe
from frappe import _
from frappe.utils import cint, flt

from ozturkapp.ozturkapp.setup.waiter_setup import WAITER_ROLE, WAITER_ROLES
from ozturkapp.ozturkapp.utils import (
    cashier_billing,
    cashier_permissions,
    kitchen_status,
    notifications,
    table_status,
)
from ozturkapp.ozturkapp.utils.cashier_realtime import emit_floor_change, emit_order_change


# ═══════════════════════════════════════════════════════════════════
#  Ruxsat (TZ §12)
# ═══════════════════════════════════════════════════════════════════

def require_waiter():
    if frappe.session.user == "Guest":
        raise frappe.PermissionError(_("Iltimos, tizimga kiring"))

    if not set(frappe.get_roles()).intersection(WAITER_ROLES):
        raise frappe.PermissionError(
            _("Ofitsant ilovasiga ruxsat yo'q. Kerakli rollardan biri: {0}").format(
                ", ".join(WAITER_ROLES)
            )
        )


# ═══════════════════════════════════════════════════════════════════
#  1. Kontekst / Autentifikatsiya (TZ §7.1)
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_context():
    """Kirgandan keyin ilova oladigan barcha sozlama."""
    require_waiter()
    scope = cashier_permissions.resolve_scope()

    restaurant = frappe.db.get_value(
        "URY Restaurant", scope.restaurant, ["name", "default_room"], as_dict=True
    )

    return {
        "user": frappe.session.user,
        "full_name": frappe.db.get_value("User", frappe.session.user, "full_name"),
        "branch": scope.branch,
        "restaurant": restaurant,
        "pos_profile": scope.pos_profile,
        "company": scope.company,
        "currency": scope.currency,
        "default_customer": scope.default_customer,
        "rooms": frappe.get_all(
            "URY Room",
            filters={"branch": scope.branch},
            fields=["name", "room_type"],
            order_by="name asc",
        ),
        # DIQQAT: `service_charge` ATAYLAB yuborilmaydi — ofitsant 12% xizmat
        # haqini ko'rmasligi kerak (biznes qoidasi).
        # Ilova o'zi hisoblamasligi uchun holat nomlari serverdan keladi.
        "item_statuses": [
            {"key": s, "label": kitchen_status.label(s)}
            for s in kitchen_status.STATUSES
        ],
        # KASSA SMENASI
        # =============
        # `open = False` bo'lsa ilova BLOKLOVCHI oynani ko'rsatadi: smenasiz
        # buyurtma qabul qilinmaydi (`assert_shift_open` uni serverda ham
        # rad etadi). Oyna `events.shift` xabari bilan o'zi yo'qoladi —
        # ofitsant ilovani qayta ochishi shart emas.
        "shift": {"open": bool(cashier_permissions.open_shift_name(scope))},
        # SOKET UCHUN MAJBURIY
        # ====================
        # Frappe realtime ko'p ijarali: socket.io namespace'i SAYT NOMIGA
        # teng bo'lishi shart, aks holda ulanish "Invalid namespace" bilan
        # rad etiladi (`frappe/realtime/middlewares/authenticate.js`).
        # Frappe'ning o'z JS mijozi ham shunday qiladi:
        # `socketio_client.js` -> `host + "/" + frappe.boot.sitename`.
        # Ilova sayt nomini bilmasligi kerak — server aytadi.
        "site": frappe.local.site,
        "events": {
            "floor": "ozturk_cashier_floor",
            "order": "ozturk_cashier_order",
            "kitchen_item": "ozturk_kitchen_item",
            # Smena ochildi/yopildi -> bloklovchi oyna.
            "shift": "ozturk_shift",
            # Menyu yoki narx o'zgardi -> `get_menu()` ni qayta so'rash.
            "menu": "ozturk_menu",
        },
        "server_time": frappe.utils.now(),
    }


@frappe.whitelist()
def get_shift_state():
    """Kassa smenasi ochiqmi — bloklovchi oyna uchun YENGIL so'rov.

    Ilova buni ikki holatda chaqiradi:

    1. `ozturk_shift` realtime xabari kelganda. Xabarning o'ziga ISHONMAYDI
       — u sayt xonasiga ketadi va ruxsat tekshiruvidan o'tmaydi. Xabar
       faqat "borib so'ra" degan turtki (`utils/cashier_realtime.py`).

    2. Soket uzilib qayta ulanganda — uzilgan vaqtda holat o'zgargan
       bo'lishi mumkin, `get_context()` ni to'liq qayta yuklash esa ortiqcha.
    """
    require_waiter()
    scope = cashier_permissions.resolve_scope()
    return {
        "open": bool(cashier_permissions.open_shift_name(scope)),
        "branch": scope.branch,
        "server_time": frappe.utils.now(),
    }


# ═══════════════════════════════════════════════════════════════════
#  2. Stollar (TZ §7.2)
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_tables(room=None):
    """Zal bo'yicha stollar — kassa bilan AYNAN bir xil holat mantig'i."""
    require_waiter()
    scope = cashier_permissions.resolve_scope()

    state = table_status.build_floor_state(scope.branch, room or None)

    return {
        "room": room or None,
        "counts": state["counts"],
        "tables": [
            {
                "name": t["name"],
                "room": t["restaurant_room"],
                "seats": cint(t["no_of_seats"]),
                "shape": t["table_shape"],
                "status": t["status"],
                "is_merged": t["is_merged"],
                "amount": (t["order"] or {}).get("amount", 0),
                "order": (t["order"] or {}).get("name"),
                "waiter": (t["order"] or {}).get("waiter"),
                "pax": (t["order"] or {}).get("pax", 0),
                "billed": (t["order"] or {}).get("billed", False),
                "reservation": t["reservation"],
            }
            for t in state["tables"]
        ],
    }


# ═══════════════════════════════════════════════════════════════════
#  3. Menyu (TZ §7.4)
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_menu(room=None):
    """Menyu — URY'ning MAVJUD `getRestaurantMenu()` funksiyasidan.

    Narx, rasm, kurs (course) — hammasi URY Menu'dan keladi. Ilova
    narxni O'ZI saqlamaydi va hisoblamaydi.
    """
    require_waiter()
    scope = cashier_permissions.resolve_scope()

    from ury.ury_pos.api import getRestaurantMenu

    menu = getRestaurantMenu(scope.pos_profile, room=room or None)

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


@frappe.whitelist()
def mark_delivered(kot_item):
    """Ichimlikni «yetkazib berildi» deb belgilaydi.

    NEGA ALOHIDA ENDPOINT
    =====================
    Ichimlikni oshxona tayyorlamaydi: ofitsant barga borib oladi va
    mijozga eltadi. Unga "Tayyorlanmoqda -> Tayyor" bosqichlari ma'nosiz,
    shuning uchun oqim ikki bosqichli: Kutilmoqda -> Berildi.

    Oshxona endpointi (`kitchen.update_kot_item_status`) ISHLATILMAYDI —
    u `URY Kitchen` rolini talab qiladi va ofitsantda u yo'q. Ikkinchi
    tomondan, ofitsantga oshxona mahsulotini yopishga ruxsat berib
    bo'lmaydi: u faqat O'ZI olib boradigan nuqtaning mahsulotini yopadi.
    Shu cheklov quyida serverda tekshiriladi.

    Args:
        kot_item: `URY KOT Items` qatorining `name` si
            (`get_order()` javobidagi `kitchen.kot_item`).
    """
    require_waiter()
    scope = cashier_permissions.resolve_scope()

    row = frappe.db.sql(
        """
        SELECT ki.name, ki.parent, ki.custom_kitchen_status AS status,
               k.branch, k.docstatus, k.production, k.invoice
        FROM `tabURY KOT Items` ki
        INNER JOIN `tabURY KOT` k ON k.name = ki.parent
        WHERE ki.name = %s
        FOR UPDATE
        """,
        (kot_item,),
        as_dict=True,
    )
    if not row:
        frappe.throw(_("Mahsulot topilmadi"), frappe.DoesNotExistError)
    row = row[0]

    if row.branch != scope.branch:
        raise frappe.PermissionError(_("Bu mahsulot boshqa filialga tegishli"))

    if cint(row.docstatus) != 1:
        frappe.throw(_("KOT bekor qilingan yoki tasdiqlanmagan"))

    # ENG MUHIM TEKSHIRUV: ofitsant faqat "o'zi olib boriladi" nuqtasining
    # mahsulotini yopa oladi. Oshxona taomini u BERILDI deb belgilay
    # olmasligi kerak — buni oshpaz qiladi.
    if row.production not in kitchen_status.self_service_stations():
        frappe.throw(
            _(
                "Bu mahsulotni oshxona tayyorlaydi — uni ofitsant «berildi» "
                "deb belgilay olmaydi."
            ),
            title=_("Ruxsat yo'q"),
        )

    current = kitchen_status.normalize(row.status)
    kitchen_status.assert_transition(current, kitchen_status.SERVED, self_service=True)

    frappe.db.set_value(
        "URY KOT Items",
        kot_item,
        {
            "custom_kitchen_status": kitchen_status.SERVED,
            "custom_served_at": frappe.utils.now_datetime(),
            "custom_status_changed_by": frappe.session.user,
        },
        update_modified=False,
    )

    from ozturkapp.ozturkapp.utils.kitchen_realtime import emit_item_change, emit_kot_change

    emit_item_change(
        scope.branch, row.parent, kot_item, kitchen_status.SERVED,
        row.production, row.invoice,
    )
    emit_kot_change(
        scope.branch, row.parent, "KOT_ITEM_STATUS_CHANGED", row.production, row.invoice
    )
    emit_order_change(scope.branch, row.invoice, "ITEM_DELIVERED")

    return {
        "kot_item": kot_item,
        "status": kitchen_status.SERVED,
        "label": kitchen_status.label(kitchen_status.SERVED),
    }


# ═══════════════════════════════════════════════════════════════════
#  4. Buyurtmani o'qish (TZ §7.5, §7.7)
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_order(table=None, invoice=None):
    """Stol yoki chek bo'yicha faol buyurtma + mahsulot holatlari.

    Har bir mahsulotda `kitchen` bloki bo'ladi — ilova shunga qarab
    "Bekor qilish" tugmasini o'chiradi (TZ §8). Qoidani ilova
    O'ZI HISOBLAMAYDI.
    """
    require_waiter()
    scope = cashier_permissions.resolve_scope()

    if invoice:
        cashier_permissions.assert_invoice_in_scope(invoice, scope)
        name = invoice
    elif table:
        cashier_permissions.assert_table_in_scope(table, scope)
        name = _active_invoice_for_table(table, scope)
        if not name:
            return None
    else:
        frappe.throw(_("Stol yoki chek ko'rsatilishi kerak"))

    doc = frappe.get_doc("POS Invoice", name)
    bill = cashier_billing.build_bill(doc, scope)
    bill = _strip_financials(bill)

    bill["bill_requested"] = bool(cint(doc.get("custom_bill_requested")))
    bill["can_edit"] = cint(doc.docstatus) == 0 and not cint(doc.get("invoice_printed"))
    # Ilova buni `submit_order(last_modified_time=...)` da QAYTARIB yuboradi —
    # URY'ning optimistik qulfi shu asosda ishlaydi.
    bill["last_modified_time"] = str(doc.modified)
    return bill


def _strip_financials(bill: dict) -> dict:
    """Ofitsantga xizmat haqi va yakuniy summa KO'RSATILMAYDI.

    Biznes qoidasi: ofitsant faqat taomlar va ularning summasini ko'radi.
    12% xizmat haqi va u bilan hisoblangan jami — mijoz va kassirning ishi.

    Maydonlar javobdan BUTUNLAY olib tashlanadi (nolga tenglashtirilmaydi),
    shunda ilova ularni tasodifan ham ko'rsata olmaydi.
    """
    for field in (
        "service_charge",
        "service_charge_rate",
        "taxes",
        "total_taxes",
        "grand_total",
        "rounded_total",
    ):
        bill.pop(field, None)

    # Ofitsant uchun "jami" = taomlar summasi (soliqsiz).
    bill["items_total"] = bill.get("subtotal", 0)
    return bill


def _active_invoice_for_table(table: str, scope):
    orders = table_status.get_open_orders(scope.branch)
    mapping = table_status.map_orders_to_tables(orders)
    row = mapping.get(table)
    return row.name if row else None


# ═══════════════════════════════════════════════════════════════════
#  5. Buyurtma yuborish (TZ §7.3, §7.6) — ASOSIY INTEGRATSIYA
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def submit_order(
    table,
    items,
    customer=None,
    pax=None,
    comments=None,
    client_ref=None,
    last_modified_time=None,
):
    """Buyurtmani yaratadi/yangilaydi va OSHXONAGA yuboradi.

    URY'ning `sync_order()` chaqiriladi — u POS Invoice + KOT ni o'zi
    yaratadi. Ikkinchi buyurtma tizimi YO'Q.

    Args:
        table: `URY Table` nomi.
        items: `[{"item": "BURGER", "item_name": "Burger", "qty": 2,
                  "comment": "Piyozsiz"}]`
        client_ref: ilova bergan UUID — oflayn qayta yuborishda dublikatni
            oldini oladi (TZ §11).
        last_modified_time: optimistik qulf (URY'ning o'z mexanizmi).

    Returns:
        dict: chek nomi, hisob va mahsulot holatlari.
    """
    require_waiter()
    scope = cashier_permissions.resolve_scope()
    # Kassa smenasi ochilmasa buyurtma ham qabul qilinmaydi — aks holda
    # ofitsant taomlarni tanlab bo'lgach ERPNext ichkarida inglizcha xato
    # bilan yiqiladi (`validate_pos_opening_entry`).
    cashier_permissions.assert_shift_open(scope)
    table_row = cashier_permissions.assert_table_in_scope(table, scope)

    # ── Oflayn qayta yuborish (TZ §11) ────────────────────────────────
    # Aloqa uzilganda ilova AYNI so'rovni qayta yuboradi. Bu holda hech
    # narsa o'zgartirilmaydi va optimistik qulf ham TALAB QILINMAYDI —
    # avval yaratilgan buyurtma qaytariladi.
    if client_ref:
        already = frappe.db.get_value(
            "POS Invoice", {"custom_client_ref": client_ref}, "name"
        )
        if already:
            frappe.logger("ozturk_waiter").info(
                "submit_order: takroriy so'rov (client_ref=%s) -> %s", client_ref, already
            )
            return get_order(invoice=already)

    items = _parse_items(items)
    if not items:
        frappe.throw(_("Kamida bitta taom tanlanishi kerak"))

    existing = _active_invoice_for_table(table, scope)

    if existing:
        if cint(frappe.db.get_value("POS Invoice", existing, "invoice_printed")):
            frappe.throw(
                _("Hisob allaqachon chiqarilgan — buyurtmani o'zgartirib bo'lmaydi."),
                title=_("Buyurtma yopilgan"),
            )

        # ── TZ §8: tayyorlanayotgan taomni olib tashlab/kamaytirib bo'lmaydi ──
        _assert_removals_allowed(existing, items)

        # ── URY'ning optimistik qulfi ─────────────────────────────────
        # `sync_order` mavjud chekni yangilashda `last_modified_time` ni
        # TALAB QILADI: usiz u "Table occupied" deb Failure qaytaradi
        # (`ury_order.py:836`). Ilova buyurtmani yuklaganda `modified` ni
        # oladi va shu yerda qaytarib yuboradi — shu tariqa ikki ofitsant
        # bir stolni bir vaqtda o'zgartira olmaydi.
        if not last_modified_time:
            frappe.throw(
                _(
                    "Buyurtmani yangilash uchun uning joriy holati kerak. "
                    "Ilovada buyurtmani qayta yuklang."
                ),
                title=_("Eskirgan ma'lumot"),
            )

    customer = customer or scope.default_customer
    if not customer:
        frappe.throw(_("Mijoz ko'rsatilmagan va POS Profile'da standart mijoz yo'q"))

    mode_of_payment = _default_mode_of_payment(scope.pos_profile)

    # ── Chek EGASI — smenadagi KASSIR, ofitsant emas ──────────────────
    # ERPNext smena hisobotini `POS Invoice.owner` bo'yicha yig'adi
    # (`pos_closing_entry.get_pos_invoices`: `where owner = %s`). Agar chek
    # egasi ofitsant bo'lib qolsa, uning olgan buyurtmasi kassirning
    # Z-hisobotiga UMUMAN TUSHMAYDI va kassa taqchilligi ko'rinadi.
    #
    # URY `waiter` va `cashier` ni alohida maydonlarda saqlaydi — biz
    # ham shunday qilamiz: ofitsant `waiter` da, kassir `cashier`/`owner` da.
    shift_user = _current_shift_user(scope) or frappe.session.user

    # ── URY'ning MAVJUD oqimi (POS Invoice + KOT) ─────────────────────
    from ozturkapp.ozturkapp.overrides.ury_order import sync_order

    result = sync_order(
        items=items,
        cashier=shift_user,
        owner=shift_user,
        mode_of_payment=mode_of_payment,
        customer=customer,
        # Ofitsant mehmonlar sonini KIRITMAYDI — mavjud qiymat saqlanadi,
        # yangi buyurtmada 1 qo'yiladi. Kerak bo'lsa kassir to'g'rilaydi.
        no_of_pax=cint(pax) or _existing_pax(existing) or 1,
        last_invoice=existing,
        waiter=frappe.session.user,
        pos_profile=scope.pos_profile,
        last_modified_time=last_modified_time,
        table=table,
        invoice=existing,
        comments=comments,
        room=table_row.restaurant_room,
        client_ref=client_ref or str(uuid.uuid4()),
    )

    if isinstance(result, dict) and result.get("status") == "Failure":
        # `sync_order` konfliktda `msgprint` + Failure qaytaradi.
        frappe.throw(
            _("Buyurtma o'zgargan. Ilovani yangilab, qaytadan urinib ko'ring."),
            title=_("Konflikt"),
        )

    invoice_name = result.get("name") if isinstance(result, dict) else existing
    if not invoice_name:
        frappe.throw(_("Buyurtma yaratilmadi"))

    emit_order_change(scope.branch, invoice_name, "ORDER_UPDATED", table)
    emit_floor_change(scope.branch, [table], "ORDER_UPDATED", invoice_name)

    return get_order(invoice=invoice_name)


def _parse_items(items) -> list:
    """Mijozdan kelgan mahsulot ro'yxatini tozalaydi.

    Narx QABUL QILINMAYDI — u serverda `Item Price` dan olinadi (TZ §12).
    """
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except ValueError:
            frappe.throw(_("Mahsulot ro'yxati noto'g'ri formatda"))

    if not isinstance(items, list):
        frappe.throw(_("Mahsulot ro'yxati noto'g'ri formatda"))

    cleaned = []
    for row in items:
        code = (row or {}).get("item") or (row or {}).get("item_code")
        qty = cint((row or {}).get("qty"))

        if not code or qty <= 0:
            continue
        if not frappe.db.exists("Item", code):
            frappe.throw(_("Mahsulot topilmadi: {0}").format(code))

        cleaned.append(
            {
                "item": code,
                "item_name": frappe.db.get_value("Item", code, "item_name"),
                "qty": qty,
                "comment": (row or {}).get("comment") or "",
            }
        )
    return cleaned


def _assert_removals_allowed(invoice: str, incoming: list):
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


def _current_shift_user(scope):
    """Ochiq smenani kim ochgan (kassir). Smena bo'lmasa `None`."""
    from ozturkapp.ozturkapp.api.desktop_pos import _get_user_room, _open_opening_entry

    room = ""
    try:
        room = _get_user_room(scope.branch)
    except Exception:
        pass

    opening = _open_opening_entry(scope.branch, scope.pos_profile, room)
    return frappe.db.get_value("POS Opening Entry", opening, "user") if opening else None


def _existing_pax(invoice) -> int:
    """Mavjud chekdagi mehmonlar soni (ofitsant uni o'zgartirmaydi)."""
    if not invoice:
        return 0
    return cint(frappe.db.get_value("POS Invoice", invoice, "no_of_pax"))


def _default_mode_of_payment(pos_profile: str) -> str:
    methods = cashier_billing.get_payment_methods(pos_profile)
    if not methods:
        frappe.throw(_("POS Profile'da to'lov usuli sozlanmagan"))
    for m in methods:
        if m["default"]:
            return m["mode_of_payment"]
    return methods[0]["mode_of_payment"]


# ═══════════════════════════════════════════════════════════════════
#  6. Hisob so'rash (TZ §7.8)
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def request_bill(invoice):
    """Mijoz hisob so'radi — kassir buni ko'radi.

    Bu TO'LOV EMAS va chekni submit QILMAYDI. Faqat bayroq qo'yiladi;
    to'lovni faqat kassir amalga oshiradi (TZ §7.8, §6).
    """
    require_waiter()
    scope = cashier_permissions.resolve_scope()
    row = cashier_permissions.assert_invoice_in_scope(invoice, scope, docstatus=0)

    if not frappe.db.count("POS Invoice Item", {"parent": invoice}):
        frappe.throw(_("Bo'sh buyurtma uchun hisob so'rab bo'lmaydi"))

    if not cint(row.custom_bill_requested):
        frappe.db.set_value(
            "POS Invoice",
            invoice,
            {
                "custom_bill_requested": 1,
                "custom_bill_requested_at": frappe.utils.now_datetime(),
                "custom_bill_requested_by": frappe.session.user,
            },
            update_modified=False,
        )

    emit_order_change(scope.branch, invoice, "BILL_REQUESTED", row.restaurant_table)
    if row.restaurant_table:
        emit_floor_change(
            scope.branch, [row.restaurant_table], "BILL_REQUESTED", invoice
        )

    # Kassirga KO'RINADIGAN xabar. Yuqoridagi hodisalar ekranni jim
    # yangilaydi — kassir aynan o'sha stolga qarab turmasa sezmaydi.
    notifications.bill_requested(
        scope.branch, invoice, row.restaurant_table, frappe.session.user
    )

    return {"invoice": invoice, "bill_requested": True}


# ═══════════════════════════════════════════════════════════════════
#  6.5. Buyurtmani bekor qilish (TZ §8, §14)
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def cancel_order(invoice, reason):
    """Xato olingan buyurtmani BUTUNLAY bekor qilish.

    QACHON MUMKIN
    =============
    Oshxona ishga kirishmaguncha — ya'ni chekdagi HAMMA taom hali
    «Kutilmoqda» holatida turganda. Oshpaz birortasini boshlab yuborsa,
    ofitsant uchun yo'l yopiladi (`utils/order_cancel.py`).

    Bu qoida `submit_order()` dagi qoidaning davomi:

        bitta taomni olib tashlash   -> `_assert_removals_allowed()`
        BUTUN buyurtmani bekor qilish -> shu metod

    Ikkalasi ham `kitchen_status` dagi AYNI holatga qaraydi, shuning
    uchun ilova ikki xil javob olmaydi.

    NEGA `submit_order` ni bo'sh ro'yxat bilan chaqirish MUMKIN EMAS
    ===============================================================
    Bekor qilish — alohida, ataylab qilinadigan amal va u SABAB talab
    qiladi (sabab hisobotga tushadi). Bo'sh ro'yxat esa ilovadagi
    xatodan ham kelib chiqishi mumkin, shuning uchun `submit_order`
    kamida bitta taom talab qilishda davom etadi.

    Args:
        invoice: `POS Invoice` nomi.
        reason: bekor qilish sababi — majburiy.

    Returns:
        dict: invoice, cancelled_items, freed_tables, kitchen_started
    """
    require_waiter()
    scope = cashier_permissions.resolve_scope()
    row = cashier_permissions.assert_invoice_in_scope(invoice, scope, docstatus=0)

    if cint(row.invoice_printed):
        frappe.throw(
            _("Hisob allaqachon chiqarilgan — buyurtmani bekor qilib bo'lmaydi."),
            title=_("Buyurtma yopilgan"),
        )

    from ozturkapp.ozturkapp.utils import order_cancel

    return order_cancel.cancel_invoice(row, reason, scope)


# ═══════════════════════════════════════════════════════════════════
#  7. Mijoz yaratish (TZ §7.3 — ixtiyoriy)
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def create_customer(customer_name, mobile_number=None):
    """Yangi mijoz — URY'ning MAVJUD funksiyasidan."""
    require_waiter()

    from ury.ury_pos.api import create_customer as _create

    return _create(customer_name=customer_name, mobile_number=mobile_number)


@frappe.whitelist()
def search_customers(query=None, limit=20):
    require_waiter()

    filters = {}
    if query:
        filters = {"customer_name": ["like", f"%{query}%"]}

    return frappe.get_all(
        "Customer",
        filters=filters,
        fields=["name", "customer_name", "mobile_number"],
        limit_page_length=cint(limit) or 20,
        order_by="modified desc",
    )
