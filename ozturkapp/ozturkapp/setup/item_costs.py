# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Menyu tovarlarining tannarxi (valuation rate).

NEGA BU KERAK
=============
POS Profile'da `update_stock = 1` — ya'ni har sotuv omborni kamaytiradi.
Smena yopilganda ERPNext cheklarni konsolidatsiya qilib, tannarx (COGS)
provodkasini yozadi. Tovarda baholash narxi bo'lmasa u qanday summada
yozishni bilmaydi va BUTUN YOPISH YIQILADI::

    Valuation Rate for the Item <X> is required to do accounting entries

Bundan ham yomoni: ERPNext bu xatoni `except` blokida yutib yuboradi va
o'rniga chalg'ituvchi xabar chiqaradi::

    Could not find Reference Name: POS-CLO-2026-00002

(Sabab: `except` ichida `frappe.db.rollback()` qilinadi, shundan keyin
o'sha hujjatga izoh yozishga urinadi — hujjat esa endi mavjud emas.)

Shuning uchun tannarx OLDINDAN kiritilishi va yo'qligi ERTA aniqlanishi
kerak — `missing_costs()` shu uchun.

ISHLATISH
=========
    # Ro'yxatni ko'rish
    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.item_costs.report

    # Tannarx yozish
    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.item_costs.set_costs \
        --kwargs "{'costs': {'AFGAN BREAD': 20000, 'COCA-COLA': 8000}}"

    # Yoki sotuv narxining foizi sifatida (vaqtinchalik yechim)
    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.item_costs.set_from_margin \
        --kwargs "{'percent': 40}"
"""

import frappe
from frappe.utils import flt


def _menu_items(restaurant: str = None) -> list:
    """Faol menyudagi tovarlar (tannarx faqat ularga kerak)."""
    filters = {"name": restaurant} if restaurant else {}
    menus = [
        m.active_menu
        for m in frappe.get_all(
            "URY Restaurant", filters=filters, fields=["active_menu"]
        )
        if m.active_menu
    ]
    if not menus:
        return []

    return frappe.get_all(
        "URY Menu Item",
        filters={"parent": ["in", menus], "disabled": 0},
        fields=["item", "item_name", "rate"],
        order_by="item_name",
    )


def missing_costs(restaurant: str = None) -> list:
    """Tannarxi yo'q ZAXIRA tovarlari.

    Zaxira tovari bo'lmagan (`is_stock_item = 0`) taomlarga tannarx kerak
    emas — ular uchun stok provodkasi yozilmaydi.
    """
    missing = []
    for row in _menu_items(restaurant):
        item = frappe.db.get_value(
            "Item", row.item, ["is_stock_item", "valuation_rate"], as_dict=True
        )
        if not item or not item.is_stock_item:
            continue
        if flt(item.valuation_rate) > 0:
            continue
        missing.append({"item": row.item, "item_name": row.item_name, "rate": flt(row.rate)})
    return missing


def report():
    """Tannarx holatini ko'rsatadi."""
    rows = _menu_items()
    if not rows:
        print("⏭️  Faol menyu topilmadi")
        return

    print(f"{'TAOM':<28}{'SOTUV':>10}{'TANNARX':>10}")
    print("-" * 48)
    for row in rows:
        cost = flt(frappe.db.get_value("Item", row.item, "valuation_rate"))
        mark = " " if cost else "!"
        print(f"{mark}{row.item_name[:27]:<27}{flt(row.rate):>10,.0f}{cost:>10,.0f}")

    gaps = missing_costs()
    print("-" * 48)
    print(f"Jami {len(rows)} ta taom, tannarxi yo'q: {len(gaps)} ta")
    if gaps:
        print("\n⚠️  Tannarxsiz tovar bo'lsa KASSA YOPILMAYDI.")
    return gaps


def set_costs(costs: dict):
    """Berilgan tovarlarga tannarx yozadi.

    Args:
        costs: `{"AFGAN BREAD": 20000, "COCA-COLA": 8000}`
    """
    if isinstance(costs, str):
        import json

        costs = json.loads(costs)

    updated, skipped = 0, []
    for item, value in (costs or {}).items():
        if not frappe.db.exists("Item", item):
            skipped.append(item)
            continue
        frappe.db.set_value("Item", item, "valuation_rate", flt(value))
        updated += 1

    frappe.db.commit()
    print(f"✅ Tannarx yozildi: {updated} ta")
    if skipped:
        print(f"⏭️  Topilmadi: {', '.join(skipped)}")

    gaps = missing_costs()
    if gaps:
        print(f"⚠️  Hali {len(gaps)} ta tovarda tannarx yo'q")
    return updated


def set_from_margin(percent: float = 40, overwrite: int = 0):
    """Tannarxni sotuv narxining foizi sifatida qo'yadi.

    ⚠️  BU VAQTINCHALIK YECHIM. Haqiqiy tannarx kiritilmaguncha foyda
    hisoboti TAXMINIY bo'ladi. Faqat tizimni ishga tushirish uchun.

    Args:
        percent: sotuv narxining necha foizi tannarx deb olinsin.
        overwrite: 1 bo'lsa mavjud tannarxlar ham qayta yoziladi.
    """
    percent = flt(percent)
    if not 0 < percent < 100:
        frappe.throw("Foiz 0 va 100 orasida bo'lishi kerak")

    updated = 0
    for row in _menu_items():
        item = frappe.db.get_value(
            "Item", row.item, ["is_stock_item", "valuation_rate"], as_dict=True
        )
        if not item or not item.is_stock_item:
            continue
        if flt(item.valuation_rate) > 0 and not int(overwrite or 0):
            continue

        frappe.db.set_value(
            "Item", row.item, "valuation_rate", flt(row.rate) * percent / 100
        )
        updated += 1

    frappe.db.commit()
    print(f"✅ {updated} ta tovarga tannarx qo'yildi (sotuvning {percent:g}%)")
    print("⚠️  Bu TAXMINIY qiymat — haqiqiy tannarxni keyinroq kiriting.")
    return updated
