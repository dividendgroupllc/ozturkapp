# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Stock Entry — avto ishlab chiqarishda kasr dona (Nos) ruxsat.

MUAMMO
======
"Non xamiri (tayyor)" BOM'ida tuxum bor: 5 kg xamirga 5 dona (Nos). Taomga
0.1 kg xamir ketadi, ya'ni buyurtma bo'yicha ishlab chiqarish 0.1 dona
tuxum yozadi. "Nos" UOM'i `must_be_whole_number = 1` — ERPNext butun
hujjatni "Quantity should be whole number" deb rad etadi va buning uchun
flag yo'q.

YECHIM
======
Tekshiruv FAQAT `utils/auto_manufacture.py` yaratgan hujjatda
(`flags.ozturk_auto_manufacture`) o'tkazib yuboriladi. Qo'lda kiritiladigan
Stock Entry'lar odatdagidek tekshiriladi. ERPNext kodi o'zgartirilmaydi.
"""

from erpnext.stock.doctype.stock_entry.stock_entry import StockEntry


class OzturkStockEntry(StockEntry):
    def validate_uom_is_integer(self, uom_field, qty_fields, child_dt=None):
        if self.flags.get("ozturk_auto_manufacture"):
            return
        super().validate_uom_is_integer(uom_field, qty_fields, child_dt)
