# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""`retry_on_deadlock` — MariaDB deadlock'da so'rovni xavfsiz qayta urinish."""

import inspect
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from ozturkapp.ozturkapp.api import billing
from ozturkapp.ozturkapp.utils import deadlock


class TestRetryOnDeadlock(FrappeTestCase):
    def setUp(self):
        # Testda kutmaymiz va bazani haqiqatan qaytarmaymiz.
        patches = [
            mock.patch.object(deadlock.time, "sleep"),
            mock.patch.object(frappe.db, "rollback"),
        ]
        self.sleep, self.rollback = [patch.start() for patch in patches]
        for patch in patches:
            self.addCleanup(patch.stop)

    def _flaky(self, failures, error=frappe.QueryDeadlockError):
        calls = []

        @deadlock.retry_on_deadlock
        def function(value):
            calls.append(value)
            if len(calls) <= failures:
                raise error("1213")
            return value * 2

        return function, calls

    def test_retries_after_a_deadlock_and_returns_the_result(self):
        function, calls = self._flaky(failures=1)

        self.assertEqual(function(21), 42)
        self.assertEqual(calls, [21, 21], "ikkinchi urinish AYNAN shu argumentlar bilan")
        self.rollback.assert_called_once()
        self.sleep.assert_called_once()

    def test_gives_up_after_the_last_attempt(self):
        function, calls = self._flaky(failures=deadlock.MAX_ATTEMPTS)

        with self.assertRaises(frappe.QueryDeadlockError):
            function(1)
        self.assertEqual(len(calls), deadlock.MAX_ATTEMPTS)
        # Oxirgi (muvaffaqiyatsiz) urinishdan keyin qayta rollback qilinmaydi: uni Frappe o'zi bajaradi.
        self.assertEqual(self.rollback.call_count, deadlock.MAX_ATTEMPTS - 1)

    def test_other_errors_are_never_retried(self):
        for error in (frappe.ValidationError, frappe.QueryTimeoutError, ValueError):
            function, calls = self._flaky(failures=5, error=error)
            with self.assertRaises(error):
                function(1)
            self.assertEqual(len(calls), 1, f"{error.__name__} qayta urinilmasligi kerak")
        self.rollback.assert_not_called()

    def test_submit_payment_is_wrapped_and_still_whitelisted(self):
        self.assertTrue(hasattr(billing.submit_payment, "__wrapped__"), "submit_payment deadlock'dan himoyalanmagan")
        self.assertIn(billing.submit_payment, frappe.whitelisted)
        # Frappe/testlar imzoni `__wrapped__` orqali o'qiydi: parametr nomlari o'zgarmaydi.
        self.assertEqual(list(inspect.signature(billing.submit_payment).parameters), ["invoice", "payments", "tip"])
