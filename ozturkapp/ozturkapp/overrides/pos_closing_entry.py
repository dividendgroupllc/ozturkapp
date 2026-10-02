# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""POS Closing Entry — cheklar KUN bo'yicha konsolidatsiya qilinadi.

MUAMMO
======
ERPNext smenadagi cheklarni faqat mijoz va hisob o'lchovlari bo'yicha
guruhlab BITTA Sales Invoice'ga birlashtiradi; uning sanasi har chek
qo'shilganda qayta yoziladi (`pos_invoice_merge_log.merge_pos_invoice_into`)
va OXIRGI chekning sanasi bo'lib qoladi. Smena bir necha kun ochiq tursa
(masalan 20.09 – 01.10), butun sotuv, tannarx va to'lovlar buxgalteriya hamda
omborda bitta kunga tushardi.

YECHIM
======
ERPNext'ning o'z `create_merge_logs` iga guruhlarni KUN bo'yicha ham
ajratib beramiz: har bir kun uchun alohida Sales Invoice, o'z sanasida.
Kunlar o'sish tartibida — ombor tannarxi (FIFO) ham xronologik yoziladi.
ERPNext kodi o'zgartirilmaydi (upstream).
"""

import frappe
from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import POSClosingEntry
from erpnext.accounts.doctype.pos_invoice_merge_log import pos_invoice_merge_log as merge
from frappe.utils import get_datetime, getdate

from ozturkapp.ozturkapp.utils import kassa_telegram

#: ERPNext ham shu chegaradan boshlab konsolidatsiyani fon jarayoniga beradi.
QUEUE_THRESHOLD = 10


def _posted_at(row):
    date = row.get("posting_date") or frappe.db.get_value("POS Invoice", row.pos_invoice, "posting_date")
    time = frappe.db.get_value("POS Invoice", row.pos_invoice, "posting_time") or "00:00:00"
    return getdate(date), get_datetime(f"{getdate(date)} {time}")


def invoice_map_by_day(invoices) -> dict:
    """ERPNext xaritasi (mijoz -> o'lchov -> cheklar) + kun: `{mijoz: {"<o'lchov>|<sana>": [...]}}`."""
    stamp = {row.pos_invoice: _posted_at(row) for row in invoices}
    ordered = sorted(invoices, key=lambda row: stamp[row.pos_invoice][1])
    result = {}
    for customer, groups in merge.get_invoice_customer_map(ordered).items():
        by_day = {}
        for key, rows in groups.items():
            for row in rows:
                by_day.setdefault(f"{key}|{stamp[row.pos_invoice][0]}", []).append(row)
        # Kunlar xronologik tartibda (dict tartibi saqlanadi).
        result[customer] = dict(sorted(by_day.items(), key=lambda kv: kv[0].rsplit("|", 1)[1]))
    return result


def consolidate_by_day(closing_entry):
    invoices = closing_entry.get("pos_transactions")
    by_customer = invoice_map_by_day(invoices)
    if len(invoices) >= QUEUE_THRESHOLD:
        closing_entry.set_status(update=True, status="Queued")
        _enqueue_after_commit(by_customer, closing_entry)
    else:
        merge.create_merge_logs(by_customer, closing_entry)


def _enqueue_after_commit(by_customer, closing_entry):
    """Konsolidatsiya fon vazifasi — FAQAT tranzaksiya commit bo'lgandan keyin.

    ERPNext `enqueue_job` vazifani darhol navbatga qo'yadi. Kassa yopilishi
    (`api/cashier.close_shift`) esa submit'dan keyin Z-hisobotni ham tuzadi —
    ko'p chekli smenada worker vazifani so'rov commit bo'lmasdan olib,
    «Could not find POS Closing Entry» bilan yiqilardi (2026-10-02,
    POS-CLO-2026-00003). `enqueue_after_commit` bu poygani yo'q qiladi.
    """
    merge.check_scheduler_status()
    job_id = f"pos_invoice_merge::{closing_entry.get('name')}"
    if merge.is_job_enqueued(job_id):
        return
    frappe.enqueue(
        merge.create_merge_logs,
        queue="long",
        timeout=10000,
        event="processing_merge_logs",
        job_id=job_id,
        enqueue_after_commit=True,
        now=frappe.conf.developer_mode or frappe.flags.in_test,
        invoice_by_customer=by_customer,
        closing_entry=closing_entry,
    )
    frappe.msgprint(frappe._("POS Invoices will be consolidated in a background process"), alert=1)


class OzturkPOSClosingEntry(POSClosingEntry):
    def on_submit(self):
        consolidate_by_day(self)
        kassa_telegram.enqueue_closing(self)
        frappe.publish_realtime(
            f"poe_{self.pos_opening_entry}_closed",
            self,
            docname=f"POS Opening Entry/{self.pos_opening_entry}",
        )

    @frappe.whitelist()
    def retry(self):
        consolidate_by_day(self)
