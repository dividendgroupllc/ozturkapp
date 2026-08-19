# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Oshxona holat mashinasi (TZ §5, §14, §23, §24).

    PENDING ──> PREPARING ──> READY ──> SERVED
       │
       └──> CANCELLED

KELAJAKDAGI OFITSANT QOIDASI (TZ §14)
=====================================
Ofitsant faqat `PENDING` holatidagi mahsulotni bekor qila oladi. Tayyorlash
BOSHLANGANDAN keyin (`PREPARING`, `READY`, `SERVED`) bekor qilish MUMKIN EMAS.

Bu qoida shu yerda, YAGONA joyda kodlangan (`can_waiter_cancel()`), shuning
uchun kelajakdagi Ofitsant ilovasi uni qayta yozmaydi va chetlab o'tolmaydi.
Umumiy `item.cancelled = True` amali YO'Q (TZ §14).

KOT DARAJASIDAGI HOLAT
======================
`URY KOT.order_status` SEMANTIKASI O'ZGARTIRILMAYDI — URY'ning Mosaic KDS'i
(`kot_list()`) unga `"Ready For Prepare"` bo'yicha tayanadi. Oshxona ekrani
uchun KOT holati mahsulotlardan KELTIRIB CHIQARILADI (`derive_kot_status`),
bazaga yozilmaydi.
"""

import frappe
from frappe import _
from frappe.utils import cint

PENDING = "Pending"
PREPARING = "Preparing"
READY = "Ready"
SERVED = "Served"
CANCELLED = "Cancelled"

#: Ko'rinadigan holatlar tartibi (UI ustunlari shu tartibda).
STATUSES = (PENDING, PREPARING, READY, SERVED, CANCELLED)

#: Faol (hali yakunlanmagan) holatlar.
OPEN_STATUSES = (PENDING, PREPARING, READY)

#: RUXSAT ETILGAN o'tishlar — boshqa hech qanday o'tish qabul qilinmaydi.
#: Diqqat: PREPARING/READY -> CANCELLED YO'Q (TZ §14).
TRANSITIONS = {
    PENDING: (PREPARING, CANCELLED),
    PREPARING: (READY,),
    READY: (SERVED,),
    SERVED: (),
    CANCELLED: (),
}

#: "O'ZI OLIB BORILADI" NUQTASI (bar) uchun soddalashtirilgan oqim.
#:
#: Ichimlikni hech kim tayyorlamaydi — ofitsant barga borib oladi va
#: mijozga eltadi. Ya'ni "Tayyorlanmoqda" va "Tayyor" bosqichlari ma'nosiz:
#: real hayotda ular hech qachon bosilmaydi va mahsulot abadiy
#: "Kutilmoqda" da osilib qolardi.
#:
#: Shuning uchun bunday nuqtada BITTA o'tish bor: Kutilmoqda -> Berildi.
SELF_SERVICE_TRANSITIONS = {
    PENDING: (SERVED, CANCELLED),
    SERVED: (),
    CANCELLED: (),
}


def self_service_stations() -> set:
    """Ofitsant o'zi olib boradigan nuqtalar (`URY Production Unit`).

    Maydon `kitchen_setup.create_fields()` da yaratiladi. U hali
    ishga tushmagan saytda ustun bo'lmaydi — shuning uchun avval
    tekshiriladi, aks holda SQL xato berardi.
    """
    if not frappe.db.has_column("URY Production Unit", "custom_self_service"):
        return set()

    return set(
        frappe.get_all(
            "URY Production Unit", filters={"custom_self_service": 1}, pluck="name"
        )
    )

#: Holatga o'tganda yoziladigan vaqt maydoni.
TIMESTAMP_FIELD = {
    PREPARING: "custom_started_at",
    READY: "custom_ready_at",
    SERVED: "custom_served_at",
}

#: Oshxona ekranidagi tugma matni va keyingi holat.
NEXT_ACTION = {
    PENDING: (PREPARING, "Tayyorlashni boshlash"),
    PREPARING: (READY, "Tayyor"),
    READY: (SERVED, "Berildi"),
}

#: Foydalanuvchiga ko'rinadigan nomlar.
LABELS = {
    PENDING: "Kutilmoqda",
    PREPARING: "Tayyorlanmoqda",
    READY: "Tayyor",
    SERVED: "Berildi",
    CANCELLED: "Bekor qilingan",
}


class InvalidTransition(frappe.ValidationError):
    """Ruxsat etilmagan holat o'tishi."""


def normalize(status) -> str:
    """Bo'sh/noma'lum qiymatni `PENDING` ga keltiradi.

    Custom field qo'shilishidan OLDIN yaratilgan KOT qatorlarida bu maydon
    `NULL` bo'ladi — ular kutilayotgan deb qaraladi.
    """
    value = (status or "").strip()
    return value if value in STATUSES else PENDING


def assert_transition(current: str, target: str, self_service: bool = False):
    """O'tish ruxsat etilganini tekshiradi. Mijozga ISHONMAYMIZ (TZ §23).

    Args:
        self_service: mahsulot "o'zi olib boriladi" nuqtasidanmi (bar).
            Unda oqim ikki bosqichli: Kutilmoqda -> Berildi.
    """
    current = normalize(current)
    target = (target or "").strip()
    allowed = SELF_SERVICE_TRANSITIONS if self_service else TRANSITIONS

    if target not in STATUSES:
        raise InvalidTransition(_("Noma'lum holat: {0}").format(target))

    if target == current:
        # Idempotent emas — takroriy bosish xato beradi, chunki bu odatda
        # ikkinchi oshpaz allaqachon o'zgartirganini bildiradi (TZ §24).
        raise InvalidTransition(
            _("Mahsulot allaqachon '{0}' holatida").format(label(current))
        )

    if target not in allowed.get(current, ()):
        raise InvalidTransition(
            _("'{0}' holatidan '{1}' holatiga o'tib bo'lmaydi").format(
                label(current), label(target)
            )
        )


def can_waiter_cancel(status) -> bool:
    """Ofitsant bu mahsulotni bekor qila oladimi? (TZ §14)

    Kelajakdagi Ofitsant ilovasi AYNAN shu funksiyani chaqirishi kerak —
    o'z tekshiruvini yozmasligi kerak.
    """
    return normalize(status) == PENDING


def label(status) -> str:
    return _(LABELS.get(normalize(status), status))


def next_action(status):
    """Keyingi qadam: `(target_status, tugma_matni)` yoki `None`."""
    action = NEXT_ACTION.get(normalize(status))
    if not action:
        return None
    return {"status": action[0], "label": _(action[1])}


# ═══════════════════════════════════════════════════════════════════
#  KOT darajasidagi keltirilgan holat (bazaga YOZILMAYDI)
# ═══════════════════════════════════════════════════════════════════

def derive_kot_status(item_statuses) -> str:
    """Mahsulot holatlaridan KOT holatini keltirib chiqaradi (TZ §11).

        hammasi PENDING            -> PENDING
        kamida bittasi PREPARING   -> PREPARING
        hammasi READY              -> READY
        hammasi SERVED             -> SERVED

    Bekor qilinganlar hisobga OLINMAYDI — ular oshxona ishini bildirmaydi.
    """
    active = [normalize(s) for s in item_statuses if normalize(s) != CANCELLED]

    if not active:
        return CANCELLED
    if all(s == SERVED for s in active):
        return SERVED
    if all(s in (READY, SERVED) for s in active):
        return READY
    if any(s == PREPARING for s in active):
        return PREPARING
    if any(s in (READY, SERVED) for s in active):
        # Bir qismi tayyor, qolgani hali boshlanmagan — ish ketmoqda.
        return PREPARING
    return PENDING


# ═══════════════════════════════════════════════════════════════════
#  Buyurtma bo'yicha holat — Kassa va kelajakdagi Ofitsant uchun (TZ §26)
# ═══════════════════════════════════════════════════════════════════

def get_item_statuses_for_invoice(invoice: str) -> dict:
    """{item_code: {status, qty, can_cancel}} — chek bo'yicha.

    Kelajakdagi Ofitsant ilovasi shu ma'lumot asosida "Bekor qilish"
    tugmasini o'chiradi (TZ §26). Kassa esa faqat KO'RSATADI (TZ §25).
    """
    if not invoice or not frappe.db.exists("DocType", "URY KOT"):
        return {}

    rows = frappe.db.sql(
        """
        SELECT ki.name AS kot_item, ki.item, ki.quantity,
               ki.custom_kitchen_status AS status, k.production
        FROM `tabURY KOT Items` ki
        INNER JOIN `tabURY KOT` k ON k.name = ki.parent
        WHERE k.invoice = %s AND k.docstatus = 1
          AND k.type IN ('New Order', 'Order Modified')
        """,
        (invoice,),
        as_dict=True,
    )

    self_service = self_service_stations()

    result = {}
    for row in rows:
        status = normalize(row.status)
        current = result.get(row.item)

        # Bir mahsulot bir necha KOT'da bo'lsa — eng ORQADA qolgan holat
        # ko'rsatiladi (hammasi tayyor bo'lmaguncha "tayyor" demaymiz).
        if not current or STATUSES.index(status) < STATUSES.index(current["status"]):
            result[row.item] = {
                "status": status,
                "label": label(status),
                "can_waiter_cancel": can_waiter_cancel(status),
                # Ofitsant ilovasi "Berildi" tugmasini shu ikkisiga qarab
                # chizadi va AYNAN shu qatorni serverga yuboradi.
                "kot_item": row.kot_item,
                "self_service": row.production in self_service,
            }

        result[row.item]["qty"] = result[row.item].get("qty", 0) + cint(row.quantity)

    return result
