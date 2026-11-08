# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""POS Invoice — xizmat haqi chek chegirmasidan OLDINGI summadan.

NEGA KLASS OVERRIDE
===================
Jami summani ERPNext `validate` da, URY esa o'z oqimlarida (`sync_order`,
`split_bill`, `make_invoice`) va bizning kod (choychaqa, chegirma,
qaytarish) to'g'ridan-to'g'ri `doc.calculate_taxes_and_totals()` bilan
hisoblaydi — ko'pincha saqlashdan OLDIN, to'lov summasini olish uchun.
`before_validate` hook'i bu chaqiruvlarning hech birini ko'rmaydi.
Barcha yo'llar shu BITTA metoddan o'tadi, shuning uchun qoida shu yerda:

    1. ERPNext odatdagidek hisoblaydi (`doc.total` yangilanadi);
    2. `service_charge.apply()` xizmat haqi qatorini "Actual" qilib,
       summasini foiz × `doc.total` ga qo'yadi;
    3. o'zgargan bo'lsa ERPNext jami summani qayta hisoblaydi.

Qoidaning o'zi va sabablari: `utils/service_charge.py`. Shu yerda
«Aksiya» chegirmasi ham ichimliklarsiz hisoblanadi (`utils/promo_discount.py`).
ERPNext kodi o'zgartirilmaydi (upstream).
"""

from erpnext.accounts.doctype.pos_invoice.pos_invoice import POSInvoice

from ozturkapp.ozturkapp.overrides import ury_order_number
from ozturkapp.ozturkapp.utils import promo_discount, service_charge

# Amend chek (`INV-00871-1`) URY tartib raqami hook'ini yiqitmasin.
ury_order_number.patch()


class OzturkPOSInvoice(POSInvoice):
    def before_insert(self):
        parent = getattr(super(), "before_insert", None)
        if parent:
            parent()
        # Amend: Frappe `copy_doc(from_amend=True)` `no_copy` maydonlarni
        # HAM nusxalaydi, shuning uchun bekor qilingan chekning
        # `custom_client_ref` (unique) yangi hujjatga o'tib, saqlashda
        # «Client Ref (UUID) must be unique» xatosi chiqadi. Amend — yangi
        # hujjat, ofitsant/kassa idempotency kaliti unga tegishli emas.
        if self.amended_from:
            self.custom_client_ref = None

    def calculate_taxes_and_totals(self):
        super().calculate_taxes_and_totals()
        # «Aksiya» chegirmasi ichimliklarsiz summadan (`utils/promo_discount.py`).
        with promo_discount.fixed_amount(self) as promo:
            if promo:
                super().calculate_taxes_and_totals()
            if service_charge.apply(self):
                super().calculate_taxes_and_totals()
        promo_discount.redistribute(self)
