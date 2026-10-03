# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kunlik sotuv — har bir tranzaksiya (POS chek) alohida qatorda: qancha sotildi,
qancha chegirma berildi va qaysi to'lov turidan qancha tushdi / qancha chegirma ketdi.
Tepadagi kartalar va grafik — tanlangan davr bo'yicha jami (grafik kunlar bo'yicha).

Manba — POS cheklari (submit qilingan, kassa yopilmagan bo'lsa ham). Pul
hisobi sotuv dashboardi bilan BIR XIL (`api/sales_dashboard._invoices`):

    yalpi       = menyu narxidagi sotuv (chegirmagacha)
    chegirma    = qator + chek chegirmasi
    sof sotuv   = yalpi - chegirma
    tushum      = sof sotuv + xizmat haqi + choychaqa = to'lovlar (qaytim ayirilgan)

Bo'lib to'langan chekda chegirma to'lov turlari orasida TO'LOV ULUSHIGA
qarab bo'linadi (dashboarddagi «To'lov usuli bo'yicha» jadvali kabi).
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

from ozturkapp.ozturkapp.api import sales_dashboard as sd


def execute(filters=None):
    filters = frappe._dict(filters or {})
    parsed = sd._parse_filters(filters.from_date, filters.to_date, filters.company, filters.branch, None)
    invoices = sorted(sd._invoices(parsed), key=lambda inv: (str(inv.posting_date), str(inv.posting_time or "")))

    modes, data = {}, []
    for inv in invoices:
        reason = (inv.get("custom_discount_reason") or "").strip() if flt(inv.discount) else ""
        row = frappe._dict(
            date=str(getdate(inv.posting_date)),
            time=str(inv.posting_time or "")[:5],
            invoice=inv.name,
            voucher_type=inv.get("voucher_type") or "POS Invoice",
            legacy=1 if inv.get("legacy") else 0,
            is_return=inv.is_return,
            reason=reason,
            gross=flt(inv.gross),
            discount=flt(inv.discount),
            net=flt(inv.net_total),
            service=flt(inv.service),
            tips=flt(inv.tips),
            revenue=flt(inv.amount),
        )
        paid = sum(abs(flt(a)) for _m, a in inv.payments)
        for mode, amount in inv.payments:
            slot = modes.setdefault(mode, len(modes))
            row[f"pay_{slot}"] = flt(row.get(f"pay_{slot}")) + flt(amount)
            share = flt(inv.discount) * abs(flt(amount)) / paid if paid else 0
            row[f"disc_{slot}"] = flt(row.get(f"disc_{slot}")) + share
        data.append(row)

    # To'lov turlari — jami tushum bo'yicha kattadan kichikka
    totals = {m: sum(flt(r.get(f"pay_{s}")) for r in data) for m, s in modes.items()}
    ordered = sorted(modes.items(), key=lambda kv: -totals[kv[0]])
    for row in data:
        for _mode, slot in ordered:
            row.setdefault(f"pay_{slot}", 0.0)
            row.setdefault(f"disc_{slot}", 0.0)

    return columns(ordered), data, None, chart(data), summary(data)


def columns(ordered_modes):
    cols = [
        {"label": _("Sana"), "fieldname": "date", "fieldtype": "Date", "width": 95},
        {"label": _("Vaqt"), "fieldname": "time", "fieldtype": "Data", "width": 60},
        {"label": _("Chek"), "fieldname": "invoice", "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 105},
        {"label": _("Hujjat turi"), "fieldname": "voucher_type", "fieldtype": "Data", "hidden": 1, "width": 90},
        {"label": _("Chegirma sababi"), "fieldname": "reason", "fieldtype": "Data", "width": 115},
        {"label": _("Yalpi sotuv"), "fieldname": "gross", "fieldtype": "Currency", "width": 115},
        {"label": _("Chegirma"), "fieldname": "discount", "fieldtype": "Currency", "width": 105},
        {"label": _("Sof sotuv"), "fieldname": "net", "fieldtype": "Currency", "width": 115},
        {"label": _("Xizmat haqi"), "fieldname": "service", "fieldtype": "Currency", "width": 105},
        {"label": _("Choychaqa"), "fieldname": "tips", "fieldtype": "Currency", "width": 95},
        {"label": _("Tushum"), "fieldname": "revenue", "fieldtype": "Currency", "width": 120},
    ]
    for mode, slot in ordered_modes:
        cols.append({"label": _("{0}: to'langan").format(mode), "fieldname": f"pay_{slot}",
                     "fieldtype": "Currency", "width": 140})
        cols.append({"label": _("{0}: chegirma").format(mode), "fieldname": f"disc_{slot}",
                     "fieldtype": "Currency", "width": 130})
    return cols


def chart(data):
    """Grafik — kunlar bo'yicha jami (jadval esa tranzaksiyalar bo'yicha)."""
    if not data:
        return None
    days = {}
    for r in data:
        day = days.setdefault(r.date, [0.0, 0.0])
        day[0] += flt(r.revenue)
        day[1] += flt(r.discount)
    keys = sorted(days)
    return {
        "data": {
            "labels": [getdate(k).strftime("%d.%m") for k in keys],
            "datasets": [
                {"name": _("Tushum"), "values": [days[k][0] for k in keys]},
                {"name": _("Chegirma"), "values": [days[k][1] for k in keys]},
            ],
        },
        "type": "bar",
        "colors": ["#2490ef", "#e24c4c"],
        "axisOptions": {"yAxisRange": {"min": 0}},
    }


def summary(data):
    total = lambda f: sum(flt(r[f]) for r in data)  # noqa: E731
    return [
        {"label": _("Yalpi sotuv"), "value": total("gross"), "datatype": "Currency", "indicator": "Green"},
        {"label": _("Chegirma"), "value": total("discount"), "datatype": "Currency", "indicator": "Red"},
        {"label": _("Tushum"), "value": total("revenue"), "datatype": "Currency", "indicator": "Blue"},
        {"label": _("Xizmat haqi"), "value": total("service"), "datatype": "Currency", "indicator": "Grey"},
        {"label": _("Cheklar soni"), "value": sum(1 for r in data if not r.is_return and not r.legacy),
         "datatype": "Int", "indicator": "Grey"},
    ]
