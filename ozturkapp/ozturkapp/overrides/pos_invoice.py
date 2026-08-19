# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""POS Invoice override hook'lari — stol bandligini YAKUNIY hal qilish.

MUAMMO
======
Chek submit bo'lganda stolni UCHTA turli joy bo'shatadi va ular bir-biriga
zid ishlaydi:

  1. `ury/hooks/ury_pos_invoice.py:release_merged_tables`
        -> shartsiz bo'shatadi (faqat "Dine In" uchun)
  2. `ury/.../ury_order.py:make_invoice -> _free_tables_if_no_open_invoices`
        -> boshqa ochiq cheklarni tekshiradi (to'g'ri yo'l)
  3. shu fayldagi eski `on_submit`
        -> shartsiz bo'shatardi (barcha buyurtma turlari uchun)

Natijada HISOB BO'LINGAN holatda birinchi chek to'langanida stol
bo'shab qolardi — ikkinchi chek hali to'lanmagan bo'lsa ham. Bu TZ §23
("to'lov muvaffaqiyatli bo'lsagina stol bo'shaydi") va §24 (konkurensiya)
talablarini buzadi.

YECHIM
======
Bu hook `ozturkapp` da, ya'ni `ury` dan KEYIN ishga tushadi
(o'rnatilgan ilovalar tartibi: frappe, erpnext, hrms, ury, ozturkapp).
Shuning uchun u OXIRGI so'zni aytadi va URY shartsiz bo'shatgan stolni
kerak bo'lsa QAYTA band qiladi. `ury` manbasiga tegilmaydi (TZ §32).

QOIDA
=====
    Klasterda boshqa to'lanmagan chek qolgan bo'lsa -> stol BAND qoladi
    Aks holda                                       -> stol BO'SHAYDI
"""

import frappe

from ozturkapp.ozturkapp.utils.cashier_realtime import on_pos_invoice_change
from ozturkapp.ozturkapp.utils.table_status import build_clusters, parse_merged_with


def on_submit(doc, method=None):
    """To'lovdan keyin stol holatini yakuniy va to'g'ri qilib o'rnatish."""
    _reconcile_tables(doc)
    on_pos_invoice_change(doc, "on_submit")


def _reconcile_tables(doc):
    if not doc.get("restaurant_table"):
        return

    cluster = _cluster_of(doc.restaurant_table, doc.get("custom_merged_tables"))
    remaining = _open_invoices_for(cluster, exclude=doc.name)

    if remaining:
        # URY hook'i stolni allaqachon bo'shatgan bo'lishi mumkin — qaytaramiz.
        for member in cluster:
            frappe.db.set_value(
                "URY Table", member, "occupied", 1, update_modified=False
            )
        frappe.logger("ozturk_cashier").info(
            "Stol band qoldirildi: %s | qolgan cheklar: %s",
            ", ".join(cluster),
            ", ".join(remaining),
        )
        return

    for member in cluster:
        frappe.db.set_value(
            "URY Table",
            member,
            {"occupied": 0, "latest_invoice_time": None},
            update_modified=False,
        )


def _cluster_of(table: str, merged_csv: str = None) -> list:
    """Stolning birlashtirilgan klasteri (`merged_with` bo'yicha)."""
    branch = frappe.db.get_value("URY Table", table, "branch")
    if not branch:
        return [table]

    rows = frappe.get_all(
        "URY Table", filters={"branch": branch}, fields=["name", "merged_with"]
    )
    cluster = build_clusters([dict(row) for row in rows]).get(table, [table])

    # Chekdagi CSV ham hisobga olinsin — klaster yechilib ketgan bo'lishi mumkin.
    for extra in parse_merged_with(merged_csv):
        if extra not in cluster:
            cluster.append(extra)

    return cluster


def _open_invoices_for(tables: list, exclude: str = None) -> list:
    """Klasterdagi stollarga bog'langan, hali to'lanmagan cheklar.

    URY'ning `_has_open_pos_invoices_for_cluster` dan farqi: u faqat
    `invoice_printed = 0` bo'lgan cheklarni hisobga oladi. Hisob bo'lingan
    holatda IKKALA chek ham chop etilgan bo'ladi, ya'ni URY'ning tekshiruvi
    "ochiq chek yo'q" deb xato javob beradi. Biz `docstatus = 0` ni
    mezon qilamiz — bu qat'iyroq va TZ §23 ga mos.
    """
    if not tables:
        return []

    filters = {"docstatus": 0, "restaurant_table": ["in", tables]}
    if exclude:
        filters["name"] = ["!=", exclude]
    if frappe.db.has_column("POS Invoice", "custom_cancelled"):
        filters["custom_cancelled"] = 0

    names = set(frappe.get_all("POS Invoice", filters=filters, pluck="name"))

    # Birlashtirilgan stollar CSV orqali bog'langan cheklar.
    for table in tables:
        merged_filters = {
            "docstatus": 0,
            "custom_merged_tables": ["like", f"%{table}%"],
        }
        if exclude:
            merged_filters["name"] = ["!=", exclude]
        if frappe.db.has_column("POS Invoice", "custom_cancelled"):
            merged_filters["custom_cancelled"] = 0

        names.update(frappe.get_all("POS Invoice", filters=merged_filters, pluck="name"))

    return sorted(names)
