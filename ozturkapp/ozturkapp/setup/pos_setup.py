# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""URY POS + Kassa to'liq sozlamasi (bitta kompaniya uchun).

Jazira'dagi ko'p filialli sxemaning bitta filialga moslashtirilgan varianti.
Barcha qadamlar idempotent — qayta ishga tushirsa dublikat yaratmaydi va
qo'lda (UI orqali) kiritilgan qiymatlarni buzmaydi.

    bench --site ozturk.local execute ozturkapp.ozturkapp.setup.pos_setup.run_all

Yaratiladigan zanjir:
    Kassir -> Branch -> Item Group -> Warehouse -> Customer
    -> Mode of Payment (+ hisoblar) -> URY Room -> URY Menu Course
    -> URY Menu (+ Price List) -> URY Restaurant -> URY Table
    -> POS Profile (+ PIN) -> URY Production Unit -> Kassa Filial

MUHIM — menyu tovarlari BU YERDA YARATILMAYDI. Skript bazadagi mavjud
`Готовый продукт` daraxti ostidagi tovarlarni o'qib menyuga qo'shadi. Ya'ni
tovarlar avval ERPNext'ga import qilingan bo'lishi kerak.
"""

import frappe
from frappe.utils import cint, flt

# =============================================================================
# KONFIGURATSIYA — kerak bo'lsa shu yerdan o'zgartiring
# =============================================================================

# DIQQAT: nomlar ozturk.local'da QO'LDA yaratilgan yozuvlarga mos. O'zgartirsangiz
# skript mavjudini topa olmay YANGISINI yaratadi (dublikat).
BRANCH = "Maksim Gorkiy"
RESTAURANT = "O'zTurk"
ROOM = "Ichki zal"
MENU = "O'zTurk Menu"
POS_PROFILE = "kassa"
POS_CUSTOMER = "Ozturk Maksim Gorkiy klient"
INVOICE_SERIES_PREFIX = "OZT-.YYYY.-"

#: POS ombori. None bo'lsa — mavjud POS Profile'dagi ombor ishlatiladi,
#: u ham bo'lmasa shu nomda yangisi yaratiladi.
POS_WAREHOUSE_NAME = None

#: 0 qo'yilsa stollar yaratilmaydi. «Stiker» rejimida stol kerak emas —
#: lekin keyinchalik «Stol» rejimiga o'tsangiz kerak bo'ladi.
TABLE_COUNT = 10
SEATS_PER_TABLE = 4

CASHIER_EMAIL = "kassa@gmail.com"
CASHIER_FIRST_NAME = "Kassir"
CASHIER_PIN = "1111"
ADMIN_PIN = "1234"

# Tayyor mahsulot daraxtining ildizi — menyu shu daraxt ostidan yig'iladi
FINISHED_GROUP = "Готовый продукт"

# POS'dan tashqaridagi (xom ashyo) guruhlar — faqat mavjudligi ta'minlanadi
RAW_GROUPS = ["Сырьё", "Полуфабрикат"]

# (Mode of Payment nomi, turi, hisob raqami, hisob nomi)
# account_number None bo'lsa — kompaniyaning mavjud naqd hisobi ishlatiladi.
PAYMENT_MODES = [
    ("Нахт", "Cash", None, None),
    ("Click Pass", "Bank", "1111", "Click Pass"),
    ("PayMe Go", "Bank", "1112", "PayMe Go"),
    ("Терминал Humo", "Bank", "1113", "Терминал Humo"),
    ("Терминал UzCard", "Bank", "1114", "Терминал UzCard"),
]

# ERPNext standart usullari — restoran oqimida ishlatilmaydi
DISABLE_MODES = ["Cheque", "Credit Card", "Wire Transfer", "Bank Draft", "Cash"]

# ─────────────────────────────────────────────────────────────────────────────
# MENYU KATEGORIYALARI (URY Menu Course)
# ─────────────────────────────────────────────────────────────────────────────
# Kategoriya = Item Group nomi. Kassa ekranidagi tugmalar aynan shu tartibda
# chiqadi. Ro'yxatda yo'q guruhlar oxiriga qo'shiladi (prioritet 100+).
#
# DIQQAT: POS kataloği `course` bo'sh tovarlarni butunlay filtrlab tashlaydi
# (ui/components/item_browser.py). Shuning uchun har bir menyu qatoriga course
# majburiy qo'yiladi.
MENU_COURSE_ORDER = [
    "Дёнер и Лаваш",
    "Пиде и Лахмаджун",
    "Кебаб и Мясные блюда",
    "Супы",
    "Салаты",
    "Гарниры",
    "Хлеб",
    "Десерты",
    "Напитки",
]

# ─────────────────────────────────────────────────────────────────────────────
# ISHLAB CHIQARISH NUQTALARI (oshxona / bar chek printerlari)
# ─────────────────────────────────────────────────────────────────────────────
# Printer qurilmasining o'zi (`Network Printer Settings`) BU YERDA ulanmaydi —
# u real qurilmaga bog'liq, ERPNext UI'dan biriktiriladi. Skript faqat
# "qaysi Item Group qaysi nuqtaga ketadi" xaritasini tayyorlaydi.
PRODUCTION_UNITS = [
    (
        "Oshxona",
        [
            "Дёнер и Лаваш",
            "Пиде и Лахмаджун",
            "Кебаб и Мясные блюда",
            "Супы",
            "Салаты",
            "Гарниры",
            "Хлеб",
        ],
    ),
    ("Bar", ["Напитки", "Десерты"]),
]

# ─────────────────────────────────────────────────────────────────────────────
# NARXLAR
# ─────────────────────────────────────────────────────────────────────────────
# Bu yerga narx yozilsa — menyu shu narx bilan yaratiladi. Bo'sh (0) qolsa
# `Item.standard_rate` ishlatiladi, u ham 0 bo'lsa tovar 0 so'mda qoladi va
# `run_all` oxirida ogohlantirish chiqaradi.
#
# Narxni keyinroq ERPNext UI'dan ham kiritish mumkin:
#     URY Menu -> "O'zTurk Menu" -> items jadvali -> rate ustuni -> Save
# (Save bosilganda URY o'zi Item Price'larni qayta hosil qiladi.)
#
# MUHIM: bu yerdagi narxlar FAQAT menyuda hali yo'q tovarlarga qo'llanadi.
# Menyuda allaqachon bor qatorning narxiga skript tegmaydi — UI'dagi
# tahrirlaringiz qayta ishga tushirishda yo'qolmaydi.
ITEM_PRICES = {
    # Дёнер и Лаваш
    "DÖNER DURUM": 0,
    "DÖNER DURUM CHICKEN": 0,
    "GOBIT DÖNER": 0,
    "GOBIT DÖNER CHICKEN": 0,
    "LAVASH ÜSTÜ": 0,
    "LAVASH ÜSTÜ CHICKEN": 0,
    "PORTION DÖNER": 0,
    "PORTION DÖNER CHICKEN": 0,
    # Пиде и Лахмаджун
    "CLOSED PIDE": 0,
    "KUŞBAŞI PIDE": 0,
    "LAHMACUN": 0,
    "MIXED PIDE": 0,
    "PIDE WITH CHEESE": 0,
    # Кебаб и Мясные блюда
    "ISKENDER KEBAB": 0,
    "ISKENDER KEBAB CHICKEN": 0,
    "TANDIR BEYTI": 0,
    "TANDIR BEYTI CHICKEN": 0,
    # Супы
    "LENTIL SOUP": 0,
    # Салаты
    "ARUGULA SALAD": 0,
    "ÇOBAN SALAD": 0,
    "SEASONAL SALAD": 0,
    # Гарниры
    "FRENCH FRIES": 0,
    "PILAV": 0,
    # Хлеб
    "AFGAN BREAD": 0,
    "LAVASH BREAD": 0,
    "TIRNAK BREAD": 0,
    # Напитки
    "AYRAN 0,5L": 0,
    "BARDAK TEA": 0,
    "COCA-COLA": 0,
    "FANTA": 0,
    "PEPSI": 0,
    "SPRITE": 0,
}

# ─────────────────────────────────────────────────────────────────────────────
# DESKTOP POS KO'RINISH SOZLAMALARI (POS Profile.custom_*)
# ─────────────────────────────────────────────────────────────────────────────
# Yangi POS Profile yaratilganda hammasi qo'llanadi. Mavjud profilda esa faqat
# BO'SH (None yoki "") maydonlar to'ldiriladi — UI'dagi tanlovlaringiz saqlanadi.
POS_DISPLAY_SETTINGS = {
    "custom_company_brand_name": "O'ZTURK",
    "custom_receipt_footer": "Xaridingiz uchun rahmat!",
    "custom_order_number_type": "Stiker",
    "custom_show_comment": 1,
    "custom_show_ticket": 1,
    "custom_show_customer": 0,
    "custom_show_history": 1,
    "custom_show_shifts": 0,
    "custom_order_type_dine_in": 1,
    "custom_order_type_take_away": 1,
    "custom_order_type_delivery": 0,
    "custom_order_type_delivery_saboy": 0,
    "custom_item_columns": 0,
    "custom_quick_slots_count": 4,
    "custom_enable_multiple_cashier": 1,
    # Printer: protokol/kenglik/kodirovka default, qurilma nomi UI'dan kiritiladi
    "customer_qz_printer_driver": "ESC/POS",
    "customer_qz_printer_width": 80,
    "customer_qz_printer_codepage": "CP1251",
}


def _company():
    company = frappe.db.get_value("Company", {}, "name")
    if not company:
        frappe.throw("Company topilmadi — avval setup wizard'ni yakunlang")
    return company


def _abbr(company):
    return frappe.db.get_value("Company", company, "abbr")


def _log(msg):
    print(msg)


# =============================================================================
# 1. BRANCH
# =============================================================================

def create_branch(users):
    """Branch yaratadi. URY «user» (URY User) jadvali majburiy — kamida
    bitta foydalanuvchi bo'lishi kerak, aks holda POS filialni topa olmaydi."""
    if frappe.db.exists("Branch", BRANCH):
        doc = frappe.get_doc("Branch", BRANCH)
        existing = {r.user for r in doc.user}
        added = [u for u in users if u and u not in existing]
        for u in added:
            doc.append("user", {"user": u, "room": ROOM if frappe.db.exists("URY Room", ROOM) else None})
        if added:
            doc.save(ignore_permissions=True)
            _log(f"✅ Branch'ga foydalanuvchi qo'shildi: {', '.join(added)}")
        else:
            _log(f"⏭️  Branch bor: {BRANCH}")
        return BRANCH

    doc = frappe.new_doc("Branch")
    doc.branch = BRANCH
    for u in users:
        if u:
            doc.append("user", {"user": u})
    doc.insert(ignore_permissions=True)
    _log(f"✅ Branch: {BRANCH} (foydalanuvchilar: {', '.join(u for u in users if u)})")
    return BRANCH


# =============================================================================
# 2. ITEM GROUP
# =============================================================================

def ensure_item_groups():
    """Faqat KERAKLI guruhlar borligini ta'minlaydi.

    Menyu daraxti (`Готовый продукт` ostidagi kategoriyalar) bu yerda
    YARATILMAYDI — u bazadagi real tovarlarga bog'liq va ERPNext'dan
    import qilingan bo'ladi. Skript unga tegmaydi.
    """
    for name in RAW_GROUPS:
        if not frappe.db.exists("Item Group", name):
            doc = frappe.new_doc("Item Group")
            doc.item_group_name = name
            doc.parent_item_group = "All Item Groups"
            doc.is_group = 0
            doc.insert(ignore_permissions=True)
            _log(f"✅ Item Group: {name}")

    if not frappe.db.exists("Item Group", FINISHED_GROUP):
        frappe.throw(
            f"«{FINISHED_GROUP}» Item Group topilmadi. Avval tovarlarni ERPNext'ga "
            f"import qiling — menyu shu daraxt ostidan yig'iladi."
        )

    if not frappe.db.get_value("Item Group", FINISHED_GROUP, "is_group"):
        frappe.db.set_value("Item Group", FINISHED_GROUP, "is_group", 1)
        _log(f"✅ «{FINISHED_GROUP}» guruh (is_group=1) qilib belgilandi")


# =============================================================================
# 2b. STOCK SETTINGS
# =============================================================================

def configure_stock_settings():
    """POS sotuvi uchun manfiy zaxiraga ruxsat beradi.

    POS Profile'da update_stock=1 — ya'ni har sotuv omborni kamaytiradi.
    Restoranda tayyor taom zaxirasi real vaqtda yuritilmaydi (xom ashyo
    keyinroq Production Entry / Purchase Receipt bilan kiritiladi), shuning
    uchun zaxira nol bo'lsa sotuv YIQILADI:

        Row #1: Item «LAHMACUN» has no stock in warehouse «Restoran Sklad - OMG»

    Jazira'da ham aynan shu sozlama yoqilgan (allow_negative_stock=1,
    update_stock=1) — POS omborlarida manfiy qoldiq bemalol uchraydi.
    """
    settings = frappe.get_single("Stock Settings")
    if settings.allow_negative_stock:
        _log("⏭️  Stock Settings: manfiy zaxiraga allaqachon ruxsat berilgan")
        return
    settings.allow_negative_stock = 1
    settings.save(ignore_permissions=True)
    _log("✅ Stock Settings: manfiy zaxiraga ruxsat berildi (POS sotuvi uchun)")


# =============================================================================
# 3. WAREHOUSE
# =============================================================================

def create_warehouse(company):
    """POS ombori. Mavjud POS Profile'niki ustuvor — qo'lda tanlangan omborni
    almashtirib yubormaslik uchun."""
    existing = frappe.db.get_value("POS Profile", POS_PROFILE, "warehouse")
    if existing:
        _log(f"⏭️  Warehouse POS Profile'dan olindi: {existing}")
        return existing

    if not POS_WAREHOUSE_NAME:
        fallback = frappe.db.get_value(
            "Warehouse", {"company": company, "is_group": 0}, "name", order_by="lft"
        )
        if not fallback:
            frappe.throw(f"'{company}' uchun ombor topilmadi")
        _log(f"⏭️  Warehouse (birinchi mavjud): {fallback}")
        return fallback

    abbr = _abbr(company)
    full_name = f"{POS_WAREHOUSE_NAME} - {abbr}"
    if frappe.db.exists("Warehouse", full_name):
        _log(f"⏭️  Warehouse bor: {full_name}")
        return full_name

    doc = frappe.new_doc("Warehouse")
    doc.warehouse_name = POS_WAREHOUSE_NAME
    doc.company = company
    doc.parent_warehouse = f"All Warehouses - {abbr}"
    doc.is_group = 0
    doc.insert(ignore_permissions=True)
    _log(f"✅ Warehouse: {doc.name}")
    return doc.name


# =============================================================================
# 4. POS MIJOZI
# =============================================================================

def create_pos_customer():
    if frappe.db.exists("Customer", POS_CUSTOMER):
        _log(f"⏭️  Customer bor: {POS_CUSTOMER}")
        return POS_CUSTOMER

    # Ma'noli guruh/hudud tanlaymiz (birinchi topilganini emas — u «Government»
    # bo'lib qolishi mumkin). Topilmasa ERPNext o'zi standartini qo'yadi.
    group = next(
        (g for g in ("Individual", "Commercial")
         if frappe.db.exists("Customer Group", g)),
        frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
    )
    country = frappe.db.get_value("Company", _company(), "country")
    territory = next(
        (t for t in (country, "Rest Of The World")
         if t and frappe.db.exists("Territory", t)),
        frappe.db.get_value("Territory", {"is_group": 0}, "name"),
    )

    doc = frappe.new_doc("Customer")
    doc.customer_name = POS_CUSTOMER
    doc.customer_type = "Individual"
    if group:
        doc.customer_group = group
    if territory:
        doc.territory = territory
    doc.insert(ignore_permissions=True)
    _log(f"✅ Customer: {doc.name}")
    return doc.name


# Price List'ni URY Menu O'ZI yaratadi (restaurant_menu maydoni orqali) —
# qarang ury/ury/doctype/ury_menu/ury_menu.py: get_price_list(). Shuning uchun
# bu yerda oldindan yaratmaymiz, aks holda nom bo'yicha to'qnashuv bo'ladi.


# =============================================================================
# 6. MODE OF PAYMENT + HISOBLAR
# =============================================================================

def create_payment_modes(company):
    cash_parent = frappe.db.get_value(
        "Account", {"company": company, "account_type": "Cash", "is_group": 1}, "name"
    )
    if not cash_parent:
        frappe.throw(f"'{company}' uchun «Cash In Hand» guruh hisobi topilmadi")

    default_cash = frappe.db.get_value("Company", company, "default_cash_account")

    created = []
    for mode_name, mode_type, acc_num, acc_name in PAYMENT_MODES:
        # a) Hisobni tayyorlash
        if acc_num:
            account = frappe.db.get_value(
                "Account", {"company": company, "account_number": acc_num, "is_group": 0}, "name"
            )
            if not account:
                acc = frappe.new_doc("Account")
                acc.account_name = acc_name
                acc.account_number = acc_num
                acc.parent_account = cash_parent
                acc.company = company
                acc.account_type = "Cash"
                acc.is_group = 0
                acc.insert(ignore_permissions=True)
                account = acc.name
                _log(f"✅ Hisob: {account}")
        else:
            account = default_cash
            if not account:
                frappe.throw(f"'{company}' uchun Default Cash Account sozlanmagan")

        # b) Mode of Payment
        if not frappe.db.exists("Mode of Payment", mode_name):
            mop = frappe.new_doc("Mode of Payment")
            mop.mode_of_payment = mode_name
            mop.type = mode_type
            mop.enabled = 1
            mop.append("accounts", {"company": company, "default_account": account})
            mop.insert(ignore_permissions=True)
            _log(f"✅ Mode of Payment: {mode_name} -> {account}")
        else:
            mop = frappe.get_doc("Mode of Payment", mode_name)
            mop.enabled = 1
            if not any(r.company == company for r in mop.accounts):
                mop.append("accounts", {"company": company, "default_account": account})
            mop.save(ignore_permissions=True)
            _log(f"⏭️  Mode of Payment bor: {mode_name}")

        created.append(mode_name)

    # c) Ishlatilmaydigan standart usullarni o'chirish
    for name in DISABLE_MODES:
        if name in created:
            continue
        if frappe.db.exists("Mode of Payment", name):
            frappe.db.set_value("Mode of Payment", name, "enabled", 0)
            _log(f"🚫 O'chirildi: {name}")

    return created


# =============================================================================
# 7. URY ROOM
# =============================================================================

def create_room():
    if frappe.db.exists("URY Room", ROOM):
        _log(f"⏭️  URY Room bor: {ROOM}")
        return ROOM
    doc = frappe.new_doc("URY Room")
    doc.name = ROOM
    doc.branch = BRANCH
    doc.room_type = "AC"
    doc.insert(ignore_permissions=True)
    _log(f"✅ URY Room: {ROOM}")
    return ROOM


def assign_branch_rooms():
    """Branch.user qatorlariga zal (room) tayinlaydi.

    URY'ning getBranchRoom() funksiyasi room bo'sh bo'lsa xato beradi:
        "No room assigned to this user. Please contact your administrator."
    Shuning uchun Room yaratilgandan KEYIN backfill qilamiz.
    """
    doc = frappe.get_doc("Branch", BRANCH)
    changed = [r.user for r in doc.user if not r.room]
    if not changed:
        _log("⏭️  Branch foydalanuvchilariga zal allaqachon tayinlangan")
        return
    for row in doc.user:
        if not row.room:
            row.room = ROOM
    doc.save(ignore_permissions=True)
    _log(f"✅ Zal tayinlandi ({ROOM}): {', '.join(changed)}")


# =============================================================================
# 8. MENYU TOVARLARI (bazadagi mavjud tovarlardan)
# =============================================================================

def get_menu_items():
    """`Готовый продукт` daraxti ostidagi barcha faol tovarlarni qaytaradi.

    Returns:
        list[dict]: `[{"item_code", "item_name", "course", "rate"}]`
                    `course` = tovarning Item Group nomi.
    """
    lft, rgt = frappe.db.get_value("Item Group", FINISHED_GROUP, ["lft", "rgt"])
    groups = frappe.get_all(
        "Item Group",
        filters={"lft": [">=", lft], "rgt": ["<=", rgt], "is_group": 0},
        pluck="name",
    )
    if not groups:
        frappe.throw(f"«{FINISHED_GROUP}» ostida bironta ham kategoriya yo'q")

    rows = frappe.get_all(
        "Item",
        filters={"item_group": ["in", groups], "disabled": 0},
        fields=["name as item_code", "item_name", "item_group", "standard_rate"],
        order_by="item_group asc, item_name asc",
    )
    if not rows:
        frappe.throw(
            f"«{FINISHED_GROUP}» ostida bironta ham tovar topilmadi. "
            f"Avval tovarlarni ERPNext'ga import qiling."
        )

    items = []
    for r in rows:
        rate = flt(ITEM_PRICES.get(r.item_code) or 0) or flt(r.standard_rate or 0)
        items.append({
            "item_code": r.item_code,
            "item_name": r.item_name or r.item_code,
            "course": r.item_group,
            "rate": rate,
        })

    _log(f"ℹ️  Menyuga nomzod: {len(items)} ta tovar, {len(set(i['course'] for i in items))} ta kategoriya")
    return items


# =============================================================================
# 9. URY MENU COURSE (kassa ekranidagi kategoriyalar)
# =============================================================================

def create_menu_courses(items):
    """Har bir ishlatilgan Item Group uchun URY Menu Course yaratadi.

    `custom_serving_priority` — kassa ekranida tugmalar tartibi.
    MENU_COURSE_ORDER da yo'q guruhlar oxiriga tushadi.
    """
    used = sorted({i["course"] for i in items})
    created = 0
    for name in used:
        try:
            priority = MENU_COURSE_ORDER.index(name) + 1
        except ValueError:
            priority = 100 + used.index(name)

        if frappe.db.exists("URY Menu Course", name):
            if not cint(frappe.db.get_value("URY Menu Course", name, "custom_serving_priority")):
                frappe.db.set_value("URY Menu Course", name, "custom_serving_priority", priority)
            continue

        doc = frappe.new_doc("URY Menu Course")
        doc.course = name
        doc.custom_serving_priority = priority
        doc.insert(ignore_permissions=True)
        created += 1

    _log(f"✅ URY Menu Course: {created} ta yangi (jami {len(used)})")
    return used


# =============================================================================
# 10. URY MENU
# =============================================================================

def create_menu(items):
    """URY Menu yaratadi/yangilaydi. Price List'ni URY o'zi yaratib,
    menu.price_list ga yozadi va har bir taom uchun Item Price hosil qiladi
    (qarang: URYMenu.on_update -> make_price_list).

    Idempotent va NOTO'G'RI YOZMAYDI:
      - menyuda bor qatorning `rate` iga tegilmaydi (UI'dagi narxlar saqlanadi)
      - faqat yangi tovarlar qo'shiladi va bo'sh `course` to'ldiriladi

    Qaytaradi: (menu_nomi, price_list_nomi)
    """
    course_of = {i["item_code"]: i["course"] for i in items}

    if not frappe.db.exists("URY Menu", MENU):
        doc = frappe.new_doc("URY Menu")
        doc.name = MENU
        doc.branch = BRANCH
        doc.enabled = 1
        for i in items:
            doc.append("items", {
                "item": i["item_code"],
                "item_name": i["item_name"],
                "rate": i["rate"],
                "course": i["course"],
                "disabled": 0,
            })
        doc.insert(ignore_permissions=True)
        price_list = frappe.db.get_value("URY Menu", MENU, "price_list")
        _log(f"✅ URY Menu: {MENU} ({len(items)} ta taom) | Price List: {price_list}")
        return MENU, price_list

    doc = frappe.get_doc("URY Menu", MENU)
    existing = {r.item for r in doc.items}
    changed = False

    for i in items:
        if i["item_code"] not in existing:
            doc.append("items", {
                "item": i["item_code"],
                "item_name": i["item_name"],
                "rate": i["rate"],
                "course": i["course"],
                "disabled": 0,
            })
            changed = True

    # Eski qatorlarda course bo'sh bo'lsa to'ldiramiz — aks holda POS ularni
    # kataloqda ko'rsatmaydi
    for row in doc.items:
        if not row.course and course_of.get(row.item):
            row.course = course_of[row.item]
            changed = True

    if changed:
        doc.save(ignore_permissions=True)
        _log(f"✅ URY Menu yangilandi: {MENU} ({len(doc.items)} ta taom)")
    else:
        _log(f"⏭️  URY Menu o'zgarishsiz: {MENU} ({len(doc.items)} ta taom)")

    return MENU, doc.price_list


# =============================================================================
# 11. URY RESTAURANT
# =============================================================================

def create_restaurant(company, menu):
    if frappe.db.exists("URY Restaurant", RESTAURANT):
        doc = frappe.get_doc("URY Restaurant", RESTAURANT)
        if not doc.active_menu:
            doc.active_menu = menu
            doc.save(ignore_permissions=True)
            _log(f"✅ active_menu o'rnatildi: {menu}")
        _log(f"⏭️  URY Restaurant bor: {RESTAURANT}")
        return RESTAURANT

    doc = frappe.new_doc("URY Restaurant")
    doc.name = RESTAURANT
    doc.company = company
    doc.branch = BRANCH
    doc.invoice_series_prefix = INVOICE_SERIES_PREFIX
    doc.default_room = ROOM
    doc.active_menu = menu
    doc.insert(ignore_permissions=True)
    _log(f"✅ URY Restaurant: {RESTAURANT}")
    return RESTAURANT


# =============================================================================
# 12. URY TABLE
# =============================================================================

def create_tables():
    if not TABLE_COUNT:
        _log("⏭️  TABLE_COUNT=0 — stollar yaratilmadi (Stiker rejimi)")
        return
    created = 0
    for i in range(1, TABLE_COUNT + 1):
        name = f"{i}-stol"
        if frappe.db.exists("URY Table", name):
            continue
        doc = frappe.new_doc("URY Table")
        doc.name = name
        doc.restaurant = RESTAURANT
        doc.restaurant_room = ROOM
        doc.branch = BRANCH
        doc.no_of_seats = SEATS_PER_TABLE
        doc.minimum_seating = 1
        doc.is_take_away = 0
        doc.table_shape = "Square"
        doc.insert(ignore_permissions=True)
        created += 1
    _log(f"✅ URY Table: {created} ta yangi (jami {TABLE_COUNT})")


# =============================================================================
# 13. KASSIR FOYDALANUVCHI
# =============================================================================

def create_cashier():
    if frappe.db.exists("User", CASHIER_EMAIL):
        _log(f"⏭️  Kassir bor: {CASHIER_EMAIL}")
        return CASHIER_EMAIL, None

    password = frappe.generate_hash(length=12)
    doc = frappe.new_doc("User")
    doc.email = CASHIER_EMAIL
    doc.first_name = CASHIER_FIRST_NAME
    doc.send_welcome_email = 0
    doc.user_type = "System User"
    doc.new_password = password
    for role in ("URY Cashier", "Accounts User"):
        if frappe.db.exists("Role", role):
            doc.append("roles", {"role": role})
    doc.insert(ignore_permissions=True)
    _log(f"✅ Kassir: {CASHIER_EMAIL}")
    return CASHIER_EMAIL, password


# =============================================================================
# 14. POS PROFILE
# =============================================================================

def _apply_display_settings(doc, only_if_empty=False):
    """Desktop POS `custom_*` sozlamalarini qo'llaydi.

    only_if_empty=True — faqat bo'sh (None/"") maydonlar to'ldiriladi, ya'ni
    UI'da qo'lda tanlangan qiymatlar buzilmaydi.
    """
    applied = []
    for field, value in POS_DISPLAY_SETTINGS.items():
        if only_if_empty and doc.get(field) not in (None, ""):
            continue
        doc.set(field, value)
        applied.append(field)
    return applied


def _apply_cashier_pins(doc, cashier):
    """POS Profile User qatorlariga PIN va asosiy kassir bayrog'ini qo'yadi.

    Mavjud PIN o'zgartirilmaydi — kassir UI'dan o'zgartirgan bo'lishi mumkin.
    """
    pins = {cashier: CASHIER_PIN, "Administrator": ADMIN_PIN}
    changed = False
    for row in doc.applicable_for_users:
        if not (row.get("custom_pin") or "").strip() and pins.get(row.user):
            row.custom_pin = pins[row.user]
            changed = True
    # Asosiy kassir — kassa yopishni (POS Closing) faqat u yakunlay oladi
    if not any(cint(r.get("custom_main_cashier")) for r in doc.applicable_for_users):
        for row in doc.applicable_for_users:
            if row.user == cashier:
                row.custom_main_cashier = 1
                changed = True
                break
    return changed


def create_pos_profile(company, warehouse, customer, price_list, modes, cashier):
    cost_center = (
        frappe.db.get_value("Company", company, "cost_center")
        or frappe.db.get_value("Account", {"company": company}, "name")
    )
    income_account = frappe.db.get_value("Company", company, "default_income_account")

    if frappe.db.exists("POS Profile", POS_PROFILE):
        doc = frappe.get_doc("POS Profile", POS_PROFILE)
        applied = _apply_display_settings(doc, only_if_empty=True)
        pins_changed = _apply_cashier_pins(doc, cashier)
        # sync_order Item Price'ni AYNAN shu price list'dan qidiradi va topmasa
        # xato beradi — shuning uchun menyu price list'iga bog'lab qo'yamiz
        pl_changed = False
        if price_list and doc.selling_price_list != price_list:
            _log(f"   selling_price_list: {doc.selling_price_list} -> {price_list}")
            doc.selling_price_list = price_list
            pl_changed = True

        # To'lov usullari: profilda faqat ERPNext standartlari (Cash, Bank Draft…)
        # qolgan bo'lsa, ular `create_payment_modes` da o'chirilgan — POS ularni
        # ko'rsatib, to'lov qila olmaydi. Shuning uchun almashtiramiz.
        pay_changed = False
        if modes and not any(r.mode_of_payment in modes for r in doc.payments):
            old = [r.mode_of_payment for r in doc.payments]
            doc.set("payments", [])
            for i, mode in enumerate(modes):
                doc.append("payments", {"mode_of_payment": mode, "default": 1 if i == 0 else 0})
            _log(f"   to'lov usullari: {old} -> {modes}")
            pay_changed = True

        if applied or pins_changed or pl_changed or pay_changed:
            doc.save(ignore_permissions=True)
            _log(f"✅ POS Profile yangilandi: {POS_PROFILE} ({len(applied)} sozlama)")
        else:
            _log(f"⏭️  POS Profile o'zgarishsiz: {POS_PROFILE}")
        return POS_PROFILE

    doc = frappe.new_doc("POS Profile")
    doc.name = POS_PROFILE
    doc.company = company
    doc.customer = customer
    doc.warehouse = warehouse
    doc.cost_center = cost_center
    doc.write_off_account = income_account
    doc.write_off_cost_center = cost_center
    doc.selling_price_list = price_list
    doc.currency = frappe.db.get_value("Company", company, "default_currency")
    doc.restaurant = RESTAURANT
    doc.branch = BRANCH
    doc.update_stock = 1
    doc.allow_rate_change = 0
    doc.allow_discount_change = 0
    doc.disabled = 0

    for i, mode in enumerate(modes):
        doc.append("payments", {"mode_of_payment": mode, "default": 1 if i == 0 else 0})

    doc.append("item_groups", {"item_group": FINISHED_GROUP})

    for user in ("Administrator", cashier):
        if user:
            doc.append("applicable_for_users", {"user": user})

    _apply_display_settings(doc)
    _apply_cashier_pins(doc, cashier)

    doc.insert(ignore_permissions=True)
    _log(f"✅ POS Profile: {POS_PROFILE}")
    return POS_PROFILE


# =============================================================================
# 15. URY PRODUCTION UNIT (oshxona / bar)
# =============================================================================

def create_production_units(company, warehouse, available_courses):
    """Oshxona/bar chek marshrutini tayyorlaydi.

    Printer qurilmasi (`printer_settings` -> `Network Printer Settings`) bu
    yerda ULANMAYDI — u real qurilma nomiga bog'liq va ERPNext UI'dan
    biriktiriladi. Printer biriktirilmaguncha `get_printer_config` bu nuqta
    uchun bo'sh `printer_name` qaytaradi va POS oshxona chekini chiqarmaydi.
    """
    created = 0
    for unit_name, groups in PRODUCTION_UNITS:
        groups = [g for g in groups if g in available_courses]
        if not groups:
            _log(f"⏭️  {unit_name}: mos Item Group yo'q, o'tkazib yuborildi")
            continue

        if frappe.db.exists("URY Production Unit", unit_name):
            doc = frappe.get_doc("URY Production Unit", unit_name)
            have = {r.item_group for r in doc.item_groups}
            added = [g for g in groups if g not in have]
            for g in added:
                doc.append("item_groups", {"item_group": g})
            if added:
                doc.save(ignore_permissions=True)
                _log(f"✅ {unit_name}: {len(added)} ta guruh qo'shildi")
            else:
                _log(f"⏭️  URY Production Unit bor: {unit_name}")
            continue

        doc = frappe.new_doc("URY Production Unit")
        doc.production = unit_name
        doc.pos_profile = POS_PROFILE
        doc.branch = BRANCH
        doc.warehouse = warehouse
        for g in groups:
            doc.append("item_groups", {"item_group": g})
        doc.insert(ignore_permissions=True)
        created += 1
        _log(f"✅ URY Production Unit: {unit_name} ({len(groups)} ta guruh)")

    return created


# =============================================================================
# 16. KASSA FILIAL (ozturkapp Kassa moduli uchun)
# =============================================================================

def create_kassa_filial(company):
    if not frappe.db.exists("DocType", "Kassa Filial"):
        _log("⏭️  «Kassa Filial» doctype yo'q — Kassa moduli o'tkazib yuborildi")
        return

    expense_group = frappe.db.get_value(
        "Account", {"company": company, "root_type": "Expense", "is_group": 1}, "name",
        order_by="lft",
    )
    for name, mop in ((RESTAURANT, "Нахт"), ("Administrativ", "Нахт")):
        if frappe.db.exists("Kassa Filial", name):
            _log(f"⏭️  Kassa Filial bor: {name}")
            continue
        doc = frappe.new_doc("Kassa Filial")
        doc.filial_name = name
        doc.is_active = 1
        doc.company = company
        if frappe.db.exists("Mode of Payment", mop):
            doc.mode_of_payment = mop
        doc.expense_group = expense_group
        doc.insert(ignore_permissions=True)
        _log(f"✅ Kassa Filial: {name}")


# =============================================================================
# ORKESTRATOR
# =============================================================================

def run_all():
    company = _company()
    _log("=" * 60)
    _log(f"POS + KASSA SOZLAMASI — {company}")
    _log("=" * 60)

    # Kassir Branch'dan OLDIN yaratiladi — Branch.user jadvali majburiy
    _log("\n1. Kassir")
    cashier, password = create_cashier()

    _log("\n2. Branch")
    create_branch(["Administrator", cashier])

    _log("\n3. Item Group'lar")
    ensure_item_groups()

    _log("\n4. Warehouse va Stock Settings")
    warehouse = create_warehouse(company)
    configure_stock_settings()

    _log("\n5. POS mijozi")
    customer = create_pos_customer()

    _log("\n6. To'lov usullari va hisoblar")
    modes = create_payment_modes(company)

    _log("\n7. URY Room")
    create_room()
    assign_branch_rooms()

    _log("\n8. Menyu tovarlari (bazadan)")
    items = get_menu_items()

    _log("\n9. URY Menu Course (kategoriyalar)")
    courses = create_menu_courses(items)

    _log("\n10. URY Menu (+ Price List)")
    menu, price_list = create_menu(items)

    _log("\n11. URY Restaurant")
    create_restaurant(company, menu)

    _log("\n12. Stollar")
    create_tables()

    _log("\n13. POS Profile")
    create_pos_profile(company, warehouse, customer, price_list, modes, cashier)

    _log("\n14. URY Production Unit")
    create_production_units(company, warehouse, courses)

    _log("\n15. Kassa Filial")
    create_kassa_filial(company)

    frappe.db.commit()

    _log("\n" + "=" * 60)
    _log("✅ TAYYOR")
    _log("=" * 60)
    if password:
        _log(f"Kassir login : {cashier}")
        _log(f"Kassir parol : {password}   <-- DARHOL O'ZGARTIRING")
        _log(f"Kassir PIN   : {CASHIER_PIN}   (Administrator PIN: {ADMIN_PIN})")
    _log(f"POS manzili  : /app/point-of-sale  yoki  /urypos")

    try:
        _report_pending(price_list)
    except Exception as e:
        _log(f"\n⚠️  Yakuniy hisobot tuzilmadi ({e}) — sozlamaning o'zi muvaffaqiyatli.")


def _report_pending(price_list):
    """UI orqali bajarilishi kerak bo'lgan qolgan ishlar ro'yxati.

    Hisobot — `run_all` ning oxirgi, ixtiyoriy qadami. Bu yerdagi xato butun
    sozlamani muvaffaqiyatsiz ko'rsatmasligi kerak, shuning uchun to'liq
    try/except ichida.
    """
    todo = []

    zero = [r.item for r in frappe.get_all(
        "URY Menu Item", filters={"parent": MENU, "rate": 0}, fields=["item"]
    )]
    if zero:
        todo.append(
            f"NARXI 0 BO'LGAN {len(zero)} TA TOVAR — URY Menu «{MENU}» -> items -> rate:\n"
            + "\n".join(f"       · {i}" for i in zero)
        )

    # Maydon `ozturkapp` custom field'i — app o'rnatilmagan bo'lsa hali yo'q
    if frappe.get_meta("POS Profile").has_field("customer_qz_printer_name"):
        printer = frappe.db.get_value("POS Profile", POS_PROFILE, "customer_qz_printer_name")
        if not (printer or "").strip():
            todo.append(
                f"MIJOZ CHEK PRINTERI ulanmagan — POS Profile «{POS_PROFILE}» ->\n"
                f"       customer_qz_printer_name (Windows'dagi aniq printer nomi).\n"
                f"       Bo'sh qolsa POS chekni umuman chiqarmaydi."
            )
    else:
        todo.append(
            "POS Profile'da `customer_qz_printer_*` maydonlari yo'q — ozturkapp\n"
            "       o'rnatilmagan yoki `bench migrate` ishlamagan."
        )

    units_wo_printer = [
        u.name for u in frappe.get_all("URY Production Unit", filters={"pos_profile": POS_PROFILE}, fields=["name"])
        if not frappe.db.exists("URY Printer Settings", {"parent": u.name})
    ]
    if units_wo_printer:
        todo.append(
            f"OSHXONA/BAR PRINTERI ulanmagan: {', '.join(units_wo_printer)} ->\n"
            f"       URY Production Unit -> printer_settings (Network Printer Settings)."
        )

    pins_missing = []
    if frappe.get_meta("POS Profile User").has_field("custom_pin"):
        pins_missing = [
            r.user for r in frappe.get_all(
                "POS Profile User",
                filters={"parent": POS_PROFILE, "custom_pin": ["in", ["", None]]},
                fields=["user"],
            )
        ]
    if pins_missing:
        todo.append(
            f"PIN qo'yilmagan kassir(lar): {', '.join(pins_missing)} ->\n"
            f"       POS Profile «{POS_PROFILE}» -> applicable_for_users -> custom_pin."
        )

    if not todo:
        _log("\n🎉 UI orqali bajariladigan ish qolmadi.")
        return

    _log("\n" + "=" * 60)
    _log("⚠️  UI ORQALI BAJARILISHI KERAK")
    _log("=" * 60)
    for n, line in enumerate(todo, start=1):
        _log(f"  {n}. {line}")
