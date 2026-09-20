# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""ESC/POS chek generatori — chek formati SERVERDA tuziladi.

NEGA SERVERDA
=============
Agent (monoblok) "ahmoq": baytlarni oladi va printerga uzatadi. Format,
til, kodlash, ustunlar — hammasi shu faylda. O'zgartirish uchun
restoranga borish yoki agentni yangilash shart emas.

BOG'LIQLIK YO'Q
===============
`python-escpos` ATAYLAB ishlatilmaydi — bizga kerak bo'lgan 6-7 ta buyruq
(init, kodlash, tekislash, qalin, katta shrift, kesish) shu yerda. Bitta
kutubxona kamroq, versiya ziddiyati yo'q.

BUYRUQLAR (Epson ESC/POS, Xprinter/Rongta/Gprinter mos)
=======================================================
    ESC @         init
    ESC t n       kod jadvali (cp866=17, cp1251=73, cp437=0)
    ESC a n       tekislash (0 chap, 1 markaz, 2 o'ng)
    ESC E n       qalin (1/0)
    GS  ! n       shrift o'lchami (0x00 oddiy, 0x11 2x kenglik+balandlik, 0x01 2x balandlik)
    GS  V 66 0    qisman kesish (oldin qog'oz suriladi)
    ESC p m t1 t2 g'aladon impulsi (printerning DK portiga ulangan kassa g'aladoni)
"""

from __future__ import annotations

import base64
import re
from datetime import datetime

from frappe.utils import cint, flt, get_datetime

ESC = b"\x1b"
GS = b"\x1d"

#: Sanoq ustunlari: Font A da 80mm -> 48, 58mm -> 32 belgi.
COLUMNS = {"80": 48, "58": 32}

#: Choychaqa soliq qatorining nomi (`Sales Taxes and Charges.description`). Chekda
#: tip qatori AYNAN shu nom bo'yicha taniladi; `cashier_billing.TIPS_DESCRIPTION`
#: bilan bir xil bo'lishi shart (bu fayl `ozturkapp` modullariga bog'liq emas,
#: shuning uchun qiymat takrorlanadi va testda tenglashtiriladi).
TIP_LABEL = "Choychaqa"

#: G'aladon impulsi (`ESC p m t1 t2`). `Ozturk Printer` da g'aladon pini uchun
#: maydon yo'q, shuning uchun bu yerda modul konstantasi: 0 — DK-1 (2-pin,
#: deyarli barcha RJ11 g'aladonlar), 1 — DK-2 (5-pin). Impuls uzunligi
#: millisekundda; printer buyrug'i uni 2 ms birlikda oladi (`t1 = ms / 2`).
#: 50 ms yoqilgan / 500 ms tanaffus — Epson hujjatidagi odatiy qiymat, g'aladon
#: solenoidini kuydirmaydi va qulfni ishonchli ochadi.
DRAWER_PIN = 0
DRAWER_ON_MS = 50
DRAWER_OFF_MS = 500

#: O'zbek lotin apostroflari — cp866/cp1251 da yo'q, oddiy apostrofga almashtiriladi.
_APOSTROPHES = {
    "ʻ": "'",  # ʻ
    "ʼ": "'",  # ʼ
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
    " ": " ",
}


#: Kod jadvalida YO'Q harflar uchun zaxira transliteratsiya. Faqat kodlash
#: muvaffaqiyatsiz bo'lganda ishlatiladi: cp1251 da "ў" bor — o'zgarmaydi,
#: cp866 da yo'q — "у" bo'ladi. Turkcha harflar (restoran turk oshxonasi)
#: cp866/cp1251 da umuman yo'q — lotin asosiga tushiriladi.
_TRANSLIT = {
    # Turkcha
    "Ö": "O", "ö": "o", "Ü": "U", "ü": "u", "Ç": "C", "ç": "c", "Ğ": "G", "ğ": "g",
    "Ş": "S", "ş": "s", "İ": "I", "ı": "i", "Â": "A", "â": "a", "Î": "I", "î": "i",
    "Û": "U", "û": "u",
    # O'zbek kirill (cp866 da yo'q)
    "Ғ": "Г", "ғ": "г", "Қ": "К", "қ": "к", "Ҳ": "Х", "ҳ": "х", "Ў": "У", "ў": "у",
    # Boshqa lotin diakritikalar
    "É": "E", "é": "e", "È": "E", "è": "e", "Ä": "A", "ä": "a", "Ñ": "N", "ñ": "n",
    "Á": "A", "á": "a", "Ó": "O", "ó": "o", "Í": "I", "í": "i", "Ú": "U", "ú": "u",
    "€": "EUR", "₽": "rub", "№": "No", "…": "...",
}


#: `cp857` (DOS Turkish) uchun `ESC t n` raqami. Epson standartida
#: qat'iy belgilanmagan — har bir printer modeli o'zicha raqamlaydi,
#: shuning uchun sinov cheki bilan aniqlanadi (n=0..47 bo'yicha o'tib
#: chiqiladi). `None` — printer cp857 ni bilmaydi, turkcha harflar
#: `_TRANSLIT` orqali lotin asosiga tushadi (eski xatti-harakat).
CP857_NUMBER = None

#: `ESC t n` — ma'lum kod jadvallari.
CODEPAGE_NUMBERS = {"cp437": 0, "cp866": 17, "cp1251": 73}

#: Asosiy jadvalga sig'magan harf shu jadvallardan qidiriladi. Tartib
#: muhim: turkcha harflar cp857 da, kirill cp866 da.
FALLBACK_CODEPAGES = ("cp857", "cp866", "cp1251", "cp437")


def codepage_number(codepage: str):
    """`ESC t n` raqami; jadval noma'lum bo'lsa `None`."""
    if codepage == "cp857":
        return CP857_NUMBER
    return CODEPAGE_NUMBERS.get(codepage)


def _pick_codepage(ch: str, active: str, primary: str, allow_switch: bool = True):
    """Harfni kodlay oladigan jadval.

    Avval JORIY jadval sinaladi — shunda ketma-ket turkcha harflar uchun
    har safar `ESC t n` yuborilmaydi. Keyin asosiy, keyin zaxiralar.

    `allow_switch=False` bo'lsa FAQAT asosiy jadval sinaladi. Diqqat: uni
    butunlay o'tkazib yuborish MUMKIN EMAS — aks holda oddiy ASCII harf
    ham jadvalsiz qolib `?` ga aylanadi (`ÖzTürk` -> `O??u??`).
    """
    candidates = (active, primary, *FALLBACK_CODEPAGES) if allow_switch else (primary,)
    for cp in candidates:
        if cp != primary and codepage_number(cp) is None:
            continue        # raqami noma'lum jadvalga o'tib bo'lmaydi
        try:
            ch.encode(cp)
            return cp
        except (UnicodeEncodeError, LookupError):
            continue
    return None


#: Bitta chop etiladigan matn (izoh, sabab, manzil) uchun eng ko'p belgi. Kassir/ofitsant
#: yuz minglab belgi kiritib printerni qog'ozga to'ldirib yubormasin va topshiriq
#: `payload` i bazani shishirmasin. Haqiqiy izohlar bundan ancha qisqa.
MAX_TEXT_CHARS = 600

#: Yangi qator (`wrap` shu bo'yicha bo'ladi) dan boshqa barcha boshqaruv belgilari.
_CONTROL_CHARS = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]")


def normalize(text) -> str:
    """Kodlashda yo'qoladigan tipografik belgilarni oddiy belgilarga almashtirish.

    BOSHQARUV BELGILARI OLIB TASHLANADI (bo'shliqqa almashadi). Chekka bosiladigan
    har bir matn (taom izohi, chegirma/harakat sababi, mijoz manzili, ...) kassir
    yoki ofitsant kiritgan; unga `ESC p 0 25 250` (g'aladon impulsi) yoki `GS V`
    (kesish) baytlari yashirilsa, printer buni buyruq sifatida bajarardi —
    g'aladon hech qanday izsiz (`Ozturk Print Job` da Drawer yozuvisiz) ochilardi.
    Generator o'zining buyruqlarini `Receipt` orqali bevosita baytlar bilan yozadi,
    matn esa faqat shu funksiyadan o'tadi.
    """
    s = "" if text is None else str(text)
    for src, dst in _APOSTROPHES.items():
        s = s.replace(src, dst)
    return _CONTROL_CHARS.sub(" ", s)


def encode_text(text: str, codepage: str, codepage_number_: int | None = None,
                allow_switch: bool = True) -> bytes:
    """Matnni kod jadvaliga o'tkazish; sig'magan harf uchun jadval almashadi.

    Butun satrni bir yo'la kodlashga urinamiz (tez yo'l — 99% hollarda
    shu ishlaydi). Sig'magan harf bo'lsa harfma-harf yuriladi va kerak
    bo'lganda `ESC t n` bilan boshqa jadvalga o'tiladi:

        "ÖzTürk"  -> cp857 (turkcha)
        "Нахт"    -> cp866 (kirill)

    Satr oxirida asosiy jadval TIKLANADI — aks holda keyingi satr
    noto'g'ri jadvalda chiqardi. Hech qaysi jadvalda yo'q harf
    `_TRANSLIT` dan, u ham bo'lmasa '?' bo'ladi.
    """
    text = normalize(text)
    try:
        return text.encode(codepage)
    except UnicodeEncodeError:
        pass

    if codepage_number_ is None:
        codepage_number_ = codepage_number(codepage)

    out = bytearray()
    active = codepage
    for ch in text:
        cp = _pick_codepage(ch, active, codepage, allow_switch)
        if cp is None:
            alt = _TRANSLIT.get(ch)
            if alt is None:
                out += b"?"
            else:
                try:
                    out += alt.encode(codepage)
                except UnicodeEncodeError:
                    out += b"?"
            continue
        if cp != active:
            n = codepage_number(cp)
            if n is None:
                n = codepage_number_
            out += ESC + b"t" + bytes([n & 0xFF])
            active = cp
        out += ch.encode(cp)

    if active != codepage and codepage_number_ is not None:
        out += ESC + b"t" + bytes([codepage_number_ & 0xFF])
    return bytes(out)


def money(value) -> str:
    """35000 -> '35 000', 35000.5 -> '35 000.50'."""
    value = flt(value)
    if abs(value - round(value)) < 0.005:
        return f"{int(round(value)):,}".replace(",", " ")
    return f"{value:,.2f}".replace(",", " ")


def fmt_qty(value) -> str:
    value = flt(value)
    if abs(value - round(value)) < 0.005:
        return str(int(round(value)))
    return f"{value:g}"


def fmt_dt(value) -> str:
    dt = get_datetime(value) if value else datetime.now()
    return dt.strftime("%d.%m.%Y %H:%M")


class Receipt:
    """Chek quruvchi — matnli qatorlarni ESC/POS baytlarga yig'adi."""

    def __init__(self, columns: int = 48, codepage: str = "cp866", codepage_number: int = 17,
                 cut: bool = True, allow_switch: bool = True):
        self.columns = cint(columns) or 48
        self.codepage = (codepage or "cp866").lower()
        self.codepage_number = cint(codepage_number)
        self.cut = bool(cut)
        self.allow_switch = bool(allow_switch)
        self._buf = bytearray()
        self._buf += ESC + b"@"                                 # init
        self._buf += ESC + b"t" + bytes([self.codepage_number & 0xFF])  # kod jadvali
        self._buf += ESC + b"a" + b"\x00"                       # chapga

    # ── past daraja ────────────────────────────────────────────
    def _enc(self, text: str) -> bytes:
        return encode_text(text, self.codepage, self.codepage_number, self.allow_switch)

    def raw(self, data: bytes):
        self._buf += data
        return self

    def align(self, mode: str):
        n = {"left": 0, "center": 1, "right": 2}.get(mode, 0)
        self._buf += ESC + b"a" + bytes([n])
        return self

    def bold(self, on: bool):
        self._buf += ESC + b"E" + (b"\x01" if on else b"\x00")
        return self

    def size(self, mode: str):
        """'normal' | 'double' (2x kenglik+balandlik) | 'tall' (2x balandlik)."""
        n = {"normal": 0x00, "double": 0x11, "tall": 0x01, "wide": 0x10}.get(mode, 0x00)
        self._buf += GS + b"!" + bytes([n])
        return self

    def feed(self, lines: int = 1):
        self._buf += b"\n" * max(0, lines)
        return self

    # ── matn ───────────────────────────────────────────────────
    def line(self, text: str = ""):
        """Bitta qator; uzun bo'lsa so'z chegarasida bo'linadi."""
        for chunk in self.wrap(text, self.columns):
            self._buf += self._enc(chunk) + b"\n"
        if text == "":
            pass
        return self

    def text(self, text: str, align: str = "left", bold: bool = False, size: str = "normal"):
        width = self.columns // 2 if size in ("double", "wide") else self.columns
        self.align(align)
        if bold:
            self.bold(True)
        if size != "normal":
            self.size(size)
        for chunk in self.wrap(text, width):
            self._buf += self._enc(chunk) + b"\n"
        if size != "normal":
            self.size("normal")
        if bold:
            self.bold(False)
        self.align("left")
        return self

    def rule(self, char: str = "-"):
        self._buf += self._enc(char * self.columns) + b"\n"
        return self

    def pair(self, left: str, right: str, bold: bool = False):
        """Chap va o'ng chekkaga tekislangan ikkita matn (masalan 'Jami:' ... '150 000')."""
        left, right = normalize(left), normalize(right)
        space = self.columns - len(right)
        if space < 1:
            self.line(left)
            self.line(right.rjust(self.columns))
            return self
        if len(left) > space - 1:
            left = left[: max(0, space - 1)]
        row = left.ljust(space) + right
        if bold:
            self.bold(True)
        self._buf += self._enc(row) + b"\n"
        if bold:
            self.bold(False)
        return self

    def table_row(self, cells: list, widths: list, aligns: list | None = None):
        """Ustunli qator; birinchi ustun uzun bo'lsa keyingi qatorga o'tadi."""
        aligns = aligns or ["left"] * len(cells)
        cells = [normalize(c) for c in cells]
        first_lines = self.wrap(cells[0], widths[0]) or [""]
        rest = ""
        for c, w, a in zip(cells[1:], widths[1:], aligns[1:]):
            c = c[:w]
            rest += c.rjust(w) if a == "right" else c.ljust(w)
        first = first_lines[0]
        self._buf += self._enc(first.ljust(widths[0]) + rest) + b"\n"
        for extra in first_lines[1:]:
            self._buf += self._enc(extra) + b"\n"
        return self

    def finish(self) -> bytes:
        self._buf += b"\n\n\n"
        if self.cut:
            self._buf += GS + b"V" + b"\x42" + b"\x00"
        return bytes(self._buf)

    def finish_b64(self) -> str:
        return base64.b64encode(self.finish()).decode("ascii")

    @staticmethod
    def wrap(text: str, width: int) -> list:
        text = normalize(text)[:MAX_TEXT_CHARS]
        if width <= 0:
            return [text]
        out = []
        for para in text.split("\n"):
            words = para.split(" ")
            cur = ""
            for w in words:
                while len(w) > width:
                    if cur:
                        out.append(cur)
                        cur = ""
                    out.append(w[:width])
                    w = w[width:]
                if not cur:
                    cur = w
                elif len(cur) + 1 + len(w) <= width:
                    cur += " " + w
                else:
                    out.append(cur)
                    cur = w
            out.append(cur)
        return out


def _receipt_for(printer) -> Receipt:
    """`Ozturk Printer` hujjatidan (yoki dict) quruvchi yaratish."""
    get = printer.get if hasattr(printer, "get") else (lambda k, d=None: getattr(printer, k, d))
    columns = COLUMNS.get(str(get("paper_width") or "80"), 48)
    return Receipt(
        columns=columns,
        codepage=get("codepage") or "cp866",
        codepage_number=get("codepage_number") if get("codepage_number") is not None else 17,
        cut=bool(cint(get("cut_paper", 1))),
        # ATAYLAB o'chiq (`default: 0`). Almashuvni qo'llamaydigan printer
        # `ESC t n` ni e'tiborsiz qoldiradi va harf o'rniga axlat bosadi
        # (Ö -> Щ, chunki 0x99 cp866 da Щ). Transliteratsiya xunukroq,
        # lekin har qanday printerda o'qiladi. Printerda sinab ko'rgach
        # `Ozturk Printer` da yoqiladi.
        allow_switch=bool(cint(get("allow_codepage_switch", 0))),
    )


# ═══════════════════════════════════════════════════════════════
#  Cheklar
# ═══════════════════════════════════════════════════════════════

def build_bill(bill: dict, printer, header: dict | None = None) -> bytes:
    """Mijoz hisob-cheki.

    `bill` — `utils/cashier_billing.build_bill()` natijasi (barcha summalar
    ERPNext'dan). `header` — {"line1", "line2", "footer"}.

    IXTIYORIY KALITLAR
    ==================
    `discount_percent`, `discount_reason`, `tip`, `delivery`, `is_return`,
    `return_against` — hisob-kitob qatlami qo'shadi; bo'lmasa chek eskicha
    chiqadi. Hech biri bo'lmaganda ham chek YIQILMASLIGI shart: kassa
    cheki chiqmasa mijoz ketib qoladi.
    """
    header = header or {}
    r = _receipt_for(printer)
    cols = r.columns
    is_return = bool(cint(bill.get("is_return")))

    title = header.get("line1") or bill.get("restaurant") or bill.get("company") or "CHEK"
    r.text(title, align="center", bold=True, size="double")
    if header.get("line2"):
        r.text(header["line2"], align="center")
    if is_return:
        # Qaytarish cheki oddiy chekdan bir qarashda ajralishi kerak: kassir
        # va mijoz uni sotuv cheki bilan adashtirmasin.
        r.text("QAYTARISH", align="center", bold=True, size="double")
        if bill.get("return_against"):
            r.text(f"Asl chek: {bill['return_against']}", align="center")
    r.feed(1)

    r.line(fmt_dt(bill.get("opened_at")))
    if bill.get("table"):
        r.line(f"Stol: {bill.get('table')}")
    if bill.get("waiter_name"):
        r.line(f"Ofitsiant: {bill.get('waiter_name')}")
    if bill.get("cashier_name"):
        r.line(f"Kassir: {bill.get('cashier_name')}")
    delivery = bill.get("delivery")
    if isinstance(delivery, dict) and (delivery.get("phone") or delivery.get("address")):
        r.line("Yetkazib berish")
        if delivery.get("phone"):
            r.line(f"Telefon: {delivery['phone']}")
        if delivery.get("address"):
            r.line(f"Manzil: {delivery['address']}")
    r.rule()

    # Ustunlar: nomi | soni | summa. Narx ustuni ATAYLAB yo'q — mijozga
    # soni va summasi yetarli; bo'shagan joy nom ustuniga beriladi
    # (80mm: 31 belgi), uzun taom nomlari kamroq bo'linadi.
    qty_w, amount_w = (5, 12) if cols >= 48 else (4, 12)
    widths = [cols - qty_w - amount_w, qty_w, amount_w]
    aligns = ["left", "right", "right"]
    r.bold(True)
    r.table_row(["Nomi", "Soni", "Summa"], widths, aligns)
    r.bold(False)
    r.rule()
    for item in bill.get("items") or []:
        r.table_row(
            [item.get("item_name") or item.get("item_code") or "",
             fmt_qty(item.get("qty")),
             money(item.get("amount"))],
            widths, aligns,
        )
        if item.get("comment"):
            r.line(f"   * {item['comment']}")
    r.rule()

    r.pair("Jami:", money(bill.get("total")))

    # Choychaqa ERPNext'da oddiy soliq qatori (tax row) — `taxes` ichida ham
    # keladi, `tip` kaliti bilan ham. Ikki marta bosilmasligi uchun soliq
    # qatori tsiklda o'tkazib yuboriladi va bitta qator bilan chiqariladi.
    tip = flt(bill.get("tip"))
    for tax in bill.get("taxes") or []:
        label = tax.get("description") or ""
        if tax.get("is_tip") or label.strip().lower() == TIP_LABEL.lower():
            tip = tip or flt(tax.get("amount"))
            continue
        if tax.get("is_service_charge"):
            rate = flt(bill.get("service_charge_rate") or tax.get("rate"))
            label = f"Xizmat haqi {fmt_qty(rate)}%" if rate else "Xizmat haqi"
        r.pair(f"{label}:", money(tax.get("amount")))

    discount = flt(bill.get("discount"))
    if discount:
        percent = flt(bill.get("discount_percent"))
        label = f"Chegirma {fmt_qty(percent)}%" if percent else "Chegirma"
        r.pair(f"{label}:", money(-discount))
        if bill.get("discount_reason"):
            r.line(f"  ({bill['discount_reason']})")
    if tip:
        r.pair(f"{TIP_LABEL}:", money(tip))
    r.rule()
    r.size("tall").pair("QAYTARILADI:" if is_return else "JAMI TO'LOV:",
                        money(bill.get("rounded_total") or bill.get("grand_total")), bold=True)
    r.size("normal")

    if bill.get("paid") and bill.get("payments"):
        r.feed(1)
        for p in bill["payments"]:
            r.pair(f"{p.get('mode_of_payment') or ''}:", money(p.get("amount")))
        if flt(bill.get("change_amount")):
            r.pair("Qaytim:", money(bill.get("change_amount")))

    r.feed(1)
    r.text(header.get("footer") or "Tashrifingiz uchun rahmat!", align="center")
    return r.finish()


def build_kot(kot: dict, printer) -> bytes:
    """Oshxona buyurtma cheki (KOT) — yangi buyurtma tushganda.

    `kot` = {"station", "kot", "order_number", "table", "waiter", "time",
             "type", "comments", "items": [{"item_name", "qty", "comment"}]}

    MARKAZLASH
    ==========
    Butun chek MARKAZDA chiqadi (`ESC a 1`) — oshxona xodimi uchun o'qishga
    qulay va chap/o'ng chekkalarga yopishmaydi. Ajratgich ham qisqa va
    markazlangan.
    """
    r = _receipt_for(printer)
    align = "center"
    sep = "=" * min(24, r.columns)

    station = kot.get("station") or "OSHXONA"
    r.text(station.upper(), align=align, bold=True, size="double")
    if kot.get("type") and kot["type"] not in ("Order", "New Order"):
        r.text(str(kot["type"]).upper(), align=align, bold=True)
    r.feed(1)
    r.text(f"Buyurtma: {kot.get('order_number') or kot.get('kot') or ''}", align=align)
    r.text(fmt_dt(kot.get("time")), align=align)
    if kot.get("table"):
        r.text(f"STOL: {kot['table']}", align=align, bold=True, size="tall")
    if kot.get("waiter"):
        r.text(f"Ofitsiant: {kot['waiter']}", align=align)
    r.text(sep, align=align)
    for item in kot.get("items") or []:
        qty = fmt_qty(item.get("qty"))
        r.text(f"{qty} x {item.get('item_name') or ''}", align=align, bold=True, size="tall")
        if item.get("comment"):
            r.text(f"* {item['comment']}", align=align)
    r.text(sep, align=align)
    if kot.get("comments"):
        r.text(f"Izoh: {kot['comments']}", align=align)
    return r.finish()


def build_item_ticket(ticket: dict, printer) -> bytes:
    """"Taom tayyor" cheki — bitta mahsulot uchun (oshxona planshetidan).

    Xuddi KOT kabi MARKAZDA chiqadi.
    """
    r = _receipt_for(printer)
    align = "center"
    r.text("TAYYOR", align=align, bold=True, size="double")
    r.feed(1)
    r.text(f"{fmt_qty(ticket.get('quantity'))} x {ticket.get('item_name') or ''}",
           align=align, bold=True, size="tall")
    if ticket.get("table"):
        r.text(f"Stol: {ticket['table']}", align=align, size="tall")
    if ticket.get("station"):
        r.text(str(ticket["station"]), align=align)
    if ticket.get("waiter"):
        r.text(f"Ofitsiant: {ticket['waiter']}", align=align)
    r.text(fmt_dt(ticket.get("printed_at")), align=align)
    return r.finish()


def build_test(printer, label: str = "") -> bytes:
    """Sinov cheki — printer ulanishini tekshirish uchun."""
    r = _receipt_for(printer)
    r.text("SINOV CHEKI", align="center", bold=True, size="double")
    r.feed(1)
    name = getattr(printer, "name", None) or (printer.get("name") if hasattr(printer, "get") else "")
    r.line(f"Printer: {name}")
    if label:
        r.line(label)
    r.line(f"Vaqt: {fmt_dt(None)}")
    r.line("Lotin: O'zbekiston, sho'rva, g'isht")
    r.line("Кирилл: Ўзбекистон, шўрва, ғишт")
    r.rule()
    r.pair("Jami:", money(123456.5))
    r.text("OK", align="center", bold=True)
    return r.finish()


def build_drawer_pulse(pin: int = DRAWER_PIN, on_ms: int = DRAWER_ON_MS,
                       off_ms: int = DRAWER_OFF_MS) -> bytes:
    """G'aladonni ochadigan `ESC p m t1 t2` buyrug'i.

    FAQAT impuls: `ESC @`, matn, kesish YO'Q. Agent baytlarni printerga
    aynan shunday uzatadi, shuning uchun bu ishlash uchun agentni
    yangilash shart emas. Printer holatiga tegilmaydi (kod jadvali, shrift
    o'zgarmaydi) — keyingi chek odatdagidek chiqadi.
    """
    m = 1 if cint(pin) == 1 else 0
    t1 = min(max(cint(on_ms) // 2, 1), 255)
    t2 = min(max(cint(off_ms) // 2, 1), 255)
    return ESC + b"p" + bytes([m, t1, t2])


def _hhmm(value) -> str:
    return get_datetime(value).strftime("%H:%M") if value else ""


def build_shift_report(report: dict, printer, header: dict | None = None) -> bytes:
    """Smena hisoboti: X (oraliq, smena ochiq) yoki Z (smena yopilgan).

    `report` — `utils/shift_report.build_report()` natijasi. Bu yerda
    HECH NARSA HISOBLANMAYDI, faqat chiqariladi.

    KO'R SANOQ
    ==========
    `report["restricted"]` bo'lsa (kassir uchun) savdo jami, naqd pul
    aylanmasi, kutilgan summa va farq hisobotda UMUMAN yo'q — server ularni
    `None` qilib yuboradi va bu yerda ular uchun qator ham chizilmaydi.
    Chekka chiqqan qog'oz ham ko'r sanoqni buzmasligi kerak.
    """
    header = header or {}
    r = _receipt_for(printer)
    kind = str(report.get("kind") or "X").upper()
    restricted = bool(report.get("restricted"))

    r.text(header.get("line1") or report.get("restaurant") or report.get("company") or "HISOBOT",
           align="center", bold=True, size="double")
    r.text(f"{kind}-HISOBOT", align="center", bold=True, size="double")
    r.text("Oraliq hisobot - smena ochiq" if kind == "X" else "Smena yopilgan", align="center")
    r.feed(1)

    cashier = report.get("cashier") or {}
    r.line(f"Kassir: {cashier.get('full_name') or cashier.get('user') or ''}")
    r.line(f"Smena: {report.get('pos_opening_entry') or ''}")
    r.line(f"Ochilgan: {fmt_dt(report.get('period_start'))}")
    r.line(f"{'Hozir' if kind == 'X' else 'Yopilgan'}: {fmt_dt(report.get('period_end'))}")
    r.rule()

    counts = report.get("counts") or {}
    r.pair("Cheklar soni:", str(cint(counts.get("invoices"))))
    r.pair("Qaytarishlar:", str(cint(counts.get("returns"))))
    r.pair("Bekor qilingan buyurtma:", str(cint(counts.get("cancelled_orders"))))
    r.pair("G'aladon (savdosiz):", str(cint(counts.get("drawer_no_sale"))))
    r.rule()

    sales = report.get("sales") or {}
    if restricted:
        r.line("Savdo va naqd pul summalari menejer hisobotida.")
    else:
        r.pair("Yalpi savdo:", money(sales.get("gross_sales")))
        if flt(sales.get("discounts")):
            r.pair("Chegirma:", money(-flt(sales.get("discounts"))))
        r.pair("Xizmat haqi:", money(sales.get("service_charge")))
        if flt(sales.get("tips")):
            r.pair(f"{TIP_LABEL}:", money(sales.get("tips")))
        if flt(sales.get("other_taxes")):
            r.pair("Boshqa soliqlar:", money(sales.get("other_taxes")))
        if flt(sales.get("rounding")):
            r.pair("Yaxlitlash:", money(sales.get("rounding")))
        r.pair("Sotuv jami:", money(sales.get("sales_total")))
        if flt(sales.get("returns_total")):
            r.pair("Qaytarishlar:", money(-flt(sales.get("returns_total"))))
        r.size("tall").pair("SOF SAVDO:", money(sales.get("net_total")), bold=True)
        r.size("normal")
    r.rule()

    payments = report.get("payments") or []
    if payments:
        r.bold(True).line("TO'LOV USULLARI")
        r.bold(False)
        for pay in payments:
            name = pay.get("mode_of_payment") or ""
            count = cint(pay.get("sales_count"))
            if pay.get("net_amount") is None:
                r.pair(f"{name}:", f"{count} ta")
                continue
            r.pair(f"{name} ({count}):", money(pay.get("net_amount")))
            if flt(pay.get("refund_amount")):
                r.line(f"  shundan qaytarish: {money(pay.get('refund_amount'))}")
        r.rule()

    movements = report.get("cash_movements") or {}
    if cint(movements.get("count")):
        r.bold(True).line("KASSA HARAKATI")
        r.bold(False)
        r.pair("Kirim:", money(movements.get("total_in")))
        r.pair("Chiqim:", money(-flt(movements.get("total_out"))))
        for item in movements.get("items") or []:
            sign = "+" if item.get("kind") == "In" else "-"
            r.line(f"{_hhmm(item.get('posting_datetime'))} {sign}{money(item.get('amount'))} "
                   f"{item.get('category') or ''}")
            if item.get("reason"):
                r.line(f"   {item['reason']}")
        r.rule()

    cash = report.get("cash") or {}
    r.bold(True).line("NAQD PUL")
    r.bold(False)
    r.pair("Boshlang'ich:", money(cash.get("opening")))
    if cash.get("counted") is not None:
        r.pair("Sanalgan:", money(cash.get("counted")))
    if cash.get("expected") is not None:
        r.pair("Kutilgan:", money(cash.get("expected")))
    if cash.get("difference") is not None:
        r.pair("Farq:", money(cash.get("difference")), bold=True)
    r.rule()

    openings = report.get("drawer_openings") or []
    if openings:
        r.bold(True).line("G'ALADON (SAVDOSIZ)")
        r.bold(False)
        for item in openings:
            r.line(f"{_hhmm(item.get('time'))} {item.get('user_name') or ''}: "
                   f"{item.get('reason') or ''}")
        r.rule()

    r.line(f"Chop etdi: {report.get('printed_by') or ''}")
    r.line(fmt_dt(report.get("generated_at")))
    return r.finish()


def to_b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
