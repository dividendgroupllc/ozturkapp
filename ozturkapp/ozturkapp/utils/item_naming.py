# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Item kodi (`Item.item_code`) — avtomatik, `ITEM-####` ko'rinishida.

NEGA
====
Kodni foydalanuvchi qo'lda yozganda kodlar bir xil qoidaga bo'ysunmay qoldi
(`GOBIT DÖNER`, `LAVASH ÜSTÜ` — aslida taom nomining o'zi). Endi kodni tizim
beradi, foydalanuvchi faqat nomini (`item_name`) kiritadi va kodni
o'zgartira olmaydi.

NEGA ERPNext'NING "NAMING SERIES" REJIMI EMAS
=============================================
`set_name_by_naming_series` nomga `.#####` ni o'zi qo'shadi — natijada 5
xonali `ITEM-00001` chiqadi, kerak bo'lgan `ITEM-0001` emas. Shuning uchun
`Stock Settings` dagi "Item Naming By = Item Code" o'z holicha qoladi, kodni
esa `before_naming` hook'ida o'zimiz beramiz — `Item.autoname` keyin
`name = item_code` qiladi.

NIMA QILINADI
=============
1. `assign_item_code` — yangi Item kodi HAR DOIM seriyadan olinadi.
   Foydalanuvchi yozgan (yoki "Duplicate" nusxalagan) kod e'tiborga olinmaydi:
   nusxalangan kod qolsa, "Item allaqachon mavjud" xatosi chiqardi va maydon
   faqat o'qiladigan bo'lgani uchun uni tuzatib ham bo'lmasdi.
2. `ITEM_PROPERTY_SETTERS` — `item_code` faqat o'qiladi, Rename o'chiriladi
   (Rename — kodni o'zgartirishning ikkinchi yo'li).
3. `on_stock_settings_update` — ERPNext `Stock Settings` saqlanganda
   `item_code` ni majburiy (`reqd=1`) qilib qaytaradi. Kod bo'sh va faqat
   o'qiladigan bo'lgani uchun yangi Item saqlanmay qolardi. Shu hook uni
   qayta to'g'rilaydi.
4. `ensure_series_floor` — `ITEM-` hisoblagichi mavjud kodlardan past
   qolmasin, aks holda yangi kod band bo'lgan raqamga to'qnashadi.

Eski kodlarni `ITEM-####` ga o'tkazish: `setup/item_codes.py`.
"""

import re

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.model.naming import make_autoname
from frappe.utils import cint

PREFIX = "ITEM-"
SERIES = f"{PREFIX}.####"
CODE_PATTERN = re.compile(rf"^{re.escape(PREFIX)}(\d+)$")

# (maydon, xossa, qiymat, tur) — maydon None bo'lsa DocType darajasidagi xossa
ITEM_PROPERTY_SETTERS = [
    ("item_code", "read_only", "1", "Check"),
    # Bo'sh va faqat o'qiladigan maydon majburiy bo'lsa forma saqlanmaydi.
    ("item_code", "reqd", "0", "Check"),
    ("item_code", "hidden", "0", "Check"),
    # Kod ma'nosiz raqam bo'lgani uchun taom nomi majburiy. (Server tomonida
    # `Item.validate` bo'sh nomni kod bilan to'ldiradi — bu faqat forma uchun.)
    ("item_name", "reqd", "1", "Check"),
    (None, "allow_rename", "0", "Check"),
]


# ═══════════════════════════════════════════════════════════════════
#  Kod berish
# ═══════════════════════════════════════════════════════════════════

def next_item_code() -> str:
    """Seriyadagi keyingi bo'sh kod (`ITEM-0001`, `ITEM-0002`, ...)."""
    while True:
        code = make_autoname(SERIES)
        # Hisoblagich orqada qolgan bo'lsa band raqamni o'tkazib yuboramiz.
        if not frappe.db.exists("Item", code):
            return code


def assign_item_code(doc, method=None):
    """`Item` nomlanishidan oldin (`before_naming`) kodni seriyadan beradi."""
    doc.item_code = next_item_code()


def existing_numbers() -> list:
    """Mavjud `ITEM-####` kodlarning raqamlari."""
    names = frappe.get_all("Item", filters={"name": ["like", f"{PREFIX}%"]}, pluck="name")
    return [int(match.group(1)) for name in names if (match := CODE_PATTERN.match(name))]


def series_current() -> int:
    """`ITEM-` seriyasining hozirgi hisoblagichi (yozmaydi)."""
    # `frappe.db.get_value` `modified` bo'yicha saralaydi, `tabSeries` da esa
    # bu ustun yo'q.
    row = frappe.db.sql("select `current` from `tabSeries` where `name`=%s", PREFIX)
    return cint(row[0][0]) if row else 0


def ensure_series_floor() -> int:
    """Hisoblagichni mavjud eng katta raqamdan past qoldirmaydi.

    Returns:
        int: hisoblagichning yakuniy qiymati.
    """
    floor = max(existing_numbers(), default=0)
    row = frappe.db.sql("select `current` from `tabSeries` where `name`=%s for update", PREFIX)

    if not row:
        if floor:
            frappe.db.sql("insert into `tabSeries` (`name`, `current`) values (%s, %s)", (PREFIX, floor))
        return floor

    current = cint(row[0][0])
    if current < floor:
        frappe.db.sql("update `tabSeries` set `current`=%s where `name`=%s", (floor, PREFIX))
        return floor
    return current


# ═══════════════════════════════════════════════════════════════════
#  Forma sozlamalari
# ═══════════════════════════════════════════════════════════════════

def apply_form_settings():
    """`ITEM_PROPERTY_SETTERS` ni qo'llaydi (idempotent)."""
    for fieldname, prop, value, prop_type in ITEM_PROPERTY_SETTERS:
        make_property_setter(
            "Item",
            fieldname,
            prop,
            value,
            prop_type,
            for_doctype=not fieldname,
            validate_fields_for_doctype=False,
        )


def on_stock_settings_update(doc, method=None):
    """`Stock Settings` saqlangach `item_code` sozlamalarini qaytaradi.

    ERPNext uni saqlaganda `Item.item_code` ni majburiy qilib qo'yadi
    (`erpnext.utilities.naming.set_by_naming_series`).
    """
    apply_form_settings()


# ═══════════════════════════════════════════════════════════════════
#  Sozlash
# ═══════════════════════════════════════════════════════════════════

def setup():
    apply_form_settings()
    current = ensure_series_floor()
    frappe.db.commit()
    print(f"✅ Item kodi avtomatik: {PREFIX}#### (seriya hisoblagichi: {current})")
