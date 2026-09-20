# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Prixod (kirim) — Purchase Invoice formasi va Kassa workspace'i.

Bu testlar BAZAGA tegmaydi: faqat repodagi fayllarni tekshiradi.

Ishga tushirish::

    cd apps/ozturkapp && ../../env/bin/python -m unittest \
        ozturkapp.ozturkapp.tests.test_prixod -v
"""

import json
import os
import unittest

APP = os.path.join(os.path.dirname(__file__), "..", "..")
PI_SCRIPT = os.path.join(APP, "public", "js", "purchase_invoice_item_filter.js")
KASSA_JSON = os.path.join(APP, "ozturkapp", "workspace", "kassa", "kassa.json")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestPurchaseInvoiceScript(unittest.TestCase):
    def setUp(self):
        self.script = _read(PI_SCRIPT)

    def test_purchased_groups_are_selectable(self):
        """Xomashyo, yarim tayyor va ichimlik — hammasi prixod qilinadi."""
        for group in ("Сырьё", "Полуфабрикат", "Напитки"):
            self.assertIn(f"'{group}'", self.script, f"{group} filtrda yo'q")

    def test_finished_dishes_are_not_selectable(self):
        """Taomlar ishlab chiqariladi — ularni sotib olib bo'lmasin."""
        self.assertNotIn("Готовый продукт", self.script)

    def test_blank_invoice_opens_in_receiving_mode(self):
        """`Update Stock` yoqilmasa faktura omborga kirim yozmaydi."""
        self.assertIn("update_stock", self.script)
        self.assertIn("frm.is_new()", self.script)

    def test_linked_invoices_are_left_alone(self):
        """Qatorlari tayyor (buyurtmadan) va qaytarish fakturalari o'zgarmaydi."""
        self.assertIn("is_return", self.script)
        self.assertIn("item_code", self.script)


class TestKassaWorkspacePrixod(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads(_read(KASSA_JSON))
        self.blocks = json.loads(self.doc["content"])
        self.shortcuts = {row["label"]: row for row in self.doc["shortcuts"]}

    def test_prixod_shortcut_opens_new_purchase_invoice(self):
        row = self.shortcuts.get("Prixod qilish")
        self.assertIsNotNone(row, "Kassa'da 'Prixod qilish' yorlig'i yo'q")
        self.assertEqual(row["type"], "DocType")
        self.assertEqual(row["link_to"], "Purchase Invoice")
        self.assertEqual(row["doc_view"], "New")

    def test_every_shortcut_block_has_a_shortcut_row(self):
        """Blok bor, lekin qatori yo'q bo'lsa — yorliq sahifada ko'rinmaydi."""
        names = [b["data"]["shortcut_name"] for b in self.blocks if b["type"] == "shortcut"]
        self.assertIn("Prixod qilish", names)
        for name in names:
            self.assertIn(name, self.shortcuts, f"'{name}' bloki uchun shortcut qatori yo'q")

    def test_cash_register_shortcut_is_kept(self):
        row = self.shortcuts.get("Kassa")
        self.assertIsNotNone(row)
        self.assertEqual((row["type"], row["link_to"]), ("Page", "restaurant-cashier"))

    def test_no_paragraph_blocks(self):
        """Workspace'da ortiqcha izoh bo'lmasligi kerak (test_cashier.py bilan mos)."""
        self.assertNotIn("paragraph", [b["type"] for b in self.blocks])

    def test_block_ids_are_unique(self):
        ids = [b["id"] for b in self.blocks]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
