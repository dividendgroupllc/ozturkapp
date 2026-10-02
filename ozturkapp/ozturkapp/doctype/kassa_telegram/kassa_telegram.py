# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa yopilganda Telegram guruhga kassa qoldig'i — sozlamalar (bot token, guruh ID).

Xabar matni va yuborish: `utils/kassa_telegram.py`.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class KassaTelegram(Document):
    def validate(self):
        if self.enabled and not (self.get_password("bot_token", raise_exception=False) and self.chat_id):
            frappe.throw(_("Yoqish uchun Bot token va Guruh ID kiritilishi shart"))


@frappe.whitelist()
def send_test():
    """Sozlamalarni tekshirish — guruhga sinov xabari."""
    frappe.only_for(("System Manager", "Accounts Manager"))
    from ozturkapp.ozturkapp.utils.kassa_telegram import send_message

    send_message(_("✅ Ozturk kassa: sinov xabari. Sozlamalar to'g'ri."), raise_errors=True)
    return True
