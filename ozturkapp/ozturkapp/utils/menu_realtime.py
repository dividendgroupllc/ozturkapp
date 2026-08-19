# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Menyu o'zgarishi haqida realtime xabar (ofitsant ilovasi uchun).

NEGA ALOHIDA KANAL
==================
Ofitsant ilovasi menyuni ochilishda bir marta yuklab, xotirada saqlaydi.
Menyu o'zgarsa (narx tahrirlandi, taom o'chirildi, dinamik narxlash
ishladi) ilova buni BILMAYDI va eski narxni ko'rsatib turaveradi. Buyurtma
esa serverda `Item Price` dan narxlanadi — natijada ofitsant aytgan summa
bilan chekdagi summa boshqa-boshqa bo'lib chiqadi.

Shuning uchun menyu o'zgarganda bitta yupqa signal ketadi, ilova esa
`waiter.get_menu()` ni qayta so'raydi.

MENYU IKKI XIL YO'L BILAN O'ZGARADI
===================================
1. QO'LDA — `URY Menu` hujjati tahrirlanadi. Hujjat hodisasi ishlaydi.

2. DINAMIK NARXLASH — `api/dynamic_pricing.py` narxni
   `frappe.db.set_value(..., update_modified=False)` bilan yozadi.
   Bu HECH QANDAY hujjat hodisasini uyg'otmaydi, shuning uchun u yerda
   `emit_menu_change()` TO'G'RIDAN-TO'G'RI chaqiriladi.

Ikkinchi yo'l unutilsa xato "jimgina" bo'ladi: menyu o'zgaradi, ilova
esa eski narxni ko'rsatib turaveradi. Shu sabab bu izoh shu yerda turibdi.
"""

import frappe

#: Menyu o'zgardi — ilova `waiter.get_menu()` ni qayta so'raydi.
EVENT_MENU = "ozturk_menu"


def emit_menu_change(branch: str, reason: str = "", menu: str = None):
    """Menyu o'zgargani haqida xabar (faqat identifikatorlar).

    Args:
        branch: qaysi filial menyusi.
        reason: `MENU_UPDATED` / `PRICING_UPDATED`.
        menu: `URY Menu` nomi (ixtiyoriy).
    """
    if not branch:
        return

    frappe.publish_realtime(
        EVENT_MENU,
        {"branch": branch, "menu": menu, "reason": reason},
        after_commit=True,
    )


def on_menu_change(doc, method=None):
    """`URY Menu` qo'lda tahrirlanganda.

    `URY Menu` da `branch` maydoni yo'q — u menyuni ishlatayotgan
    restoran(lar) orqali topiladi. Bitta menyu bir nechta restoranga
    biriktirilgan bo'lishi mumkin, shuning uchun har biriga xabar ketadi.
    """
    if doc.doctype != "URY Menu":
        return

    branches = frappe.get_all(
        "URY Restaurant",
        filters={"active_menu": doc.name},
        pluck="branch",
    )
    for branch in dict.fromkeys(b for b in branches if b):
        emit_menu_change(branch, "MENU_UPDATED", doc.name)
