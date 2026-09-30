# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Menejer tasdig'i — kassir o'zi qila olmaydigan amallar uchun.

QAYSI AMALLAR
=============
to'langan chekni qaytarish, kassadan katta summa chiqarish. Qoida har
amalning O'Z modulida; bu modul faqat "menejermi?" savoliga javob beradi.

Avval kassir menejer PIN-kodi (`User.custom_pos_pin`) bilan tasdiq olardi.
PIN foydalanuvchi talabi bilan butunlay olib tashlandi: bu amallarni endi
faqat menejerning O'ZI (URY Manager / System Manager) kassada bajaradi.
Chegirma esa umuman tasdiq talab qilmaydi (`utils/discounts.py`).

Har bir menejer amali hujjat tarixiga (Comment) yoziladi.
"""

import frappe
from frappe import _

from ozturkapp.ozturkapp.utils import cashier_permissions

#: Eski PIN maydoni — migrate paytida o'chiriladi (`setup()`).
OBSOLETE_PIN_FIELD = "custom_pos_pin"


class ApprovalRequired(frappe.ValidationError):
    """Amalni faqat menejer bajara oladi."""

    http_status_code = 403


# ═══════════════════════════════════════════════════════════════════
#  Sozlash
# ═══════════════════════════════════════════════════════════════════

def setup():
    remove_pin_field()
    frappe.db.commit()


def remove_pin_field():
    """Eski `User.custom_pos_pin` maydonini (bo'lsa) o'chiradi."""
    name = frappe.db.get_value("Custom Field", {"dt": "User", "fieldname": OBSOLETE_PIN_FIELD})
    if name:
        frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
        print("🧹 Menejer PIN maydoni o'chirildi (User.custom_pos_pin)")
    # Shifrlangan qiymatlar ham qolib ketmasin.
    frappe.db.delete("__Auth", {"doctype": "User", "fieldname": OBSOLETE_PIN_FIELD})


# ═══════════════════════════════════════════════════════════════════
#  Tasdiqlash
# ═══════════════════════════════════════════════════════════════════

def require(
    action: str,
    reference_doctype: str = None,
    reference_name: str = None,
    details: str = "",
) -> str:
    """Amalni faqat menejerga ruxsat beradi va uni qaytaradi.

    Args:
        action: amal nomi (xabar va logda ko'rinadi), masalan «Chekni qaytarish».
        reference_doctype / reference_name: amal izi qaysi hujjat tarixiga
            yozilsin (masalan `POS Invoice`).
        details: izohga qo'shimcha matn (sabab, summa).

    Raises:
        ApprovalRequired: joriy foydalanuvchi menejer emas.
    """
    if not cashier_permissions.has_supervisor_role():
        frappe.throw(
            _("«{0}» ni faqat menejer bajara oladi.").format(action),
            exc=ApprovalRequired,
            title=_("Menejer kerak"),
        )

    _audit(action, reference_doctype, reference_name, details)
    return frappe.session.user


def _audit(action, reference_doctype, reference_name, details):
    """Tasdiq izini hujjat tarixiga yozadi (hisobotda ko'rinadi)."""
    if not (reference_doctype and reference_name):
        return

    text = _("{0}: «{1}»").format(_("Menejer amali"), frappe.utils.escape_html(action))
    if details:
        text += f" ({frappe.utils.escape_html(details)})"

    frappe.get_doc(
        {
            "doctype": "Comment",
            "comment_type": "Info",
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "content": text,
        }
    ).insert(ignore_permissions=True)
