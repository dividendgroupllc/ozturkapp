# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Oshxona (KDS) uchun sozlash — custom fieldlar, rol va ruxsatlar (TZ §21, §22, §15).

NEGA CUSTOM FIELD
=================
`URY KOT Items` da mahsulot darajasidagi tayyorlash holati UMUMAN YO'Q.
URY faqat KOT darajasida `order_status` ni ("Ready For Prepare" -> "Served")
saqlaydi. TZ §4 esa HAR BIR MAHSULOT uchun alohida holat talab qiladi.

TZ §21/§22 ga ko'ra yangi DocType YARATILMAYDI — mavjud `URY KOT Items`
ga minimal custom field qo'shiladi.

`allow_on_submit = 1` MAJBURIY
==============================
`URY KOT` yaratilishi bilan submit qilinadi (`kot_doc.insert(); kot_doc.submit()`).
Submit qilingan hujjatning bola jadvali odatdagi `save()` bilan o'zgarmaydi —
shuning uchun bu maydonlar `allow_on_submit` bo'lishi shart.

`order_status` GA TEGILMAYDI
============================
URY'ning Mosaic KDS'i (`/URYMosaic`) `kot_list()` orqali
`order_status == "Ready For Prepare"` bo'yicha filtrlaydi. Uning
semantikasini o'zgartirsak Mosaic buziladi (TZ §18/#18, #19).
Biz `order_status` ni faqat URY'ning o'zi kabi — hamma mahsulot berilganda
"Served" ga o'tkazamiz.

Ishga tushirish::

    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.kitchen_setup.setup

    # Oshxona/bar marshruti — KOT yaratilishi uchun MAJBURIY.
    # `after_migrate` da ATAYLAB yo'q: bu ma'lumot sozlamasi, har
    # migratsiyada qayta yozilmasligi kerak.
    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.kitchen_setup.create_production_units
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.permissions import add_permission, update_permission_property

#: Oshxona xodimi roli. URY'da oshxonaga oid rol YO'Q (faqat Cashier,
#: Captain, Manager), shuning uchun bu dublikat emas (TZ §15).
KITCHEN_ROLE = "URY Kitchen"

#: Oshxona ekraniga kira oladigan rollar.
KITCHEN_ROLES = (KITCHEN_ROLE, "URY Manager", "System Manager")

#: Mahsulot holatlari — `utils/kitchen_status.py` bilan bir xil bo'lishi shart.
STATUS_OPTIONS = "Pending\nPreparing\nReady\nServed\nCancelled"


KITCHEN_FIELDS = {
    "URY KOT Items": [
        {
            "fieldname": "custom_kitchen_status",
            "label": "Kitchen Status",
            "fieldtype": "Select",
            "options": STATUS_OPTIONS,
            "default": "Pending",
            "in_list_view": 1,
            "allow_on_submit": 1,
            "insert_after": "comments",
            "description": (
                "Mahsulotning tayyorlanish holati. Faqat oshxona ekrani orqali "
                "o'zgartiriladi — o'tishlar serverda tekshiriladi."
            ),
        },
        {
            "fieldname": "custom_started_at",
            "label": "Started At",
            "fieldtype": "Datetime",
            "read_only": 1,
            "allow_on_submit": 1,
            "insert_after": "custom_kitchen_status",
        },
        {
            "fieldname": "custom_ready_at",
            "label": "Ready At",
            "fieldtype": "Datetime",
            "read_only": 1,
            "allow_on_submit": 1,
            "insert_after": "custom_started_at",
        },
        {
            "fieldname": "custom_served_at",
            "label": "Served At",
            "fieldtype": "Datetime",
            "read_only": 1,
            "allow_on_submit": 1,
            "insert_after": "custom_ready_at",
        },
        {
            # Audit (TZ §33) — Frappe'ning Version mexanizmi bola jadval
            # `db_set` o'zgarishlarini yozmaydi, shuning uchun kim
            # o'zgartirganini o'zimiz saqlaymiz.
            "fieldname": "custom_status_changed_by",
            "label": "Status Changed By",
            "fieldtype": "Link",
            "options": "User",
            "read_only": 1,
            "allow_on_submit": 1,
            "insert_after": "custom_served_at",
        },
    ]
}


#: Oshxona xodimi NIMANI ko'ra oladi. Narx, mijoz, to'lov, buxgalteriya —
#: YO'Q (TZ §15, §27). `URY KOT` ga `write` beriladi, chunki mahsulot
#: holati bola jadvalda turadi; `submit`/`cancel`/`create` BERILMAYDI.
KITCHEN_PERMISSIONS = {
    "URY KOT": {KITCHEN_ROLE: ["read", "write", "print", "report"]},
    "URY Production Unit": {KITCHEN_ROLE: ["read"]},
    "URY Table": {KITCHEN_ROLE: ["read"]},
    "URY Menu Course": {KITCHEN_ROLE: ["read"]},
    "Item": {KITCHEN_ROLE: ["read"]},
}


def setup():
    """Custom fieldlar + rol + ruxsatlar (idempotent)."""
    create_fields()
    create_role()
    create_permissions()
    frappe.db.commit()


#: `URY Production Unit` uchun — "o'zi olib boriladi" bayrog'i.
#:
#: NEGA STANSIYA DARAJASIDA
#: ========================
#: Ichimlikni oshxona tayyorlamaydi: ofitsant barga borib oladi va
#: mijozga eltadi. Ya'ni unga "Tayyorlanmoqda -> Tayyor" bosqichlari
#: ma'nosiz — faqat "berildi" kerak.
#:
#: Bayroq TAOM darajasida emas, STANSIYA darajasida turadi: item guruhini
#: bir joyda (stansiya sozlamasida) belgilash yetarli, har bir taomga
#: alohida tegish shart emas. Yangi ichimlik qo'shilsa u avtomatik shu
#: qoidaga tushadi.
SELF_SERVICE_FIELD = {
    "URY Production Unit": [
        {
            "fieldname": "custom_self_service",
            "label": "O'zi olib boriladi (bar)",
            "fieldtype": "Check",
            "default": "0",
            "insert_after": "warehouse",
            "description": (
                "Belgilansa: bu nuqta OSHXONA EKRANIDA KO'RINMAYDI. "
                "Mahsulotni ofitsant mobil ilovadan «Berildi» deb belgilaydi "
                "(Kutilmoqda -> Berildi, oraliq bosqichlarsiz)."
            ),
        }
    ]
}


#: Foydalanuvchini bitta oshxona stansiyasiga biriktirish.
#:
#: NEGA KERAK
#: ==========
#: Stansiya tanlash avval FAQAT brauzer sozlamasi edi (localStorage),
#: standart holat esa "barcha stansiyalar" — bitta oshpaz (masalan
#: faqat non pishiruvchi) BOSHQA stansiya taomlarini ham (shu jumladan
#: bar) ko'rib turardi. Bu maydon SERVERDA majburlanadi
#: (`api/kitchen.py:_sees_all_stations`) — oddiy oshxona xodimi
#: (faqat `URY Kitchen` roli) FAQAT shu maydonda ko'rsatilgan
#: stansiyani ko'radi va o'zgartira oladi. Menejer/Administrator
#: cheklanmaydi.
KITCHEN_STATION_FIELD = {
    "User": [
        {
            "fieldname": "custom_kitchen_station",
            "label": "Ury Production Unit",
            "fieldtype": "Link",
            "options": "URY Production Unit",
            "insert_after": "roles",
        }
    ]
}


#: Stansiya darajasida — "Tayyor" bosilganda mahsulot cheki avtomatik
#: chop etilsinmi.
#:
#: NEGA STANSIYA DARAJASIDA
#: ========================
#: `custom_self_service` kabi — bir joyda yoqilsa yetarli, har bir taomga
#: alohida tegish shart emas. Har bir stansiya (masalan "Non") printerini
#: mustaqil yoqib/o'chirib sozlaydi (TZ-tashqi talab).
PRINT_ON_READY_FIELD = {
    "URY Production Unit": [
        {
            "fieldname": "custom_print_on_ready",
            "label": "Tayyor bo'lganda chek chiqarilsin",
            "fieldtype": "Check",
            "default": "0",
            "insert_after": "custom_self_service",
            "description": (
                "Yoqilsa: bu stansiyada oshpaz mahsulotni «Tayyor» deb "
                "belgilaganda, brauzerda mahsulot ma'lumoti bilan chop "
                "etish oynasi avtomatik ochiladi."
            ),
        }
    ]
}


def create_fields():
    create_custom_fields(KITCHEN_FIELDS, ignore_validate=True)
    create_custom_fields(SELF_SERVICE_FIELD, ignore_validate=True)
    create_custom_fields(PRINT_ON_READY_FIELD, ignore_validate=True)
    create_custom_fields(KITCHEN_STATION_FIELD, ignore_validate=True)
    print(
        "✅ Oshxona custom fieldlari tayyor (URY KOT Items, URY Production Unit, User)"
    )


def create_role():
    if frappe.db.exists("Role", KITCHEN_ROLE):
        print(f"⏭️  Rol allaqachon bor: {KITCHEN_ROLE}")
        return

    frappe.get_doc(
        {
            "doctype": "Role",
            "role_name": KITCHEN_ROLE,
            "desk_access": 1,
            "is_custom": 1,
        }
    ).insert(ignore_permissions=True)
    print(f"✅ Rol yaratildi: {KITCHEN_ROLE}")


def create_permissions():
    """Faqat KERAKLI bayroqlarni yoqadi, hech qachon o'chirmaydi."""
    granted = 0

    for doctype, roles in KITCHEN_PERMISSIONS.items():
        if not frappe.db.exists("DocType", doctype):
            print(f"⏭️  DocType yo'q: {doctype}")
            continue

        for role, ptypes in roles.items():
            if not frappe.db.exists("Role", role):
                continue

            existing = frappe.db.get_value(
                "Custom DocPerm",
                {"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0},
                "name",
            )
            if not existing:
                add_permission(doctype, role, 0)

            for ptype in ptypes:
                current = frappe.db.get_value(
                    "Custom DocPerm",
                    {"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0},
                    ptype,
                )
                if current:
                    continue
                update_permission_property(doctype, role, 0, ptype, 1, validate=False)
                granted += 1

    frappe.clear_cache()
    print(f"✅ Oshxona ruxsatlari tayyor ({granted} ta yangi)")
    return granted


# ═══════════════════════════════════════════════════════════════════
#  URY Production Unit — oshxona/bar marshruti
# ═══════════════════════════════════════════════════════════════════
#
# NEGA BU KERAK
# =============
# `URY Production Unit` bo'lmasa KOT UMUMAN yaratilmaydi. URY buni
# `ury/ury/api/ury_kot_generate.py:183` da to'xtatadi:
#
#     Create URY Production unit against POS Profile: <profil>
#
# Xato esa `ury_order.py:950` da `frappe.log_error()` bilan YUTIB
# YUBORILADI — buyurtma muvaffaqiyatli yaratilgandek ko'rinadi, lekin
# oshxona ekraniga hech narsa tushmaydi. Nosozlik faqat Error Log'da
# ko'rinadi, shuning uchun uni sezish qiyin.
#
# `pos_setup.py` da ham shunday funksiya bor, lekin u QATTIQ YOZILGAN
# filial/profil nomlariga tayanadi va butun demo-sozlashning bir qismi.
# Bu yerdagi variant esa saytdagi HAQIQIY ma'lumotdan kelib chiqadi,
# shuning uchun istalgan filialda ishlaydi.

#: Ichimlik guruhini nomi bo'yicha aniqlash — qolgani oshxonaga ketadi.
#: Qisqa so'zlar (bar) FAQAT to'liq moslikda — aks holda "Barbecue" ham
#: barga tushib ketardi.
_DRINK_HINTS = ("напит", "drink", "beverage", "ichimlik", "coffee", "чай", "кофе")
_DRINK_EXACT = ("bar", "бар")

BAR_UNIT = "Bar"
KITCHEN_UNIT = "Oshxona"


def _menu_item_groups(pos_profile: str) -> list:
    """Faol menyudagi taomlarning Item Group'lari."""
    restaurant = frappe.db.get_value("POS Profile", pos_profile, "restaurant")
    menu = frappe.db.get_value("URY Restaurant", restaurant, "active_menu") if restaurant else None
    if not menu:
        return []

    groups = frappe.db.sql(
        """
        SELECT DISTINCT i.item_group
        FROM `tabURY Menu Item` m
        INNER JOIN `tabItem` i ON i.name = m.item
        WHERE m.parent = %s AND IFNULL(m.disabled, 0) = 0
        """,
        menu,
        pluck=True,
    )
    return [g for g in groups if g]


def _is_drink(item_group: str) -> bool:
    low = (item_group or "").strip().lower()
    return low in _DRINK_EXACT or any(hint in low for hint in _DRINK_HINTS)


def create_production_units(pos_profile: str = None):
    """Oshxona va bar nuqtalarini yaratadi (idempotent).

    Guruhlar faol menyudan olinadi: ichimliklar `Bar` ga, qolgan hamma
    narsa `Oshxona` ga yo'naltiriladi.

    Args:
        pos_profile: POS Profile nomi. Berilmasa — saytdagi yagonasi.
    """
    if not pos_profile:
        profiles = frappe.get_all("POS Profile", pluck="name")
        if len(profiles) != 1:
            frappe.throw(
                "Saytda {0} ta POS Profile bor — qaysi biri ekanini ko'rsating.".format(
                    len(profiles)
                )
            )
        pos_profile = profiles[0]

    profile = frappe.db.get_value(
        "POS Profile", pos_profile, ["branch", "warehouse"], as_dict=True
    )
    if not profile:
        frappe.throw("POS Profile topilmadi: {0}".format(pos_profile))

    groups = _menu_item_groups(pos_profile)
    if not groups:
        print("⏭️  Faol menyu yoki undagi taomlar topilmadi — nuqta yaratilmadi")
        return 0

    plan = {
        KITCHEN_UNIT: [g for g in groups if not _is_drink(g)],
        BAR_UNIT: [g for g in groups if _is_drink(g)],
    }

    touched = 0
    for unit_name, unit_groups in plan.items():
        if not unit_groups:
            print(f"⏭️  {unit_name}: mos guruh yo'q")
            continue

        existing = frappe.db.get_value(
            "URY Production Unit",
            {"production": unit_name, "pos_profile": pos_profile},
            "name",
        )

        if existing:
            doc = frappe.get_doc("URY Production Unit", existing)
            have = {r.item_group for r in doc.item_groups}
            added = [g for g in unit_groups if g not in have]
            for g in added:
                doc.append("item_groups", {"item_group": g})
            if added:
                doc.save(ignore_permissions=True)
                print(f"✅ {unit_name}: {len(added)} ta guruh qo'shildi")
            else:
                print(f"⏭️  {unit_name}: allaqachon to'g'ri")
            touched += 1
            continue

        doc = frappe.new_doc("URY Production Unit")
        doc.production = unit_name
        doc.pos_profile = pos_profile
        doc.branch = profile.branch
        doc.warehouse = profile.warehouse
        for g in unit_groups:
            doc.append("item_groups", {"item_group": g})
        doc.insert(ignore_permissions=True)
        touched += 1
        print(f"✅ {unit_name} yaratildi ({len(unit_groups)} ta guruh)")

    frappe.db.commit()

    # Printer ALOHIDA masala — u real qurilma nomiga bog'liq va Desk'dan
    # biriktiriladi. Printersiz KOT yaratiladi (oshxona ekrani ishlaydi),
    # faqat qog'oz chek chiqmaydi.
    no_printer = []
    for name in frappe.get_all(
        "URY Production Unit", filters={"pos_profile": pos_profile}, pluck="name"
    ):
        if not frappe.db.count("URY Printer Settings", {"parent": name}):
            no_printer.append(name)
    if no_printer:
        print(f"ℹ️  Printer biriktirilmagan: {', '.join(no_printer)} (KOT baribir yaratiladi)")

    return touched
