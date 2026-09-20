# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""To'lov funksiyalari frontend modulining shartnoma testlari (wave 2, FE-PAYMENT).

Modul: `public/js/cashier/features/payment/` — choychaqa, chegirma va qaytarish
(to'lov oynasining o'zi — usullar ro'yxati — yadroda: `ui/payment.js`). Brauzersiz tekshiriladigan qismi: yig'ma `assets.json` da bormi,
slot/funksiya nomlari to'qnashmaydimi, JS chaqiradigan serverdagi funksiyalar
mavjud va `@frappe.whitelist()` bilanmi (parametr nomlari imzoga mos), JS
o'qiydigan kalitlar serverning `build_bill()` / `get_refundable()` javobida
bormi. Ko'rinish (1366x768 va 1024x768 ga sig'ish) va oqimlar (PIN, xato
matni) haqiqiy Chrome'da sinalgan — bu testlar ularning REGRESSIYASINI ushlaydi.
"""

import inspect
import os
import re

import frappe
from frappe.tests.utils import FrappeTestCase

from ozturkapp.ozturkapp.setup import cashier_features
from ozturkapp.ozturkapp.utils import cashier_billing, refunds

JS_ROOT = frappe.get_app_path("ozturkapp", "public", "js", "cashier")
PAYMENT_DIR = os.path.join(JS_ROOT, "features", "payment")
API_REF = re.compile(r"ozturkapp\.ozturkapp\.api\.(\w+)\.(\w+)")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _files(root, suffixes=(".js",)):
    found = []
    for folder, _dirs, names in sorted(os.walk(root)):
        for name in sorted(names):
            if name.endswith(suffixes):
                found.append(os.path.join(folder, name))
    return found


def _payment_sources() -> dict:
    """`{fayl nomi: matn}` — modulning barcha JS/CSS manbasi."""
    return {
        os.path.relpath(path, PAYMENT_DIR): _read(path)
        for path in _files(PAYMENT_DIR, (".js", ".css"))
    }


def _code(text: str) -> str:
    """Izohlarsiz kod: `/* ... */` va `// ...` olib tashlanadi (matn tekshiruvlari uchun)."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"(?<!:)//.*$", "", line) for line in text.split("\n"))


# ═══════════════════════════════════════════════════════════════════
#  JS obyekt harflarini o'qish (`screen.call(REF, { ... })` argumentlari)
# ═══════════════════════════════════════════════════════════════════

def _balanced(text: str, start: int) -> str:
    """`start` da turgan `{` dan mos `}` gacha bo'lgan matn."""
    depth, quote, at = 0, None, start
    while at < len(text):
        char = text[at]
        if quote:
            if char == "\\":
                at += 1
            elif char == quote:
                quote = None
        elif char in "\"'`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : at + 1]
        at += 1
    raise ValueError("figurali qavs yopilmagan")


def _split_top_level(body: str) -> list:
    parts, depth, quote, current = [], 0, None, ""
    for char in body:
        if quote:
            current += char
            if char == quote:
                quote = None
            continue
        if char in "\"'`":
            quote = char
        elif char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current)
    return [part.strip() for part in parts if part.strip()]


def _object_keys(literal: str) -> set:
    """`{a: 1, b, ...(cond ? {c: 2} : {})}` -> `{a, b, c}` (spread ichidagi obyektlar ham)."""
    keys = set()
    for part in _split_top_level(literal.strip()[1:-1]):
        if part.startswith("..."):
            for match in re.finditer(r"\{", part):
                keys |= _object_keys(_balanced(part, match.start()))
            continue
        key = part.split(":", 1)[0].strip().strip("\"'")
        if re.fullmatch(r"\w+", key):
            keys.add(key)
    return keys


def _call_sites(source: str) -> list:
    """`screen.call(<REF>, { ... })` chaqiruvlari: `[(python yo'li, {kalitlar})]`."""
    constants = dict(re.findall(r'const (\w+) = "(ozturkapp\.ozturkapp\.api\.\w+\.\w+)"', source))
    sites = []
    for match in re.finditer(r"\.call\(\s*(\w+|\"[^\"]+\")\s*,\s*(\{)", source):
        ref = match.group(1).strip('"')
        path = constants.get(ref, ref)
        if not API_REF.fullmatch(path):
            continue
        sites.append((path, _object_keys(_balanced(source, match.start(2)))))
    return sites


# ═══════════════════════════════════════════════════════════════════
#  Testlar
# ═══════════════════════════════════════════════════════════════════

class TestPaymentBundle(FrappeTestCase):
    """Yig'ma va reestr: sahifa uni topadi, nomlar to'qnashmaydi."""

    def test_bundle_is_built_and_discoverable(self):
        from frappe.utils import get_assets_json

        assets = get_assets_json()
        for bundle in ("cashier_payment.bundle.js", "cashier_payment.bundle.css"):
            # Sahifa kirish fayli aynan shu andoza bo'yicha izlaydi.
            self.assertRegex(bundle, r"^cashier_[a-z0-9_]+\.bundle\.(js|css)$")
            self.assertIn(bundle, assets, f"{bundle} yig'ilmagan — `bench build --app ozturkapp`")
            built = frappe.get_site_path("..", "assets", assets[bundle].replace("/assets/", "", 1))
            self.assertTrue(os.path.exists(built), f"{assets[bundle]} diskda yo'q")

    def test_slot_ids_are_unique_and_slots_exist(self):
        """Noma'lum slot yoki takroriy `id` ishga tushishda XATO beradi (`core/slots.js`)."""
        slots_js = _read(os.path.join(JS_ROOT, "core", "slots.js"))
        known = set(re.findall(r'"([^"]+)"', re.search(r"SLOT_NAMES = \[(.*?)\];", slots_js, re.S).group(1)))

        pattern = re.compile(r'slots\.contribute\(\s*"([^"]+)"\s*,\s*\{\s*(?:\/\/[^\n]*\n\s*)?id:\s*"([^"]+)"')

        mine, others = {}, {}
        for path in _files(JS_ROOT):
            target = mine if path.startswith(PAYMENT_DIR) else others
            for slot, item_id in pattern.findall(_read(path)):
                self.assertIn(slot, known, f"{path}: noma'lum slot {slot}")
                bucket = target.setdefault(slot, [])
                self.assertNotIn(item_id, bucket, f"{slot}: '{item_id}' modul ichida takrorlangan")
                bucket.append(item_id)

        self.assertTrue(mine, "modul birorta slotga element qo'shmagan")
        for slot, ids in mine.items():
            clash = set(ids) & set(others.get(slot, []))
            self.assertFalse(clash, f"{slot}: boshqa modul/yadro bilan id to'qnashdi: {clash}")

        # Vazifa bo'yicha kutilgan joylashuv.
        self.assertIn("payment-tips", mine["payment.beforeAmount"])
        self.assertIn("payment-discount", mine["panel.more"])
        self.assertIn("payment-discount-info", mine["panel.info"])
        self.assertIn("payment-reprint", mine["panel.info"])
        self.assertIn("payment-refund", mine["history.detailActions"])

    def test_feature_keys_and_flags(self):
        register = re.compile(r'features\.register\(\{\s*key:\s*"([^"]+)"(?:,\s*flag:\s*"([^"]+)")?')

        keys, mine = [], {}
        for path in _files(os.path.join(JS_ROOT, "features")):
            for key, flag in register.findall(_read(path)):
                keys.append(key)
                if path.startswith(PAYMENT_DIR):
                    mine[key] = flag
        self.assertEqual(len(keys), len(set(keys)), "funksiya kaliti takrorlangan (`features.register` xato beradi)")

        # Har biri o'z bayrog'i bilan; bayroqsiz bittasi — eskirgan chek eslatmasi.
        # Qaytarish chekining «QAYTARISH» belgisi yadroda (`ui/history.js`), bu modulda emas.
        self.assertEqual(
            mine,
            {
                "payment-tips": "tips",
                "payment-discount": "discount",
                "payment-refunds": "refunds",
                "payment-reprint-banner": "",
            },
        )
        for flag in filter(None, mine.values()):
            self.assertIn(flag, cashier_features.FEATURES, f"'{flag}' POS Profile bayrog'i emas")


class TestPaymentSourceRules(FrappeTestCase):
    """Loyiha qoidalari: Desk oynalari yo'q, frontend'da biznes mantiq yo'q."""

    def setUp(self):
        self.sources = _payment_sources()
        self.js = {name: text for name, text in self.sources.items() if name.endswith(".js")}
        self.all_code = "\n".join(_code(text) for text in self.sources.values())

    def test_no_desk_dialogs(self):
        for call in ("frappe.prompt(", "frappe.confirm(", "frappe.msgprint(", "frappe.show_alert("):
            self.assertNotIn(call, self.all_code, f"{call} o'rniga `ui.form/confirm/alert/toast`")
        # `ui.alert(` mumkin; brauzerning yalang `alert(` i — yo'q.
        self.assertIsNone(re.search(r"(?<![\w.])alert\(", self.all_code), "yalang alert() ishlatilgan")
        self.assertNotIn("window.alert(", self.all_code)

    def test_no_hardcoded_service_charge_or_number_inputs(self):
        self.assertIsNone(re.search(r"\b0\.12\b", self.all_code), "xizmat haqi foizi qattiq yozilgan")
        self.assertIsNone(re.search(r"\b12\s*%", self.all_code))
        self.assertNotIn('type="number"', self.all_code, "summa maydoni `type=number` bo'lmasin (probel)")

    def test_no_storage_or_logging_of_secrets(self):
        for name in ("localStorage", "sessionStorage", "console.log"):
            self.assertNotIn(name, self.all_code)

    def test_payment_modal_lists_every_method_as_a_row(self):
        """Chapda usul nomi, o'ngda summa: «Aralash to'lov» / «Usul qo'shish» yo'q."""
        core = _code(_read(os.path.join(JS_ROOT, "ui", "payment.js")))
        for marker in ("rc-pay__row", "rc-pay__name", "rc-pay__input", 'data-mode="${esc(m.mode_of_payment)}"'):
            self.assertIn(marker, core)
        # Eski bitta-usulli tanlagich va aralash to'lov ulanmasi yo'q.
        for gone in ("rc-mode", "setPaymentsProvider", "session.mode", "payment-split"):
            self.assertNotIn(gone, core, f"`{gone}` eski to'lov oynasidan qolgan")
        self.assertNotIn("split.js", self.sources, "aralash to'lov moduli olib tashlangan")

    def test_payment_modal_only_hints_and_the_server_decides(self):
        core = _code(_read(os.path.join(JS_ROOT, "ui", "payment.js")))
        # Naqd bo'lmagan usul ishorasi usul nomiga emas, serverdan kelgan `cash_modes` ga tayanadi.
        self.assertIn("ctx.cash_modes", core)
        self.assertIsNone(re.search(r'["\']Cash["\']', core), "naqd usul nomi qattiq yozilmasin")
        # Bir nechta usulga summa yozish POS Profile bayrog'i bilan (server ham majburlaydi).
        self.assertIn("features || {}).split_payment", core)
        # Server qoidasi bajarilmaguncha «To'lovni tasdiqlash» o'chiq turadi va bu holat
        # `screen.busy(false)` dan omon qoladi (`setConfirmEnabled`).
        self.assertIn("this.setConfirmEnabled(plan.ok)", core)
        self.assertNotIn("MutationObserver", core)
        # Yagona kiritish maydonlari ekran raqam paneliga ulanadi (ikkinchi panel yo'q).
        self.assertIn("this.mountNumpad($body, $inputs)", core)

    def test_workarounds_replaced_by_core_api_are_gone(self):
        """Yadro (`ui/payment.js`) ularni o'zi bajaradi: bo'lim xatosini ushlaydi, ustunni aylantiradi, xatoni ko'rsatadi."""
        forbidden = ("safeSection", "revealErrors", "guardConfirm", "enableConfirm", "MutationObserver")
        for name, code in ((n, _code(t)) for n, t in self.sources.items()):
            for word in forbidden:
                self.assertNotIn(word, code, f"{name}: `{word}` yadroda bor — o'chiriladi")

        self.assertNotIn("shared.js", self.sources, "bo'sh yordamchi fayl qolmasin")
        css = self.sources["cashier_payment.bundle.css"]
        self.assertNotIn(":has(", css, "ustun aylanishi yadroda (modal.css)")

    def test_tip_is_an_argument_and_never_double_counted(self):
        tips = _code(self.js["tips.js"])
        self.assertIn('setArg("tip"', tips)
        self.assertIn("session.setDue(", tips)
        # Mavjud choychaqa qatori asosdan ayriladi, aks holda ikki marta sanalardi.
        self.assertIn("session.due - this.existing", tips)
        self.assertIn("bill.tip", tips)
        self.assertIn("tip_percent_options", tips)

    def test_discount_sends_percent_or_amount_never_both_and_asks_approval(self):
        discount = _code(self.js["discount.js"])
        self.assertIsNotNone(
            re.search(r'\.\.\.\(mode === "percent" \? \{ percent: value \} : \{ amount: value \}\)', discount)
        )
        self.assertIsNotNone(
            re.search(r"ui\.withApproval\(\s*\(approval\)\s*=>\s*screen\.call\(APPLY", discount),
            "chegirma menejer tasdig'i oqimi (`ui.withApproval`) ichida yuborilishi kerak",
        )
        self.assertIn("max_cashier_discount_percent", discount)
        self.assertIn("offerReprint(", discount)

    def test_refund_always_goes_through_manager_approval(self):
        refunds_js = _code(self.js["refunds.js"])
        self.assertIsNotNone(
            re.search(r"ui\.withApproval\(\s*\(approval\)\s*=>\s*screen\.call\(REFUND", refunds_js),
            "qaytarish HAR DOIM `ui.withApproval` ichida yuborilishi kerak",
        )
        # Qaytariladigan summa oldindan JS'da hisoblanmaydi: faqat serverning javobi ko'rsatiladi.
        self.assertIn("result.refunded", refunds_js)
        self.assertNotIn("rate *", refunds_js)
        self.assertNotIn("* item.rate", refunds_js)
        self.assertIn("allow_in_returns", refunds_js)
        self.assertIn("Allow In Returns", refunds_js)

    def test_reprint_banner_offers_printing_through_the_core(self):
        reprint = _code(self.js["reprint.js"])
        self.assertIn("reprint_needed", reprint)
        self.assertIn("screen.printReceipt(", reprint)


class TestPaymentBackendReferences(FrappeTestCase):
    """JS chaqiradigan har bir serverdagi funksiya mavjud, whitelist'da va imzosi mos."""

    def setUp(self):
        self.sources = {name: text for name, text in _payment_sources().items() if name.endswith(".js")}

    def test_every_api_string_resolves_to_a_whitelisted_function(self):
        seen = set()
        for name, text in self.sources.items():
            for module, function in API_REF.findall(_code(text)):
                path = f"ozturkapp.ozturkapp.api.{module}.{function}"
                seen.add(path)
                method = frappe.get_attr(path)
                self.assertIn(method, frappe.whitelisted, f"{name}: {path} whitelist'da emas")
        # Modul ishlatishi kerak bo'lgan endpointlar (vazifa shartnomasi).
        expected = {
            "billing.apply_discount", "billing.remove_discount",
            "billing.get_refundable", "billing.refund_invoice",
        }
        self.assertTrue(expected <= {p.replace("ozturkapp.ozturkapp.api.", "") for p in seen}, seen)

    def test_call_arguments_match_the_python_signatures(self):
        checked = 0
        for name, text in self.sources.items():
            for path, keys in _call_sites(_code(text)):
                signature = inspect.signature(frappe.get_attr(path))
                params = set(signature.parameters)
                self.assertTrue(keys <= params, f"{name}: {path}: imzoda yo'q argumentlar {keys - params}")

                required = {
                    p.name for p in signature.parameters.values() if p.default is inspect.Parameter.empty
                }
                self.assertTrue(required <= keys, f"{name}: {path}: majburiy argument berilmagan {required - keys}")
                checked += 1
        self.assertGreaterEqual(checked, 4, "chaqiruvlar topilmadi — tekshiruv ishlamayapti")

    def test_call_site_parser_understands_spreads(self):
        keys = _object_keys(
            _balanced(
                'x({ invoice: a, reason, ...(m ? { percent: v } : { amount: v }), '
                '...(ap ? { approval: JSON.stringify(ap) } : {}) })',
                2,
            )
        )
        self.assertEqual(keys, {"invoice", "reason", "percent", "amount", "approval"})


class TestPaymentServerShapes(FrappeTestCase):
    """JS o'qiydigan kalitlar serverning haqiqiy javobida bor (mavjud chekda, faqat o'qish)."""

    def setUp(self):
        self.sources = {name: _code(text) for name, text in _payment_sources().items() if name.endswith(".js")}
        names = frappe.get_all(
            "POS Invoice", filters={"docstatus": 1, "is_return": 0}, pluck="name", limit_page_length=1, order_by="creation desc"
        )
        if not names:
            self.skipTest("Dev bazada to'langan chek yo'q")
        self.doc = frappe.get_doc("POS Invoice", names[0])

    def test_bill_keys_read_by_the_frontend_exist(self):
        bill = cashier_billing.build_bill(self.doc, include_kitchen=False)

        read = set()
        for text in self.sources.values():
            read |= set(re.findall(r"\bbill\.(\w+)", text))
        # `order` faqat `get_order_bill_preview` qo'shadigan qo'shimcha kalit.
        missing = read - set(bill) - {"order"}
        self.assertFalse(missing, f"build_bill() bu kalitlarni bermaydi: {missing}")
        for key in ("reprint_needed", "discount_approved_by_name", "discount_percent", "tip", "payable"):
            self.assertIn(key, bill)

    def test_refundable_shape_matches_the_dialog(self):
        info = refunds.get_refundable(self.doc)
        self.assertTrue({"invoice", "items", "paid", "refundable", "tip"} <= set(info))
        self.assertTrue(
            {"name", "item_code", "item_name", "sold_qty", "returned_qty", "refundable_qty"} <= set(info["items"][0])
        )
        self.assertTrue({"mode_of_payment", "paid", "refundable", "allow_in_returns"} <= set(info["paid"][0]))

        read_items = set(re.findall(r"\bitem\.(\w+)", self.sources["refunds.js"]))
        read_modes = set(re.findall(r"\bmode\.(\w+)", self.sources["refunds.js"]))
        self.assertFalse(read_items - set(info["items"][0]), read_items - set(info["items"][0]))
        self.assertFalse(read_modes - set(info["paid"][0]), read_modes - set(info["paid"][0]))

    def test_settings_the_frontend_reads_are_provided(self):
        settings = cashier_features.get_settings(self.doc.pos_profile)
        for key in ("tip_percent_options", "max_cashier_discount_percent"):
            self.assertIn(key, settings)
            self.assertTrue(any(key in text for text in self.sources.values()), f"{key} JS'da o'qilmaydi")
        self.assertIsInstance(settings["tip_percent_options"], list)
