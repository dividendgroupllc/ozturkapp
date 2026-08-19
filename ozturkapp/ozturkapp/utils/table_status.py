# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Stol holatini HAQIQIY biznes ma'lumotidan keltirib chiqarish (TZ §5).

QOIDA
=====
    Faol buyurtma bor       -> OCCUPIED
    Faol bron bor           -> RESERVED
    Aks holda               -> AVAILABLE

`URY Table.occupied` bayrog'i YAGONA manba EMAS — u faqat moslik uchun
saqlanadi (URY POS, Desktop POS va KOT unga tayanadi). Kassa oynasi esa
ochiq `POS Invoice` qoralamalarini asosiy dalil sifatida oladi, chunki
`occupied` bayrog'i "orphan" holatga tushib qolishi mumkin (masalan
`frappe.db.set_value` bilan yozilgan, keyin chek o'chirilgan).

BIRLASHTIRILGAN STOLLAR
=======================
URY stollarni `merged_with` (CSV) orqali klasterga biriktiradi. Klasterning
bitta a'zosida buyurtma bo'lsa — BUTUN klaster band. Shuning uchun holat
alohida stol emas, klaster darajasida hisoblanadi.

ISHLASH (TZ §25)
================
Har bir stol uchun alohida so'rov YUBORILMAYDI. Butun zal uchun 3 ta bulk
so'rov: stollar, ochiq cheklar, bronlar.
"""

import frappe
from frappe.utils import cint, flt

AVAILABLE = "AVAILABLE"
RESERVED = "RESERVED"
OCCUPIED = "OCCUPIED"

#: Kassa oynasi ko'rsatadigan yagona holatlar to'plami (TZ §5).
STATUSES = (AVAILABLE, RESERVED, OCCUPIED)

#: Layout koordinatasi berilmagan stollar uchun avtomatik to'r o'lchamlari.
DEFAULT_TABLE_WIDTH = 120.0
DEFAULT_TABLE_HEIGHT = 120.0
GRID_GAP = 32.0
GRID_COLUMNS = 5

#: "Barcha zallar" ko'rinishida zal bloklari orasidagi vertikal masofa.
ROOM_GAP = 40.0

#: Zal sarlavhasi uchun AJRATILADIGAN balandlik. Stollar shu qiymatga
#: pastga suriladi, aks holda zal nomi birinchi qator stollar ustiga tushadi.
ROOM_HEADER = 38.0


# ═══════════════════════════════════════════════════════════════════
#  Klaster (birlashtirilgan stollar)
# ═══════════════════════════════════════════════════════════════════

def parse_merged_with(value) -> list:
    """`merged_with` CSV -> ro'yxat. URY'dagi `_parse_merged_with` bilan bir xil."""
    if not value:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def build_clusters(tables: list) -> dict:
    """Stollarni `merged_with` bo'yicha klasterlarga ajratadi.

    Args:
        tables: `name` va `merged_with` maydonlari bor dict/Document ro'yxati.

    Returns:
        dict: {stol_nomi: [klaster a'zolari]} — har bir stol uchun.
    """
    by_name = {t["name"]: t for t in tables}
    seen = set()
    result = {}

    for name in by_name:
        if name in seen:
            continue

        # Klaster a'zolarini BFS bilan yig'amiz.
        members, queue = [], [name]
        while queue:
            current = queue.pop(0)
            if current in seen or current not in by_name:
                continue
            seen.add(current)
            members.append(current)
            for partner in parse_merged_with(by_name[current].get("merged_with")):
                if partner in by_name and partner not in seen:
                    queue.append(partner)

        for member in members:
            result[member] = members

    return result


# ═══════════════════════════════════════════════════════════════════
#  Faol buyurtmalar (ochiq POS Invoice qoralamalari)
# ═══════════════════════════════════════════════════════════════════

#: Kassa uchun kerakli chek maydonlari — bitta joyda, takrorlanmasin.
OPEN_ORDER_FIELDS = (
    "name",
    "restaurant_table",
    "custom_merged_tables",
    "customer",
    "customer_name",
    "waiter",
    "cashier",
    "no_of_pax",
    "grand_total",
    "rounded_total",
    "net_total",
    "order_type",
    "invoice_printed",
    "custom_ury_order_number",
    "custom_ticket_number",
    "custom_restaurant_room",
    "custom_comments",
    "creation",
    "modified",
    "owner",
)


def get_open_orders(branch: str, tables: list = None) -> list:
    """Filialdagi barcha to'lanmagan (draft) cheklar — bitta so'rovda.

    `custom_cancelled` — ozturkapp qo'shgan maydon: chek o'chirilmaydi,
    faqat bekor qilingan deb belgilanadi. Ular faol hisoblanmaydi.
    """
    filters = {"docstatus": 0, "branch": branch}
    if frappe.db.has_column("POS Invoice", "custom_cancelled"):
        filters["custom_cancelled"] = 0

    orders = frappe.get_all(
        "POS Invoice",
        filters=filters,
        fields=list(OPEN_ORDER_FIELDS),
        order_by="creation asc",
    )

    if tables is None:
        return orders

    # Faqat so'ralgan stollarga tegishlilarini qoldiramiz (zal filtri).
    wanted = set(tables)
    kept = []
    for order in orders:
        touched = {order.restaurant_table} | set(
            parse_merged_with(order.custom_merged_tables)
        )
        if touched.intersection(wanted):
            kept.append(order)
    return kept


def map_orders_to_tables(orders: list) -> dict:
    """{stol_nomi: chek} xaritasi.

    Bitta stolda bir nechta ochiq chek bo'lsa (masalan hisob bo'lingan),
    ENG ESKISI asosiy hisoblanadi — u stolni birinchi band qilgan.
    `get_open_orders` allaqachon `creation asc` bo'yicha tartiblangan.
    """
    mapping = {}
    for order in orders:
        touched = [order.restaurant_table] + parse_merged_with(
            order.custom_merged_tables
        )
        for table in touched:
            if table and table not in mapping:
                mapping[table] = order
    return mapping


def count_orders_per_table(orders: list) -> dict:
    """{stol_nomi: ochiq cheklar soni} — bo'lingan hisoblarni ko'rsatish uchun."""
    counts = {}
    for order in orders:
        touched = [order.restaurant_table] + parse_merged_with(
            order.custom_merged_tables
        )
        for table in touched:
            if table:
                counts[table] = counts.get(table, 0) + 1
    return counts


# ═══════════════════════════════════════════════════════════════════
#  Bron — kelajakdagi modul uchun ulanish nuqtasi (TZ §6)
# ═══════════════════════════════════════════════════════════════════

def get_reservation_provider():
    """Bron ma'lumotini beruvchi funksiyani qaytaradi.

    Kelajakda to'liq bron moduli yozilganda `hooks.py` da::

        cashier_reservation_provider = "myapp.reservations.get_active"

    deb ko'rsatiladi va kassa mantig'i UMUMAN o'zgarmaydi (TZ §14, §35).
    """
    hooked = frappe.get_hooks("cashier_reservation_provider")
    if hooked:
        return frappe.get_attr(hooked[-1])
    return get_active_reservations


def get_active_reservations(branch: str, tables: list = None) -> dict:
    """Hozir kuchda bo'lgan bronlar: {stol_nomi: bron ma'lumoti}.

    "Faol" = bugungi sanaga, `Confirmed`/`Pending` holatida va hali
    o'tirilmagan (`Seated` emas). Mehmon o'tirgach chek ochiladi va stol
    OCCUPIED bo'ladi — bron endi ko'rsatilmaydi.
    """
    if not frappe.db.exists("DocType", "URY Table Reservation"):
        return {}

    filters = {
        "branch": branch,
        "reservation_date": frappe.utils.nowdate(),
        "status": ["in", ["Confirmed", "Pending"]],
        "docstatus": ["<", 2],
    }
    if tables:
        filters["table"] = ["in", list(tables)]

    rows = frappe.get_all(
        "URY Table Reservation",
        filters=filters,
        fields=[
            "name",
            "table",
            "customer",
            "customer_name",
            "phone",
            "reservation_date",
            "from_time",
            "to_time",
            "pax",
            "status",
            "notes",
        ],
        order_by="from_time asc",
    )

    mapping = {}
    for row in rows:
        # Bir stolga bir nechta bron bo'lsa — eng yaqini ko'rsatiladi.
        if row.table and row.table not in mapping:
            mapping[row.table] = row
    return mapping


# ═══════════════════════════════════════════════════════════════════
#  Holatni keltirib chiqarish
# ═══════════════════════════════════════════════════════════════════

def derive_status(table_name: str, cluster: list, order_map: dict, reservation_map: dict) -> str:
    """Bitta stolning uchta ko'rinadigan holatidan birini qaytaradi.

    Klaster a'zolaridan birortasida buyurtma bo'lsa — hammasi OCCUPIED.
    """
    for member in cluster or [table_name]:
        if order_map.get(member):
            return OCCUPIED

    if reservation_map.get(table_name):
        return RESERVED

    return AVAILABLE


# ═══════════════════════════════════════════════════════════════════
#  Layout — saqlangan koordinata yoki avtomatik to'r (TZ §29)
# ═══════════════════════════════════════════════════════════════════

def has_stored_layout(tables: list) -> bool:
    """Blokda hech bo'lmasa bitta joylashtirilgan stol bormi?

    `apply_layout` endi aralash holatni ham qo'llab-quvvatlagani uchun bu
    funksiya ichki mantiqda ishlatilmaydi; u tashqi kod (masalan hisobot yoki
    kelajakdagi layout muharriri) uchun qulaylik sifatida qoldirilgan.
    """
    return any(is_positioned(table) for table in tables)


def apply_layout(tables: list, stack_rooms: bool = False) -> list:
    """Har bir stolga `layout` (x, y, width, height) qo'shadi.

    Saqlangan koordinata bo'lsa — o'sha ishlatiladi (URY Table yagona manba).
    Bo'lmasa — indeks bo'yicha barqaror avtomatik to'r hisoblanadi, ya'ni
    ekran har safar bir xil ko'rinadi.

    Args:
        stack_rooms: `True` bo'lsa har bir zal ALOHIDA blok sifatida
            hisoblanadi va bloklar vertikal ravishda ustma-ust EMAS, ketma-ket
            joylashtiriladi. "Barcha zallar" ko'rinishi uchun shart, chunki
            har bir zalning saqlangan koordinatasi o'z (0,0) idan boshlanadi —
            aks holda "Ichki zal"ning 1-stoli "Tashqi zal"ning 1-stoli ustiga
            tushib qoladi.

    Returns:
        list: `stack_rooms=True` bo'lsa zal bloklari ma'lumoti
              (`[{room, y, header_height, height, count}]`), aks holda
              bo'sh ro'yxat.

              `y` — blok boshlanishi (sarlavha shu yerda turadi),
              `header_height` — sarlavha uchun AJRATILGAN balandlik,
              `height` — sarlavha bilan birga blokning to'liq balandligi.
    """
    if not stack_rooms:
        _layout_block(tables)
        return []

    bands, offset = [], 0.0

    for room in _ordered_rooms(tables):
        block = [t for t in tables if (t.get("restaurant_room") or "") == room]
        if not block:
            continue

        _layout_block(block)

        tables_height = max(
            flt(t["layout"]["y"]) + flt(t["layout"]["height"]) for t in block
        )

        # Stollarni sarlavha balandligicha pastga suramiz — aks holda zal
        # nomi birinchi qator stollar USTIGA tushib qoladi.
        for table in block:
            table["layout"]["y"] += offset + ROOM_HEADER

        bands.append(
            {
                "room": room,
                "y": offset,
                "header_height": ROOM_HEADER,
                "height": tables_height + ROOM_HEADER,
                "count": len(block),
            }
        )
        offset += tables_height + ROOM_HEADER + ROOM_GAP

    return bands


def _ordered_rooms(tables: list) -> list:
    """Zallarning barqaror tartibi — ekran har safar bir xil ko'rinsin."""
    seen, ordered = set(), []
    for table in tables:
        room = table.get("restaurant_room") or ""
        if room not in seen:
            seen.add(room)
            ordered.append(room)
    return ordered


def is_positioned(table) -> bool:
    """Stol zal rejasida HAQIQATDA joylashtirilganmi?

    Desk'da yangi `URY Table` yaratilganda `layout_*` maydonlari bo'sh (0)
    qoladi. Bunday stolni saqlangan koordinata deb qabul qilsak, u (0,0) ga
    tushadi va allaqachon o'sha yerda turgan stol bilan ustma-ust qoladi.

    Shuning uchun to'rttala maydon ham nol bo'lsa — stol "joylashtirilmagan"
    hisoblanadi. (0,0) da turgan haqiqiy stolning kamida eni/bo'yi bo'ladi.
    """
    return any(
        flt(table.get(field))
        for field in ("layout_x", "layout_y", "layout_width", "layout_height")
    )


def _grid_slot(index: int, origin_y: float = 0.0) -> tuple:
    column = index % GRID_COLUMNS
    row = index // GRID_COLUMNS
    return (
        column * (DEFAULT_TABLE_WIDTH + GRID_GAP),
        origin_y + row * (DEFAULT_TABLE_HEIGHT + GRID_GAP),
    )


def _layout_block(tables: list) -> list:
    """Bitta blok (bitta zal yoki butun ro'yxat) uchun koordinata hisoblash.

    Uch xil holat:
      1. Hech biri joylashtirilmagan  -> hammasi avtomatik to'rda.
      2. Hammasi joylashtirilgan      -> saqlangan koordinata (URY Table
                                          yagona manba).
      3. ARALASH                      -> joylashtirilganlari o'z joyida,
                                          qolganlari ULARDAN PASTDA to'rda.

    3-holat amalda eng ko'p uchraydi: menejer Desk'dan yangi stol qo'shadi,
    lekin koordinata kiritmaydi.
    """
    positioned = [t for t in tables if is_positioned(t)]
    loose = [t for t in tables if not is_positioned(t)]

    # Joylashtirilganlar — saqlangan koordinata bo'yicha.
    for table in positioned:
        table["layout"] = {
            "x": flt(table.get("layout_x")),
            "y": flt(table.get("layout_y")),
            "width": flt(table.get("layout_width")) or DEFAULT_TABLE_WIDTH,
            "height": flt(table.get("layout_height")) or DEFAULT_TABLE_HEIGHT,
            "auto": False,
        }

    if not loose:
        return tables

    # Qolganlari joylashtirilganlarning ostidan boshlanadigan to'rda.
    origin_y = 0.0
    if positioned:
        origin_y = (
            max(t["layout"]["y"] + t["layout"]["height"] for t in positioned)
            + GRID_GAP
        )

    for index, table in enumerate(loose):
        x, y = _grid_slot(index, origin_y)
        table["layout"] = {
            "x": x,
            "y": y,
            "width": DEFAULT_TABLE_WIDTH,
            "height": DEFAULT_TABLE_HEIGHT,
            "auto": True,
        }

    return tables


def layout_extent(tables: list) -> dict:
    """Zal tuvalining (canvas) umumiy o'lchami — frontend shuni scale qiladi."""
    max_x = max_y = 0.0
    for table in tables:
        layout = table.get("layout") or {}
        max_x = max(max_x, flt(layout.get("x")) + flt(layout.get("width")))
        max_y = max(max_y, flt(layout.get("y")) + flt(layout.get("height")))
    return {"width": max_x + GRID_GAP, "height": max_y + GRID_GAP}


# ═══════════════════════════════════════════════════════════════════
#  Zal holatini yig'ish — kassa oynasining asosiy manbasi
# ═══════════════════════════════════════════════════════════════════

TABLE_FIELDS = (
    "name",
    "restaurant",
    "restaurant_room",
    "branch",
    "no_of_seats",
    "minimum_seating",
    "table_shape",
    "layout_x",
    "layout_y",
    "layout_width",
    "layout_height",
    "occupied",
    "merged_with",
    "is_take_away",
    "latest_invoice_time",
)


def build_floor_state(branch: str, room: str = None) -> dict:
    """Zal rejasining to'liq holati — 3 ta bulk so'rov bilan (TZ §25).

    Returns:
        dict: tables[], extent, counts, generated_at
    """
    filters = {"branch": branch}
    if room:
        filters["restaurant_room"] = room

    tables = frappe.get_all(
        "URY Table",
        filters=filters,
        fields=list(TABLE_FIELDS),
        order_by="restaurant_room asc, name asc",
    )
    if not tables:
        return {
            "tables": [],
            "extent": {"width": 0, "height": 0},
            "room_bands": [],
            "counts": {status: 0 for status in STATUSES} | {"ALL": 0},
            "generated_at": frappe.utils.now(),
        }

    table_names = [t.name for t in tables]

    # Klaster hisoblash uchun ZALDAGI emas, FILIALDAGI barcha stollar kerak —
    # birlashtirilgan sherik boshqa zalda bo'lishi mumkin emas, lekin
    # `merged_with` faqat nom saqlagani uchun to'liq ro'yxat xavfsizroq.
    cluster_source = tables if not room else frappe.get_all(
        "URY Table",
        filters={"branch": branch},
        fields=["name", "merged_with"],
    )
    clusters = build_clusters([dict(t) for t in cluster_source])

    orders = get_open_orders(branch)
    order_map = map_orders_to_tables(orders)
    order_counts = count_orders_per_table(orders)
    reservation_map = get_reservation_provider()(branch, table_names)

    counts = {status: 0 for status in STATUSES}

    for table in tables:
        cluster = clusters.get(table.name, [table.name])
        status = derive_status(table.name, cluster, order_map, reservation_map)
        counts[status] += 1

        order = order_map.get(table.name)
        reservation = reservation_map.get(table.name)

        table["status"] = status
        table["cluster"] = cluster
        table["is_merged"] = len(cluster) > 1
        table["open_order_count"] = order_counts.get(table.name, 0)
        table["order"] = _thin_order(order) if order else None
        table["reservation"] = _thin_reservation(reservation) if reservation else None
        # `occupied` bayrog'i keltirilgan holat bilan mos kelmasa — kassir
        # buni ko'rishi kerak (ma'lumot buzilganini bildiradi).
        table["occupied_flag"] = cint(table.get("occupied"))
        table["flag_mismatch"] = bool(
            cint(table.get("occupied")) != (1 if status == OCCUPIED else 0)
        )

    # Aniq zal so'ralmagan bo'lsa — har bir zal alohida blok sifatida
    # joylashtiriladi, aks holda zallarning koordinatalari ustma-ust tushadi.
    bands = apply_layout(tables, stack_rooms=not room)

    return {
        "tables": tables,
        "extent": layout_extent(tables),
        "room_bands": bands,
        "counts": counts | {"ALL": len(tables)},
        "generated_at": frappe.utils.now(),
    }


def _thin_order(order) -> dict:
    """Zal rejasida ko'rsatiladigan minimal buyurtma ma'lumoti."""
    return {
        "name": order.name,
        "amount": flt(order.rounded_total) or flt(order.grand_total),
        "waiter": order.waiter,
        "customer": order.customer,
        "customer_name": order.customer_name or order.customer,
        "pax": cint(order.no_of_pax),
        "order_type": order.order_type,
        "billed": bool(cint(order.invoice_printed)),
        "order_number": order.custom_ury_order_number or order.custom_ticket_number,
        "opened_at": str(order.creation or ""),
    }


def _thin_reservation(reservation) -> dict:
    return {
        "name": reservation.name,
        "customer": reservation.customer,
        "customer_name": reservation.customer_name or reservation.customer,
        "phone": reservation.phone,
        "date": str(reservation.reservation_date or ""),
        "from_time": str(reservation.from_time or ""),
        "to_time": str(reservation.to_time or ""),
        "pax": cint(reservation.pax),
        "status": reservation.status,
        "notes": reservation.notes,
    }
