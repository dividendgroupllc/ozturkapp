# -*- coding: utf-8 -*-
"""Chop etish navbati va ESC/POS generatori testlari.

    bench --site ozturk.local run-tests --module ozturkapp.ozturkapp.tests.test_printing
"""

import base64

import frappe
from frappe.tests.utils import FrappeTestCase

from ozturkapp.ozturkapp.utils import escpos, print_queue

TEST_BRANCH = "_Test Print Branch"


def _strip_escpos(raw: bytes) -> bytes:
    import re

    raw = raw.replace(b"\x1b@", b"").replace(b"\x1bt\x11", b"")
    return re.sub(rb"\x1b[aEt].|\x1d!.|\x1dV\x42\x00", b"", raw)


def _decode_escpos(raw: bytes, start: str = "cp866") -> str:
    """`ESC t n` kod jadvali almashuvini kuzatib matnni to'g'ri o'qish.

    Printer aynan shunday talqin qiladi: `ESC t 0` -> cp437, `ESC t 17`
    -> cp866 va h.k. Test ham xuddi shunday dekod qilishi kerak, aks holda
    almashtirilgan (masalan turkcha) harflar buzuq ko'rinadi.
    """
    num2cp = {0: "cp437", 17: "cp866", 73: "cp1251"}
    c857 = escpos.codepage_number("cp857")
    if c857 is not None:
        num2cp[c857] = "cp857"
    out, cp, i = [], start, 0
    while i < len(raw):
        if raw[i] == 0x1B and i + 2 < len(raw) and raw[i + 1] == ord("t"):
            cp = num2cp.get(raw[i + 2], cp)
            i += 3
            continue
        if raw[i] in (0x1B, 0x1D):  # boshqa ESC/GS buyruqlari — o'tkazib yuboramiz
            i += 3 if raw[i] == 0x1B else 4
            continue
        out.append(bytes([raw[i]]).decode(cp, errors="replace"))
        i += 1
    return "".join(out)


class TestEscpos(FrappeTestCase):
    """Sof funksiyalar — DB kerak emas."""

    def test_money_and_qty(self):
        self.assertEqual(escpos.money(338000), "338 000")
        self.assertEqual(escpos.money(1234.5), "1 234.50")
        self.assertEqual(escpos.money(0), "0")
        self.assertEqual(escpos.fmt_qty(2), "2")
        self.assertEqual(escpos.fmt_qty(1.5), "1.5")

    def test_encode_codepage_switch(self):
        # Kod jadvali almashuvini kuzatib o'qiganda:
        #   Ö, ü — cp437 da bor (ESC t 0), to'g'ri chiqadi
        #   kirill — cp866 da saqlanadi
        out = _decode_escpos(escpos.encode_text("ÖzTürk DÖNER шўрва", "cp866"))
        self.assertEqual(out, "ÖzTürk DÖNER шўрва")
        # Hech qaysi jadvalda yo'q harf `_TRANSLIT` orqali lotinga tushadi:
        #   ғ -> г, ş -> s (cp857 o'chirilgan bo'lsa)
        out2 = _decode_escpos(escpos.encode_text("ғишт", "cp866"))
        self.assertEqual(out2, "гишт")
        # O'zbek/tipografik apostrof ASCII '.' ga normallashadi (jadval kerak emas)
        self.assertEqual(escpos.encode_text("sho‘rva", "cp866"), b"sho'rva")

    def test_encode_pure_ascii_fast_path(self):
        # ASCII matn hech qanday ESC almashuvisiz, to'g'ridan-to'g'ri
        self.assertEqual(escpos.encode_text("Jami: 150 000", "cp866"), b"Jami: 150 000")

    def test_receipt_structure(self):
        r = escpos.Receipt(columns=48, codepage="cp866", codepage_number=17, cut=True)
        r.text("SARLAVHA", align="center", bold=True, size="double")
        r.pair("Jami:", "10 000")
        raw = r.finish()
        self.assertTrue(raw.startswith(b"\x1b@\x1bt\x11"))
        self.assertTrue(raw.endswith(b"\x1dV\x42\x00"))
        txt = _strip_escpos(raw).decode("cp866")
        self.assertIn("SARLAVHA", txt)
        line = [l for l in txt.splitlines() if l.startswith("Jami:")][0]
        self.assertEqual(len(line), 48)
        self.assertTrue(line.endswith("10 000"))

    def test_wrap_long_words(self):
        self.assertEqual(escpos.Receipt.wrap("abcdefghij", 4), ["abcd", "efgh", "ij"])
        self.assertEqual(escpos.Receipt.wrap("aa bb cc", 5), ["aa bb", "cc"])

    def test_build_bill_58mm(self):
        printer = {"paper_width": "58", "codepage": "cp866", "codepage_number": 17, "cut_paper": 1}
        bill = {
            "invoice": "INV-1", "order_number": "7", "table": "T1", "pax": 2,
            "items": [{"item_name": "Lag'mon", "qty": 2, "rate": 35000, "amount": 70000}],
            "total": 70000, "taxes": [{"description": "SC", "amount": 8400, "is_service_charge": True}],
            "service_charge_rate": 12, "discount": 0, "grand_total": 78400, "rounded_total": 78400,
            "paid": False, "payments": [],
        }
        txt = _strip_escpos(escpos.build_bill(bill, printer, {"line1": "TEST"})).decode("cp866")
        self.assertIn("TEST", txt)
        self.assertIn("Lag'mon", txt)
        self.assertIn("Xizmat haqi 12%", txt)
        self.assertIn("78 400", txt)
        for line in txt.splitlines():
            self.assertLessEqual(len(line), 32, line)


TEST_USER = "_test_print_agent@example.com"

#: Test qoldig'ini har ehtimolga qarshi tozalash uchun (ba'zi metodlar —
#: `recover_stale` — `frappe.db.commit()` chaqiradi, ya'ni setUpClass'dagi
#: yozuvlar bazada QOLIB KETISHI mumkin; shuning uchun tearDownClass'da
#: MAJBURAN o'chiramiz. AKS HOLDA test filiali Administrator'ni o'ziga
#: bog'lab, kassa sahifasi "POS Profile topilmadi" bilan ochilmay qoladi.)
def _cleanup_fixtures():
    for name in ("_Test Kassa Printer", "_Test Bad", "_Test Bad2"):
        if frappe.db.exists("Ozturk Printer", name):
            frappe.delete_doc("Ozturk Printer", name, force=1, ignore_permissions=True)
    frappe.db.delete("Ozturk Print Job", {"branch": TEST_BRANCH})
    if frappe.db.exists("Branch", TEST_BRANCH):
        frappe.delete_doc("Branch", TEST_BRANCH, force=1, ignore_permissions=True)
    if frappe.db.exists("User", TEST_USER):
        frappe.delete_doc("User", TEST_USER, force=1, ignore_permissions=True)


class TestPrintQueue(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _cleanup_fixtures()
        # Administrator'ni bog'lamaymiz — bo'lmasa test filiali
        # `resolve_branch()` orqali Administrator'ga ilinib qolardi. Alohida
        # dummy foydalanuvchi ishlatamiz.
        if not frappe.db.exists("User", TEST_USER):
            frappe.get_doc({
                "doctype": "User", "email": TEST_USER, "first_name": "Test Print Agent",
                "send_welcome_email": 0, "enabled": 1,
            }).insert(ignore_permissions=True)
        if not frappe.db.exists("Branch", TEST_BRANCH):
            frappe.get_doc({
                "doctype": "Branch", "branch": TEST_BRANCH,
                "user": [{"user": TEST_USER}],
            }).insert(ignore_permissions=True)
        cls.printer = "_Test Kassa Printer"
        if not frappe.db.exists("Ozturk Printer", cls.printer):
            frappe.get_doc({
                "doctype": "Ozturk Printer", "printer_name": cls.printer, "branch": TEST_BRANCH,
                "role": "Kassa", "ip_address": "192.0.2.10", "port": 9100,
            }).insert(ignore_permissions=True)
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        _cleanup_fixtures()
        frappe.db.commit()
        super().tearDownClass()

    def setUp(self):
        frappe.db.delete("Ozturk Print Job", {"branch": TEST_BRANCH})

    def test_printer_validation(self):
        bad = frappe.get_doc({
            "doctype": "Ozturk Printer", "printer_name": "_Test Bad", "branch": TEST_BRANCH,
            "role": "Kassa", "ip_address": "not an ip!", "port": 9100,
        })
        self.assertRaises(frappe.ValidationError, bad.insert)
        kitchen_no_unit = frappe.get_doc({
            "doctype": "Ozturk Printer", "printer_name": "_Test Bad2", "branch": TEST_BRANCH,
            "role": "Oshxona", "ip_address": "192.0.2.11", "port": 9100,
        })
        self.assertRaises(frappe.ValidationError, kitchen_no_unit.insert)

    def test_enqueue_pull_ack_cycle(self):
        job = print_queue.enqueue_test(self.printer, "unit")
        self.assertEqual(frappe.db.get_value("Ozturk Print Job", job, "status"), "Pending")

        pulled = print_queue.pull_jobs(TEST_BRANCH, "agent-1")
        self.assertEqual([j["job"] for j in pulled], [job])
        self.assertEqual(pulled[0]["host"], "192.0.2.10")
        self.assertEqual(pulled[0]["port"], 9100)
        self.assertTrue(base64.b64decode(pulled[0]["payload"]).startswith(b"\x1b@"))
        self.assertEqual(frappe.db.get_value("Ozturk Print Job", job, "status"), "Printing")

        # Ikkinchi agent xuddi shu topshiriqni OLMAYDI
        self.assertEqual(print_queue.pull_jobs(TEST_BRANCH, "agent-2"), [])

        print_queue.ack(job, True, agent="agent-1")
        row = frappe.db.get_value("Ozturk Print Job", job, ["status", "printed_at", "agent"], as_dict=True)
        self.assertEqual(row.status, "Done")
        self.assertIsNotNone(row.printed_at)
        self.assertEqual(row.agent, "agent-1")

    def test_failed_retry_then_failed(self):
        job = print_queue.enqueue_test(self.printer)
        for attempt in range(1, print_queue.MAX_ATTEMPTS + 1):
            pulled = print_queue.pull_jobs(TEST_BRANCH, "agent-1")
            self.assertEqual(len(pulled), 1, f"attempt {attempt}")
            result = print_queue.ack(job, False, error="connection refused", agent="agent-1")
            if attempt < print_queue.MAX_ATTEMPTS:
                self.assertEqual(result["status"], "Pending")
            else:
                self.assertEqual(result["status"], "Failed")
        self.assertEqual(frappe.db.get_value("Ozturk Print Job", job, "error"), "connection refused")
        self.assertEqual(print_queue.pull_jobs(TEST_BRANCH, "agent-1"), [])

    def test_recover_stale(self):
        job = print_queue.enqueue_test(self.printer)
        print_queue.pull_jobs(TEST_BRANCH, "agent-1")
        frappe.db.sql(
            "UPDATE `tabOzturk Print Job` SET modified = DATE_SUB(NOW(), INTERVAL 600 SECOND) WHERE name=%s",
            job,
        )
        self.assertEqual(print_queue.recover_stale(), 1)
        self.assertEqual(frappe.db.get_value("Ozturk Print Job", job, "status"), "Pending")

    def test_requeue(self):
        job = print_queue.enqueue_test(self.printer)
        copy = print_queue.requeue(job)
        self.assertNotEqual(copy, job)
        a, b = (frappe.db.get_value("Ozturk Print Job", n, "payload") for n in (job, copy))
        self.assertEqual(a, b)

    def test_heartbeat_status(self):
        print_queue.heartbeat(TEST_BRANCH, "agent-x", {"version": "1"})
        status = print_queue.agent_status(TEST_BRANCH)
        self.assertTrue(status["online"])
        self.assertEqual(status["agent"], "agent-x")

    def test_disabled_printer_rejected(self):
        frappe.db.set_value("Ozturk Printer", self.printer, "enabled", 0)
        try:
            self.assertRaises(frappe.ValidationError, print_queue.enqueue_test, self.printer)
        finally:
            frappe.db.set_value("Ozturk Printer", self.printer, "enabled", 1)

    def test_agent_api_requires_role(self):
        from ozturkapp.ozturkapp.api import print_agent

        frappe.set_user("Guest")
        try:
            self.assertRaises(frappe.AuthenticationError, print_agent.pull_jobs, TEST_BRANCH)
        finally:
            frappe.set_user("Administrator")
