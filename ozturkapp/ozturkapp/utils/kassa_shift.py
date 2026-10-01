# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Operator Kassa hujjatlari -> POS smenasi (g'aladondagi naqd).

NEGA
====
Kassir xarajat kiritmaydi — g'aladondan olingan pulni operator «Kassa»
doctype'iga Расход qilib yozadi. Bu hujjat smena hisobiga kirmasa, kutilgan
naqd summa xarajatcha ko'p chiqib, kassir «kamomad» bilan yopgan bo'lib
qolardi. Shuning uchun g'aladon to'lov turidagi (POS Profile'dagi Cash usul)
Kassa harakati smenaga AVTOMATIK bog'lanadi — operator smena tanlamaydi:

* Smena OCHIQ bo'lsa — shu smenaga: yopishdagi kutilgan summa o'zgaradi
  (Расход kamaytiradi, Приход oshiradi), kamomad umuman chiqmaydi.
* Smena yopilgandan KEYIN kiritilsa — FIFO: kamomadi (Расход uchun) yoki
  ortiqchasi (Приход uchun) hali qoplanmagan ENG ESKI yopilgan smenaga.
  Yopilish hujjati o'zgarmaydi; smena hisobotidagi farq qayta hisoblanadi.
  Qoplanadigan smena bo'lmasa — hujjat smenaga bog'lanmaydi (faqat buxgalteriya).

Bekor qilingan Kassa (docstatus 2) hisobga olinmaydi.
"""

import frappe
from frappe.utils import flt

SHIFT_FIELD = "pos_opening_entry"
AFTER_CLOSE_FIELD = "pos_shift_after_close"


def drawer_modes(pos_profile: str | None = None) -> set:
    """POS Profile(lar)dagi naqd (type = Cash) to'lov turlari — g'aladon."""
    filters = {"parenttype": "POS Profile"}
    if pos_profile:
        filters["parent"] = pos_profile
    modes = set(frappe.get_all("POS Payment Method", filters=filters, pluck="mode_of_payment"))
    if not modes:
        return set()
    return set(
        frappe.get_all("Mode of Payment", filters={"name": ["in", list(modes)], "type": "Cash"}, pluck="name")
    )


def effects(doc) -> dict:
    """`{mode_of_payment: imzoli summa}` — g'aladonga kirim (+) / chiqim (-)."""
    amount = flt(doc.summa)
    out = {}
    if doc.oborot == "Приход" and doc.source_account:
        out[doc.source_account] = amount
    elif doc.oborot == "Расход" and doc.source_account:
        out[doc.source_account] = -amount
    elif doc.oborot == "Перемещение":
        if doc.transfer_source_display:
            out[doc.transfer_source_display] = out.get(doc.transfer_source_display, 0) - amount
        if doc.target_account:
            out[doc.target_account] = out.get(doc.target_account, 0) + amount
    return out


def _profiles_for(mode: str) -> list:
    return frappe.get_all(
        "POS Payment Method", filters={"parenttype": "POS Profile", "mode_of_payment": mode}, pluck="parent"
    )


def _closed_cash_difference(opening: str, mode: str) -> float:
    """Yopilgan smenadagi shu usul farqi (sanalgan - kutilgan) + keyin bog'langan Kassa."""
    closing = frappe.db.get_value(
        "POS Closing Entry", {"pos_opening_entry": opening, "docstatus": 1}, "name"
    )
    if not closing:
        return 0.0
    diff = flt(frappe.db.get_value(
        "POS Closing Entry Detail", {"parent": closing, "mode_of_payment": mode}, "difference"
    ))
    return diff + sum(r[mode] for r in after_close_rows(opening) if mode in r)


def link(doc):
    """Kassa submit: g'aladon usuliga tegsa — smenaga bog'laydi (ochiq yoki FIFO)."""
    drawer = {m: v for m, v in effects(doc).items() if v and m in drawer_modes()}
    if not drawer:
        return
    mode, signed = next(iter(drawer.items()))
    profiles = _profiles_for(mode)

    opening = frappe.db.get_value(
        "POS Opening Entry",
        {"status": "Open", "docstatus": 1, "pos_profile": ["in", profiles]},
        "name",
        order_by="period_start_date asc",
    )
    after_close = 0
    if not opening:
        # FIFO: Расход eng eski qoplanmagan KAMOMADni, Приход ORTIQCHAni yopadi.
        closed = frappe.get_all(
            "POS Opening Entry",
            filters={"status": "Closed", "docstatus": 1, "pos_profile": ["in", profiles]},
            fields=["name"],
            order_by="period_start_date asc",
        )
        for row in closed:
            diff = _closed_cash_difference(row.name, mode)
            if (signed < 0 and diff < -0.5) or (signed > 0 and diff > 0.5):
                opening, after_close = row.name, 1
                break
    if opening:
        doc.db_set({SHIFT_FIELD: opening, AFTER_CLOSE_FIELD: after_close})


def _rows(opening: str, after_close: int) -> list:
    if not frappe.get_meta("Kassa").has_field(SHIFT_FIELD):
        return []
    docs = frappe.get_all(
        "Kassa",
        filters={SHIFT_FIELD: opening, AFTER_CLOSE_FIELD: after_close, "docstatus": 1},
        fields=["name", "oborot", "summa", "source_account", "transfer_source_display", "target_account"],
    )
    return [effects(frappe._dict(d)) for d in docs]


def after_close_rows(opening: str) -> list:
    return _rows(opening, 1)


def apply_to_expected(payments: list, opening: str):
    """Smena yopilishi: OCHIQ paytda bog'langan Kassa harakatlarini kutilgan summaga qo'shadi."""
    for row in _rows(opening, 0):
        for mode, amount in row.items():
            existing = [p for p in payments if p.mode_of_payment == mode]
            if existing:
                existing[0].expected_amount += amount
            else:
                payments.append(frappe._dict(mode_of_payment=mode, opening_amount=0, expected_amount=amount))


def operator_summary(opening: str) -> dict:
    """Smena hisoboti uchun: operator Kassa harakatlari (ochiq paytda / keyin)."""
    if not frappe.get_meta("Kassa").has_field(SHIFT_FIELD):
        return {"rows": [], "during": 0, "after": 0}
    docs = frappe.get_all(
        "Kassa",
        filters={SHIFT_FIELD: opening, "docstatus": 1},
        fields=["name", "oborot", "summa", "primechaniya", "owner", AFTER_CLOSE_FIELD,
                "source_account", "transfer_source_display", "target_account"],
        order_by="creation asc",
    )
    rows, during, after = [], 0.0, 0.0
    for d in docs:
        net = sum(effects(d).values())
        rows.append({"name": d.name, "oborot": d.oborot, "amount": net, "note": d.primechaniya or "",
                     "owner": d.owner, "after_close": d.get(AFTER_CLOSE_FIELD)})
        if d.get(AFTER_CLOSE_FIELD):
            after += net
        else:
            during += net
    return {"rows": rows, "during": flt(during, 2), "after": flt(after, 2)}
