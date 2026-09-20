# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa oynasi — «Stollar» funksiya moduli (stolni ko'chirish/birlashtirish/
ajratish va bronlar) frontend testlari.

Brauzersiz tekshiriladi: yig'ma, slot shartnomasi va serverga murojaatlar.
Ko'rinish va xatti-harakat haqiqiy Chrome (CDP) bilan alohida tekshirilgan.

Ishga tushirish::

    bench --site ozturk.local run-tests \
        --module ozturkapp.ozturkapp.tests.test_cashier_fe_tables
"""

import inspect
import os
import re

import frappe
from frappe.tests.utils import FrappeTestCase

from ozturkapp.ozturkapp.setup import cashier_features

BUNDLE_JS = "cashier_tables.bundle.js"
BUNDLE_CSS = "cashier_tables.bundle.css"

#: Stollar modulining `panel.more` / `topbar.menu` tartib oralig'i (wave-2 shartnomasi).
ORDER_RANGE = range(300, 400)

CALL_SITE = re.compile(r"screen\.call\(\s*METHODS\.(\w+)\s*,\s*\{(.*?)\}\s*\)", re.S)
CONTRIBUTE = re.compile(
    r'slots\.contribute\(\s*"([\w.]+)"\s*,\s*\{\s*id:\s*"([\w-]+)"(?:\s*,\s*order:\s*(\d+))?'
)


def _js_root():
    return frappe.get_app_path("ozturkapp", "public", "js", "cashier")


def _feature_dir():
    return os.path.join(_js_root(), "features", "tables")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _own_js():
    """{fayl nomi: matn} — modul papkasidagi barcha JS."""
    folder = _feature_dir()
    return {
        name: _read(os.path.join(folder, name))
        for name in sorted(os.listdir(folder))
        if name.endswith(".js")
    }


def _code_only(source):
    """Izohlarsiz matn (qator izohlari va `/* */` bloklari olib tashlanadi)."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return "\n".join(re.sub(r"(?<!:)//.*$", "", line) for line in source.split("\n"))


def _methods():
    """`METHODS = {...}` jadvali: {kalit: 'ozturkapp....funksiya'}."""
    source = _own_js()["table_data.js"]
    block = re.search(r"export const METHODS = \{(.*?)\n\};", source, re.S).group(1)
    return dict(re.findall(r'(\w+):\s*"([\w.]+)"', block))


def _top_level_keys(body):
    """Ob'ekt literali tanasidagi kalitlar (ichma-ich qavs/qator ichidagi vergullar hisobga olinmaydi)."""
    parts, depth, quote, current = [], 0, None, ""
    for char in body:
        if quote:
            current += char
            if char == quote:
                quote = None
            continue
        if char in "\"'`":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(current)
            current = ""
            continue
        current += char
    parts.append(current)

    keys = []
    for part in parts:
        match = re.match(r"\s*(\w+)\s*(?::|$)", part)
        if match:
            keys.append(match.group(1))
    return keys


class TestTablesBundleIsBuilt(FrappeTestCase):
    def test_bundle_is_registered_in_assets_json(self):
        """`bench build` yig'mani `assets.json` ga yozgan bo'lishi shart."""
        from frappe.utils import get_assets_json

        assets = get_assets_json()
        for name in (BUNDLE_JS, BUNDLE_CSS):
            self.assertIn(name, assets, f"{name} yig'ilmagan — `bench build --app ozturkapp`")
            built = frappe.get_site_path("..", "assets", assets[name].replace("/assets/", "", 1))
            self.assertTrue(os.path.exists(built), f"{assets[name]} diskda yo'q")

    def test_bundle_name_is_found_by_the_page_entry(self):
        """Sahifa `cashier_<kichik_harf_raqam_>.bundle.(js|css)` andozasi bo'yicha topadi."""
        for name in (BUNDLE_JS, BUNDLE_CSS):
            self.assertRegex(name, r"^cashier_[a-z0-9_]+\.bundle\.(js|css)$")
            self.assertTrue(os.path.exists(os.path.join(_feature_dir(), name)), name)


class TestTablesSlotContract(FrappeTestCase):
    def _contributions(self):
        found = []
        for source in _own_js().values():
            found += CONTRIBUTE.findall(_code_only(source))
        return found

    def test_slot_ids_are_unique_per_slot_and_in_range(self):
        seen = set()
        contributions = self._contributions()
        self.assertGreaterEqual(len(contributions), 5)

        for slot, item_id, order in contributions:
            self.assertNotIn((slot, item_id), seen, f"{slot}/{item_id} ikki marta qo'shilgan")
            seen.add((slot, item_id))
            self.assertIn(int(order), ORDER_RANGE, f"{slot}/{item_id}: tartib 300-399 oralig'ida emas")

    def test_slot_names_exist(self):
        slots_js = _read(os.path.join(_js_root(), "core", "slots.js"))
        known = set(re.findall(r'"([\w.]+)"', re.search(r"SLOT_NAMES = \[(.*?)\];", slots_js, re.S).group(1)))
        for slot, _item_id, _order in self._contributions():
            self.assertIn(slot, known, f"noma'lum slot: {slot}")

    def test_ids_do_not_clash_with_core_or_other_features(self):
        """Slot ichida takror id `slots.contribute` da xato tashlaydi — sahifa ochilmay qoladi."""
        mine = {(slot, item_id) for slot, item_id, _ in self._contributions()}

        others = set()
        for folder, _dirs, files in os.walk(_js_root()):
            if os.path.abspath(folder) == os.path.abspath(_feature_dir()):
                continue
            for name in files:
                if name.endswith(".js"):
                    others |= {
                        (slot, item_id)
                        for slot, item_id, _order in CONTRIBUTE.findall(
                            _code_only(_read(os.path.join(folder, name)))
                        )
                    }
        self.assertFalse(mine & others, f"boshqa modul bilan to'qnashuv: {mine & others}")

    def test_core_reserve_actions_are_not_reused(self):
        """Yadroning `reserve` / `unreserve` tugmalariga TEGILMAYDI va id lari qayta ishlatilmaydi."""
        for source in _own_js().values():
            self.assertNotRegex(_code_only(source), r'id:\s*"(reserve|unreserve)"')

    def test_features_are_registered_with_the_right_flags(self):
        entry = _read(os.path.join(_feature_dir(), "cashier_tables.bundle.js"))
        registered = dict(
            re.findall(r'features\.register\(\{\s*key:\s*"(\w+)"(?:,\s*flag:\s*"(\w+)")?', entry)
        )
        self.assertEqual(set(registered), {"table_transfer", "table_reservations"})

        # Ko'chirish/birlashtirish POS Profile bayrog'i bilan (reestrda bor kalit), bronlar — bayroqsiz.
        self.assertEqual(registered["table_transfer"], "table_transfer")
        self.assertIn("table_transfer", cashier_features.FEATURES)
        self.assertEqual(registered["table_reservations"], "")


class TestTablesBackendReferences(FrappeTestCase):
    """Frontend chaqiradigan har bir server funksiyasi haqiqatan bor va ochiq."""

    def test_every_referenced_method_resolves_and_is_whitelisted(self):
        methods = _methods()
        self.assertGreaterEqual(len(methods), 8)

        for key, path in methods.items():
            self.assertRegex(path, r"^ozturkapp\.ozturkapp\.api\.\w+\.\w+$", key)
            function = frappe.get_attr(path)
            self.assertIn(function, frappe.whitelisted, f"{path} whitelist qilinmagan")
            self.assertIn(
                "POST",
                frappe.allowed_http_methods_for_whitelisted_func[function],
                f"{path} POST ni qabul qilmaydi (screen.call POST yuboradi)",
            )

    def test_no_method_string_outside_the_table(self):
        """Server nomlari FAQAT `METHODS` jadvalida turadi — test ularni shu yerdan o'qiydi."""
        for name, source in _own_js().items():
            if name == "table_data.js":
                continue
            self.assertNotRegex(_code_only(source), r"ozturkapp\.ozturkapp\.api\.", name)

    def test_every_call_passes_only_parameters_the_function_accepts(self):
        methods = _methods()
        checked = set()

        for name, source in _own_js().items():
            code = _code_only(source)
            for key, body in CALL_SITE.findall(code):
                self.assertIn(key, methods, f"{name}: METHODS.{key} yo'q")

                accepted = set(inspect.signature(frappe.get_attr(methods[key])).parameters)
                passed = set(_top_level_keys(body))
                self.assertTrue(
                    passed <= accepted,
                    f"{name}: {methods[key]} ga noma'lum parametr yuborilyapti: {passed - accepted}",
                )
                checked.add(key)

        # Har bir jadval yozuvi haqiqatan chaqiriladi va tekshirilgan (o'lik yozuv yo'q).
        self.assertEqual(checked, set(methods))

    def test_every_call_goes_through_the_methods_table(self):
        """`screen.call("...literal...")` yo'q: parametr tekshiruvi shu andozaga tayanadi."""
        for name, source in _own_js().items():
            self.assertNotRegex(_code_only(source), r'screen\.call\(\s*["\'`]', name)

    def test_required_parameters_are_always_sent(self):
        """Majburiy (standartsiz) parametrlar har bir chaqiruvda bor."""
        methods = _methods()
        for name, source in _own_js().items():
            for key, body in CALL_SITE.findall(_code_only(source)):
                parameters = inspect.signature(frappe.get_attr(methods[key])).parameters
                required = {
                    p for p, spec in parameters.items() if spec.default is inspect.Parameter.empty
                }
                self.assertTrue(
                    required <= set(_top_level_keys(body)),
                    f"{name}: {methods[key]} majburiy parametrlari: {required}",
                )

    def test_reservations_are_not_feature_gated_on_the_server(self):
        """Frontend bronlarni bayroqsiz ko'rsatadi — server ham bayroq so'ramasligi kerak."""
        from ozturkapp.ozturkapp.api import table as table_api

        for name in ("get_reservations", "reserve_table", "cancel_reservation"):
            self.assertNotIn("assert_enabled", inspect.getsource(getattr(table_api, name)), name)

    def test_transfer_actions_are_gated_by_the_same_flag(self):
        from ozturkapp.ozturkapp.api import table as table_api

        self.assertIn('"table_transfer"', inspect.getsource(table_api._prepare_table_operation))

    def test_reservation_parameters_match_the_backend_contract(self):
        """Yangi bron `guests` va `reservation_date` ni yuboradi (pax emas)."""
        source = _code_only(_own_js()["reservations.js"])
        body = next(b for k, b in CALL_SITE.findall(source) if k == "reserve")
        keys = set(_top_level_keys(body))
        self.assertTrue({"table", "customer_name", "guests", "from_time", "reservation_date"} <= keys)
        self.assertNotIn("pax", keys)


class TestTablesSourceRules(FrappeTestCase):
    def _all_sources(self):
        sources = dict(_own_js())
        sources[BUNDLE_CSS] = _read(os.path.join(_feature_dir(), BUNDLE_CSS))
        return sources

    def test_no_desk_or_native_dialogs(self):
        for name, source in self._all_sources().items():
            code = _code_only(source)
            for call in ("frappe.prompt(", "frappe.confirm(", "frappe.msgprint(", "frappe.show_alert("):
                self.assertNotIn(call, code, f"{name}: {call}")
            self.assertNotRegex(code, r"(?<![\w.])(alert|confirm|prompt)\(", name)
            self.assertNotIn("window.alert(", code, name)

    def test_no_hardcoded_service_charge_or_money_math(self):
        """Foiz va summa hisobi serverda; bu modulda `0.12` ham, `%` hisobi ham yo'q."""
        for name, source in _own_js().items():
            code = _code_only(source)
            self.assertNotRegex(code, r"\b0\.12\b", name)
            self.assertNotRegex(code, r"\*\s*0\.\d+|/\s*100\b", name)

    def test_html_templates_escape_server_text(self):
        """Serverdan kelgan qiymat HTML tegi/atributi ichiga faqat `esc()` orqali tushadi.

        Xom `${row.x}` (to'g'ridan-to'g'ri maydon) faqat oddiy matn yig'ishda, ya'ni HTML
        belgisiz qatorda bo'lishi mumkin — keyin u butunligicha `esc()` dan o'tadi.
        """
        for name in ("reservations.js", "table_picker.js"):
            for line in _code_only(_own_js()[name]).split("\n"):
                if re.search(r"\$\{\s*(row|entry|reservation)\.[\w.]+\s*\}", line):
                    self.assertNotRegex(line, r'[<>]|="', f"{name}: ekranlanmagan qiymat: {line.strip()}")

    def test_time_formatting_uses_the_core_helper(self):
        """Server `9:00:00` (nol qo'yilmagan soat) qaytaradi: `slice(0, 5)` `9:00:` beradi.

        Vaqtni yagona `ozturk.cashier.util.hhmm` normallashtiradi (yadro testi uni haqiqiy JS'da
        tekshiradi); modul o'zining nusxasini yozmaydi va qo'lda qirqmaydi.
        """
        for name, source in _own_js().items():
            code = _code_only(source)
            self.assertNotRegex(code, r"function hhmm\(|export function hhmm", f"{name}: hhmm nusxasi")
            self.assertNotIn(".slice(0, 5)", code, f"{name}: vaqt qo'lda qirqilgan")

        for name in ("table_data.js", "table_ops.js", "reservations.js"):
            self.assertRegex(_code_only(_own_js()[name]), r"const \{[^}]*\bhhmm\b[^}]*\} = (?:ozturk\.cashier\.)?util", name)
        self.assertNotIn("hhmm,", _code_only(_own_js()["table_data.js"]).split("export const METHODS")[0])

    def test_touch_targets_are_at_least_48px(self):
        css = _read(os.path.join(_feature_dir(), BUNDLE_CSS))
        heights = [int(value) for value in re.findall(r"min-height:\s*(\d+)px", css)]
        self.assertTrue(heights)
        self.assertTrue(all(value >= 48 for value in heights), heights)
        # Zal tugmalari va bekor qilish tugmasi yadroning 48px o'zgaruvchisidan foydalanadi.
        self.assertIn("var(--rc-touch)", css)

    def test_bundle_selectors_are_namespaced(self):
        css = _read(os.path.join(_feature_dir(), BUNDLE_CSS))
        selectors = re.findall(r"^([.#][^{\n]+?)\s*\{", css, re.M)
        self.assertTrue(selectors)
        for selector in selectors:
            for part in selector.split(","):
                self.assertRegex(part.strip(), r"^\.rc-(tt|tres|tstep)(?![a-z])", selector)
