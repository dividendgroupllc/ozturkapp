# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Xodimlararo bildirishnomalar (kassir / ofitsant / oshpaz).

NEGA MAVJUD REALTIME YETMAYDI
=============================
`cashier_realtime` va `kitchen_realtime` hodisalari ekranni JIM yangilaydi:
ma'lumot o'zgaradi, lekin hech kim buni SEZMAYDI. Ofitsant hisob so'raganda
kassir ekranda o'zgarishni ko'rishi uchun aynan o'sha stolga qarab turishi
kerak edi.

Bu modul esa E'TIBOR TORTADIGAN xabar yuboradi: ekranda ko'rinadigan
banner + ovoz. Ya'ni "ma'lumot yangilandi" emas, "senga ish bor" degani.

UCHTA OQIM
==========
    Ofitsant hisob so'radi        ->  KASSIR
    Oshpaz taomni tayyor qildi    ->  OFITSANT
    Ofitsant buyurtma yubordi     ->  OSHPAZ

KIMGA YETADI — `audience`
=========================
Xabar bitta kanalga (`ozturk_notify`) yuboriladi, har bir ekran esa
`audience` bo'yicha O'ZI filtrlaydi. Aniq foydalanuvchiga (`user=`)
yuborilmaydi, chunki restoranda ish shaxsga emas, ISH JOYIGA bog'langan:
kassada kim turgan bo'lsa hisob so'rovini ko'rishi kerak, oshxonada kim
turgan bo'lsa yangi buyurtmani. `user` maydoni faqat MASLAHAT sifatida
qo'shiladi — ofitsant ilovasi "bu meniki" deb ajratishi uchun.

PAYLOAD NIMA TASHIYDI
=====================
Stol raqami, taom nomi, chek nomi. Summa, mijoz ismi, to'lov — YO'Q.
Xabar sayt xonasiga ketadi (`frappe/realtime.py`), shuning uchun undagi
ma'lumot ekranda allaqachon ko'rinadigan darajada bo'lishi kerak.
"""

import frappe

#: Yagona bildirishnoma kanali.
EVENT_NOTIFY = "ozturk_notify"

#: Kim uchun mo'ljallangan.
CASHIER = "cashier"
WAITER = "waiter"
KITCHEN = "kitchen"

#: Hodisa turlari — mijoz ikonka/ovozni shunga qarab tanlaydi.
BILL_REQUESTED = "BILL_REQUESTED"
ITEM_READY = "ITEM_READY"
ORDER_PLACED = "ORDER_PLACED"


def notify(branch, audience, kind, title, body="", **refs):
    """Bildirishnoma yuboradi.

    Args:
        branch: filial — mijoz o'zinikini shu bo'yicha ajratadi.
        audience: `CASHIER` / `WAITER` / `KITCHEN`.
        kind: hodisa turi (yuqoridagi konstantalar).
        title: qisqa sarlavha (masalan "Hisob so'raldi").
        body: tafsilot (masalan "Stol 5 — 3 ta taom").
        **refs: `table`, `invoice`, `station`, `user` kabi havolalar.
    """
    if not branch:
        return

    payload = {
        "branch": branch,
        "audience": audience,
        "kind": kind,
        "title": title,
        "body": body,
    }
    payload.update(refs)

    frappe.publish_realtime(EVENT_NOTIFY, payload, after_commit=True)


# ═══════════════════════════════════════════════════════════════════
#  Uchta oqim
# ═══════════════════════════════════════════════════════════════════

def bill_requested(branch, invoice, table=None, waiter=None):
    """Ofitsant hisob so'radi -> KASSIRGA."""
    notify(
        branch,
        CASHIER,
        BILL_REQUESTED,
        "Hisob so'raldi",
        f"Stol {table}" if table else "Stolsiz buyurtma",
        invoice=invoice,
        table=table,
        waiter=waiter,
    )


def item_ready(branch, invoice, item_name, table=None, waiter=None, station=None):
    """Oshpaz taomni tayyor qildi -> OFITSANTGA.

    `waiter` — chekdagi `POS Invoice.waiter`. Ilova "bu meniki" deb
    ajratishi uchun yuboriladi, lekin xabar baribir barcha ofitsantlarga
    ketadi: birov almashib turgan bo'lsa taom sovib qolmasligi kerak.
    """
    notify(
        branch,
        WAITER,
        ITEM_READY,
        "Taom tayyor",
        f"{item_name} — stol {table}" if table else str(item_name),
        invoice=invoice,
        table=table,
        waiter=waiter,
        station=station,
    )


def order_placed(branch, invoice, table=None, item_count=0, station=None):
    """Ofitsant buyurtma yubordi -> OSHXONAGA."""
    notify(
        branch,
        KITCHEN,
        ORDER_PLACED,
        "Yangi buyurtma",
        f"Stol {table} — {item_count} ta taom" if table else f"{item_count} ta taom",
        invoice=invoice,
        table=table,
        station=station,
    )
