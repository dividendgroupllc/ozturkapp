# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Oshxona (KDS) API — TZ §23, §24, §27.

OSHXONA BUYURTMA YARATMAYDI
===========================
Bu modulda buyurtma yaratish, mahsulot qo'shish, narx yoki to'lovga oid
BIRORTA amal yo'q (TZ §27). Oshxona faqat MAVJUD `URY KOT` yozuvlarining
mahsulot holatini o'zgartiradi.

KOT YARATISH URY'NING ISHI
==========================
`ury/api/ury_kot_generate.py` ga TEGILMAYDI. KOT'lar avvalgidek ofitsant/POS
oqimida yaratiladi, printerga yuboriladi va Mosaic KDS'ga ko'rinadi.
Bu sahifa — QO'SHIMCHA operatsion ekran (TZ §28).
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime, time_diff_in_seconds

from ozturkapp.ozturkapp.setup.kitchen_setup import KITCHEN_ROLE, KITCHEN_ROLES
from ozturkapp.ozturkapp.utils import cashier_permissions, kitchen_status, notifications
from ozturkapp.ozturkapp.utils.kitchen_realtime import emit_kot_change, emit_item_change

#: Oshxonaga ovqat tayyorlash uchun keladigan KOT turlari.
COOKING_KOT_TYPES = ("New Order", "Order Modified", "Duplicate")

#: Bekor qilish ko'rsatmasi bo'lgan KOT turlari — bular OVQAT EMAS (TZ §9).
#: Ta'rif `utils/kitchen_status.py` da — u yerda `order_cancel` ham
#: shu ro'yxatga tayanadi, ikki joyda ayri yozilsa bir kun ajralib ketardi.
CANCELLATION_KOT_TYPES = kitchen_status.CANCELLATION_KOT_TYPES

#: Ekranda ko'rsatiladigan vaqt oynasi (soat).
KOT_WINDOW_HOURS = 12


# ═══════════════════════════════════════════════════════════════════
#  Ruxsat
# ═══════════════════════════════════════════════════════════════════

def require_kitchen():
    """Oshxona ekranining HAR BIR metodi shu bilan boshlanadi (TZ §15)."""
    if frappe.session.user == "Guest":
        raise frappe.PermissionError(_("Iltimos, tizimga kiring"))

    if not set(frappe.get_roles()).intersection(KITCHEN_ROLES):
        raise frappe.PermissionError(
            _("Oshxona ekraniga ruxsat yo'q. Kerakli rollardan biri: {0}").format(
                ", ".join(KITCHEN_ROLES)
            )
        )


# ═══════════════════════════════════════════════════════════════════
#  Kontekst
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_kitchen_context():
    """Oshxona sahifasining boshlang'ich konteksti."""
    require_kitchen()
    branch = cashier_permissions.resolve_branch()

    # "O'zi olib boriladi" nuqtalari (bar) oshxona ekranida UMUMAN
    # ko'rinmaydi — ularni ofitsant mobil ilovadan yopadi. Ro'yxatdan ham
    # olib tashlanadi, aks holda oshpaz uni tanlab, hech qachon
    # tayyorlamaydigan ichimliklarni ko'rib turardi.
    stations = [
        st
        for st in frappe.get_all(
            "URY Production Unit",
            filters={"branch": branch},
            fields=["name", "production", "branch"],
            order_by="name asc",
        )
        if st.name not in kitchen_status.self_service_stations()
    ]

    return {
        "branch": branch,
        "user": frappe.session.user,
        "full_name": frappe.db.get_value("User", frappe.session.user, "full_name"),
        "stations": stations,
        "statuses": [
            {"key": s, "label": kitchen_status.label(s)}
            for s in kitchen_status.OPEN_STATUSES
        ],
        "events": {
            "kot": "ozturk_kitchen_kot",
            "item": "ozturk_kitchen_item",
            # Yangi buyurtma tushganda KO'RINADIGAN xabar.
            "notify": notifications.EVENT_NOTIFY,
        },
        "server_time": frappe.utils.now(),
    }


# ═══════════════════════════════════════════════════════════════════
#  O'qish
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_active_kots(station=None, include_served=0):
    """Faol KOT'lar ro'yxati (TZ §3, §10, §17, §18).

    Har bir KOT ALOHIDA ko'rsatiladi — keyingi raundda qo'shilgan mahsulotlar
    yangi KOT sifatida keladi va eski KOT takrorlanmaydi (TZ §10).
    """
    require_kitchen()
    branch = cashier_permissions.resolve_branch()

    filters = {
        "branch": branch,
        "docstatus": 1,
        "creation": [">", frappe.utils.add_to_date(now_datetime(), hours=-KOT_WINDOW_HOURS)],
    }
    if station:
        filters["production"] = station
    else:
        # Stansiya tanlanmagan = "barcha stansiyalar". Bar KOT'lari bu
        # yerga ham TUSHMASLIGI kerak, aks holda oshpaz standart ko'rinishda
        # ichimliklarni ko'rardi.
        hidden = kitchen_status.self_service_stations()
        if hidden:
            filters["production"] = ["not in", list(hidden)]

    kots = frappe.get_all(
        "URY KOT",
        filters=filters,
        # DIQQAT: `custom_merged_tables` bu yerda SO'RALMAYDI.
        # `ury_kot_generate.create_kot_doc()` uni KOT hujjatiga yozmoqchi
        # bo'ladi, lekin `URY KOT` da bunday maydon TA'RIFLANMAGAN — Frappe
        # uni jimgina tashlab yuboradi va bazada ustun ham yo'q.
        # Shuning uchun birlashtirilgan stollar chekdan olinadi.
        fields=[
            "name", "invoice", "restaurant_table",
            "customer_name", "type", "order_status", "production", "order_no",
            "comments", "creation", "date", "time", "table_takeaway",
            "user", "verified", "is_aggregator",
        ],
        order_by="creation asc",  # eng eski birinchi (TZ §18)
    )
    if not kots:
        return []

    items_by_kot = _load_items([k.name for k in kots])
    invoice_meta = _invoice_meta([k.invoice for k in kots])
    now = now_datetime()

    result = []
    for kot in kots:
        items = items_by_kot.get(kot.name, [])
        is_cancellation = kot.type in CANCELLATION_KOT_TYPES

        derived = kitchen_status.derive_kot_status([i["status"] for i in items])

        # ── Karta ro'yxatda qolsinmi? ─────────────────────────────
        if is_cancellation:
            # «To'xtat» kartasi FAQAT oshxona ishni boshlab yuborgan
            # bo'lsa kerak. Boshlanmagan bo'lsa to'xtatadigan ish yo'q va
            # `_on_cancellation_kot()` uni allaqachon yopib qo'ygan
            # (`verified = 1`, `order_status = "Cancelled"`) — o'sha
            # maydonlar Mosaic KDS uchun ham signal.
            if cint(kot.verified) or (kot.order_status or "") == "Cancelled":
                continue

        # Yakunlangan KOT'lar ro'yxatni to'ldirmasin.
        elif not cint(include_served):
            if derived in (kitchen_status.SERVED, kitchen_status.CANCELLED):
                continue

        result.append(
            {
                "kot": kot.name,
                "invoice": kot.invoice,
                "order_no": kot.order_no,
                "table": kot.restaurant_table,
                "merged_tables": invoice_meta.get(kot.invoice, {}).get("merged_tables"),
                "takeaway": bool(cint(kot.table_takeaway)),
                "customer": kot.customer_name,
                "waiter": invoice_meta.get(kot.invoice, {}).get("waiter", ""),
                "station": kot.production,
                "type": kot.type,
                # Bekor qilish KOT'i — OVQAT BUYURTMASI EMAS (TZ §9).
                "is_cancellation": is_cancellation,
                "verified": bool(cint(kot.verified)),
                "comments": kot.comments,
                "status": kitchen_status.CANCELLED if is_cancellation else derived,
                "status_label": kitchen_status.label(
                    kitchen_status.CANCELLED if is_cancellation else derived
                ),
                "created_at": str(kot.creation or ""),
                "elapsed_seconds": int(max(0, time_diff_in_seconds(now, kot.creation))),
                "items": items,
            }
        )

    return result


@frappe.whitelist()
def get_kot(kot):
    """Bitta KOT — o'tish xatosidan keyin nuqtali yangilash uchun (TZ §31, §32)."""
    require_kitchen()
    branch = cashier_permissions.resolve_branch()
    _assert_kot_in_branch(kot, branch)

    for row in get_active_kots(include_served=1):
        if row["kot"] == kot:
            return row
    return None


def _load_items(kot_names: list) -> dict:
    """Barcha KOT mahsulotlarini BITTA so'rovda yuklaydi (TZ §32)."""
    if not kot_names:
        return {}

    rows = frappe.get_all(
        "URY KOT Items",
        filters={"parent": ["in", kot_names], "parenttype": "URY KOT"},
        fields=[
            "name", "parent", "idx", "item", "item_name", "quantity",
            "cancelled_qty", "comments", "course", "serve_priority",
            "indicate_course", "custom_kitchen_status", "custom_started_at",
            "custom_ready_at", "custom_served_at", "custom_status_changed_by",
        ],
        # Kurs ustuvorligi URY'da allaqachon bor — qayta ixtiro qilmaymiz (TZ §18).
        order_by="parent asc, serve_priority asc, idx asc",
    )

    now = now_datetime()
    grouped = {}
    for row in rows:
        status = kitchen_status.normalize(row.custom_kitchen_status)

        # Bekor qilingan taom oshxona ekranidan BUTUNLAY yo'qoladi.
        #
        # Uni "Bekor qilingan" yorlig'i bilan ko'rsatish ham mumkin edi,
        # lekin oshpazga bu shovqin: u pishirmaydigan narsa ro'yxatni
        # to'ldirib turadi. 4 ta taomdan 1 tasi olib tashlansa ekranda
        # aniq 3 ta qolishi kerak.
        if status == kitchen_status.CANCELLED:
            continue

        action = kitchen_status.next_action(status)

        elapsed = None
        if status == kitchen_status.PREPARING and row.custom_started_at:
            elapsed = int(max(0, time_diff_in_seconds(now, row.custom_started_at)))

        grouped.setdefault(row.parent, []).append(
            {
                "id": row.name,
                "idx": row.idx,
                "item": row.item,
                "item_name": row.item_name or row.item,
                "qty": cint(row.quantity),
                "cancelled_qty": cint(row.cancelled_qty),
                "comments": row.comments,
                "course": row.course,
                "indicate_course": bool(cint(row.indicate_course)),
                "status": status,
                "status_label": kitchen_status.label(status),
                "next": action,
                "can_waiter_cancel": kitchen_status.can_waiter_cancel(status),
                "started_at": str(row.custom_started_at or ""),
                "ready_at": str(row.custom_ready_at or ""),
                "served_at": str(row.custom_served_at or ""),
                "changed_by": row.custom_status_changed_by,
                "preparing_seconds": elapsed,
            }
        )
    return grouped


def _invoice_meta(invoices: list) -> dict:
    """Chek -> {waiter, merged_tables} (bitta so'rov).

    `URY KOT` da ofitsant ham, birlashtirilgan stollar ham saqlanmaydi —
    ikkalasi ham `POS Invoice` da turadi.
    """
    names = [i for i in set(invoices) if i]
    if not names:
        return {}

    rows = frappe.get_all(
        "POS Invoice",
        filters={"name": ["in", names]},
        fields=["name", "waiter", "custom_merged_tables"],
    )

    users = {r.waiter for r in rows if r.waiter}
    full_names = (
        dict(
            frappe.get_all(
                "User", filters={"name": ["in", list(users)]},
                fields=["name", "full_name"], as_list=True,
            )
        )
        if users
        else {}
    )

    return {
        r.name: {
            "waiter": full_names.get(r.waiter, r.waiter or ""),
            "merged_tables": r.custom_merged_tables,
        }
        for r in rows
    }


def _assert_kot_in_branch(kot: str, branch: str) -> frappe._dict:
    row = frappe.db.get_value(
        "URY KOT", kot, ["name", "branch", "docstatus", "production", "invoice", "type"],
        as_dict=True,
    )
    if not row:
        frappe.throw(_("KOT topilmadi: {0}").format(kot), frappe.DoesNotExistError)
    if row.branch != branch:
        raise frappe.PermissionError(_("Bu KOT boshqa filialga tegishli"))
    return row


# ═══════════════════════════════════════════════════════════════════
#  Yozish — YAGONA holat o'zgartirish nuqtasi
# ═══════════════════════════════════════════════════════════════════

@frappe.whitelist()
def update_kot_item_status(kot_item, status):
    """Mahsulotning tayyorlanish holatini o'zgartiradi (TZ §23, §24).

    Args:
        kot_item: `URY KOT Items` qatorining `name` si.
        status: maqsad holat (`Preparing` / `Ready` / `Served` / `Cancelled`).

    ATOMIYLIK (TZ §24)
    ==================
    Qator `SELECT ... FOR UPDATE` bilan qulflanadi va joriy holat QULFDAN
    KEYIN qayta o'qiladi. Ikki oshpaz bir vaqtda bossa — ikkinchisi
    "allaqachon shu holatda" xatosini oladi, holat ustiga yozilmaydi.

    Mijozdan kelgan "joriy holat" QABUL QILINMAYDI — u faqat bazadan olinadi.
    """
    require_kitchen()
    branch = cashier_permissions.resolve_branch()

    # ── Qatorni qulflaymiz ────────────────────────────────────────────
    locked = frappe.db.sql(
        """
        SELECT ki.name, ki.parent, ki.custom_kitchen_status AS status,
               k.branch, k.docstatus, k.type, k.production, k.invoice
        FROM `tabURY KOT Items` ki
        INNER JOIN `tabURY KOT` k ON k.name = ki.parent
        WHERE ki.name = %s
        FOR UPDATE
        """,
        (kot_item,),
        as_dict=True,
    )
    if not locked:
        frappe.throw(
            _("Mahsulot topilmadi. Buyurtma o'zgargan bo'lishi mumkin."),
            frappe.DoesNotExistError,
        )

    row = locked[0]

    if row.branch != branch:
        raise frappe.PermissionError(_("Bu mahsulot boshqa filialga tegishli"))

    if cint(row.docstatus) != 1:
        frappe.throw(_("KOT bekor qilingan yoki tasdiqlanmagan"))

    if row.type in CANCELLATION_KOT_TYPES:
        # Bekor qilish KOT'i ovqat buyurtmasi emas (TZ §9).
        frappe.throw(_("Bekor qilish KOT'ining holatini o'zgartirib bo'lmaydi"))

    current = kitchen_status.normalize(row.status)
    target = (status or "").strip()

    # ── O'tish qoidasi (TZ §14 shu yerda kuchga kiradi) ───────────────
    # Bar nuqtasida oqim ikki bosqichli (Kutilmoqda -> Berildi).
    kitchen_status.assert_transition(
        current, target, self_service=row.production in kitchen_status.self_service_stations()
    )

    values = {
        "custom_kitchen_status": target,
        "custom_status_changed_by": frappe.session.user,
    }
    timestamp_field = kitchen_status.TIMESTAMP_FIELD.get(target)
    if timestamp_field:
        values[timestamp_field] = now_datetime()

    # Bola jadval — `db_set` orqali (KOT submit qilingan, custom fieldlar
    # `allow_on_submit = 1`).
    for field, value in values.items():
        frappe.db.set_value("URY KOT Items", kot_item, field, value, update_modified=False)

    frappe.logger("ozturk_kitchen").info(
        "KOT item %s: %s -> %s | user=%s", kot_item, current, target, frappe.session.user
    )

    _sync_kot_order_status(row.parent)

    emit_item_change(branch, row.parent, kot_item, target, row.production, row.invoice)
    emit_kot_change(branch, row.parent, "KOT_ITEM_STATUS_CHANGED", row.production, row.invoice)

    # TAOM TAYYOR -> OFITSANTGA xabar.
    # Faqat `Ready` da: "Tayyorlanmoqda" yoki "Berildi" ofitsantdan
    # HECH NARSA talab qilmaydi, ularga xabar bersak — u shovqinga
    # aylanadi va haqiqiy xabar ham e'tibordan qoladi.
    if target == kitchen_status.READY:
        item_row = frappe.db.get_value(
            "URY KOT Items", kot_item, ["item", "item_name"], as_dict=True
        )
        invoice_row = frappe.db.get_value(
            "POS Invoice", row.invoice, ["restaurant_table", "waiter"], as_dict=True
        ) or frappe._dict()
        notifications.item_ready(
            branch,
            row.invoice,
            (item_row or {}).get("item_name") or (item_row or {}).get("item") or "",
            invoice_row.get("restaurant_table"),
            invoice_row.get("waiter"),
            row.production,
        )

    return {
        "kot_item": kot_item,
        "kot": row.parent,
        "status": target,
        "status_label": kitchen_status.label(target),
        "next": kitchen_status.next_action(target),
        "can_waiter_cancel": kitchen_status.can_waiter_cancel(target),
    }


def _sync_kot_order_status(kot: str):
    """URY'ning KOT darajasidagi `order_status` ini MOSLIGICHA yangilaydi.

    URY'ning Mosaic KDS'i `order_status` ga tayanadi:
        "Ready For Prepare" -> ro'yxatda ko'rinadi
        "Served"            -> ro'yxatdan chiqadi

    Biz uning SEMANTIKASINI o'zgartirmaymiz — faqat BARCHA mahsulot
    berilganda "Served" ga o'tkazamiz, xuddi URY'ning `serve_kot()` kabi.
    Shu tufayli Mosaic va bu sahifa bir-biriga zid ko'rsatmaydi.
    """
    statuses = frappe.get_all(
        "URY KOT Items",
        filters={"parent": kot, "parenttype": "URY KOT"},
        pluck="custom_kitchen_status",
    )
    derived = kitchen_status.derive_kot_status(statuses)

    current = frappe.db.get_value("URY KOT", kot, "order_status")

    if derived == kitchen_status.SERVED and current != "Served":
        frappe.db.set_value("URY KOT", kot, "order_status", "Served", update_modified=False)
    elif derived != kitchen_status.SERVED and current == "Served":
        # Yangi mahsulot qo'shilgan bo'lsa KOT yana ishga qaytadi.
        frappe.db.set_value(
            "URY KOT", kot, "order_status", "Ready For Prepare", update_modified=False
        )
