# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""POS Opening Entry — smena EGASI kim bo'lishi mumkinligi.

NEGA HUJJAT DARAJASIDA
======================
Smenani ochishning bir nechta yo'li bor: kassa sahifasi, Desktop POS va
ERPNext Desk. Tekshiruvni har uchalasiga alohida yozsak, ular bir kun
kelib bir-biridan ajralib ketadi — bu app'da aynan shu xato bo'lgan
(smena yopish sanog'i bekor qilingan cheklarni ham qo'shib hisoblagan,
chunki filtr ikki joyda ayri yozilgan edi).

Shuning uchun asosiy qoida HUJJATNING O'ZIGA osilgan: qaysi yo'l bilan
kelishidan qat'i nazar, `POS Opening Entry.user` POS Profile'ga
biriktirilgan kassir bo'lishi shart.

NEGA `doc.user`, `frappe.session.user` EMAS
===========================================
Ahamiyatlisi — smena KIMGA yozilgani, kim tugma bosgani emas. ERPNext
yopishda cheklarni `where owner = <opening.user>` bilan yig'adi
(`pos_closing_entry.get_pos_invoices`), ya'ni hisobotning to'g'riligi
AYNAN shu maydonga bog'liq.

Shu tanlov tufayli menejer uchun zaxira yo'l ham ochiq qoladi: u Desk'dan
smenani kassir NOMIGA ocha oladi (masalan kassir kirolmay qolganda), va
hisobot baribir buzilmaydi. Interaktiv yo'llar — kassa sahifasi va
Desktop POS — qo'shimcha ravishda sessiya foydalanuvchisini ham
tekshiradi (`api/desktop_pos.py`).
"""

import frappe
from frappe import _

from ozturkapp.ozturkapp.utils import cashier_permissions


def validate(doc, method=None):
    """Smena egasi POS Profile'ga biriktirilgan kassir bo'lishi shart."""
    allowed = cashier_permissions.pos_profile_users(doc.pos_profile)

    # Ro'yxat bo'sh — profil hali sozlanmagan. Cheklov qo'llanmaydi
    # (`cashier_permissions.can_operate_shift()` bilan bir xil kelishuv).
    if not allowed or doc.user in allowed:
        return

    frappe.throw(
        _(
            "Kassa smenasi {0} nomiga ochilmaydi. '{1}' kassasi faqat "
            "quyidagi kassir(lar) uchun ishlaydi: {2}.\n\n"
            "Boshqa foydalanuvchi nomidagi smena Z-hisobotni buzadi: "
            "cheklar {3} nomiga yozilib, hisobotga umuman tushmaydi."
        ).format(
            frappe.bold(cashier_permissions._user_label(doc.user)),
            doc.pos_profile,
            cashier_permissions.shift_operator_names(doc.pos_profile),
            frappe.bold(cashier_permissions._user_label(doc.user)),
        ),
        title=_("Kassir biriktirilmagan"),
    )
