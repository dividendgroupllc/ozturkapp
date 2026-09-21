# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""URY KOT tekshiruv jobini to'xtatish (setup/kot_failsafe_setup.py)."""

import inspect
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from ozturkapp.ozturkapp.setup import after_migrate, kot_failsafe_setup
from ozturkapp.ozturkapp.setup.kot_failsafe_setup import JOB_METHOD


class TestKotFailsafeSetup(FrappeTestCase):
    def setUp(self):
        # Testlar DB'ni tozalamaydi — `setup()` ichidagi `commit` lokal saytga
        # doimiy o'zgarish yozib qo'ymasligi uchun har testda o'chiriladi.
        patcher = mock.patch.object(frappe.db, "commit")
        patcher.start()
        self.addCleanup(patcher.stop)

        self.job = self._ury_job()

    @staticmethod
    def _ury_job():
        """URY job yozuvi; sayt `ury` siz bo'lsa, testning o'zi yaratadi (rollback bilan ketadi)."""
        name = frappe.db.get_value("Scheduled Job Type", {"method": JOB_METHOD}, "name")
        if name:
            return name
        return (
            frappe.get_doc(
                {
                    "doctype": "Scheduled Job Type",
                    "method": JOB_METHOD,
                    "frequency": "Cron",
                    "cron_format": "* * * * *",
                }
            )
            .insert(ignore_permissions=True)
            .name
        )

    def _stopped(self, name=None):
        return frappe.db.get_value("Scheduled Job Type", name or self.job, "stopped")

    def _set_stopped(self, value, name=None):
        frappe.db.set_value("Scheduled Job Type", name or self.job, "stopped", value)

    # ── to'xtatish ────────────────────────────────────────────────

    def test_running_ury_job_is_stopped(self):
        self._set_stopped(0)

        changed = kot_failsafe_setup.stop_validation_job()

        self.assertGreaterEqual(changed, 1)
        self.assertEqual(self._stopped(), 1)

    def test_setup_is_idempotent(self):
        self._set_stopped(0)

        first = kot_failsafe_setup.setup()
        second = kot_failsafe_setup.setup()

        self.assertGreaterEqual(first, 1)
        self.assertEqual(second, 0, "ikkinchi chaqiruv hech narsani o'zgartirmasligi kerak")
        self.assertEqual(self._stopped(), 1)

    def test_job_that_was_already_stopped_stays_stopped(self):
        self._set_stopped(1)

        self.assertEqual(kot_failsafe_setup.stop_validation_job(), 0)
        self.assertEqual(self._stopped(), 1)

    def test_other_scheduled_jobs_are_not_touched(self):
        other = frappe.get_all(
            "Scheduled Job Type",
            filters={"method": ["!=", JOB_METHOD], "stopped": 0},
            pluck="name",
            limit=1,
        )
        if not other:
            self.skipTest("Saytda to'xtatilmagan boshqa job yo'q")

        kot_failsafe_setup.stop_validation_job()

        self.assertEqual(self._stopped(other[0]), 0)

    def test_unknown_method_changes_nothing(self):
        self._set_stopped(0)

        self.assertEqual(kot_failsafe_setup.stop_validation_job("no.such.module.job"), 0)
        self.assertEqual(self._stopped(), 0, "boshqa metodning to'xtatilishi URY jobiga tegmasligi kerak")

    # ── migrate'ga ulanganligi va to'xtatish sababi ────────────────

    def test_setup_is_wired_into_after_migrate(self):
        self.assertIn(
            "setup_kot_failsafe",
            inspect.getsource(after_migrate.run),
            "setup after_migrate ro'yxatida yo'q — migrate'dan keyin job qayta yoqilib qolishi mumkin",
        )

    def test_reason_for_stopping_still_holds(self):
        """To'xtatishning ASOSIY sababi: URY `split_bill()` yangi draft invoysga KOT yaratmaydi.

        Job tuzalsa, bo'lingan chek uchun 1–5 daqiqadan keyin soxta `Duplicate`
        KOT chiqadi (u oshxona ekranida «pishirilayotgan» ko'rinadi). Bu test
        yiqilsa — URY o'zgargan: jobni yoqish xavfsiz bo'lib qolgan bo'lishi
        mumkin, qaroringizni qayta ko'rib chiqing.
        """
        from ury.ury.doctype.ury_order import ury_order

        self.assertNotIn(
            "kot_execute",
            inspect.getsource(ury_order.split_bill),
            "URY endi split_bill'da KOT yaratadi — job to'xtatilishining sababi yo'qolgan bo'lishi mumkin",
        )
