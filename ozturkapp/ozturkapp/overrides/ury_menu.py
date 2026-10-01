# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""`getRestaurantMenu` uchun ozturkapp o'ram (wrapper) — o'chirilgan tovarlarni yashirish.

MUAMMO
======
`ury.ury_pos.api.getRestaurantMenu` faqat "URY Menu Item" qatoridagi
`disabled` ni tekshiradi, Item kartochkasidagi `disabled` ni EMAS. Natijada
ERPNext'da o'chirilgan (Item.disabled = 1) taom/ichimlik POS menyusida
qolib, sotuvga chiqib ketardi.

YECHIM
======
`hooks.override_whitelisted_methods` orqali chaqiruv shu modulga yo'naltiriladi:
ury natijasi olinadi va Item darajasida o'chirilganlari ro'yxatdan chiqariladi.
`ury` ning o'zi tegilmaydi (upstream repo — yangilanishda konflikt bo'lmasin).

Ofitsant ilovasi va kassa sahifasi menyuni `utils/order_items.build_menu()`
orqali oladi — u ham shu funksiyani chaqiradi, ya'ni filtr hamma joyda bir xil.

Jazira (`jazira_app/overrides/ury_menu.py`) dan ko'chirilgan.
"""

import frappe

from ury.ury_pos.api import getRestaurantMenu as _ury_get_restaurant_menu


@frappe.whitelist()
def getRestaurantMenu(pos_profile, room=None, order_type=None):
    data = _ury_get_restaurant_menu(pos_profile, room=room, order_type=order_type)
    return filter_disabled_items(data)


def filter_disabled_items(data):
    """Menyu javobidan Item.disabled = 1 bo'lgan qatorlarni olib tashlaydi."""
    if not isinstance(data, dict):
        return data

    items = data.get("items") or []
    item_codes = list({it.get("item") for it in items if it.get("item")})
    if not item_codes:
        return data

    disabled = set(frappe.get_all(
        "Item", filters={"name": ["in", item_codes], "disabled": 1}, pluck="name"
    ))
    if disabled:
        data["items"] = [it for it in items if it.get("item") not in disabled]
    return data
