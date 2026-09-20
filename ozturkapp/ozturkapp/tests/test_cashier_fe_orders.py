# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassadan buyurtma va mijoz frontend modulining testlari (`features/orders/`).

Ishga tushirish::

    bench --site ozturk.local run-tests \
        --module ozturkapp.ozturkapp.tests.test_cashier_fe_orders

Bu yerda JS ni BRAUZERSIZ tekshiramiz: yig'ma `assets.json` da bormi, slot
identifikatorlari to'qnashmaydimi, JS chaqiradigan har bir server metodi
haqiqatan bormi va aynan shu argumentlarni qabul qiladimi, JS o'qiydigan
javob kalitlarini server haqiqatan qaytaradimi, serverdan kelgan matn HTML'ga
ekranlanmasdan tushmaydimi va narx HECH QACHON yuborilmaydimi.

Brauzerdagi xatti-harakat (oyna, savat, klaviatura, xato holatlari) alohida —
haqiqiy Chrome (CDP) bilan, server javoblari MOCK qilingan holda tekshirilgan.
"""

import inspect
import os
import re

import frappe
from frappe.tests.utils import FrappeTestCase

from ozturkapp.ozturkapp.api import cashier_orders
from ozturkapp.ozturkapp.setup import cashier_features

BUNDLE_JS = "cashier_orders.bundle.js"
BUNDLE_CSS = "cashier_orders.bundle.css"

#: Bu modul yoqadigan POS Profile bayroqlari (`order_view` bayroqsiz — faqat ko'rsatadi).
FEATURE_FLAGS = {
    "cashier_orders": "cashier_orders",
    "customer_attach": "customer_attach",
    "order_view": None,
}

#: Slot `order` oralig'i (wave-2 shartnomasi): to'lov 100-199, buyurtma 200-299,
#: stollar 300-399, smena 400-499.
ORDER_RANGE = range(200, 300)

#: Modul chaqiradigan server metodlarining TO'LIQ ro'yxati.
EXPECTED_ENDPOINTS = {
    "cashier_orders.get_menu",
    "cashier_orders.create_order",
    "cashier_orders.add_items",
    "cashier_orders.remove_item",
    "cashier_orders.search_customers",
    "cashier_orders.create_customer",
    "cashier_orders.set_customer",
    "table.get_floor_plan",
    "order.get_order_bill_preview",
}


def _js_root():
    return frappe.get_app_path("ozturkapp", "public", "js", "cashier")


def _orders_dir():
    return os.path.join(_js_root(), "features", "orders")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _sources(folder=None):
    """`{fayl nomi: matn}` — papkadagi barcha `.js` fayllar (rekursiv)."""
    root = folder or _orders_dir()
    out = {}
    for base, _dirs, files in sorted(os.walk(root)):
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


def _all_code():
    return "\n".join(_code(body) for body in _sources().values())


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


def _call_sites():
    """`screen.call("<metod>", ...{ argumentlar })`: `[(modul.funksiya, {argument nomlari})]`.

    Argumentlar `present({...})` ichida ham bo'lishi mumkin — sonlarni saqlab qolish
    uchun birinchi `{...}` ob'ektining yuqori daraja kalitlari olinadi.
    """
    sites = []
    source = _code(_read(os.path.join(_orders_dir(), "api.js")))
    for match in re.finditer(r'"ozturkapp\.ozturkapp\.api\.(\w+)\.(\w+)"', source):
        rest = source[match.end() :]
        literal = _balanced(rest, rest.index("{"), "{", "}")
        keys = set()
        for item in _split_top_level(literal[1:-1]):
            assert not item.startswith("..."), f"{match.group(0)}: spread argumentni tekshirib bo'lmaydi"
            keys.add(re.match(r"(\w+)", item).group(1))
        sites.append((f"{match.group(1)}.{match.group(2)}", keys))
    return sites


def _slot_items():
    """Barcha `slots.contribute("<slot>", { id, order })` — `[(slot, id, order)]`."""
    items = []
    source = _code(_read(os.path.join(_orders_dir(), BUNDLE_JS)))
    for match in re.finditer(r'slots\.contribute\(\s*"([\w.]+)"\s*,\s*\{', source):
        literal = _balanced(source, match.end() - 1, "{", "}")
        item_id = re.search(r'\bid:\s*"([\w-]+)"', literal).group(1)
        order = int(re.search(r"\border:\s*(\d+)", literal).group(1))
        items.append((match.group(1), item_id, order))
    return items


def _interpolations(source):
    """Shablon satrlaridagi barcha `${...}` ifodalari."""
    out, at = [], 0
    while True:
        at = source.find("${", at)
        if at < 0:
            return out
        end = _balanced(source, at + 1, "{", "}")
        out.append(re.sub(r"\s+", " ", end[1:-1]).strip())
        at += 1 + len(end)


class TestBundleRegistration(FrappeTestCase):
    def test_bundle_is_built_and_registered(self):
        """`bench build` yig'ma va uslubni `assets.json` ga yozgan bo'lishi shart."""
        from frappe.utils import get_assets_json

        assets = get_assets_json()
        for bundle in (BUNDLE_JS, BUNDLE_CSS):
            self.assertIn(bundle, assets, f"{bundle} yig'ilmagan — `bench build --app ozturkapp`")
            built = frappe.get_site_path("..", "assets", assets[bundle].replace("/assets/", "", 1))
            self.assertTrue(os.path.exists(built), f"{assets[bundle]} diskda yo'q")

    def test_entry_names_follow_the_page_loader_convention(self):
        """Sahifa yig'malarni `cashier_<nom>.bundle.(js|css)` andozasi bo'yicha topadi."""
        entry = _read(
            frappe.get_app_path(
                "ozturkapp", "ozturkapp", "page", "restaurant_cashier", "restaurant_cashier.js"
            )
        )
        pattern = re.search(r"/(\^cashier_[^/]+\$)/\.test", entry).group(1)
        for name in (BUNDLE_JS, BUNDLE_CSS):
            self.assertRegex(name, pattern)

    def test_only_the_entry_file_is_a_bundle(self):
        """Yordamchi fayllar `.bundle.` nomini olmaydi: ular kirish faylga import bilan kiradi."""
        bundles = [name for name in _sources() if ".bundle." in name]
        self.assertEqual(bundles, [os.path.join("features", "orders", BUNDLE_JS)])


class TestSlotsAndFeatures(FrappeTestCase):
    def test_slot_ids_are_unique_per_slot(self):
        seen = set()
        for slot, item_id, _order in _slot_items():
            self.assertNotIn((slot, item_id), seen, f"{slot}: '{item_id}' takrorlangan")
            seen.add((slot, item_id))

    def test_slot_ids_do_not_clash_with_core_or_other_modules(self):
        """Bir slotda id to'qnashsa `slots.contribute` xato tashlaydi va ekran ochilmaydi."""
        mine = {(slot, item_id) for slot, item_id, _ in _slot_items()}
        others = set()
        pattern = re.compile(r'slots\.contribute\(\s*"([\w.]+)"\s*,\s*\{[^}]*?\bid:\s*"([\w-]+)"', re.S)
        for base, _dirs, files in os.walk(_js_root()):
            if os.path.abspath(base).startswith(os.path.abspath(_orders_dir())):
                continue
            for name in files:
                if name.endswith(".js"):
                    others.update(pattern.findall(_read(os.path.join(base, name))))
        self.assertFalse(mine & others, f"boshqa modullar bilan to'qnashuv: {sorted(mine & others)}")

    def test_orders_stay_inside_the_reserved_range(self):
        items = _slot_items()
        self.assertGreaterEqual(len(items), 6)
        for slot, item_id, order in items:
            self.assertIn(order, ORDER_RANGE, f"{slot}/{item_id}: order={order} 200-299 dan tashqarida")

    def test_only_known_slots_are_used(self):
        slots_js = _read(os.path.join(_js_root(), "core", "slots.js"))
        block = re.search(r"SLOT_NAMES = \[(.*?)\];", slots_js, re.S).group(1)
        known = set(re.findall(r'"([^"]+)"', block))
        for slot, _item_id, _order in _slot_items():
            self.assertIn(slot, known, f"noma'lum slot: {slot}")

    def test_features_are_gated_by_registered_pos_profile_flags(self):
        source = _code(_read(os.path.join(_orders_dir(), BUNDLE_JS)))
        registered = dict(
            re.findall(r'features\.register\(\{\s*key:\s*"(\w+)",\s*(?:flag:\s*"(\w+)",)?', source)
        )
        registered = {key: (flag or None) for key, flag in registered.items()}
        self.assertEqual(registered, FEATURE_FLAGS)

        for flag in filter(None, FEATURE_FLAGS.values()):
            self.assertIn(flag, cashier_features.FEATURES, f"'{flag}' reestrda yo'q")

    def test_gated_slots_live_in_the_flagged_feature(self):
        """Buyurtma/mijoz amallari bayroqsiz `order_view` ichida bo'lmasligi kerak."""
        source = _code(_read(os.path.join(_orders_dir(), BUNDLE_JS)))
        view = source[source.index('key: "order_view"') :]
        for mutating in ("orders-new", "orders-open", "orders-edit", "orders-customer"):
            self.assertNotIn(mutating, view, f"'{mutating}' bayroqsiz blokda")


class TestBackendReference(FrappeTestCase):
    """JS chaqiradigan har bir server metodi mavjud, `whitelist` qilingan va argumentlari mos."""

    def test_the_module_calls_exactly_the_expected_endpoints(self):
        called = {name for name, _keys in _call_sites()}
        self.assertEqual(called, EXPECTED_ENDPOINTS)

    def test_every_referenced_method_exists_and_is_whitelisted(self):
        for name, _keys in _call_sites():
            path = f"ozturkapp.ozturkapp.api.{name}"
            function = frappe.get_attr(path)
            self.assertIn(function, frappe.whitelisted, f"{path} whitelist qilinmagan")

    def test_keyword_arguments_match_the_python_signatures(self):
        for name, keys in _call_sites():
            path = f"ozturkapp.ozturkapp.api.{name}"
            parameters = inspect.signature(frappe.get_attr(path)).parameters

            unknown = keys - set(parameters)
            self.assertFalse(unknown, f"{path}: Python imzosida yo'q argumentlar {sorted(unknown)}")

            required = {
                key for key, parameter in parameters.items() if parameter.default is inspect.Parameter.empty
            }
            self.assertFalse(
                required - keys, f"{path}: majburiy argumentlar yuborilmayapti {sorted(required - keys)}"
            )

    def test_create_order_arguments_are_the_contract_ones(self):
        keys = dict(_call_sites())["cashier_orders.create_order"]
        self.assertEqual(
            keys,
            {"order_type", "items", "table", "customer", "pax", "comments", "delivery", "client_ref"},
        )

    def test_remove_item_has_no_approval_flow(self):
        """`remove_item` menejer PIN-i so'ramaydi (`ApprovalRequired` tashlamaydi).

        Oshxona boshlagan taomni faqat menejer roli sabab bilan olib tashlaydi, oddiy
        kassirga esa xabar ko'rsatiladi. Server PIN oqimini qo'shsa, bu test JS ham
        `ui.withApproval` ga o'tishi kerakligini eslatadi.
        """
        self.assertNotIn("approval", inspect.signature(cashier_orders.remove_item).parameters)
        self.assertNotIn("manager_approval", inspect.getsource(cashier_orders.remove_item))
        code = _all_code()
        self.assertNotIn("withApproval", code)
        self.assertNotIn("approval", dict(_call_sites())["cashier_orders.remove_item"])

    def test_order_type_strings_match_the_backend(self):
        shared = _read(os.path.join(_orders_dir(), "shared.js"))
        for constant, expected in (
            ("DINE_IN", cashier_orders.DINE_IN),
            ("TAKE_AWAY", cashier_orders.TAKE_AWAY),
            ("DELIVERY", cashier_orders.DELIVERY),
        ):
            self.assertIn(f'export const {constant} = "{expected}";', shared)
        self.assertEqual(
            set(cashier_orders.ORDER_TYPES),
            {cashier_orders.DINE_IN, cashier_orders.TAKE_AWAY, cashier_orders.DELIVERY},
        )

    def test_delivery_and_menu_shapes_are_produced_by_the_backend(self):
        """JS o'qiydigan javob kalitlari serverda haqiqatan yasaladi."""
        from ozturkapp.ozturkapp.api import cashier as cashier_api
        from ozturkapp.ozturkapp.utils import cashier_billing, order_items, table_status

        billing = inspect.getsource(cashier_billing.build_bill) + inspect.getsource(
            cashier_billing._extended_keys
        )
        for key in (
            "modified",
            "billed",
            "paid",
            "cancelled",
            "order_type",
            "table",
            "room",
            "customer",
            "customer_name",
            "mobile_number",
            "delivery",
            "reprint_needed",
            "item_name",
            "item_code",
            "qty",
            "rate",
            "comment",
            "kitchen",
        ):
            self.assertIn(f'"{key}"', billing, f"build_bill '{key}' kalitini qaytarmaydi")

        self.assertIn('"last_modified_time"', inspect.getsource(cashier_orders._order_payload))
        self.assertIn('"default_customer"', inspect.getsource(cashier_api.get_cashier_context))

        menu = inspect.getsource(order_items.build_menu)
        for key in ("courses", "items", "item", "item_name", "rate", "course", "special"):
            self.assertIn(f'"{key}"', menu, f"build_menu '{key}' kalitini qaytarmaydi")

        self.assertIn('"restaurant_room"', inspect.getsource(table_status).split("TABLE_FIELDS")[1][:400])
        self.assertIn("AVAILABLE", table_status.STATUSES)

    def test_default_customer_is_used_to_hide_the_walk_in_customer(self):
        """`panel.info` standart mijozni ko'rsatmaydi — bunga server `default_customer` beradi."""
        customer = _read(os.path.join(_orders_dir(), "customer.js"))
        self.assertIn("screen.ctx.default_customer", customer)


class TestSourceHygiene(FrappeTestCase):
    def test_no_desk_dialogs(self):
        """Kassa ichida Desk'ning sichqoncha oynalari yo'q — UI to'plami bor."""
        code = _all_code()
        for call in ("frappe.prompt", "frappe.confirm", "frappe.msgprint", "frappe.show_alert"):
            self.assertNotIn(call, code, f"{call} o'rniga `ui.form/confirm/alert/toast`")
        self.assertIsNone(re.search(r"(?<![\w.])alert\(", code), "alert( ishlatilgan")
        self.assertIsNone(re.search(r"(?<![\w.])confirm\(", code), "confirm( ishlatilgan")

    def test_no_hardcoded_service_charge_and_no_storage_or_logging(self):
        code = _all_code()
        self.assertNotRegex(code, r"\b0\.12\b", "xizmat haqi foizi frontend'ga yozilgan")
        for banned in ("localStorage", "sessionStorage", "console.log"):
            self.assertNotIn(banned, code)

    def test_prices_are_never_sent_to_the_server(self):
        """Narxni server `Item Price` dan oladi: yuboriladigan qatorlarda narx yo'q."""
        api = _code(_read(os.path.join(_orders_dir(), "api.js")))
        for word in ("rate", "price", "amount", "total"):
            self.assertNotRegex(api, rf"\b{word}\b", f"api.js da '{word}' bor")

        cart = _code(_read(os.path.join(_orders_dir(), "cart.js")))
        payload = cart[cart.index("payload() {") :]
        self.assertEqual(
            set(re.findall(r"^\s*(\w+):", _balanced(payload, payload.index("{", payload.index("map(")), "{", "}"), re.M)),
            {"item", "item_name", "qty", "comment"},
        )

    def test_frontend_does_no_money_arithmetic_except_the_labelled_estimate(self):
        """Yagona ko'paytirish — savatdagi «taxminiy» yig'indi (`Cart.estimate`)."""
        for name, body in _sources().items():
            code = _code(body)
            if name.endswith("cart.js"):
                self.assertEqual(code.count("line.rate * line.qty"), 1)
                continue
            self.assertNotRegex(code, r"\.rate\s*[*+\-/]", f"{name}: narx bilan arifmetika")

        dialog = _read(os.path.join(_orders_dir(), "order_dialog.js"))
        self.assertIn('__("Taxminiy jami")', dialog)
        self.assertIn('__("Yangi taomlar (taxminiy)")', dialog)

    def test_duplicate_submission_is_guarded(self):
        """`client_ref` har urinishga; ma'lumot o'zgarmagan qayta urinishda o'sha ref."""
        dialog = _code(_read(os.path.join(_orders_dir(), "order_dialog.js")))
        self.assertIn("uuid()", dialog)
        method = dialog[dialog.index("clientRefFor(args) {") :]
        method = _balanced(method, method.index("{"), "{", "}")
        self.assertIn("JSON.stringify(args)", method)
        self.assertIn("this.attempt.key !== key", method)

        submit = dialog[dialog.index("async submit() {") :]
        submit = _balanced(submit, submit.index("{"), "{", "}")
        self.assertIn("if (this.busy) return;", submit)
        self.assertLess(submit.index("this.setBusy(true)"), submit.index("await"))

    def test_server_text_is_escaped_before_it_reaches_html(self):
        """Serverdan kelgan maydon `${...}` ichida faqat `esc()` orqali chiqadi."""
        server_field = re.compile(
            r"\b(?:row|entry|line|table|bill|customer|delivery|type|choice)\."
            r"(?:item_name|item_code|customer_name|mobile_number|address|phone|comment|label|name|"
            r"restaurant_room|table|invoice|value|item)\b"
        )
        safe = ("esc(", "cint(", "fmtQty(", ".qty(", "===")
        for name, body in _sources().items():
            for expression in _interpolations(_code(body)):
                if server_field.search(expression):
                    self.assertTrue(
                        any(marker in expression for marker in safe),
                        f"{name}: ekranlanmagan server matni: ${{{expression}}}",
                    )

    def test_panel_info_uses_text_content(self):
        customer = _read(os.path.join(_orders_dir(), "customer.js"))
        info = customer[customer.index("export function renderInfo") :]
        self.assertIn("textContent", info)
        self.assertNotIn("innerHTML", info)

    def test_every_visible_string_goes_through_translation(self):
        """Foydalanuvchiga ko'rinadigan matn `__()` orqali (o'zbekcha)."""
        for name, body in _sources().items():
            for match in re.finditer(r"(?:label|title|placeholder|note|message|subtitle):\s*\"([^\"]+)\"", _code(body)):
                self.assertRegex(
                    match.group(1),
                    r"^[+\d\s]*$",
                    f"{name}: '{match.group(1)}' `__()` siz",
                )


class TestStyles(FrappeTestCase):
    def test_every_selector_is_scoped_to_the_module(self):
        """Uslub Desk va yadroga tegmasligi kerak: hamma selektor `.rc-orders-` ni o'z ichiga oladi."""
        css = re.sub(r"/\*.*?\*/", "", _read(os.path.join(_orders_dir(), BUNDLE_CSS)), flags=re.S)
        for match in re.finditer(r"([^{}]+)\{", css):
            selector = match.group(1).strip()
            if selector.startswith("@"):
                continue
            for part in selector.split(","):
                self.assertIn("rc-orders-", part, f"selektor modulga bog'lanmagan: {part.strip()}")

    def test_colours_come_from_theme_variables(self):
        css = re.sub(r"/\*.*?\*/", "", _read(os.path.join(_orders_dir(), BUNDLE_CSS)), flags=re.S)
        # Faqat accent ustidagi oq matn (yadro ham shunday) — qolgan hamma rang `--rc-*` dan.
        self.assertEqual(set(re.findall(r"#[0-9a-fA-F]{3,8}\b", css)), {"#fff"})
        self.assertNotRegex(css, r"rgba?\(", "rang o'zgaruvchisiz yozilgan")
        self.assertIn("var(--rc-touch)", css)

    def test_dialog_never_exceeds_the_viewport(self):
        css = _read(os.path.join(_orders_dir(), BUNDLE_CSS))
        self.assertIn("100dvh", css)
        self.assertRegex(css, r"\.rc-orders-grid\s*\{[^}]*grid-template-rows:\s*minmax\(0,\s*1fr\)")
        self.assertRegex(css, r"\.rc-orders-items\s*\{[^}]*overflow-y:\s*auto")
        self.assertRegex(css, r"\.rc-orders-lines\s*\{[^}]*overflow-y:\s*auto")
