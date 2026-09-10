# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Chop etish topshirig'i — server va restoran agenti o'rtasidagi navbat.

HAYOT SIKLI
===========
    Pending  --(agent pull_jobs)-->  Printing  --(ack ok)-->   Done
                                        |
                                        +--(ack xato)--> Pending (qayta)  yoki  Failed
                                        +--(60s javob yo'q, scheduler)--> Pending

`payload` — TAYYOR ESC/POS baytlar (base64). Chek formati serverda
(`utils/escpos.py`) tuziladi, agent faqat baytlarni printerga uzatadi.
Shunda formatni o'zgartirish uchun monoblokka tegish shart emas.

Mantiq `utils/print_queue.py` da; bu klass faqat holat o'tishlarini
tekshiradi.
"""

import frappe
from frappe import _
from frappe.model.document import Document

STATUSES = ("Pending", "Printing", "Done", "Failed")

#: Shuncha muvaffaqiyatsiz urinishdan keyin Failed (qayta chop etish qo'lda).
MAX_ATTEMPTS = 5


class OzturkPrintJob(Document):
    def validate(self):
        if self.status not in STATUSES:
            frappe.throw(_("Noto'g'ri holat: {0}").format(self.status))
        if not self.payload:
            frappe.throw(_("Payload bo'sh — chop etadigan narsa yo'q"))
        if not self.title:
            self.title = f"{self.job_type} {self.ref_name or ''}".strip()

    def before_insert(self):
        printer_branch, enabled = frappe.db.get_value(
            "Ozturk Printer", self.printer, ["branch", "enabled"]
        ) or (None, 0)
        if not enabled:
            frappe.throw(_("Printer '{0}' o'chirilgan").format(self.printer))
        if printer_branch and printer_branch != self.branch:
            frappe.throw(
                _("Printer '{0}' boshqa filialga ({1}) tegishli").format(self.printer, printer_branch)
            )
