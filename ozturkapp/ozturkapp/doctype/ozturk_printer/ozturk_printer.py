# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Restoran ichidagi chek printeri (Ethernet, ESC/POS, port 9100).

NEGA O'Z DOCTYPE
================
Frappe'ning `Network Printer Settings` CUPS orqali SERVERDAN chop etish
uchun mo'ljallangan — server printerni to'g'ridan-to'g'ri ko'rishi shart.
Bizda server chet elda, restoran provayderi esa VPN'ni bloklaydi. Shuning
uchun printerga MONOBLOKDAGI AGENT chiqaradi, serverda faqat printerning
"manzili" saqlanadi (`utils/print_queue.py`).

Printer IP'si o'zgarsa shu hujjat tahrirlanadi — agentga tegilmaydi.
"""

import ipaddress
import re

import frappe
from frappe import _
from frappe.model.document import Document

#: Ruxsat etilgan vazifalar — `utils/print_queue.py` bilan bir xil.
ROLE_CASHIER = "Kassa"
ROLE_KITCHEN = "Oshxona"

#: Kodlash -> ESC/POS kod jadvali (ESC t n) standart qiymati.
DEFAULT_CODEPAGE_NUMBERS = {"cp866": 17, "cp1251": 73, "cp437": 0}

_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-\.]{0,253}[A-Za-z0-9])?$")


class OzturkPrinter(Document):
    def validate(self):
        self.ip_address = (self.ip_address or "").strip()
        self._validate_address()
        if not self.port or self.port <= 0 or self.port > 65535:
            frappe.throw(_("Port 1 dan 65535 gacha bo'lishi kerak"))

        if self.role == ROLE_KITCHEN and not self.production_unit:
            frappe.throw(_("Oshxona printeri uchun stansiya (URY Production Unit) tanlanishi shart"))
        if self.role != ROLE_KITCHEN:
            self.production_unit = None

        if self.codepage_number is None or self.codepage_number == 0 and self.codepage != "cp437":
            self.codepage_number = DEFAULT_CODEPAGE_NUMBERS.get(self.codepage, 17)

        if self.production_unit:
            unit_branch = frappe.db.get_value("URY Production Unit", self.production_unit, "branch")
            if unit_branch and unit_branch != self.branch:
                frappe.throw(
                    _("Stansiya '{0}' boshqa filialga ({1}) tegishli").format(
                        self.production_unit, unit_branch
                    )
                )

    def _validate_address(self):
        if not self.ip_address:
            frappe.throw(_("IP manzil kiritilishi shart"))
        try:
            ipaddress.ip_address(self.ip_address)
            return
        except ValueError:
            pass
        if not _HOSTNAME_RE.match(self.ip_address):
            frappe.throw(_("IP manzil noto'g'ri: {0}").format(self.ip_address))

    @property
    def columns(self) -> int:
        """Bir qatorga sig'adigan belgilar soni (Font A)."""
        return 32 if str(self.paper_width) == "58" else 48
