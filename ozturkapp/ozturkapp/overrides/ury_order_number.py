# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""URY `set_order_number` (POS Invoice after_insert) — amend uchun himoya.

URY chek tartib raqamini `int(doc.name[-5:])` bilan hisoblaydi. Amend
qilingan chek nomi `INV-00871-1` ko'rinishida bo'ladi -> `int("871-1")`
ValueError beradi va butun saqlash yiqiladi.

Amend — o'sha buyurtmaning tuzatilgan nusxasi, tartib raqami
(`custom_ury_order_number`) asl chekdan allaqachon nusxalangan. Shuning
uchun amend hujjatida URY funksiyasi chaqirilmaydi. Qolgan hollarda
upstream o'zgarishsiz ishlaydi.

Frappe `doc_events` dagi yo'lni har chaqiruvda `frappe.get_attr` bilan
qayta topadi, shuning uchun modul atributini almashtirish yetarli.
`patch()` POS Invoice controller moduli import qilinganda chaqiriladi
(`overrides/pos_invoice_totals.py`) — after_insert'dan oldin albatta.
"""

import functools


def patch():
    from ury.ury.api import ury_kot_order_number as mod

    original = mod.set_order_number
    if getattr(original, "_ozturk_patched", False):
        return

    @functools.wraps(original)
    def set_order_number(doc, event):
        if doc.get("amended_from"):
            return
        return original(doc, event)

    set_order_number._ozturk_patched = True
    mod.set_order_number = set_order_number
