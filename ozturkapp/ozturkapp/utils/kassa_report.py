# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kunlik kassa hisoboti (PDF) — kassa yopilganda Telegram guruhga.

Jadval: ustunlar — kassa (to'lov turi) hisoblari, qatorlar — Boshlang'ich
summa / Kirim / Chiqim / Qoldiq summa. Ostida kunning ko'rsatkichlari
(sotuv, cheklar soni, o'rtacha chek) va mol/tovuq go'shti bo'yicha sotuv.

HISOB-KITOB MANBAI — bosh kitob (GL Entry)
==========================================
Har bir kassa (Mode of Payment) hisobining kun ichidagi yozuvlari turiga
qarab qatorga bo'linadi:

    Sales Invoice                     -> «Savdo»
    Kassa (Приход/Расход)              -> kontragent / xarajat nomi bilan
    Kassa (Перемещение)               -> «Peremesheniye»
    boshqa hujjatlar (JE, PE ...)      -> «Boshqa»

Shu sababli «Qoldiq» har doim bosh kitobdagi qoldiqqa teng — qo'lda
yig'ilgan summa bilan farq bo'lishi mumkin emas.

Konsolidatsiya qilinmagan POS Invoice'lar (smena hali yopilmagan yoki fon
vazifasi tugamagan) bosh kitobda yo'q, shuning uchun ular alohida qo'shiladi:
bugungilari «Savdo» ga, avvalgilari boshlang'ich summaga.

Kassa farqi (sanalgan − kutilgan) bosh kitobga yozilmaydi, shuning uchun
jadvalga kirmaydi va hisobotda ko'rsatilmaydi.
"""

import io
import os
import re

import frappe
from frappe.utils import flt, formatdate, getdate

from ozturkapp.ozturkapp.utils.money import format_amount

LOGO_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "public", "images", "ozturk_logo.png")

#: Kirill va turk harflari (Ş Ğ İ Нахт) uchun DejaVu (serverda o'rnatilgan).
FONT_DIRS = ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF")

SALES_LABEL = "Savdo"
TRANSFER_LABEL = "Peremesheniye"
OTHER_LABEL = "Boshqa"

#: Mahsulot nomidagi kalit so'zlar (kichik harfda). Tovuq birinchi tekshiriladi:
#: «DURUM Tovuqli», «PORTION DÖNER CHICKEN».
CHICKEN_WORDS = ("tovuq", "chicken")
#: «Mol go'shti ...» va nomida go'sht turi yozilmagan sof dyoner («PORTION DÖNER»).
BEEF_WORDS = ("mol go'shti", "döner", "doner", "donar")

_WEIGHT = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|gr|g)\b", re.I)


def meat_kind(item_name: str) -> str:
    """'beef' | 'chicken' | 'other' — mahsulot nomiga qarab."""
    name = (item_name or "").lower()
    if any(word in name for word in CHICKEN_WORDS):
        return "chicken"
    if any(word in name for word in BEEF_WORDS):
        return "beef"
    return "other"


def item_kg(item_name: str) -> float:
    """Nomdagi og'irlik (`110 gr`, `0.5 kg`) -> kg; yo'q bo'lsa 0."""
    match = _WEIGHT.search(item_name or "")
    if not match:
        return 0.0
    value = flt(match.group(1).replace(",", "."))
    return value if match.group(2).lower() == "kg" else value / 1000


# ── ma'lumot ──────────────────────────────────────────────────────────────

def _kassa_columns(company: str) -> list[dict]:
    """Hisobi bor to'lov turlari: [{mode, label, account}]."""
    rows = frappe.db.sql(
        """
        SELECT mop.name AS mode, mop.custom_pos_label AS pos_label,
               mpa.default_account AS account
        FROM `tabMode of Payment` mop
        JOIN `tabMode of Payment Account` mpa
          ON mpa.parent = mop.name AND mpa.company = %s
        WHERE mop.enabled = 1 AND IFNULL(mpa.default_account, '') != ''
        ORDER BY mop.name
        """,
        company,
        as_dict=True,
    )
    return [{"mode": r.mode, "label": r.pos_label or r.mode, "account": r.account} for r in rows]


def _unconsolidated_payments(day, company: str) -> list[dict]:
    """Bosh kitobga hali tushmagan POS to'lovlari: [{date, mode, amount}].

    Qaytim naqd to'lovdan ayiriladi (kassaga qolgan pul).
    """
    rows = frappe.db.sql(
        """
        SELECT inv.name, inv.posting_date, inv.change_amount,
               pay.mode_of_payment AS mode, pay.amount, mop.type AS mode_type
        FROM `tabPOS Invoice` inv
        JOIN `tabSales Invoice Payment` pay ON pay.parent = inv.name
        LEFT JOIN `tabMode of Payment` mop ON mop.name = pay.mode_of_payment
        WHERE inv.docstatus = 1 AND inv.company = %s AND inv.posting_date <= %s
          AND IFNULL(inv.consolidated_invoice, '') = ''
        ORDER BY inv.name, pay.idx
        """,
        (company, day),
        as_dict=True,
    )
    out, change_taken = [], set()
    for row in rows:
        amount = flt(row.amount)
        if row.mode_type == "Cash" and row.name not in change_taken:
            amount -= flt(row.change_amount)
            change_taken.add(row.name)
        out.append({"date": getdate(row.posting_date), "mode": row.mode, "amount": amount})
    return out


def _kassa_docs(start, day) -> dict:
    """voucher_no -> Kassa hujjati (JE yoki PE orqali bog'langan)."""
    docs = frappe.get_all(
        "Kassa",
        filters={"docstatus": 1, "date": ["between", [start, day]]},
        fields=[
            "name", "oborot", "party_type", "kontragent", "expense_kontragent",
            "journal_entry", "payment_entry",
        ],
    )
    by_voucher = {}
    for doc in docs:
        for voucher in (doc.journal_entry, doc.payment_entry):
            if voucher:
                by_voucher[voucher] = doc
    return by_voucher


def _party_label(doc) -> str:
    if doc.expense_kontragent:
        name = frappe.db.get_value("Account", doc.expense_kontragent, "account_name")
        return name or doc.expense_kontragent
    if doc.kontragent:
        display = frappe.db.get_value(doc.party_type, doc.kontragent, "name")
        return f"{doc.party_type}: {display}" if doc.party_type else display
    return doc.party_type or doc.oborot


def day_table(day, company: str, start=None) -> dict:
    """Kassa jadvali: ustunlar, qatorlar va jami'lar.

    `start` berilsa, jadval start..day oralig'ini qamraydi (yarim tundan o'tgan smena).
    """
    start = start or day
    columns = _kassa_columns(company)
    kassa_by_voucher = _kassa_docs(start, day)
    pending = _unconsolidated_payments(day, company)

    opening = {c["mode"]: 0.0 for c in columns}
    # label -> {mode: amount}
    kirim, chiqim = {}, {}

    def add(bucket, label, mode, amount):
        bucket.setdefault(label, {})
        bucket[label][mode] = bucket[label].get(mode, 0.0) + amount

    for column in columns:
        mode, account = column["mode"], column["account"]
        before = frappe.db.sql(
            """SELECT IFNULL(SUM(debit - credit), 0) FROM `tabGL Entry`
               WHERE account = %s AND is_cancelled = 0 AND posting_date < %s""",
            (account, start),
        )[0][0]
        opening[mode] = flt(before) + sum(
            p["amount"] for p in pending if p["mode"] == mode and p["date"] < start
        )

        entries = frappe.db.sql(
            """SELECT voucher_type, voucher_no, debit, credit FROM `tabGL Entry`
               WHERE account = %s AND is_cancelled = 0 AND posting_date BETWEEN %s AND %s""",
            (account, start, day),
            as_dict=True,
        )
        for entry in entries:
            net = flt(entry.debit) - flt(entry.credit)
            kassa = kassa_by_voucher.get(entry.voucher_no)
            if kassa and kassa.oborot == "Перемещение":
                label = TRANSFER_LABEL
            elif kassa:
                label = _party_label(kassa)
            elif entry.voucher_type in ("Sales Invoice", "POS Invoice"):
                label = SALES_LABEL
            else:
                label = OTHER_LABEL
            if label == SALES_LABEL:
                add(kirim, label, mode, net)  # qaytarish Savdo'dan ayiriladi
            elif net >= 0:
                add(kirim, label, mode, net)
            else:
                add(chiqim, label, mode, -net)

        for p in pending:
            if p["mode"] == mode and start <= p["date"] <= day:
                add(kirim, SALES_LABEL, mode, p["amount"])

    def rows_of(bucket, first=None):
        labels = sorted(bucket, key=lambda l: (l != first, l))
        return [
            {"label": l, "cells": [bucket[l].get(c["mode"], 0.0) for c in columns]}
            for l in labels
            if any(abs(v) > 0.004 for v in bucket[l].values())
        ]

    kirim_rows = rows_of(kirim, SALES_LABEL)
    chiqim_rows = rows_of(chiqim)

    def column_sum(rows, i):
        return sum(r["cells"][i] for r in rows)

    opening_cells = [opening[c["mode"]] for c in columns]
    closing_cells = [
        opening_cells[i] + column_sum(kirim_rows, i) - column_sum(chiqim_rows, i)
        for i in range(len(columns))
    ]

    # Hech qanday harakati va qoldig'i yo'q kassalar ustun bo'lmaydi.
    keep = [
        i for i in range(len(columns))
        if abs(opening_cells[i]) > 0.004 or abs(closing_cells[i]) > 0.004
        or any(abs(r["cells"][i]) > 0.004 for r in kirim_rows + chiqim_rows)
    ]

    def pick(cells):
        return [cells[i] for i in keep]

    return {
        "columns": [columns[i] for i in keep],
        "opening": pick(opening_cells),
        "kirim": [{"label": r["label"], "cells": pick(r["cells"])} for r in kirim_rows],
        "chiqim": [{"label": r["label"], "cells": pick(r["cells"])} for r in chiqim_rows],
        "closing": pick(closing_cells),
    }


def day_stats(day, company: str, start=None) -> dict:
    """Kun ko'rsatkichlari va mol/tovuq go'shti bo'yicha sotuv (start..day)."""
    start = start or day
    invoices = frappe.db.sql(
        """SELECT name, grand_total, discount_amount, is_return FROM `tabPOS Invoice`
           WHERE docstatus = 1 AND company = %s AND posting_date BETWEEN %s AND %s""",
        (company, start, day),
        as_dict=True,
    )
    sales = [i for i in invoices if not i.is_return]
    returns = [i for i in invoices if i.is_return]
    total = sum(flt(i.grand_total) for i in sales)
    checks = len(sales)

    items = frappe.db.sql(
        """SELECT it.item_name, it.qty, it.net_amount FROM `tabPOS Invoice Item` it
           JOIN `tabPOS Invoice` inv ON inv.name = it.parent
           WHERE inv.docstatus = 1 AND inv.company = %s AND inv.posting_date BETWEEN %s AND %s
             AND inv.is_return = 0""",
        (company, start, day),
        as_dict=True,
    )
    kinds = {k: {"amount": 0.0, "kg": 0.0} for k in ("beef", "chicken", "other")}
    for item in items:
        bucket = kinds[meat_kind(item.item_name)]
        bucket["amount"] += flt(item.net_amount)
        bucket["kg"] += flt(item.qty) * item_kg(item.item_name)
    items_total = sum(k["amount"] for k in kinds.values())
    for bucket in kinds.values():
        bucket["share"] = bucket["amount"] / items_total * 100 if items_total else 0.0

    return {
        "total": total,
        "checks": checks,
        "average": total / checks if checks else 0.0,
        "discount": sum(flt(i.discount_amount) for i in sales),
        "returns_count": len(returns),
        "returns_amount": abs(sum(flt(i.grand_total) for i in returns)),
        "kinds": kinds,
    }


# ── PDF (reportlab) ───────────────────────────────────────────────────────

def _register_fonts() -> tuple[str, str]:
    """(oddiy, qalin) shrift nomlari; DejaVu topilmasa Helvetica (kirill bo'lmaydi)."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if "DejaVuSans" in pdfmetrics.getRegisteredFontNames():
        return "DejaVuSans", "DejaVuSans-Bold"
    for folder in FONT_DIRS:
        regular, bold = (os.path.join(folder, n) for n in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"))
        if os.path.exists(regular) and os.path.exists(bold):
            pdfmetrics.registerFont(TTFont("DejaVuSans", regular))
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", bold))
            return "DejaVuSans", "DejaVuSans-Bold"
    return "Helvetica", "Helvetica-Bold"


def _num(value) -> str:
    """Jadvalda nol ham ko'rinadi (PDFdagi kabi)."""
    return format_amount(value) if abs(flt(value)) > 0.004 else "0"


def render_pdf(day, table: dict, stats: dict, extra: dict, start=None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, SimpleDocTemplate, Spacer, Table, TableStyle

    font, bold = _register_fonts()
    date_label = formatdate(day, "dd.MM.yyyy")
    if start and getdate(start) != getdate(day):
        date_label = f"{formatdate(start, 'dd.MM.yyyy')} – {date_label}"
    grid = colors.HexColor("#999999")
    shade = colors.HexColor("#f2f2f2")
    shade_dark = colors.HexColor("#e2e2e2")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm,
        topMargin=8 * mm, bottomMargin=8 * mm, title=f"Kassa hisoboti {date_label}",
    )
    width = doc.width

    story = []
    logo = Image(LOGO_PATH, width=46 * mm, height=46 * mm * 220 / 1024)
    story += [logo, Spacer(1, 5 * mm)]

    # ── asosiy jadval ──
    columns = table["columns"]
    count = len(columns)
    ncols = count + 2
    cells = [["Sana", date_label] + [""] * count]
    cells.append([""] + [c["label"] for c in columns] + ["Jami"])
    style = [
        ("FONT", (0, 0), (-1, -1), font, 8),
        ("GRID", (0, 0), (-1, -1), 0.4, grid),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("SPAN", (1, 0), (-1, 0)), ("ALIGN", (1, 0), (-1, 0), "CENTER"),
        ("FONT", (1, 0), (-1, 0), bold, 9),
        ("FONT", (0, 1), (-1, 1), bold, 8), ("ALIGN", (1, 1), (-1, 1), "CENTER"),
    ]

    def add_row(label, values, strong=False):
        cells.append([label] + [_num(v) for v in values] + [_num(sum(values))])
        row = len(cells) - 1
        style.append(("FONT", (ncols - 1, row), (ncols - 1, row), bold, 8))
        if strong:
            style.extend([("FONT", (0, row), (-1, row), bold, 8), ("BACKGROUND", (0, row), (-1, row), shade_dark)])

    def add_section(title):
        cells.append([title] + [""] * (ncols - 1))
        row = len(cells) - 1
        style.extend([
            ("SPAN", (0, row), (-1, row)), ("ALIGN", (0, row), (-1, row), "CENTER"),
            ("FONT", (0, row), (-1, row), bold, 9), ("BACKGROUND", (0, row), (-1, row), shade),
        ])

    add_row("Boshlang'ich summa", table["opening"], strong=False)
    style.append(("FONT", (0, len(cells) - 1), (-1, len(cells) - 1), bold, 8))
    add_section("Kirim")
    for r in table["kirim"] or [{"label": "—", "cells": [0] * count}]:
        add_row(r["label"], r["cells"])
    add_section("Chiqim")
    for r in table["chiqim"] or [{"label": "—", "cells": [0] * count}]:
        add_row(r["label"], r["cells"])
    add_section("Qoldiq summa")
    add_row("Jami", table["closing"], strong=True)

    first = 42 * mm
    rest = (width - first) / (ncols - 1)
    story.append(Table(cells, colWidths=[first] + [rest] * (ncols - 1), style=TableStyle(style)))
    story.append(Spacer(1, 6 * mm))

    # ── pastki jadvallar ──
    def small(rows, widths):
        t = Table(rows, colWidths=widths)
        t.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, -1), font, 8), ("FONT", (0, 0), (0, -1), bold, 8),
            ("GRID", (0, 0), (-1, -1), 0.4, grid), ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ]))
        return t

    metrics = small([
        ["Jami sotuv", _num(stats["total"])],
        ["Cheklar soni", str(stats["checks"])],
        ["O'rtacha chek", _num(round(stats["average"]))],
        ["Chegirmalar", _num(stats["discount"])],
        ["Qaytarishlar", f"{stats['returns_count']} ta / {_num(stats['returns_amount'])}"],
    ], [48 * mm, 36 * mm])

    kinds = stats["kinds"]

    meat = small([
        [title, _num(kinds[key]["amount"]), f"{kinds[key]['share']:.2f}%".replace(".", ",")]
        for key, title in (
            ("beef", "Mol go'shti mahsulotlari"),
            ("chicken", "Tovuq go'shti mahsulotlari"),
            ("other", "Boshqa mahsulotlar"),
        )
    ], [54 * mm, 30 * mm, 20 * mm])

    lower = Table([[metrics, meat]], colWidths=[width / 2, width / 2])
    lower.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(lower)

    if extra.get("footer"):
        story += [Spacer(1, 4 * mm), _footer(extra["footer"], font)]

    doc.build(story)
    return buffer.getvalue()


def _footer(text, font):
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph
    from reportlab.lib.styles import ParagraphStyle

    return Paragraph(frappe.utils.escape_html(text), ParagraphStyle(
        "foot", fontName=font, fontSize=7, textColor=colors.HexColor("#777777")))


def shift_range(closing) -> tuple:
    """Smena qamragan sanalar (start, end).

    Smena yarim tundan keyin yopilsa, savdo smena ochilgan kunga yoziladi —
    shuning uchun yopilish sanasi emas, smena ichidagi cheklar sanasi olinadi.
    """
    opened = getdate(closing.get("period_start_date") or closing.posting_date)
    closed = getdate(closing.get("period_end_date") or closing.posting_date)
    start, end = opened, closed
    if closing.get("period_start_date") and closing.get("period_end_date"):
        row = frappe.db.sql(
            """SELECT MIN(posting_date), MAX(posting_date) FROM `tabPOS Invoice`
               WHERE docstatus = 1 AND company = %s AND creation BETWEEN %s AND %s""",
            (closing.company, closing.period_start_date, closing.period_end_date),
        )[0]
        if row[0]:
            start, end = min(start, getdate(row[0])), min(closed, max(getdate(row[1]), opened))
    return start, max(start, end)


def build_report_pdf(closing) -> bytes:
    """POS Closing Entry smenasi uchun hisobot PDF'i."""
    start, day = shift_range(closing)
    company = closing.company
    table = day_table(day, company, start)
    stats = day_stats(day, company, start)

    cashier = frappe.db.get_value("User", closing.user, "full_name") or closing.user
    extra = {
        "footer": f"{closing.name} · kassir: {cashier}",
    }
    return render_pdf(day, table, stats, extra, start)
