# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kunlik sotuv — har kuni qancha sotildi, qancha chegirma berildi va qaysi
to'lov turidan qancha tushdi / qancha chegirma ketdi.

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

MONEY = ("gross", "discount", "net", "service", "tips", "revenue")


def execute(filters=None):
    filters = frappe._dict(filters or {})
    parsed = sd._parse_filters(filters.from_date, filters.to_date, filters.company, filters.branch, None)
    invoices = sd._invoices(parsed)

    days, modes = {}, {}
    for inv in invoices:
        key = str(getdate(inv.posting_date))
        row = days.setdefault(key, frappe._dict(date=key, checks=0, **dict.fromkeys(MONEY, 0.0)))
        if not inv.is_return:
            row.checks += 1
        row.gross += flt(inv.gross)
        row.discount += flt(inv.discount)
        row.net += flt(inv.net_total)
        row.service += flt(inv.service)
        row.tips += flt(inv.tips)
        row.revenue += flt(inv.amount)

        paid = sum(abs(flt(a)) for _m, a in inv.payments)
        for mode, amount in inv.payments:
            slot = modes.setdefault(mode, len(modes))
            row[f"pay_{slot}"] = flt(row.get(f"pay_{slot}")) + flt(amount)
            share = flt(inv.discount) * abs(flt(amount)) / paid if paid else 0
            row[f"disc_{slot}"] = flt(row.get(f"disc_{slot}")) + share

    # To'lov turlari — jami tushum bo'yicha kattadan kichikka
    totals = {m: sum(flt(r.get(f"pay_{s}")) for r in days.values()) for m, s in modes.items()}
    ordered = sorted(modes.items(), key=lambda kv: -totals[kv[0]])

    data = sorted(days.values(), key=lambda r: r.date)
    for row in data:
        for _mode, slot in ordered:
            row.setdefault(f"pay_{slot}", 0.0)
            row.setdefault(f"disc_{slot}", 0.0)

    return columns(ordered), data, None, chart(data), summary(data)


def columns(ordered_modes):
    cols = [
        {"label": _("Sana"), "fieldname": "date", "fieldtype": "Date", "width": 100},
        {"label": _("Cheklar"), "fieldname": "checks", "fieldtype": "Int", "width": 75},
        {"label": _("Yalpi sotuv"), "fieldname": "gross", "fieldtype": "Currency", "width": 120},
        {"label": _("Chegirma"), "fieldname": "discount", "fieldtype": "Currency", "width": 110},
        {"label": _("Sof sotuv"), "fieldname": "net", "fieldtype": "Currency", "width": 120},
        {"label": _("Xizmat haqi"), "fieldname": "service", "fieldtype": "Currency", "width": 110},
        {"label": _("Choychaqa"), "fieldname": "tips", "fieldtype": "Currency", "width": 100},
        {"label": _("Tushum"), "fieldname": "revenue", "fieldtype": "Currency", "width": 125},
    ]
    for mode, slot in ordered_modes:
        cols.append({"label": _("{0}: to'langan").format(mode), "fieldname": f"pay_{slot}",
                     "fieldtype": "Currency", "width": 140})
        cols.append({"label": _("{0}: chegirma").format(mode), "fieldname": f"disc_{slot}",
                     "fieldtype": "Currency", "width": 130})
    return cols


def chart(data):
    if not data:
        return None
    return {
        "data": {
            "labels": [getdate(r.date).strftime("%d.%m") for r in data],
            "datasets": [
                {"name": _("Tushum"), "values": [flt(r.revenue) for r in data]},
                {"name": _("Chegirma"), "values": [flt(r.discount) for r in data]},
            ],
        },
        "type": "bar",
        "colors": ["#2490ef", "#e24c4c"],
        "axisOptions": {"yAxisRange": {"min": 0}},
    }


def summary(data):
    total = lambda f: sum(flt(r[f]) for r in data)  # noqa: E731
    gross = total("gross")
    return [
        {"label": _("Tushum"), "value": total("revenue"), "datatype": "Currency", "indicator": "Blue"},
        {"label": _("Yalpi sotuv"), "value": gross, "datatype": "Currency", "indicator": "Green"},
        {"label": _("Chegirma"), "value": total("discount"), "datatype": "Currency", "indicator": "Red"},
        {"label": _("Chegirma ulushi"), "value": (total("discount") / gross * 100) if gross else 0,
         "datatype": "Percent", "indicator": "Orange"},
        {"label": _("Xizmat haqi"), "value": total("service"), "datatype": "Currency", "indicator": "Grey"},
        {"label": _("Cheklar"), "value": sum(r.checks for r in data), "datatype": "Int", "indicator": "Grey"},
    ]
