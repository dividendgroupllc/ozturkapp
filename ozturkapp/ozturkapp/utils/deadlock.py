# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""MariaDB deadlock (xato 1213) bo'lganda so'rovni qayta urinish.

NEGA KERAK
==========
Ikki tranzaksiya bir xil qatorlarni qarama-qarshi tartibda qulflasa, MariaDB
ulardan BIRINI (qurbonni) butunlay orqaga qaytaradi va Frappe `QueryDeadlockError`
ko'taradi. Kassir esa «Server failed to process this request because of a concurrent
conflicting request» oynasini ko'radi va to'lovni qo'lda qayta bosishi kerak.

Bu xato o'zi zararsiz: qurbon tranzaksiyaning HAMMASI qaytariladi, chek o'zgarmaydi.
Shuning uchun so'rovni boshidan qayta bajarish xavfsiz. Kassirga ko'rinmaydigan
qilib, bir necha marta urinamiz; hammasi muvaffaqiyatsiz bo'lsagina xato chiqadi.

XAVFSIZLIK SHARTLARI
====================
* Faqat `QueryDeadlockError` ushlanadi. Kutish muddati tugashi (`QueryTimeoutError`)
  yoki mantiqiy xato qayta urinilmaydi.
* Qayta urinishdan oldin `frappe.db.rollback()`: Frappe kechiktirilgan
  (`after_commit`) ishlarni ham tozalaydi, shuning uchun muvaffaqiyatsiz urinishdan
  qolgan g'aladon/chop etish topshirig'i ishga tushmaydi.
* Funksiya boshidan qayta bajarilganda o'z himoyalarini (masalan «bu buyurtma
  allaqachon to'langan») qayta tekshiradi, shuning uchun ikki marta to'lov bo'lmaydi.
"""

import functools
import time

import frappe

#: Jami urinishlar soni (birinchisi ham hisobda).
MAX_ATTEMPTS = 3

#: Urinishlar orasidagi kutish: `BACKOFF * urinish raqami` soniya.
BACKOFF = 0.2


def retry_on_deadlock(function):
    """`@frappe.whitelist()` DAN KEYIN (ostida) qo'yiladi:

        @frappe.whitelist(methods=["POST"])
        @retry_on_deadlock
        def submit_payment(...): ...
    """

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        attempt = 1
        while True:
            try:
                return function(*args, **kwargs)
            except frappe.QueryDeadlockError:
                if attempt >= MAX_ATTEMPTS:
                    raise

                frappe.logger("ozturk_cashier").warning(
                    "%s: deadlock, qayta urinish %s/%s (user=%s)",
                    function.__name__,
                    attempt,
                    MAX_ATTEMPTS,
                    frappe.session.user,
                )
                frappe.db.rollback()
                time.sleep(BACKOFF * attempt)
                attempt += 1

    return wrapper
