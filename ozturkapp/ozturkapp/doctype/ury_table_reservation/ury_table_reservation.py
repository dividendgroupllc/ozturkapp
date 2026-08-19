# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Stol broni — MINIMAL model (TZ §6).

Bu TO'LIQ bron moduli EMAS. Uning yagona vazifasi — kassa oynasida
RESERVED holatini ko'rsatish uchun ma'lumot manbai bo'lish. Bron boshqaruvi
(kalendar, SMS eslatma, jadval optimizatsiyasi) keyingi bosqichda alohida
modul sifatida yoziladi.

O'sha paytda kassa mantig'ini o'zgartirish SHART EMAS: holat hisoblash
`utils/table_status.py` dagi provider seam orqali ishlaydi ::

    # hooks.py
    cashier_reservation_provider = "myapp.reservations.get_active"

Stol/xona/filial YARATILMAYDI — `URY Table` yagona manba bo'lib qoladi
(TZ §28, §29).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_time

#: Stolni haqiqatda band qiladigan holatlar (kassa RESERVED deb ko'rsatadi).
ACTIVE_STATUSES = ("Pending", "Confirmed")

#: Bron yopilgan holatlar — stolni band qilmaydi.
CLOSED_STATUSES = ("Cancelled", "No Show", "Completed", "Seated")


class URYTableReservation(Document):
    def validate(self):
        self.sync_from_table()
        self.validate_times()
        self.validate_pax()
        self.validate_overlap()

    def sync_from_table(self):
        """Filial/xona/restoranni stolning o'zidan olamiz — qo'lda kiritilmasin."""
        if not self.table:
            return

        table = frappe.db.get_value(
            "URY Table",
            self.table,
            ["restaurant", "branch", "restaurant_room", "no_of_seats"],
            as_dict=True,
        )
        if not table:
            frappe.throw(_("'{0}' stoli topilmadi").format(self.table))

        self.restaurant = table.restaurant
        self.branch = table.branch
        self.room = table.restaurant_room

    def validate_times(self):
        if not (self.from_time and self.to_time):
            return

        if get_time(self.to_time) <= get_time(self.from_time):
            frappe.throw(
                _("Tugash vaqti boshlanish vaqtidan keyin bo'lishi kerak"),
                title=_("Vaqt noto'g'ri"),
            )

    def validate_pax(self):
        """Mehmonlar soni stol sig'imidan oshmasin — ogohlantirish darajasida."""
        if not (self.table and self.pax):
            return

        seats = cint(frappe.db.get_value("URY Table", self.table, "no_of_seats"))
        if seats and cint(self.pax) > seats:
            frappe.msgprint(
                _("Diqqat: {0} stolida {1} o'rin bor, bron esa {2} kishiga.").format(
                    self.table, seats, self.pax
                ),
                indicator="orange",
                alert=True,
            )

    def validate_overlap(self):
        """Bitta stolga bir vaqtda ikkita faol bron bo'lmasin.

        Bu — konkurensiya himoyasi (TZ §24): ikkita menejer bir vaqtda
        bir stolni bron qilib yuborishining oldini oladi.
        """
        if self.status not in ACTIVE_STATUSES:
            return
        if not (self.table and self.reservation_date and self.from_time):
            return

        others = frappe.get_all(
            "URY Table Reservation",
            filters={
                "name": ["!=", self.name or ""],
                "table": self.table,
                "reservation_date": self.reservation_date,
                "status": ["in", ACTIVE_STATUSES],
                "docstatus": ["<", 2],
            },
            fields=["name", "from_time", "to_time", "customer_name"],
        )
        if not others:
            return

        start = get_time(self.from_time)
        end = get_time(self.to_time) if self.to_time else None

        for other in others:
            other_start = get_time(other.from_time)
            other_end = get_time(other.to_time) if other.to_time else None

            # Tugash vaqti ko'rsatilmagan bronni nuqta deb qaraymiz.
            if end and other_end:
                overlaps = start < other_end and other_start < end
            elif end:
                overlaps = start <= other_start < end
            elif other_end:
                overlaps = other_start <= start < other_end
            else:
                overlaps = start == other_start

            if overlaps:
                frappe.throw(
                    _("{0} stoli bu vaqtda allaqachon bron qilingan ({1}).").format(
                        self.table, other.name
                    ),
                    title=_("Bron to'qnashuvi"),
                )

    def on_update(self):
        """Kassa ekranlariga stol holati o'zgargani haqida xabar berish."""
        from ozturkapp.ozturkapp.utils.cashier_realtime import emit_floor_change

        emit_floor_change(self.branch, [self.table], "RESERVATION_UPDATED")

    def on_trash(self):
        from ozturkapp.ozturkapp.utils.cashier_realtime import emit_floor_change

        emit_floor_change(self.branch, [self.table], "RESERVATION_UPDATED")
