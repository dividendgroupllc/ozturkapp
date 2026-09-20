# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa smenasi frontend modulining testlari (`features/shift/`).

Ishga tushirish::

    bench --site ozturk.local run-tests \
        --module ozturkapp.ozturkapp.tests.test_cashier_fe_shift

Bu yerda JS ni BRAUZERSIZ tekshiramiz: yig'ma `assets.json` da bormi, slot
identifikatorlari to'qnashmaydimi, JS chaqiradigan har bir server metodi
haqiqatan bormi va shu argumentlarni qabul qiladimi, Desk oynalari
ishlatilmaganmi va — eng muhimi — KO'R SANOQ: server `None` qilib yashiradigan
har bir maydon hisobotda "0" bo'lib chizilmasligi.

Brauzerdagi xatti-harakat (oynalar, tugmalar, toast'lar) alohida — haqiqiy
Chrome (CDP) bilan, server javoblari MOCK qilingan holda tekshirilgan.
"""

import inspect
import os
import re

import frappe
from frappe.tests.utils import FrappeTestCase

from ozturkapp.ozturkapp.doctype.ozturk_cash_movement.ozturk_cash_movement import CATEGORIES
from ozturkapp.ozturkapp.setup import cashier_features
from ozturkapp.ozturkapp.utils import shift_report

BUNDLE_JS = "cashier_shift.bundle.js"
BUNDLE_CSS = "cashier_shift.bundle.css"

#: Bu modul yoqadigan POS Profile bayroqlari.
OWN_FLAGS = {"cash_drawer", "cash_movements", "shift_reports"}

#: Slot `order` oralig'i (wave-2 shartnomasi): to'lov 100-199, buyurtma 200-299,
#: stollar 300-399, smena 400-499.
ORDER_RANGE = range(400, 500)


def _js_root():
    return frappe.get_app_path("ozturkapp", "public", "js", "cashier")


def _shift_dir():
    return os.path.join(_js_root(), "features", "shift")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _sources(folder=None, skip_features=False):
    """`{fayl nomi: matn}` — papkadagi barcha `.js` fayllar (rekursiv)."""
    root = folder or _shift_dir()
    out = {}
    for base, _dirs, files in sorted(os.walk(root)):
        if skip_features and os.path.relpath(base, _js_root()).split(os.sep)[0] == "features":
            continue
        for name in sorted(files):
            if name.endswith(".js"):
                out[os.path.relpath(os.path.join(base, name), _js_root())] = _read(
                    os.path.join(base, name)
                )
    return out


def _code(source):
    """Izohlarsiz matn — izohda tushuntirilgan so'zlar tekshiruvni buzmasin."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", source)


def _balanced(text, start, open_char, close_char):
    """`text[start] == open_char` dan mos yopuvchi belgigacha bo'lgan qism (belgilar bilan)."""
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise ValueError("qavs yopilmagan")


def _split_top_level(body):
    """Vergul bo'yicha, lekin ichki qavslar/satrlar ichida emas."""
    parts, depth, current, quote = [], 0, [], None
    for char in body:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in "\"'`":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _call_sites(source):
    """`screen.call("<metod>", {...})` chaqiruvlari: `[(metod, {argument nomlari})]`."""
    sites = []
    for match in re.finditer(r'\.call\(\s*"(ozturkapp\.ozturkapp\.api\.[\w.]+)"', source):
        rest = source[match.end() :].lstrip()
        keys = set()
        if rest.startswith(","):
            rest = rest[1:].lstrip()
            literal = _balanced(rest, 0, "{", "}")
            for item in _split_top_level(literal[1:-1]):
                if item.startswith("..."):
                    if "approvalArgs(" in item:
                        keys.add("approval")
                    keys.update(re.findall(r"(\w+)\s*:", item))
                else:
                    keys.add(re.match(r"(\w+)", item).group(1))
        sites.append((match.group(1), keys))
    return sites


class TestBundleRegistration(FrappeTestCase):
    def test_bundle_is_built_and_registered(self):
        """`bench build` yig'ma va uslubni `assets.json` ga yozgan bo'lishi shart."""
        from frappe.utils import get_assets_json

        assets = get_assets_json()
        for bundle in (BUNDLE_JS, BUNDLE_CSS):
            self.assertIn(bundle, assets, f"{bundle} yig'ilmagan — `bench build --app ozturkapp`")
            built = frappe.get_site_path("..", "assets", assets[bundle].replace("/assets/", "", 1))
            self.assertTrue(os.path.exists(built), f"{assets[bundle]} diskda yo'q")

    def test_bundle_names_follow_the_page_discovery_convention(self):
        """Sahifa yig'malarni `^cashier_[a-z0-9_]+\\.bundle\\.(js|css)$` bo'yicha topadi."""
        pattern = re.compile(r"^cashier_[a-z0-9_]+\.bundle\.(js|css)$")
        for name in (BUNDLE_JS, BUNDLE_CSS):
            self.assertRegex(name, pattern)
            self.assertTrue(os.path.exists(os.path.join(_shift_dir(), name)), name)

    def test_entry_imports_every_module(self):
        entry = _read(os.path.join(_shift_dir(), BUNDLE_JS))
        for module in ("drawer", "movements", "report"):
            self.assertIn(f'import "./{module}.js";', entry)

    def test_css_selectors_do_not_collide_with_the_core(self):
        """Yadroda `.rc-shift`, `.rc-shift-table`, `.rc-shift-input` bor: bu yerda faqat `-mv` / `-xr` bloklari."""
        css = re.sub(r"/\*.*?\*/", "", _read(os.path.join(_shift_dir(), BUNDLE_CSS)), flags=re.S)
        shift_classes = {name for name in re.findall(r"\.(rc-[\w-]+)", css) if name.startswith("rc-shift")}
        self.assertTrue(shift_classes)
        for name in shift_classes:
            self.assertRegex(name, r"^rc-shift-(?:mv|xr)(?:__|-|$)", f"{name}: yadro sinfi bilan to'qnashishi mumkin")


class TestSlotsAndFeatures(FrappeTestCase):
    SLOT_PATTERN = re.compile(
        r'slots\.contribute\(\s*"([\w.]+)",\s*\{\s*id:\s*"([^"]+)",\s*order:\s*(\d+)'
    )

    def _own_slots(self):
        found = []
        for name, source in _sources().items():
            found += [(name, *match) for match in self.SLOT_PATTERN.findall(source)]
        return found

    def test_slot_ids_are_unique_and_in_the_shift_order_range(self):
        found = self._own_slots()
        self.assertEqual(
            {(slot, ident) for _file, slot, ident, _order in found},
            {
                ("topbar.quick", "shift-drawer-quick"),
                ("shortcuts", "shift-drawer-key"),
                ("topbar.menu", "shift-movements"),
                ("topbar.menu", "shift-x-report"),
            },
        )

        pairs = [(slot, ident) for _file, slot, ident, _order in found]
        self.assertEqual(len(pairs), len(set(pairs)), "slot ichida takroriy id")
        for _file, slot, ident, order in found:
            self.assertIn(int(order), ORDER_RANGE, f"{slot}:{ident} order {order} 400-499 dan tashqarida")
            self.assertTrue(ident.startswith("shift-"), f"{ident}: `shift-` prefiksi kerak")

    def test_slot_ids_do_not_clash_with_the_core_or_other_features(self):
        """`slots.contribute` takroriy id da XATO tashlaydi — ekran yiqilmasligi uchun oldindan tekshiramiz."""
        own = {(slot, ident) for _file, slot, ident, _order in self._own_slots()}

        others = {}
        for name, source in _sources(_js_root()).items():
            if name.startswith(os.path.join("features", "shift")):
                continue
            for slot, ident in re.findall(
                r'slots\.contribute\(\s*"([\w.]+)",\s*\{\s*id:\s*"([^"]+)"', source
            ):
                others[(slot, ident)] = name

        for pair in own:
            self.assertNotIn(pair, others, f"{pair} boshqa modulda ({others.get(pair)}) ham bor")

    def test_slot_names_exist_in_the_core_registry(self):
        registry = _read(os.path.join(_js_root(), "core", "slots.js"))
        names = set(re.findall(r'^\t"([\w.]+)",$', registry, flags=re.M))
        for _file, slot, _ident, _order in self._own_slots():
            self.assertIn(slot, names, f"noma'lum slot: {slot}")

    def test_features_have_unique_keys_and_real_server_flags(self):
        keys, flags = [], []
        for source in _sources().values():
            for block in re.findall(r"features\.register\(\{(.*?)install\(", source, flags=re.S):
                keys.append(re.search(r'key:\s*"([^"]+)"', block).group(1))
                flags.append(re.search(r'flag:\s*"([^"]+)"', block).group(1))

        self.assertEqual(sorted(flags), sorted(OWN_FLAGS), "har bir funksiya o'z bayrog'i bilan")
        self.assertEqual(len(keys), len(set(keys)))
        for flag in flags:
            self.assertIn(flag, cashier_features.FEATURES, f"{flag} — serverda bunday bayroq yo'q")

    def test_f9_belongs_to_the_drawer_feature_only(self):
        drawer = _read(os.path.join(_shift_dir(), "drawer.js"))
        self.assertEqual(drawer.count('key: "F9"'), 1)
        # F9 yorlig'i FAQAT `cash_drawer` yoqilganda o'rnatiladigan `install()` ichida.
        self.assertLess(drawer.index('flag: "cash_drawer"'), drawer.index('key: "F9"'))

        for name, source in _sources().items():
            if not name.endswith("drawer.js"):
                self.assertNotIn('"F9"', source, name)

        # Boshqa yorliqlar bilan to'qnashmaydi.
        taken = set()
        for source in _sources(_js_root(), skip_features=True).values():
            taken.update(re.findall(r'\bkey:\s*"([^"]+)"', source))
        self.assertNotIn("F9", taken)

    def test_shortcut_and_button_appear_only_with_an_open_shift(self):
        drawer = _read(os.path.join(_shift_dir(), "drawer.js"))
        self.assertIn("when: shiftIsOpen", drawer)
        self.assertIn("shiftIsOpen(screen) && noOverlay(screen)", drawer)
        for name in ("movements.js", "report.js"):
            self.assertIn("when: shiftIsOpen", _read(os.path.join(_shift_dir(), name)))


class TestBackendReferences(FrappeTestCase):
    """JS chaqiradigan har bir server metodi mavjud, whitelisted va argumentlari to'g'ri."""

    #: Server holatini o'zgartiradigan metodlar faqat POST.
    MUTATING = {"printing.open_drawer", "printing.print_shift_report", "cash_movements.create_cash_movement"}

    def _sites(self):
        sites = []
        for source in _sources().values():
            sites += _call_sites(_code(source))
        return sites

    def test_every_call_site_was_found(self):
        methods = sorted(method.split("api.", 1)[1] for method, _keys in self._sites())
        self.assertEqual(
            methods,
            sorted(
                [
                    "cash_movements.create_cash_movement",
                    "cash_movements.get_cash_movements",
                    "cashier.get_shift_report",
                    "printing.open_drawer",
                    "printing.print_shift_report",
                ]
            ),
        )

    def test_methods_resolve_and_are_whitelisted(self):
        for method, _keys in self._sites():
            function = frappe.get_attr(method)
            self.assertIn(function, frappe.whitelisted, f"{method} whitelisted emas")

            short = method.split("api.", 1)[1]
            allowed = frappe.allowed_http_methods_for_whitelisted_func[function]
            if short in self.MUTATING:
                self.assertEqual(list(allowed), ["POST"], f"{short} faqat POST bo'lishi kerak")

    def test_arguments_exist_in_the_python_signature_and_required_ones_are_passed(self):
        for method, keys in self._sites():
            parameters = inspect.signature(frappe.get_attr(method)).parameters
            for key in keys:
                self.assertIn(key, parameters, f"{method}: JS `{key}` yuboradi, Python'da bunday argument yo'q")

            required = {
                name
                for name, parameter in parameters.items()
                if parameter.default is inspect.Parameter.empty
            }
            self.assertFalse(required - keys, f"{method}: majburiy argument yuborilmagan: {required - keys}")

    def test_movements_show_the_approver_name_not_the_user_id(self):
        """Server `approved_by_name` beradi; yo'q bo'lsa (eski javob) foydalanuvchi nomi."""
        code = _code(_read(os.path.join(_shift_dir(), "movements.js")))
        self.assertIn("item.approved_by_name || item.approved_by", code)
        # Foydalanuvchi identifikatori tooltip'da qoladi.
        self.assertIn('title="${esc(item.approved_by || "")}"', code)

    def test_movement_categories_fall_back_to_the_doctype_options(self):
        """Server `categories` bermasa ishlatiladigan zaxira ro'yxat DocType bilan bir xil."""
        source = _read(os.path.join(_shift_dir(), "movements.js"))
        block = re.search(r"const FALLBACK_CATEGORIES = \{(.*?)\};", source, flags=re.S).group(1)
        fallback = {
            kind: re.findall(r'"([^"]+)"', values)
            for kind, values in re.findall(r"(\w+):\s*\[(.*?)\]", block)
        }
        self.assertEqual(fallback, {kind: list(names) for kind, names in CATEGORIES.items()})

        doctype = frappe.get_meta("Ozturk Cash Movement").get_field("category").options.split("\n")
        self.assertEqual(sorted(set(doctype)), sorted({name for names in CATEGORIES.values() for name in names}))


class TestNoDeskDialogs(FrappeTestCase):
    FORBIDDEN = re.compile(
        r"frappe\.(?:prompt|confirm|msgprint|show_alert|throw)\b|(?<![\w.])(?:alert|confirm|prompt)\("
    )

    def test_sources_use_only_the_ui_kit(self):
        for name, source in _sources().items():
            self.assertIsNone(self.FORBIDDEN.search(_code(source)), f"{name}: Desk oynasi ishlatilgan")

    def test_no_hard_coded_percent_or_client_side_money_maths(self):
        for name, source in _sources().items():
            code = _code(source)
            self.assertIsNone(re.search(r"\b0\.12\b|\b12\s*%|\.toFixed\(|\.reduce\(", code), name)

    def test_apostrophes_in_translatable_strings_are_plain(self):
        """`__("...'...")` — faqat oddiy apostrof (U+0027); tipografik `’` emas."""
        for name, source in _sources().items():
            self.assertNotIn("’", source, name)
            self.assertNotIn("‘", source, name)


class TestBlindCount(FrappeTestCase):
    """Kassir hech qachon kutilgan summa, farq, savdo jami yoki g'aladon qoldig'ini ko'rmasligi kerak."""

    def _report(self):
        return _read(os.path.join(_shift_dir(), "report.js"))

    def test_every_field_the_server_can_null_is_hidden_not_zeroed(self):
        """Yashiriladigan maydonlar RO'YXATI serverdan olinadi (`utils/shift_report.py`).

        Server yangi maydonni `None` qilsa bu test yiqiladi — frontend uni
        chizishda `None` ni yashirishi shart.
        """
        assemble = inspect.getsource(shift_report._assemble)
        restricted_block = assemble.split("if not full:", 1)[1].split("return {", 1)[0]
        # `sales` — `dict.fromkeys(SALES_KEYS)`; qolgani `row["..."] = ... = None`.
        self.assertIn("dict.fromkeys(SALES_KEYS)", restricted_block)
        assignments = re.findall(r'((?:\w+\["\w+"\]\s*=\s*)+)None', restricted_block)
        nulled_nested = {key for chain in assignments for key in re.findall(r'\["(\w+)"\]', chain)}

        cash = inspect.getsource(shift_report._cash)
        not_closed = set(re.findall(r'"(\w+)":[^\n]*if closed else None', cash))

        self.assertEqual(
            nulled_nested,
            {"sales_amount", "refund_amount", "net_amount", "expected", "difference"},
            "server yangi yashiriladigan maydon qo'shdi — report.js ni yangilang",
        )
        self.assertEqual(not_closed, {"counted", "difference"})

        source = self._report()
        for key in shift_report.SALES_KEYS:
            self.assertIn(f'key: "{key}"', source, f"sales.{key} chizilmaydi")
        for key in ("expected", "difference", "counted"):
            self.assertIn(f'key: "{key}"', source, f"cash.{key} chizilmaydi")
        for key in ("sales_amount", "refund_amount", "net_amount"):
            self.assertIn(f"cell(screen, pay.{key})", source, f"payments[].{key} chizilmaydi")

    def test_summa_is_rendered_through_one_none_aware_helper(self):
        """`money()` YAGONA joyda chaqiriladi va u `None` da `null` qaytaradi — «0» yo'q."""
        code = _code(self._report())
        self.assertEqual(code.count(".money("), 1)
        self.assertRegex(
            code,
            r"function amount\(screen, value\) \{\s*return isHidden\(value\) \? null : screen\.money\(value\);\s*\}",
        )
        shared = _code(_read(os.path.join(_shift_dir(), "shared.js")))
        self.assertRegex(
            shared, r"function isHidden\(value\) \{\s*return value === null \|\| value === undefined;\s*\}"
        )

        # `None` ni 0 ga aylantiradigan yo'llar yo'q: `|| 0`, `flt()/cint()` bilan yashirilgan maydon.
        hidden = "sales_amount|refund_amount|net_amount|expected|difference|counted"
        self.assertIsNone(re.search(rf"(?:{hidden})\s*\|\|\s*0", code))
        self.assertIsNone(re.search(rf"(?:flt|cint|parseFloat|Number)\([^)]*(?:{hidden})", code))

    def test_restricted_report_explains_what_is_hidden(self):
        source = self._report()
        self.assertIn("report.restricted", source)
        self.assertIn("Kutilayotgan summa va farq faqat menejerga ko'rinadi", source)

    def test_movements_and_drawer_never_read_totals_or_balances(self):
        for name in ("movements.js", "drawer.js"):
            code = _code(_read(os.path.join(_shift_dir(), name)))
            self.assertIsNone(
                re.search(r"total_in|total_out|expected|balance|qoldiq|\.count\b", code),
                f"{name}: jami/qoldiq o'qilgan",
            )

    def test_no_running_total_helpers_exist(self):
        """Ro'yxatda faqat har bir harakatning o'z summasi: yig'indi hisoblanmaydi."""
        code = _code(_read(os.path.join(_shift_dir(), "movements.js")))
        self.assertNotRegex(code, r"\+=|\breduce\b|\bsum\b")


def _skip_string(code, index):
    quote = code[index]
    index += 1
    while code[index] != quote:
        index += 2 if code[index] == "\\" else 1
    return index + 1


def _template_end(code, index):
    """`code[index]` — ochuvchi backtick; qaytadi: yopuvchi backtick indeksi."""
    index += 1
    while True:
        char = code[index]
        if char == "\\":
            index += 2
        elif char == "`":
            return index
        elif code.startswith("${", index):
            index = _expression_end(code, index + 1) + 1
        else:
            index += 1


def _expression_end(code, index):
    """`code[index] == "{"` — mos yopuvchi `}` indeksi (ichma-ich satr va shablonlar bilan)."""
    depth = 0
    while True:
        char = code[index]
        if char in "\"'":
            index = _skip_string(code, index)
            continue
        if char == "`":
            index = _template_end(code, index) + 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1


def _templates(code):
    """Yuqori darajadagi shablon satrlari: `[(boshi, oxiri)]` (backtick indekslari)."""
    spans, index = [], 0
    while index < len(code):
        if code[index] in "\"'":
            index = _skip_string(code, index)
        elif code[index] == "`":
            end = _template_end(code, index)
            spans.append((index, end))
            index = end + 1
        else:
            index += 1
    return spans


def _leaves(code, start, end):
    """Shablon ichidagi `${...}` ifodalar; ichma-ich shablonli ifodalar o'rniga ularning ichidagilari."""
    out, index = [], start + 1
    while index < end:
        if code[index] == "\\":
            index += 2
        elif code.startswith("${", index):
            close = _expression_end(code, index + 1)
            expression = code[index + 2 : close]
            nested = _templates(expression)
            if nested:
                for inner_start, inner_end in nested:
                    out += _leaves(expression, inner_start, inner_end)
            else:
                out.append(re.sub(r"\s+", " ", expression).strip())
            index = close + 1
        else:
            index += 1
    return out


class TestHtmlEscaping(FrappeTestCase):
    """Serverdan kelgan matn HTML'ga FAQAT `esc()` (yoki son/summa yordamchilari) orqali tushadi."""

    #: To'liq ifodani o'rab turuvchi xavfsiz chaqiruvlar: `esc`/`cint` ekranlaydi,
    #: `cell`/`line`/`section`/... esa faqat shu qoida bilan qurilgan HTML qaytaradi.
    WRAPPERS = (
        "esc", "cint", "cell", "section", "paymentsHtml", "movementsHtml", "drawerHtml",
    )
    #: Qat'iy literal yoki oldindan ekranlangan qiymatlar (har biri qo'lda ko'rib chiqilgan).
    SAFE = {
        "columns", "tone", "modifier", "inner",
        'isIn ? "▲" : "▼"', 'isIn ? "in" : "out"', 'isIn ? "+" : "−"',
        'strong ? " rc-shift-xr__line--strong" : ""',
    }

    def _wrapped(self, expression):
        for name in self.WRAPPERS:
            if expression.startswith(f"{name}("):
                return _balanced(expression, len(name), "(", ")") == expression[len(name) :]
        return False

    def test_interpolations_are_escaped_or_known_safe(self):
        checked = 0
        for name, source in _sources().items():
            code = _code(source)
            for start, end in _templates(code):
                if "<" not in code[start:end]:
                    continue  # HTML emas (masalan toast matni): `textContent` bilan chiziladi
                for expression in _leaves(code, start, end):
                    checked += 1
                    self.assertTrue(
                        expression in self.SAFE or self._wrapped(expression),
                        f"{name}: ekranlanmagan ifoda: {expression[:90]!r}",
                    )
        self.assertGreater(checked, 40, "shablon ifodalari topilmadi — tekshiruv bo'sh o'tib ketdi")

    def test_inner_html_only_receives_our_own_templates(self):
        for name, source in _sources().items():
            for match in re.finditer(r"innerHTML\s*=\s*([^;]+);", _code(source)):
                value = match.group(1).strip()
                self.assertRegex(
                    value,
                    r"^(?:`|listHtml\(|reportHtml\(|emptyHtml\()",
                    f"{name}: `innerHTML` ga shablon bo'lmagan qiymat: {value[:60]!r}",
                )


class TestZReportFeedback(FrappeTestCase):
    """`ui/shift.js` dagi yagona yadro tahriri: smena yopilgach Z-hisobot holati."""

    def setUp(self):
        self.source = _read(os.path.join(_js_root(), "ui", "shift.js"))

    def test_toast_follows_the_successful_close_call(self):
        call = self.source.index('const result = await this.call("ozturkapp.ozturkapp.api.cashier.close_shift"')
        closed_toast = self.source.index('ui.toast(__("Kassa yopildi"))')
        queued = self.source.index("result.z_report_queued === true")
        failed = self.source.index("result.z_report_queued === false")
        self.assertLess(call, closed_toast)
        self.assertLess(closed_toast, queued)
        self.assertLess(queued, failed)

    def test_toast_is_gated_by_the_shift_reports_flag(self):
        self.assertIn("(this.ctx.features || {}).shift_reports && result", self.source)
        self.assertIn('ui.toast(__("Z-hisobot chop etilmoqda"))', self.source)
        self.assertIn("Z-hisobot printerga yuborilmadi — menejerga xabar bering", self.source)
        # Aniq `false` — `undefined` (eski server) da ogohlantirish chiqmaydi.
        self.assertNotRegex(self.source, r"if \(!result\.z_report_queued\)")

    def test_close_shift_returns_the_flag_the_ui_reads(self):
        from ozturkapp.ozturkapp.api import cashier

        self.assertIn('result["z_report_queued"]', inspect.getsource(cashier.close_shift))
