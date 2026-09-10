# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Chop etish navbati — cheklar printerga EMAS, navbatga yoziladi.

NEGA NAVBAT
===========
Server chet elda, printerlar restoran ichida, provayder VPN'ni bloklaydi.
Serverdan printerga "kirib" bo'lmaydi, lekin restorandan serverga HTTPS
bilan chiqish doim ochiq. Shuning uchun:

    hodisa -> Ozturk Print Job (Pending) -> agent pull_jobs() -> printer -> ack()

Agent (monoblokdagi kichik Python xizmati) har 1-2 soniyada `pull_jobs`
ni chaqiradi. Internet uzilsa topshiriqlar serverda to'planadi va ulanish
qaytganda chop etiladi.

PRINTER QAYSI
=============
    Bill      -> filialning `role=Kassa` printeri (bitta)
    KOT       -> stansiyaning (`URY KOT.production`) `role=Oshxona` printer(lar)i
    KOT Item  -> xuddi KOT kabi (taom "Tayyor" bo'lganda)

Printer sozlanmagan bo'lsa `enqueue_*` `None` qaytaradi — chaqiruvchi
eski (brauzer) yo'lga tushadi. Ya'ni printer yo'q joyda hech narsa buzilmaydi.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from ozturkapp.ozturkapp.utils import escpos

ROLE_CASHIER = "Kassa"
ROLE_KITCHEN = "Oshxona"

#: Agent shuncha soniya `heartbeat` yubormasa — oflayn.
AGENT_TTL = 45

#: `Printing` da shuncha soniyadan ko'p qolgan topshiriq Pending ga qaytariladi.
STALE_AFTER = 90

#: Shuncha muvaffaqiyatsiz urinishdan keyin Failed.
MAX_ATTEMPTS = 5

#: Bir so'rovda agentga beriladigan topshiriqlar soni.
PULL_LIMIT = 10


# ═══════════════════════════════════════════════════════════════
#  Printerlarni topish
# ═══════════════════════════════════════════════════════════════

PRINTER_FIELDS = [
    "name", "printer_name", "branch", "role", "production_unit", "ip_address", "port",
    "paper_width", "codepage", "codepage_number", "cut_paper",
    "header_line_1", "header_line_2", "footer_text",
]


def get_printers(branch: str, role: str | None = None, production_unit: str | None = None) -> list:
    filters = {"branch": branch, "enabled": 1}
    if role:
        filters["role"] = role
    if production_unit:
        filters["production_unit"] = production_unit
    return frappe.get_all(
        "Ozturk Printer", filters=filters, fields=PRINTER_FIELDS, order_by="printer_name asc"
    )


def cashier_printer(branch: str):
    rows = get_printers(branch, ROLE_CASHIER)
    return rows[0] if rows else None


def kitchen_printers(branch: str, production_unit: str | None) -> list:
    if not production_unit:
        return []
    return get_printers(branch, ROLE_KITCHEN, production_unit)


def has_printer(branch: str, role: str, production_unit: str | None = None) -> bool:
    if not branch:
        return False
    if role == ROLE_KITCHEN:
        return bool(kitchen_printers(branch, production_unit))
    return cashier_printer(branch) is not None


# ═══════════════════════════════════════════════════════════════
#  Navbatga qo'yish
# ═══════════════════════════════════════════════════════════════

def enqueue(job_type: str, printer: dict, payload: bytes, branch: str,
            ref_doctype: str | None = None, ref_name: str | None = None,
            title: str | None = None) -> str:
    """Tayyor ESC/POS baytlarni navbatga yozish. Topshiriq nomini qaytaradi."""
    doc = frappe.get_doc({
        "doctype": "Ozturk Print Job",
        "branch": branch,
        "printer": printer["name"],
        "job_type": job_type,
        "status": "Pending",
        "title": title or f"{job_type} {ref_name or ''}".strip(),
        "ref_doctype": ref_doctype,
        "ref_name": ref_name,
        "payload": escpos.to_b64(payload),
        "attempts": 0,
    })
    doc.flags.ignore_permissions = True
    doc.insert()
    frappe.logger("ozturk_print").info(
        "print job %s: %s -> %s (%s)", doc.name, job_type, printer["name"], ref_name
    )
    return doc.name


def enqueue_bill(invoice: str, scope=None) -> str | None:
    """Mijoz cheki — kassa printeriga. Printer yo'q bo'lsa `None`."""
    from ozturkapp.ozturkapp.utils import cashier_billing

    if scope is None:
        from ozturkapp.ozturkapp.utils import cashier_permissions
        scope = cashier_permissions.resolve_scope()

    printer = cashier_printer(scope.branch)
    if not printer:
        return None

    bill = cashier_billing.build_bill(invoice, scope, include_kitchen=False)
    bill["restaurant"] = scope.get("restaurant")
    bill["company"] = scope.get("company")
    header = {
        "line1": printer.get("header_line_1"),
        "line2": printer.get("header_line_2"),
        "footer": printer.get("footer_text"),
    }
    payload = escpos.build_bill(bill, printer, header)
    return enqueue(
        "Bill", printer, payload, scope.branch,
        ref_doctype="POS Invoice", ref_name=bill["invoice"],
        title=f"Chek {bill.get('order_number') or bill['invoice']}"
        + (f" | Stol {bill['table']}" if bill.get("table") else ""),
    )


def _kot_payload_data(doc) -> dict:
    """`URY KOT` hujjatidan chek uchun kerakli ma'lumot."""
    invoice_meta = frappe._dict()
    if doc.get("invoice") and frappe.db.exists("POS Invoice", doc.invoice):
        invoice_meta = frappe.db.get_value(
            "POS Invoice", doc.invoice,
            ["waiter", "custom_ury_order_number", "custom_ticket_number", "restaurant_table"],
            as_dict=True,
        ) or frappe._dict()
    waiter = invoice_meta.get("waiter")
    waiter_name = frappe.db.get_value("User", waiter, "full_name") if waiter else None

    items = []
    for row in doc.get("kot_items") or []:
        qty = row.get("quantity")
        items.append({
            "item_name": row.get("item_name") or row.get("item"),
            "qty": qty,
            "comment": row.get("comments"),
        })

    time_value = None
    if doc.get("date") and doc.get("time"):
        time_value = f"{doc.date} {doc.time}"

    return {
        "station": doc.get("production"),
        "kot": doc.name,
        "order_number": doc.get("order_no") or invoice_meta.get("custom_ury_order_number")
        or invoice_meta.get("custom_ticket_number"),
        "table": doc.get("restaurant_table") or invoice_meta.get("restaurant_table"),
        "waiter": waiter_name or waiter,
        "time": time_value or doc.get("creation"),
        "type": doc.get("type"),
        "comments": doc.get("comments"),
        "items": items,
    }


def enqueue_kot(doc) -> list:
    """Yangi KOT — stansiya printer(lar)iga. Printer yo'q bo'lsa bo'sh ro'yxat."""
    printers = kitchen_printers(doc.get("branch"), doc.get("production"))
    if not printers:
        return []
    data = _kot_payload_data(doc)
    if not data["items"]:
        return []
    jobs = []
    for printer in printers:
        payload = escpos.build_kot(data, printer)
        jobs.append(enqueue(
            "KOT", printer, payload, doc.get("branch"),
            ref_doctype="URY KOT", ref_name=doc.name,
            title=f"KOT {data.get('order_number') or doc.name}"
            + (f" | Stol {data['table']}" if data.get("table") else "")
            + f" | {data.get('station') or ''}",
        ))
    return jobs


def enqueue_item_ticket(ticket: dict, branch: str, production_unit: str,
                        kot: str | None = None) -> list:
    """"Taom tayyor" cheki — stansiya printer(lar)iga."""
    printers = kitchen_printers(branch, production_unit)
    if not printers:
        return []
    jobs = []
    for printer in printers:
        payload = escpos.build_item_ticket(ticket, printer)
        jobs.append(enqueue(
            "KOT Item", printer, payload, branch,
            ref_doctype="URY KOT" if kot else None, ref_name=kot,
            title=f"Tayyor: {ticket.get('item_name') or ''}"
            + (f" | Stol {ticket['table']}" if ticket.get("table") else ""),
        ))
    return jobs


def enqueue_test(printer_name: str, label: str = "") -> str:
    printer = frappe.get_doc("Ozturk Printer", printer_name)
    if not printer.enabled:
        frappe.throw(_("Printer '{0}' o'chirilgan").format(printer_name))
    payload = escpos.build_test(printer, label)
    return enqueue("Test", {"name": printer.name}, payload, printer.branch,
                   title=f"Sinov: {printer.printer_name}")


def requeue(job_name: str) -> str:
    """Mavjud topshiriqning nusxasini yangi Pending topshiriq sifatida yaratish."""
    src = frappe.get_doc("Ozturk Print Job", job_name)
    doc = frappe.get_doc({
        "doctype": "Ozturk Print Job",
        "branch": src.branch,
        "printer": src.printer,
        "job_type": src.job_type,
        "status": "Pending",
        "title": src.title,
        "ref_doctype": src.ref_doctype,
        "ref_name": src.ref_name,
        "payload": src.payload,
        "attempts": 0,
    })
    doc.flags.ignore_permissions = True
    doc.insert()
    return doc.name


# ═══════════════════════════════════════════════════════════════
#  Agent tomoni
# ═══════════════════════════════════════════════════════════════

def pull_jobs(branch: str, agent: str, limit: int = PULL_LIMIT) -> list:
    """Pending topshiriqlarni Printing ga o'tkazib, agentga berish.

    Bir filialda odatda bitta agent bo'ladi, lekin ikkitasi bo'lsa ham
    xavfsiz: `UPDATE ... WHERE status='Pending'` — kim birinchi bo'lsa
    o'shaniki, ikkinchisi o'sha topshiriqni olmaydi.
    """
    limit = max(1, min(cint(limit) or PULL_LIMIT, 50))
    names = frappe.get_all(
        "Ozturk Print Job",
        filters={"branch": branch, "status": "Pending"},
        order_by="creation asc",
        limit=limit,
        pluck="name",
    )
    taken = []
    for name in names:
        frappe.db.sql(
            """UPDATE `tabOzturk Print Job`
               SET status='Printing', agent=%s, attempts=attempts+1,
                   modified=%s, modified_by=%s
               WHERE name=%s AND status='Pending'""",
            (agent, now_datetime(), frappe.session.user, name),
        )
        owner = frappe.db.get_value("Ozturk Print Job", name, ["status", "agent"], as_dict=True)
        if owner and owner.status == "Printing" and owner.agent == agent:
            taken.append(name)

    if not taken:
        return []

    printers = {p["name"]: p for p in get_printers(branch)}
    jobs = []
    for row in frappe.get_all(
        "Ozturk Print Job",
        filters={"name": ("in", taken)},
        fields=["name", "printer", "job_type", "title", "payload", "attempts", "creation"],
        order_by="creation asc",
    ):
        printer = printers.get(row.printer) or frappe.db.get_value(
            "Ozturk Printer", row.printer, ["name", "ip_address", "port"], as_dict=True
        )
        jobs.append({
            "job": row.name,
            "job_type": row.job_type,
            "title": row.title,
            "printer": row.printer,
            "host": printer.get("ip_address") if printer else None,
            "port": cint(printer.get("port")) if printer else 9100,
            "payload": row.payload,
            "attempt": row.attempts,
            "created": str(row.creation),
        })
    return jobs


def ack(job_name: str, ok: bool, error: str | None = None, agent: str | None = None) -> dict:
    """Agent natijani qaytaradi."""
    row = frappe.db.get_value(
        "Ozturk Print Job", job_name, ["status", "attempts", "agent"], as_dict=True
    )
    if not row:
        frappe.throw(_("Topshiriq topilmadi: {0}").format(job_name))

    if ok:
        frappe.db.set_value("Ozturk Print Job", job_name, {
            "status": "Done", "printed_at": now_datetime(), "error": None,
            "agent": agent or row.agent,
        }, update_modified=True)
        return {"job": job_name, "status": "Done"}

    error = (error or "Noma'lum xato")[:1000]
    status = "Failed" if cint(row.attempts) >= MAX_ATTEMPTS else "Pending"
    frappe.db.set_value("Ozturk Print Job", job_name, {
        "status": status, "error": error, "agent": agent or row.agent,
    }, update_modified=True)
    frappe.logger("ozturk_print").warning("print job %s failed (%s): %s", job_name, status, error)
    return {"job": job_name, "status": status}


def recover_stale():
    """Scheduler (har daqiqa): javobsiz qolgan Printing topshiriqlarni qaytarish."""
    stale = frappe.db.sql(
        """SELECT name, attempts FROM `tabOzturk Print Job`
           WHERE status='Printing' AND modified < DATE_SUB(%s, INTERVAL %s SECOND)""",
        (now_datetime(), STALE_AFTER),
        as_dict=True,
    )
    for row in stale:
        status = "Failed" if cint(row.attempts) >= MAX_ATTEMPTS else "Pending"
        frappe.db.set_value("Ozturk Print Job", row.name, {
            "status": status,
            "error": "Agent javob bermadi (vaqt tugadi)",
        }, update_modified=True)
    if stale:
        frappe.db.commit()
    return len(stale)


# ═══════════════════════════════════════════════════════════════
#  Agent holati (heartbeat) — keshda, DB emas
# ═══════════════════════════════════════════════════════════════

def _hb_key(branch: str) -> str:
    return f"ozturk_print_agent:{branch}"


def heartbeat(branch: str, agent: str, info: dict | None = None):
    payload = {
        "agent": agent,
        "at": str(now_datetime()),
        "info": info or {},
    }
    frappe.cache().set_value(_hb_key(branch), json.dumps(payload), expires_in_sec=AGENT_TTL)


def agent_status(branch: str) -> dict:
    raw = frappe.cache().get_value(_hb_key(branch))
    if not raw:
        return {"online": False, "agent": None, "at": None, "info": {}}
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except Exception:
        return {"online": False, "agent": None, "at": None, "info": {}}
    return {"online": True, **data}


def queue_summary(branch: str) -> dict:
    pending = frappe.db.count("Ozturk Print Job", {"branch": branch, "status": ("in", ["Pending", "Printing"])})
    failed = frappe.db.count("Ozturk Print Job", {"branch": branch, "status": "Failed"})
    return {"pending": pending, "failed": failed}
