# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""`sync_order` uchun ozturkapp o'ram (wrapper).

MUAMMO
======
Desktop POS `sync_order` ga to'rtta qo'shimcha maydon yuboradi::

    ticket_number, active_cashier, active_cashier_role, client_ref

Lekin upstream `ury.ury.doctype.ury_order.ury_order.sync_order` signaturasida
bu parametrlar YO'Q. Frappe whitelisted chaqiruvda kwarg'larni funksiya
signaturasiga qarab filtrlaydi (`frappe.get_newargs`) — ya'ni to'rttasi ham
JIMGINA tashlab yuboriladi. Xato chiqmaydi, ma'lumot esa yo'qoladi:

  * `custom_ticket_number = 0`  -> "Kutilayotgan buyurtmalar" oynasida №0
  * `custom_active_cashier = None` -> kassir bo'yicha KPI/filtr ishlamaydi
  * `custom_client_ref = None`  -> idempotency yo'q, oflayn qayta yuborishda
                                   DUBLIKAT POS Invoice yaratilishi mumkin

YECHIM
======
`hooks.override_whitelisted_methods` orqali chaqiruv shu modulga yo'naltiriladi.
Wrapper to'rttasini ajratib oladi, qolganini upstream funksiyaga uzatadi va
natijadagi POS Invoice'ga qo'lda yozadi.

Idempotency: `client_ref` bo'yicha mavjud chek topilsa, upstream umuman
chaqirilmaydi — o'sha chek qaytariladi. `POS Invoice.custom_client_ref`
maydoni `unique=1`, shuning uchun bir vaqtda kelgan ikkita bir xil so'rovdan
biri baza darajasida yiqiladi va u ham mavjud chekka ulanadi.
"""

import frappe
from frappe import _
from frappe.utils import cint

from ozturkapp.ozturkapp.utils.table_status import parse_merged_with

#: Upstream `sync_order` qabul qilmaydigan, biz o'zimiz yozadigan maydonlar
EXTRA_ARGS = ("ticket_number", "active_cashier", "active_cashier_role", "client_ref")


def _find_by_client_ref(client_ref: str) -> str | None:
    """`client_ref` bo'yicha avval yaratilgan POS Invoice nomini qaytaradi."""
    if not client_ref:
        return None
    return frappe.db.get_value("POS Invoice", {"custom_client_ref": client_ref}, "name")


def _resolve_invoice_for_table(table, invoice):
    """Upstream `get_order_invoice()` ning "LIKE" tuzog'idan himoya.

    MUAMMO
    ======
    URY stoldagi chekni `restaurant_table = stol` YOKI
    `custom_merged_tables LIKE %stol%` bilan qidiradi. "Table-1" nomi
    "Table-10", "Table-11"... ni ham topadi: "Table-10" boshqa stolga
    birlashtirilgan bo'lsa, "Table-1" ga BERILGAN YANGI buyurtma o'sha
    birlashtirilgan chekka tushadi. Buyurtma beruvchi kassir bo'lsa
    (`role_allowed_for_billing`) URY "stol band" tekshiruvini o'tkazib
    yuboradi va chekning taomlarini yangi ro'yxat bilan ALMASHTIRADI
    (`invoice.items = []`) — birlashtirilgan stol mehmonining buyurtmasi
    yo'qoladi.

    YECHIM
    ======
    Chek nomi berilmagan, stol berilgan chaqiruvda nomzod cheklar aniq
    (CSV bo'laklari bo'yicha) tekshiriladi:

        * begona chek topilmasa               -> hech narsa o'zgarmaydi
        * stolning HAQIQIY egasi ham bor       -> upstream unga yo'naltiriladi
        * faqat begona (prefiks mos) chek bor  -> buyurtma RAD etiladi
    """
    if not table or invoice:
        return invoice

    rows = frappe.get_all(
        "POS Invoice",
        filters={"docstatus": 0, "invoice_printed": 0},
        or_filters={
            "restaurant_table": table,
            "custom_merged_tables": ["like", f"%{table}%"],
        },
        fields=["name", "restaurant_table", "custom_merged_tables"],
        order_by="creation asc",
    )
    owners = [
        row.name
        for row in rows
        if row.restaurant_table == table or table in parse_merged_with(row.custom_merged_tables)
    ]
    foreign = [row.name for row in rows if row.name not in owners]

    if not foreign:
        return invoice
    if owners:
        return owners[0]

    frappe.throw(
        _(
            "{0} stoliga yangi buyurtma ochib bo'lmaydi: uning nomi boshqa stolga "
            "birlashtirilgan stol nomiga o'xshash ({1}) va tizim chekni adashtirib "
            "yuborishi mumkin. Avval o'sha stollarni ajrating."
        ).format(table, ", ".join(foreign)),
        title=_("Stol nomi to'qnashuvi"),
    )


def _upstream_kwargs(fn, kwargs: dict) -> dict:
    """kwargs'ni upstream signaturasiga qirqadi.

    Frappe whitelisted chaqiruvda buni o'zi qilardi; biz `**kwargs` qabul
    qilganimiz uchun endi o'zimiz qilishimiz kerak — aks holda POS'ning
    keyingi versiyasi yangi maydon qo'shsa `TypeError` bilan yiqilamiz.
    Tashlab yuborilgani jimgina emas, log'ga yoziladi.
    """
    import inspect

    params = inspect.signature(fn).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return kwargs

    accepted = {k: v for k, v in kwargs.items() if k in params}
    dropped = sorted(set(kwargs) - set(accepted))
    if dropped:
        frappe.logger("ozturkapp").warning(
            f"sync_order: upstream qabul qilmagan kwarg'lar tashlandi: {dropped}"
        )
    return accepted


def _is_duplicate(exc: Exception) -> bool:
    """Xato unique-index buzilishimi (baza drayveriga bog'liq bo'lmagan tekshiruv)."""
    if isinstance(exc, frappe.exceptions.DuplicateEntryError):
        return True
    try:
        return bool(frappe.db.is_duplicate_entry(exc))
    except Exception:
        return False


def _extra_values(extras: dict) -> dict:
    """Yuborilgan qo'shimchalardan POS Invoice maydonlari xaritasini yig'adi."""
    values = {}

    client_ref = (extras.get("client_ref") or "").strip()
    if client_ref:
        values["custom_client_ref"] = client_ref

    ticket_number = cint(extras.get("ticket_number"))
    if ticket_number:
        values["custom_ticket_number"] = ticket_number

    active_cashier = (extras.get("active_cashier") or "").strip()
    if active_cashier:
        values["custom_active_cashier"] = active_cashier

    role = (extras.get("active_cashier_role") or "").strip()
    if role:
        values["custom_active_cashier_role"] = role

    return values


@frappe.whitelist()
def sync_order(**kwargs):
    """Upstream `sync_order` + Desktop POS qo'shimcha maydonlari.

    Qaytaradi: upstream bilan bir xil — `POS Invoice.as_dict()`.
    """
    from ury.ury.doctype.ury_order.ury_order import sync_order as _upstream_sync_order

    extras = {key: kwargs.pop(key, None) for key in EXTRA_ARGS}
    client_ref = (extras.get("client_ref") or "").strip()

    # ── Idempotency: shu client_ref bilan chek allaqachon yaratilganmi? ──
    # POS tarmoq xatosidan keyin AYNI offline_id bilan qayta yuboradi
    # (qarang: database/invoice_processor.py — payload["client_ref"] = offline_id)
    existing = _find_by_client_ref(client_ref)
    if existing:
        frappe.logger("ozturkapp").info(
            f"sync_order: takroriy so'rov (client_ref={client_ref}) -> {existing}"
        )
        return frappe.get_doc("POS Invoice", existing).as_dict()

    kwargs["invoice"] = _resolve_invoice_for_table(kwargs.get("table"), kwargs.get("invoice"))

    result = _upstream_sync_order(**_upstream_kwargs(_upstream_sync_order, kwargs))

    invoice_name = result.get("name") if isinstance(result, dict) else None
    if not invoice_name:
        return result

    values = _extra_values(extras)
    if not values:
        return result

    try:
        frappe.db.set_value("POS Invoice", invoice_name, values, update_modified=False)
    except Exception as e:
        if not _is_duplicate(e):
            raise
        # Poyga (race): parallel so'rov shu client_ref ni oldinroq yozib ulgurdi.
        # `custom_client_ref` unique — UPDATE baza darajasida yiqildi. O'zimiz
        # yaratgan chekni bekor qilamiz (rollback) va g'olib chekni qaytaramiz,
        # aks holda aynan oldini olmoqchi bo'lgan dublikat qolib ketadi.
        frappe.db.rollback()
        winner = _find_by_client_ref(client_ref)
        if not winner:
            raise
        frappe.logger("ozturkapp").warning(
            f"sync_order: client_ref={client_ref} poygasi -> {winner} qaytarildi"
        )
        return frappe.get_doc("POS Invoice", winner).as_dict()

    result.update(values)
    return result
