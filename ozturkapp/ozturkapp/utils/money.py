# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Summani o'qish uchun formatlash — YAGONA qoida.

    200000   ->  200 000
    1080800  ->  1 080 800
    1234.5   ->  1 234,50

MINGLIK AJRATGICH — PROBEL
==========================
`1080800` ni bir qarashda o'qib bo'lmaydi; `1 080 800` ni bo'ladi. Kassir
ekranga bir soniyaga qaraydi va noto'g'ri o'qilgan raqam pul xatosiga
aylanadi.

Probel — o'zbek va rus yozuvidagi standart ajratgich. Vergul (`1,080,800`)
ingliz yozuvi bo'lib, bu yerda o'nlik ajratgich sifatida tushunilishi
mumkin — ya'ni chalg'itadi.

NEGA `frappe.utils.fmt_money()` EMAS
===================================
U ikkita sozlamaga bog'liq: `System Settings.number_format` va
`Currency.<kod>.number_format`. Bu saytda ikkalasi ham `#,###.##`, ya'ni
`лв 1,080,800.00` chiqadi — vergul, keraksiz nol tiyin va noto'g'ri
valyuta belgisi (UZS uchun `лв` — bolgar levi belgisi).

Sozlamani global o'zgartirish butun ERPNext'ga (hisobotlar, barcha
hujjatlar, chop etish shakllari) ta'sir qiladi. Shuning uchun O'Z
qoidamiz bor va u faqat biz chizadigan ekranlarda ishlaydi:

    kassa oynasi        page/restaurant_cashier/restaurant_cashier.js
    mijoz cheki         setup/receipt_format.py (Jinja: `format_amount`)
    ofitsant ilovasi    lib/core/format.dart -> Fmt.money

Uchalasi AYNAN shu qoidaga amal qiladi — bitta son ikki ekranda turlicha
ko'rinmasligi kerak.
"""

from frappe.utils import flt

#: Minglik ajratgich.
GROUP = " "

#: O'nlik ajratgich (probel bilan birga ishlatiladigan standart).
DECIMAL = ","


def format_amount(value, precision: int = 2) -> str:
    """Summani probel bilan guruhlaydi.

    Tiyin FAQAT nolga teng bo'lmasa ko'rsatiladi: bu yerda summalar
    butun so'mda yuritiladi va har qatorga qo'shilgan `,00` ekranni
    keraksiz belgi bilan to'ldiradi.

    Args:
        value: son yoki `None`.
        precision: o'nlik xonalar soni (kerak bo'lganda).
    """
    number = flt(value)
    negative = number < 0
    number = abs(number)

    whole = int(number)
    fraction = round((number - whole) * (10**precision))

    # Yaxlitlash butun songa o'tkazgan bo'lishi mumkin: 1.999 -> 2,00.
    if fraction >= 10**precision:
        whole += 1
        fraction = 0

    grouped = f"{whole:,}".replace(",", GROUP)
    if fraction:
        grouped = f"{grouped}{DECIMAL}{str(fraction).rjust(precision, '0')}"

    return f"-{grouped}" if negative else grouped
