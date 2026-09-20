# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa oynasi testlari.

Ishga tushirish::

    bench --site ozturk.local run-tests \
        --module ozturkapp.ozturkapp.tests.test_cashier

DIQQAT: bu yerda POS Invoice KONSOLIDATSIYASI sinalmaydi — ERPNext'ning
`consolidate_pos_invoices()` funksiyasi ichida `frappe.db.commit()` bor,
ya'ni u testning rollback'ini buzadi va saytda axlat qoldiradi.
Konsolidatsiya ERPNext'ning o'z testlari bilan qoplangan; biz faqat
POS Invoice darajasigacha tekshiramiz.
"""

import json
import os
import re
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from ozturkapp.ozturkapp.api import cashier as cashier_api
from ozturkapp.ozturkapp.overrides import pos_invoice as pos_invoice_override
from ozturkapp.ozturkapp.utils import (
    cashier_billing,
    cashier_permissions,
    kitchen_status,
    order_cancel,
    table_status,
)


def _free_table(branch=None):
    """Ochiq cheki BO'LMAGAN stolni topadi.

    Bazada demo/haqiqiy buyurtma turgan bo'lishi mumkin — test o'sha stolni
    tanlab qolsa noto'g'ri yiqiladi. Shuning uchun har doim bo'sh stol
    tanlanadi.
    """
    filters = {"branch": branch} if branch else {}
    busy = set()
    for row in frappe.get_all(
        "POS Invoice", filters={"docstatus": 0}, fields=["restaurant_table"]
    ):
        if row.restaurant_table:
            busy.add(row.restaurant_table)

    for name in frappe.get_all("URY Table", filters=filters, pluck="name"):
        if name not in busy:
            return name
    return None


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def eval_format_js(calls):
    """`util/format.js` funksiyalarini HAQIQIY JS'da baholaydi: `[("hhmm", ["9:00:00"]), ...]` -> natijalar.

    Manba matni tekshiruvi vaqtni to'g'ri ko'rsatishni kafolatlamaydi — `9:00:00` xatosi
    aynan shunday o'tib ketgan edi. `esbuild` (yig'ish uchun baribir kerak) modulni CommonJS
    ga o'giradi. Node yo'q bo'lsa test o'tkazib yuboriladi.
    """
    import shutil
    import subprocess

    node = shutil.which("node")
    frappe_js = os.path.join(frappe.get_app_path("frappe"), "..", "node_modules")
    if not node or not os.path.isdir(os.path.join(frappe_js, "esbuild")):
        return None

    path = frappe.get_app_path("ozturkapp", "public", "js", "cashier", "util", "format.js")
    script = """
        const fs = require("fs");
        const esbuild = require("esbuild");
        const code = esbuild.transformSync(fs.readFileSync(process.argv[1], "utf8"), { format: "cjs" }).code;
        const mod = { exports: {} };
        new Function("module", "exports", "frappe", "cint", "flt", "__", code)(
            mod, mod.exports, {}, (v) => parseInt(v, 10) || 0, (v) => parseFloat(v) || 0, (s) => s
        );
        const calls = JSON.parse(process.argv[2]);
        console.log(JSON.stringify(calls.map(([fn, args]) => mod.exports[fn](...args))));
    """
    result = subprocess.run(
        [node, "-e", script, path, json.dumps(calls)],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "NODE_PATH": os.path.abspath(frappe_js)},
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def js_slot_item(source, item_id):
    """`slots.contribute(..., { id: "<item_id>", ... })` bandining matni."""
    marker = f'id: "{item_id}"'
    at = source.index(marker)
    start = source.rindex("slots.contribute(", 0, at)
    end = source.index("\n});", at)
    return source[start : end + 4]


class _CashierPage:
    """Kassa sahifasining BARCHA manbasi — modullarga bo'lingandan keyin ham
    matn tekshiruvlari bir joydan o'qiydi.

    `script` — sahifa kirish fayli + HTML shablon + yig'maning YADRO
    modullari (`public/js/cashier/`, `features/` dan tashqari). Yadro
    tekshiruvlari (masalan «kassa buyurtmani tahrirlamaydi») shunga tayanadi:
    qo'shimcha funksiya modullari (`features/`) POS Profile bayrog'i bilan
    yoqiladi va o'z testlari bilan qoplanadi.

    `all_script` — `features/` bilan birga; `style` — sahifa uslubi va
    `public/css/cashier/` bo'laklari.
    """

    def __init__(self):
        page = frappe.get_doc("Page", "restaurant-cashier")
        page.load_assets()

        core, features = [], []
        js_root = frappe.get_app_path("ozturkapp", "public", "js", "cashier")
        for folder, _dirs, files in sorted(os.walk(js_root)):
            for name in sorted(files):
                if not name.endswith(".js"):
                    continue
                with open(os.path.join(folder, name), encoding="utf-8") as handle:
                    body = handle.read()
                is_feature = os.path.relpath(folder, js_root).split(os.sep)[0] == "features"
                (features if is_feature else core).append(body)

        css = [getattr(page, "style", None) or ""]
        css_root = frappe.get_app_path("ozturkapp", "public", "css", "cashier")
        for folder, _dirs, files in sorted(os.walk(css_root)):
            for name in sorted(files):
                if name.endswith((".css", ".scss")):
                    with open(os.path.join(folder, name), encoding="utf-8") as handle:
                        css.append(handle.read())

        self.entry = page.script
        self.script = page.script + "\n".join(core)
        self.all_script = self.script + "\n".join(features)
        self.style = "\n".join(css)


def cashier_page():
    return _CashierPage()


def js_method(source, signature):
    """`signature` bilan boshlanadigan JS metodining TANASI (figurali qavslar bo'yicha).

    Modullarga bo'lingandan keyin fayllar tartibi o'zgaradi, shuning uchun
    «metod boshidan keyingi matn» ga tayanib bo'lmaydi — faqat metodning
    o'zini tekshiramiz.
    """
    start = source.index(signature)
    depth, pos = 0, source.index("{", start)
    for index in range(pos, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise ValueError(f"{signature} tanasi yopilmagan")


class TestPageAssets(FrappeTestCase):
    """Desk sahifasi resurslari to'g'ri yig'ilishini tekshiradi.

    NEGA BU TEST BOR
    ================
    Frappe HTML shablonni JS satriga aylantiradi (`frappe/build.py:420`)::

        frappe.templates["restaurant_cashier"] = ' ...HTML... ';

    Mazmun BITTA TIRNOQ ichida. `scrub_html_template()` apostrofni
    ekranlashi kerak edi, lekin v15.100.0 dagi kod no-op::

        return content.replace("'", "'")

    Shu sababli HTML dagi bitta xom apostrof (masalan "bo'lmadi") JS satrini
    erta yopadi va BUTUN sahifa skripti `SyntaxError` bilan yiqiladi —
    kassir ekranida hech nima ko'rinmaydi.

    Bu aynan sodir bo'lgan xato edi; test uning qaytishini bloklaydi.
    """

    PAGE = "restaurant-cashier"

    def _assets(self):
        page = frappe.get_doc("Page", self.PAGE)
        page.load_assets()
        return page

    def test_html_template_has_no_unescaped_apostrophe(self):
        page = self._assets()
        match = re.search(
            r"frappe\.templates\[\"restaurant_cashier\"\] = '(.*)';", page.script
        )
        self.assertIsNotNone(match, "HTML shabloni skriptga qo'shilmagan")

        self.assertNotIn(
            "'",
            match.group(1),
            "HTML shablonida xom apostrof bor — `&#39;` ishlating, "
            "aks holda sahifa skripti SyntaxError bilan yiqiladi",
        )

    def test_page_entry_loads_the_bundle_and_drives_the_lifecycle(self):
        """Sahifa kirish fayli yig'mani yuklaydi va Desk hodisalarini uzatadi."""
        script = self._assets().script
        self.assertRegex(script, r"frappe\.pages\[.restaurant-cashier.\]\.on_page_load")
        self.assertIn('frappe.require(["cashier.bundle.css", "cashier.bundle.js"', script)
        self.assertIn("new ozturk.cashier.Screen(page)", script)
        self.assertRegex(script, r"on_page_show = function \(wrapper\) \{[^}]*\.resume\(\)")
        # Desk v15 `on_page_hide` ni chaqirmaydi (views/container.js faqat jQuery
        # "hide" hodisasini yuboradi) — yorliqlar va fon vazifalari shu hodisa bilan to'xtaydi.
        self.assertRegex(script, r'\$\(wrapper\)\.on\("hide", \(\) => \{[^}]*\.suspend\(\)')
        self.assertNotIn("on_page_hide", script.replace("`on_page_hide`", "").replace("// Desk v15 `on_page_hide`", ""))

    def test_entry_discovers_feature_bundles_without_being_edited(self):
        """`cashier_*.bundle.*` yig'malari `assets.json` dan o'zi topiladi (wave-2 moduli kirish faylini tahrirlamaydi)."""
        script = self._assets().script
        self.assertIn("frappe.boot.assets_json", script)
        self.assertIn("cashier_[a-z0-9_]+", script)

    def test_bundle_exposes_the_screen_and_renders_the_template(self):
        script = cashier_page().script
        self.assertIn("Screen: CashierScreen", script)
        self.assertIn('render_template("restaurant_cashier"', script)

    def test_bundle_public_namespace_is_complete(self):
        """Funksiya modullari yadroga FAQAT `ozturk.cashier` orqali ulanadi."""
        entry = _read(frappe.get_app_path("ozturkapp", "public", "js", "cashier", "cashier.bundle.js"))
        for name in ("Screen", "features", "slots", "ui", "api", "util", "BUILD"):
            self.assertRegex(entry, rf"\b{name}\b", f"ozturk.cashier.{name} e'lon qilinmagan")

    def test_page_style_is_loaded(self):
        style = cashier_page().style
        self.assertIn(".rc-root", style)
        self.assertIn(".rc-table--OCCUPIED", style)

    def test_bundle_sources_exist(self):
        for parts in (
            ("js", "cashier", "cashier.bundle.js"),
            ("css", "cashier.bundle.scss"),
            ("js", "cashier", "README.md"),
            ("js", "cashier", "core", "slots.js"),
            ("js", "cashier", "core", "features.js"),
            ("js", "cashier", "core", "shortcuts.js"),
            ("js", "cashier", "core", "layout.js"),
            ("js", "cashier", "kit", "dialog.js"),
            ("js", "cashier", "kit", "keyboard.js"),
            ("js", "cashier", "kit", "approval.js"),
            ("js", "cashier", "kit", "controls.js"),
        ):
            path = frappe.get_app_path("ozturkapp", "public", *parts)
            self.assertTrue(os.path.exists(path), f"{'/'.join(parts)} yo'q")

    def test_bundles_are_built_and_registered(self):
        """`bench build` yig'mani `assets.json` ga yozgan bo'lishi shart.

        Aks holda `frappe.require("cashier.bundle.js")` mavjud bo'lmagan fayl
        so'raydi va sahifa bo'sh qoladi (`deploy.sh` `bench build` ni
        ishga tushiradi, lekin qo'lda yig'ilmagan dev-saytda shu test ushlaydi).
        """
        from frappe.utils import get_assets_json

        assets = get_assets_json()
        for bundle in ("cashier.bundle.js", "cashier.bundle.css"):
            self.assertIn(bundle, assets, f"{bundle} yig'ilmagan — `bench build --app ozturkapp`")
            built = frappe.get_site_path("..", "assets", assets[bundle].replace("/assets/", "", 1))
            self.assertTrue(os.path.exists(built), f"{assets[bundle]} diskda yo'q")

    def test_cashier_uses_no_desk_dialogs(self):
        """Kassa ichida Desk'ning sichqoncha oynalari ishlatilmaydi — UI to'plami bor."""
        source = cashier_page().all_script
        for call in ("frappe.prompt(", "frappe.confirm(", "frappe.msgprint(", "frappe.show_alert("):
            self.assertNotIn(call, source, f"{call} o'rniga `ui.form/confirm/alert/toast`")

    def test_every_slot_is_documented(self):
        """README kengaytma nuqtalarining BARCHASINI tavsiflaydi — wave-2 uchun shartnoma."""
        slots_js = _read(frappe.get_app_path("ozturkapp", "public", "js", "cashier", "core", "slots.js"))
        block = re.search(r"SLOT_NAMES = \[(.*?)\];", slots_js, re.S).group(1)
        names = re.findall(r'"([^"]+)"', block)
        self.assertGreaterEqual(len(names), 10)

        readme = _read(frappe.get_app_path("ozturkapp", "public", "js", "cashier", "README.md"))
        for name in names:
            self.assertIn(f"`{name}`", readme, f"README'da `{name}` slot tavsifi yo'q")

    def test_touch_target_size_is_a_shared_variable(self):
        style = cashier_page().style
        self.assertIn("--rc-touch: 48px", style)
        # To'liq ekran (kiosk) rejimi yo'q: Desk paneli hech qachon yashirilmaydi.
        self.assertNotIn("rc-kiosk", style)
        # Ikki ustun 1024px gacha saqlanadi: 1100px dagi qoida faqat tugma yozuvlarini
        # yig'adi, `.rc-main` ni bir ustunga tushirmaydi.
        self.assertNotRegex(style, r"@media[^{]*\{\s*\.rc-main\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\);")
        self.assertRegex(style, r"\.rc-main\s*\{[^}]*grid-template-columns:[^;]*clamp\(340px")

    def test_service_charge_rate_is_not_hardcoded_in_frontend(self):
        """Foiz faqat ERPNext shablonida turishi kerak (TZ §8)."""
        script = cashier_page().all_script
        code = [
            line
            for line in script.split("\n")
            if not line.strip().startswith(("*", "//", "/*"))
        ]
        self.assertNotRegex(
            "\n".join(code),
            r"\b0\.12\b",
            "xizmat haqi foizi frontend'ga qattiq yozilgan",
        )


class TestCashierFrontendContract(FrappeTestCase):
    """Ilova joylashuvi (Desk sahifasi ichida), UI to'plami va kengaytma reestri shartnomalari.

    Brauzersiz tekshiriladigan qismi: manba matni va sahifa fayllari. Ko'rinish
    (masshtab, 48px nishonlar, aylantirishsiz sig'ish) `bench build` dan keyin
    brauzerda tekshirilgan — bu testlar ularning REGRESSIYASINI ushlaydi.
    """

    def setUp(self):
        self.page = cashier_page()
        self.js_root = frappe.get_app_path("ozturkapp", "public", "js", "cashier")

    def _js(self, *parts):
        return _read(os.path.join(self.js_root, *parts))

    def test_page_json_is_sane(self):
        path = frappe.get_app_path(
            "ozturkapp", "ozturkapp", "page", "restaurant_cashier", "restaurant_cashier.json"
        )
        page = json.loads(_read(path))

        self.assertEqual(page["name"], "restaurant-cashier")
        self.assertEqual(page["standard"], "Yes")
        self.assertEqual(
            {row["role"] for row in page["roles"]},
            {"URY Cashier", "URY Manager", "System Manager"},
        )

    def test_html_shell_has_the_app_structure(self):
        html = _read(
            frappe.get_app_path(
                "ozturkapp", "ozturkapp", "page", "restaurant_cashier", "restaurant_cashier.html"
            )
        )
        for hook in ("rc-topbar", "rc-rooms", "rc-filters", "rc-viewtabs", "rc-menu-btn", "rc-panel"):
            self.assertIn(hook, html)
        # To'liq ekran rejimi olib tashlangan: shablonda unga oid hech narsa yo'q.
        self.assertNotIn("kiosk", html.lower())

        # Joyi qimmat, kassirga kerak emas: ular «⋯» menyusi tagiga yoki tooltip'ga ko'chgan.
        for gone in ("rc-build", "rc-restaurant", "rc-branch", "rc-orders__title", "Yangilash"):
            self.assertNotIn(gone, html)

    def test_removed_clutter_stays_removed(self):
        script = self.page.all_script
        self.assertNotIn('__("Shakl")', script)
        self.assertNotIn("Buyurtma avtomatik yaratilmaydi", script)
        self.assertNotIn("set_primary_action", script)

        # «Joylashuvni tahrirlash» — faqat menejerga va faqat «⋯» menyusida.
        menu = self._js("ui", "menu.js")
        self.assertIn("is_supervisor", js_slot_item(menu, "layout-edit"))
        self.assertNotIn('__("Joylashuvni tahrirlash")', self._js("ui", "floor.js"))

        # Xato kodi yig'iladigan bo'limda.
        self.assertIn("<details", self._js("ui", "panel.js"))

    def test_panel_footer_is_pinned_and_body_scrolls(self):
        style = self.page.style
        self.assertRegex(style, r"\.rc-panel__body\s*\{[^}]*overflow-y:\s*auto")
        self.assertRegex(style, r"\.rc-panel__foot\s*\{[^}]*flex:\s*0 0 auto")
        # Ikki katta tugma «Hisob berish» va «To'lov» — pastki qatorda.
        self.assertIn('id: "give-bill"', self._js("ui", "panel.js"))
        self.assertIn('id: "pay"', self._js("ui", "panel.js"))

    def test_floor_scales_to_width_and_height_and_refits_on_resize(self):
        floor = self._js("ui", "floor.js")
        fit = js_method(floor, "fitFloor() {")
        # Bitta zal (`plain`) va barcha zallar (`packBlocks`) — ikkalasi ham eni VA balandligi bo'yicha.
        arrange = js_method(floor, "arrangeFloor() {")
        self.assertIn("availableWidth / extent.width", arrange)
        self.assertIn("availableHeight / extent.height", arrange)
        pack = js_method(floor, "export function packBlocks(")
        self.assertIn("availableWidth / width", pack)
        self.assertIn("availableHeight / height", pack)
        self.assertIn("this.positionFloor(this.arrangeFloor())", fit)
        self.assertIn("new ResizeObserver", self._js("core", "screen.js"))

    def test_elapsed_time_ticks_on_the_client(self):
        fmt = self._js("util", "format.js")
        self.assertIn("ELAPSED_WARN_MINUTES = 60", fmt)
        self.assertIn("ELAPSED_ALERT_MINUTES = 90", fmt)
        # Server soatiga emas, YUKLANGAN paytga tayanadi (soat/vaqt mintaqasi farq qilishi mumkin).
        self.assertIn("data-since", fmt)
        self.assertIn("tickElapsed()", js_method(self._js("core", "screen.js"), "tick() {"))

    def test_bill_requested_bell_is_persistent_and_respects_reduced_motion(self):
        self.assertIn("bill_requested", self._js("ui", "floor.js"))
        self.assertIn("bill_requested", self._js("ui", "orders.js"))
        self.assertIn("prefers-reduced-motion", self.page.style)
        # Belgi faqat server bayrog'iga bog'liq — yo'q bo'lsa chizilmaydi.
        self.assertIn('fromTable(table, "bill_requested")', self._js("ui", "floor.js"))

    def test_full_screen_mode_is_gone_and_desk_chrome_is_never_hidden(self):
        """To'liq ekran (kiosk) rejimi foydalanuvchi talabi bilan ATAYLAB olib tashlangan."""
        self.assertFalse(os.path.exists(os.path.join(self.js_root, "core", "kiosk.js")))

        # Yig'ma manbasi (JS + CSS + HTML + sahifa kirish fayli) da kiosk izi yo'q.
        everything = (self.page.all_script + "\n" + self.page.style).lower()
        for trace in ("kiosk", "rc-kiosk", "ozturk_cashier_kiosk", "kiosk_mode"):
            self.assertNotIn(trace, everything, f"`{trace}` qolib ketgan")

        # Desk navbari va sahifa sarlavhasi hech qachon yashirilmaydi.
        self.assertNotRegex(self.page.style, r"\.page-head[^{}]*\{[^}]*display:\s*none")
        self.assertNotRegex(self.page.style, r"\.sticky-top[^{}]*\{[^}]*display:\s*none")
        self.assertNotIn("overflow: hidden !important", self.page.style)
        # `body` ga hech qanday sinf qo'yilmaydi.
        self.assertNotIn("document.body.classList", self.page.all_script)

        # Balandlik ekran o'lchamidan emas, ilovaning HAQIQIY o'rnidan (`--rc-offset`) olinadi.
        layout = self._js("core", "layout.js")
        self.assertIn("getBoundingClientRect().top", js_method(layout, "export function syncOffset(root) {"))
        self.assertIn("scrollHeight", js_method(layout, "export function syncOffset(root) {"))
        screen = self._js("core", "screen.js")
        self.assertIn("syncOffset(root)", js_method(screen, "syncLayout() {"))
        self.assertIn("this.syncLayout()", js_method(screen, "setState(state) {"))
        self.assertIn("this.syncLayout()", js_method(screen, "resume() {"))
        self.assertRegex(self.page.style, r"\.rc-root\[data-state=\"ready\"\]\s*\{[^}]*calc\(100vh - var\(--rc-offset")

    def test_text_keyboard_supports_uzbek_and_cyrillic(self):
        keyboard = self._js("kit", "keyboard.js")
        for key in ("oʻ", "gʻ", "sh", "ch", "ʼ", "қ", "ғ", "ҳ", "ў"):
            self.assertIn(key, keyboard, f"'{key}' klaviaturada yo'q")
        for layout in ("uz:", "ru:", "sym:", "num:"):
            self.assertIn(layout, keyboard)
        # Klaviatura faqat POS Profile bayrog'i yoqilganda.
        self.assertIn("if (kit.virtualKeyboard) this.bindKeyboard()", self._js("kit", "dialog.js"))

    def test_approval_flow_follows_the_server_contract(self):
        approval = self._js("kit", "approval.js")
        self.assertIn("ozturkapp.ozturkapp.api.approval.get_approvers", approval)
        self.assertIn("self_approves", approval)
        self.assertIn("isApprovalRequired(error)", approval)
        self.assertIn("ApprovalCancelled", approval)
        # PIN hech qayerga yozilmaydi.
        for storage in ("localStorage", "sessionStorage", "console.log"):
            self.assertNotIn(storage, approval)
        self.assertIn('exc_type', self._js("core", "api.js"))

    def test_registry_rejects_typos_and_duplicates(self):
        slots = self._js("core", "slots.js")
        self.assertIn("Noma'lum slot", slots)
        self.assertIn("allaqachon qo'shilgan", slots)
        self.assertIn("allaqachon bor", self._js("core", "features.js"))

    def test_feature_bundles_are_found_by_name_convention(self):
        entry = _read(
            frappe.get_app_path(
                "ozturkapp", "ozturkapp", "page", "restaurant_cashier", "restaurant_cashier.js"
            )
        )
        self.assertIn("^cashier_[a-z0-9_]+\\.bundle\\.(js|css)$", entry)
        self.assertIn("features/cashier_<nom>", self._js("README.md").replace("features/<nom>/cashier_<nom>", "features/cashier_<nom>"))

    # ── Wave-2 uchun uchta qo'shimcha slot ───────────────────────────

    def test_panel_more_info_and_quick_slots_are_registered_and_documented(self):
        slots_js = self._js("core", "slots.js")
        readme = self._js("README.md")

        for name in ("panel.more", "panel.info", "topbar.quick"):
            self.assertIn(f'"{name}"', slots_js, f"{name} SLOT_NAMES da yo'q")
            # README §6 jadvalida qatori bor
            self.assertRegex(readme, rf"\| `{re.escape(name)}` \|", f"{name} jadvalda yo'q")

        # Element shakllari va yordamchi a'zolar hujjatlangan.
        self.assertIn("render(screen, detail) -> HTMLElement", readme)
        self.assertIn("icon?:", readme)
        self.assertIn("screen.detail", readme)
        self.assertIn("openMoreSheet", readme)

    def test_slot_contribution_to_new_slots_is_accepted_and_typos_are_not(self):
        """Ro'yxatdagi nomlarga qo'shish mumkin; imloviy xato jimgina o'tib ketmaydi."""
        slots_js = self._js("core", "slots.js")
        block = re.search(r"SLOT_NAMES = \[(.*?)\];", slots_js, re.S).group(1)
        names = re.findall(r'"([^"]+)"', block)
        self.assertEqual(len(names), len(set(names)), "SLOT_NAMES da takrorlanish bor")
        self.assertIn("Noma'lum slot", slots_js)

    def test_panel_more_button_appears_only_when_an_item_is_visible(self):
        panel = self._js("ui", "panel.js")
        actions = js_method(panel, "actionsHtml(detail) {")

        self.assertIn('slots.visible("panel.more", this, detail).length > 0', actions)
        self.assertIn('data-action="panel-more"', actions)
        self.assertIn("hasMore ? moreButton", actions)
        # Yadro tugmalari joyida: «Yana ⋯» ikkinchi qatorning OXIRIDA.
        self.assertLess(actions.index("secondary.map(button)"), actions.index("moreButton :"))

        sheet = js_method(panel, "openMoreSheet(detail, button) {")
        # Sabab matni qator ostida (sensorli ekranda `title` ko'rinmaydi).
        self.assertIn("rc-more__reason", sheet)
        self.assertIn("row.disabled", sheet)
        # Amal varaq YOPILGANDAN keyin ishga tushadi va `button` uzatiladi.
        self.assertLess(sheet.index("sheet.close(null)"), sheet.index("item.onClick(this, detail, button)"))

        style = self.page.style
        self.assertRegex(style, r"\.rc-more__item\s*\{[^}]*min-height:\s*60px")
        self.assertRegex(style, r"\.rc-btn--more|\.rc-panel__secondary--more")
        for kind in ("primary", "pay", "danger"):
            self.assertIn(f".rc-more__item--{kind}", style)

    def test_panel_more_keeps_the_footer_height_at_narrow_widths(self):
        """1024px da «Yana ⋯» yadro tugmalarini ikki qatorga o'ratib, pastki qatorni siljitmasin."""
        style = self.page.style
        self.assertRegex(style, r"\.rc-panel__secondary \.rc-btn\s*\{[^}]*line-height:\s*1\.1")
        narrow = re.search(r"@media \(max-width: 1100px\) \{\s*\.rc-panel__secondary--more.*?\n\}", style, re.S)
        self.assertIsNotNone(narrow, "tor ekranda «Yana» yozuvi yig'ilmagan")
        self.assertIn(".rc-btn--more .rc-btn__label { display: none; }", narrow.group(0))

    def test_panel_info_is_zero_height_when_empty_and_never_starves_the_list(self):
        panel = self._js("ui", "panel.js")

        self.assertIn("const PANEL_MIN_LIST = 120;", panel)
        # Karkasda blok bor va bo'sh paytda `hidden` (balandlik 0).
        self.assertIn('<div class="rc-panel__info" hidden></div>', panel)
        mount = js_method(panel, "mountPanelInfo(detail) {")
        self.assertIn("info.hidden = true", mount)
        # Bitta modulning `render` xatosi panelni buzmaydi.
        self.assertIn("catch (error)", mount)
        self.assertIn("console.error", mount)
        # Har `renderPanel` da qayta chiziladi.
        self.assertIn("this.mountPanelInfo(detail);", js_method(panel, "renderPanel(detail) {"))

        fit = js_method(panel, "fitPanelInfo() {")
        self.assertIn("- PANEL_MIN_LIST", re.sub(r"\s+", " ", fit))
        self.assertIn('__("+{0} yana"', fit)
        self.assertIn("Yig'ish", fit)

        # Sig'may qolsa avval blok shrink bo'ladi, ro'yxat emas.
        style = self.page.style
        self.assertRegex(style, r"\.rc-panel__info\s*\{[^}]*flex:\s*0 1 auto[^}]*overflow-y:\s*auto")
        self.assertRegex(style, r"\.rc-panel__body\s*\{[^}]*flex:\s*1 1 0[^}]*min-height:\s*120px")

        # Panel o'lchami o'zgarganda qayta sig'diriladi.
        self.assertIn("resizeObserver.observe(this.el.panel)", self._js("core", "screen.js"))

    def test_topbar_quick_is_icon_only_on_narrow_screens_and_single_row(self):
        top = self._js("ui", "topbar.js")
        quick = js_method(top, "renderQuick() {")
        # Yozuv yashirilganda ham tugma nomi bor.
        self.assertIn("aria-label=", quick)
        self.assertIn("title=", quick)
        # O'chirilgan tugma sababi bosilganda ko'rinadi (HTML `disabled` EMAS).
        self.assertIn("aria-disabled", quick)
        self.assertIn("ui.toast(disabled", js_method(top, "onQuickClick(button) {"))
        # Holat o'zgarmasa DOM'ga tegilmaydi.
        self.assertIn("this.quickHtml === html", quick)

        style = self.page.style
        self.assertRegex(style, r"\.rc-topquick__btn\s*\{[^}]*min-height:\s*var\(--rc-touch\)")
        # Sig'masa yozuv yashiriladi: JS tarkibni O'LCHAYDI (`data-compact`), ekran kengligiga tayanmaydi.
        hidden = re.search(r'\.rc-root\[data-compact="3"\] \.rc-topquick__label[^{]*\{[^}]*display:\s*none', style)
        self.assertIsNotNone(hidden, "sig'masa tez tugma yozuvi yashirilmagan")
        layout = self._js("core", "layout.js")
        self.assertIn("bar.scrollWidth > bar.clientWidth", js_method(layout, "export function syncCompact(root) {"))
        self.assertIn("syncCompact", js_method(top, "fitTopbar() {"))
        self.assertRegex(style, r"\.rc-topbar\s*\{[^}]*flex-wrap:\s*nowrap")
        # Bo'sh joyni cho'ziluvchi oraliq oladi; zallar faqat oxirgi bosqichda aylantiriladi.
        self.assertRegex(style, r"\.rc-topbar__spacer\s*\{[^}]*flex:\s*1 1 0")
        self.assertRegex(style, r'\.rc-root\[data-compact="5"\] \.rc-rooms\s*\{[^}]*overflow-x:\s*auto')

        html = _read(
            frappe.get_app_path(
                "ozturkapp", "ozturkapp", "page", "restaurant_cashier", "restaurant_cashier.html"
            )
        )
        self.assertLess(html.index("rc-topquick"), html.index("rc-menu-btn"))

    def test_screen_detail_is_exposed_for_feature_bundles(self):
        panel = self._js("ui", "panel.js")
        self.assertIn("this.detail = detail;", js_method(panel, "renderPanel(detail) {"))
        self.assertIn("this.detail = null;", js_method(panel, "clearSelection() {"))
        self.assertIn("this.detail = null;", self._js("core", "screen.js"))

        # Funksiya modullari ishlatadigan a'zolar `screen` da va hujjatlangan.
        readme = self._js("README.md")
        for member in ("screen.call(", "screen.refresh(", "selectTable(name)", "selectOrder(invoice)",
                       "screen.openPaymentModal(detail)", "ozturk.cashier.ui"):
            self.assertIn(member, readme, f"README §4 da `{member}` yo'q")

    # ── Yadro silliqlash (server vaqti, menyu tartibi, forma xatosi, bildirishnoma, ...) ──

    def test_hhmm_normalises_every_server_time_shape(self):
        """`9:00:00` → `09:00`; `slice(0, 5)` `9:00:` berardi (haqiqiy xato)."""
        cases = [
            "9:00:00", "09:00:00", "9:00", "19:30:00", "2026-09-20 9:05:00.123456",
            "2026-09-20T09:05:00", "2026-09-20", None, "", "abc", 0,
        ]
        result = eval_format_js([("hhmm", [value]) for value in cases])
        if result is None:
            self.skipTest("node/esbuild yo'q")

        self.assertEqual(
            result,
            ["09:00", "09:00", "09:00", "19:30", "09:05", "09:05", "", "", "", "", ""],
        )

    def test_core_never_slices_server_time_by_hand(self):
        for name, source in (
            ("ui/floor.js", self._js("ui", "floor.js")),
            ("ui/panel.js", self._js("ui", "panel.js")),
            ("ui/history.js", self._js("ui", "history.js")),
        ):
            self.assertNotIn(".slice(0, 5)", source, f"{name}: vaqt `hhmm()` orqali ko'rsatiladi")
        self.assertIn("hhmm(reservation.from_time)", self._js("ui", "floor.js"))
        self.assertIn("hhmm(r.from_time)", self._js("ui", "panel.js"))
        self.assertIn("hhmm(r.time)", self._js("ui", "history.js"))
        self.assertRegex(self._js("cashier.bundle.js"), r"\bhhmm,")

    def test_feature_modules_share_the_core_time_helper(self):
        """Funksiya modullari o'zining `hhmm` ini yozmaydi — yadrodagi yagona qoida."""
        features = os.path.join(self.js_root, "features")
        for folder, _dirs, files in os.walk(features):
            for name in files:
                if not name.endswith(".js"):
                    continue
                source = _read(os.path.join(folder, name))
                self.assertNotRegex(
                    source,
                    r"function hhmm\(|const hhmm = \(",
                    f"{os.path.relpath(os.path.join(folder, name), self.js_root)}: hhmm nusxasi",
                )

    def test_close_shift_sorts_below_every_feature_item(self):
        menu = self._js("ui", "menu.js")
        self.assertIn("order: 900", js_slot_item(menu, "close-shift"))

        orders = [
            int(order)
            for order in re.findall(
                r'slots\.contribute\("topbar\.menu", \{\s*id: "[\w-]+",\s*order: (\d+)', menu
            )
        ]
        self.assertEqual(max(orders), 900)
        self.assertEqual(sorted(orders)[-2] < 100, True, "yadro bandlari 10–40 oralig'ida")

    def test_form_field_error_clears_when_the_user_fixes_the_field(self):
        dialog = self._js("kit", "dialog.js")
        self.assertIn("clearFieldError(field) {", dialog)
        body = js_method(dialog, "clearFieldError(field) {")
        self.assertIn('classList.remove("rc-field--invalid")', body)
        self.assertIn("textContent = \"\"", body)
        # Yozish, tanlov va chip bosilganda; maydonga fokus tushishi xatoni olib tashlamaydi.
        for event in ('addEventListener("input"', 'addEventListener("change"', 'addEventListener("click"'):
            self.assertIn(event, dialog)
        self.assertIn('closest(".rc-chip-opt")', dialog)
        self.assertNotIn('addEventListener("focus"', js_method(dialog, "build() {"))

    def test_toasts_never_cover_dialog_buttons(self):
        style = self.page.style
        rule = re.search(
            r"\.rc-root:has\(\.rc-overlay:not\(\[hidden\]\)\) \.rc-toasts\s*\{([^}]*)\}", style
        )
        self.assertIsNotNone(rule, "oyna ochiq paytidagi bildirishnoma qoidasi yo'q")
        self.assertIn("top: 12px", rule.group(1))
        self.assertIn("bottom: auto", rule.group(1))
        self.assertRegex(
            style,
            r"\.rc-root:has\(\.rc-overlay:not\(\[hidden\]\)\) \.rc-toast\s*\{[^}]*pointer-events:\s*none",
        )

    def test_payment_session_api_for_features(self):
        payment = self._js("ui", "payment.js")
        session = payment[payment.index("class PaymentSession"): payment.index("export class PaymentMethods")]

        # (a) dinamik maydonlar bitta raqam paneliga ulanadi
        self.assertIn("bindNumpad(input) {", session)
        self.assertIn("this.numpad.bind(input)", session)
        self.assertIn("session.numpad = this.mountNumpad($body, $inputs)", payment)
        keyboard = self._js("kit", "keyboard.js")
        self.assertIn("el.bind = (input) =>", keyboard)
        self.assertIn("getClientRects().length > 0", keyboard)  # yashirin maydonga yozilmaydi

        # (d) tasdiqlash tugmasi holati `busy(false)` dan omon qoladi
        self.assertIn("setConfirmEnabled(enabled) {", session)
        self.assertIn('dataset.locked = "1"', session)
        helpers = js_method(self._js("core", "helpers.js"), "busy(button, state) {")
        self.assertIn('button.dataset.locked === "1"', helpers)

        # (c) bitta bo'lim xatosi oynani buzmaydi
        sections = js_method(payment, "mountPaymentSections($body, session) {")
        self.assertIn("catch (error)", sections)
        self.assertIn("console.error", sections)

        # (b) chap ustun ichkarida aylanadi, xato qotirilgan qatorda
        style = self.page.style
        self.assertRegex(style, r"\.rc-pay__cols\s*\{[^}]*grid-template-rows:\s*minmax\(0, 1fr\)")
        self.assertRegex(style, r"\.rc-pay__main\s*\{[^}]*overflow-y:\s*auto")
        self.assertRegex(payment, r'rc-pay__foot">\s*<div class="rc-pay__error"')

    def test_payment_confirm_needs_no_manager_approval(self):
        """`submit_payment` tasdiq qabul qilmaydi — bo'sh `withApproval` o'ramasi yo'q."""
        payment = self._js("ui", "payment.js")
        self.assertNotIn("withApproval", payment)
        call = re.search(r'this\.call\("ozturkapp\.ozturkapp\.api\.billing\.submit_payment", \{(.*?)\}\);', payment, re.S)
        self.assertIsNotNone(call, "submit_payment chaqiruvi topilmadi")
        self.assertNotIn("approval", call.group(1))

    def test_numpad_panel_accepts_dynamic_inputs(self):
        keyboard = self._js("kit", "keyboard.js")
        start = keyboard.index("export function numpad(")
        bind = keyboard[start : keyboard.index("// ═══", start)]

        self.assertIn("el.bind = (input) =>", bind)
        self.assertIn("inputs.forEach(el.bind)", bind)
        # DOM'dan olib tashlangan (qayta chizilgan) maydonlar ro'yxatni to'ldirmaydi.
        self.assertIn("bound.delete(old)", bind)
        # Faqat KO'RINIB turgan maydonga yoziladi.
        self.assertIn("getClientRects().length > 0", bind)

    def test_history_shows_returns_natively(self):
        history = self._js("ui", "history.js")
        self.assertIn("const returnBadge = () =>", history)
        self.assertIn('__("QAYTARISH")', history)
        # Ro'yxat qatorida ham, tafsilot sarlavhasida ham belgi.
        self.assertIn("r.is_return", js_method(history, "renderHistoryList(rows, $list, $summary) {"))
        detail = js_method(history, "renderHistoryDetail(bill, $detail) {")
        self.assertIn("bill.is_return ? returnBadge()", detail)
        # «Asl chek» havolasi asl chekning tafsilotini ochadi (`showDetail`).
        self.assertIn("bill.is_return && bill.return_against", detail)
        self.assertIn('data-action="history-open"', detail)
        self.assertIn('[data-action="history-open"]', js_method(history, "async openHistoryModal() {"))

        style = self.page.style
        self.assertRegex(style, r"\.rc-return-badge\s*\{[^}]*background:\s*var\(--rc-occupied\)")
        self.assertRegex(style, r"\.rc-history__origin\s*\{[^}]*min-height:\s*var\(--rc-touch\)")

    def test_history_is_scoped_to_a_shift_not_to_dates(self):
        """Tarix kassa ochilgandan yopilgunicha bo'lgan cheklarni ko'rsatadi."""
        history = self._js("ui", "history.js")
        opener = js_method(history, "async openHistoryModal() {")

        self.assertIn('data-field="shift"', opener)
        self.assertIn("get_paid_order_filter_options", opener)
        self.assertIn("shifts.find((s) => s.name === current)", opener)
        # Kassirga bitta smena keladi: tanlagich o'rniga hozirgi smena yozuvi.
        self.assertIn("if (shifts.length === 1) {", opener)
        self.assertRegex(opener, r"get_paid_orders\", \{\s*shift,")
        for stale in ("date_from", "date_to", 'type="date"', "get_today"):
            self.assertNotIn(stale, opener)

    def test_readme_documents_the_members_features_rely_on(self):
        readme = self._js("README.md")
        screen = self._js("core", "screen.js")
        floor = self._js("ui", "floor.js")
        realtime = self._js("core", "realtime.js")

        # Har bir hujjatlangan a'zo HAQIQATAN mavjud (hujjat yolg'on bo'lmasin).
        exists = {
            "screen.active": "this.active = ",
            "screen.state": "this.state = state",
            "screen.layoutEditMode": "this.layoutEditMode = ",
            "screen.renderFloor()": "renderFloor() {",
            "screen.el.canvas": "canvas: find(",
            "screen.floorLoadedAt": "this.floorLoadedAt = Date.now()",
            "screen.scheduleRefresh": "scheduleRefresh(scope = {}) {",
        }
        for member, needle in exists.items():
            self.assertIn(member, readme, f"README §4 da `{member}` yo'q")
            self.assertTrue(needle in screen or needle in floor or needle in realtime, f"{member}: manbada `{needle}` yo'q")

        self.assertIn("screen.floor.generated_at", readme)
        self.assertIn("screen.selectTable(name)", readme)
        self.assertIn("ozturk.cashier.util.hhmm", readme)

    def test_shift_and_table_features_use_the_core_helper(self):
        shift = _read(os.path.join(self.js_root, "features", "shift", "shared.js"))
        self.assertIn("export const { esc, hhmm } = util;", shift)
        tables = _read(os.path.join(self.js_root, "features", "tables", "table_data.js"))
        self.assertIn("const { hhmm } = ozturk.cashier.util;", tables)
        self.assertNotIn("export function hhmm", tables)


class TestTableStatus(FrappeTestCase):
    """Holat keltirib chiqarish — sof mantiq, bazaga tegmaydi."""

    def test_clusters_group_merged_tables(self):
        tables = [
            {"name": "T1", "merged_with": "T2"},
            {"name": "T2", "merged_with": "T1"},
            {"name": "T3", "merged_with": None},
        ]
        clusters = table_status.build_clusters(tables)

        self.assertEqual(sorted(clusters["T1"]), ["T1", "T2"])
        self.assertEqual(sorted(clusters["T2"]), ["T1", "T2"])
        self.assertEqual(clusters["T3"], ["T3"])

    def test_cluster_follows_one_sided_link(self):
        """`merged_with` faqat bitta tomonda yozilgan bo'lsa ham klaster to'liq."""
        tables = [
            {"name": "T1", "merged_with": "T2"},
            {"name": "T2", "merged_with": None},
        ]
        clusters = table_status.build_clusters(tables)
        self.assertEqual(sorted(clusters["T2"]), ["T1", "T2"])

    def test_order_on_any_member_occupies_whole_cluster(self):
        order_map = {"T1": frappe._dict(name="INV-1")}
        status = table_status.derive_status("T2", ["T1", "T2"], order_map, {})
        self.assertEqual(status, table_status.OCCUPIED)

    def test_status_precedence_order_beats_reservation(self):
        """Buyurtma bronni ustunlik bilan bosadi (TZ §5)."""
        order_map = {"T1": frappe._dict(name="INV-1")}
        reservations = {"T1": frappe._dict(name="RES-1")}

        self.assertEqual(
            table_status.derive_status("T1", ["T1"], order_map, reservations),
            table_status.OCCUPIED,
        )
        self.assertEqual(
            table_status.derive_status("T1", ["T1"], {}, reservations),
            table_status.RESERVED,
        )
        self.assertEqual(
            table_status.derive_status("T1", ["T1"], {}, {}),
            table_status.AVAILABLE,
        )

    def test_oldest_order_wins_for_table(self):
        """Bo'lingan hisobda stolni birinchi band qilgan chek asosiy bo'ladi."""
        orders = [
            frappe._dict(name="INV-1", restaurant_table="T1", custom_merged_tables=None),
            frappe._dict(name="INV-2", restaurant_table="T1", custom_merged_tables=None),
        ]
        self.assertEqual(table_status.map_orders_to_tables(orders)["T1"].name, "INV-1")
        self.assertEqual(table_status.count_orders_per_table(orders)["T1"], 2)


class TestFloorLayout(FrappeTestCase):
    """Zal rejasi koordinatalari (TZ §29)."""

    def test_auto_grid_when_no_stored_layout(self):
        """Koordinata bo'lmasa stollar ustma-ust tushmasligi kerak."""
        tables = [
            {"name": f"T{i}", "layout_x": 0, "layout_y": 0,
             "layout_width": 0, "layout_height": 0}
            for i in range(7)
        ]
        table_status.apply_layout(tables)

        positions = {(t["layout"]["x"], t["layout"]["y"]) for t in tables}
        self.assertEqual(len(positions), 7, "avtomatik to'r takrorlanmasligi kerak")
        self.assertTrue(all(t["layout"]["auto"] for t in tables))
        self.assertTrue(all(t["layout"]["width"] > 0 for t in tables))

    def test_rooms_do_not_overlap_in_all_rooms_view(self):
        """Har bir zalning koordinatasi o'z (0,0) idan boshlanadi.

        "Barcha zallar" ko'rinishida ular bloklarga ajratilmasa, "Ichki zal"
        ning 1-stoli "Tashqi zal" ning 1-stoli ustiga tushib qoladi.
        """
        tables = [
            {"name": "I-1", "restaurant_room": "Ichki zal", "layout_x": 0,
             "layout_y": 0, "layout_width": 120, "layout_height": 120},
            {"name": "I-2", "restaurant_room": "Ichki zal", "layout_x": 152,
             "layout_y": 0, "layout_width": 120, "layout_height": 120},
            {"name": "T-1", "restaurant_room": "Tashqi zal", "layout_x": 0,
             "layout_y": 0, "layout_width": 120, "layout_height": 120},
            {"name": "T-2", "restaurant_room": "Tashqi zal", "layout_x": 152,
             "layout_y": 0, "layout_width": 120, "layout_height": 120},
        ]
        bands = table_status.apply_layout(tables, stack_rooms=True)

        by_name = {t["name"]: t["layout"] for t in tables}
        boxes = [(t["name"], by_name[t["name"]]) for t in tables]

        # Hech bir juftlik kesishmasligi kerak.
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                (n1, a), (n2, b) = boxes[i], boxes[j]
                overlap = (
                    a["x"] < b["x"] + b["width"]
                    and b["x"] < a["x"] + a["width"]
                    and a["y"] < b["y"] + b["height"]
                    and b["y"] < a["y"] + a["height"]
                )
                self.assertFalse(overlap, f"{n1} va {n2} ustma-ust tushdi")

        self.assertEqual([b["room"] for b in bands], ["Ichki zal", "Tashqi zal"])
        self.assertEqual([b["count"] for b in bands], [2, 2])
        # Ikkinchi zal birinchisidan pastda turishi kerak.
        self.assertGreater(bands[1]["y"], bands[0]["y"])

    def test_room_title_never_overlaps_tables(self):
        """Zal sarlavhasi stol ustiga CHIQMASLIGI kerak.

        Sarlavha `y .. y + header_height` oralig'ida turadi; shu oraliqda
        birorta stol bo'lsa — nom stolni to'sib qo'yadi (ko'rilgan xato).
        """
        tables = [
            {"name": "I-1", "restaurant_room": "Ichki zal", "layout_x": 0,
             "layout_y": 0, "layout_width": 120, "layout_height": 120},
            {"name": "T-1", "restaurant_room": "Tashqi zal", "layout_x": 0,
             "layout_y": 0, "layout_width": 120, "layout_height": 120},
        ]
        bands = table_status.apply_layout(tables, stack_rooms=True)

        for band in bands:
            self.assertGreater(
                band["header_height"], 0, "sarlavha uchun joy ajratilmagan"
            )
            title_top = band["y"]
            title_bottom = band["y"] + band["header_height"]

            for t in tables:
                top = t["layout"]["y"]
                bottom = top + t["layout"]["height"]
                self.assertFalse(
                    top < title_bottom and title_top < bottom,
                    f"{band['room']} sarlavhasi {t['name']} stoli ustiga chiqdi",
                )

    def test_first_room_title_is_inside_canvas(self):
        """Birinchi zal sarlavhasi manfiy koordinataga tushmasligi kerak.

        Ilgari sarlavha `top: -11px` bilan blokdan TASHQARIGA chiqarilgan edi —
        birinchi zalda u tuval chegarasidan chiqib ketardi.
        """
        tables = [
            {"name": "I-1", "restaurant_room": "Ichki zal", "layout_x": 0,
             "layout_y": 0, "layout_width": 120, "layout_height": 120},
        ]
        bands = table_status.apply_layout(tables, stack_rooms=True)

        self.assertGreaterEqual(bands[0]["y"], 0)
        self.assertGreaterEqual(tables[0]["layout"]["y"], bands[0]["header_height"])

    def test_single_room_view_keeps_original_coordinates(self):
        """Bitta zal tanlanganda koordinatalar SURILMAYDI."""
        tables = [
            {"name": "I-1", "restaurant_room": "Ichki zal", "layout_x": 0,
             "layout_y": 0, "layout_width": 120, "layout_height": 120},
        ]
        bands = table_status.apply_layout(tables, stack_rooms=False)
        self.assertEqual(bands, [])
        self.assertEqual(tables[0]["layout"]["y"], 0)

    def test_stored_layout_is_respected(self):
        """URY Table yagona manba — saqlangan koordinata o'zgartirilmaydi."""
        tables = [
            {"name": "T1", "layout_x": 40, "layout_y": 90,
             "layout_width": 200, "layout_height": 100},
            {"name": "T2", "layout_x": 0, "layout_y": 0,
             "layout_width": 0, "layout_height": 0},
        ]
        table_status.apply_layout(tables)

        self.assertEqual(tables[0]["layout"]["x"], 40)
        self.assertEqual(tables[0]["layout"]["width"], 200)
        self.assertFalse(tables[0]["layout"]["auto"])

    def test_unpositioned_table_does_not_land_on_a_positioned_one(self):
        """Desk'da koordinatasiz qo'shilgan stol (0,0) ga tushmasligi kerak.

        Menejer yangi `URY Table` yaratganda `layout_*` maydonlari bo'sh
        qoladi. Ilgari bunday stol saqlangan koordinata deb qabul qilinib,
        (0,0) dagi mavjud stol ustiga chiqib qolardi.
        """
        tables = [
            {"name": "bor", "layout_x": 0, "layout_y": 0,
             "layout_width": 120, "layout_height": 120},      # (0,0) da HAQIQIY stol
            {"name": "yangi", "layout_x": 0, "layout_y": 0,
             "layout_width": 0, "layout_height": 0},          # Desk'dan, koordinatasiz
        ]
        table_status.apply_layout(tables)

        a, b = tables[0]["layout"], tables[1]["layout"]
        self.assertFalse(a["auto"])
        self.assertTrue(b["auto"], "koordinatasiz stol avtomatik joylashishi kerak")

        overlap = (
            a["x"] < b["x"] + b["width"]
            and b["x"] < a["x"] + a["width"]
            and a["y"] < b["y"] + b["height"]
            and b["y"] < a["y"] + a["height"]
        )
        self.assertFalse(overlap, "yangi stol mavjud stol ustiga tushdi")
        self.assertGreater(b["y"], a["y"], "yangi stol pastda turishi kerak")

    def test_is_positioned_heuristic(self):
        self.assertFalse(
            table_status.is_positioned(
                {"layout_x": 0, "layout_y": 0, "layout_width": 0, "layout_height": 0}
            )
        )
        # (0,0) da turgan, lekin o'lchami bor stol — joylashtirilgan hisoblanadi.
        self.assertTrue(
            table_status.is_positioned(
                {"layout_x": 0, "layout_y": 0, "layout_width": 120, "layout_height": 120}
            )
        )
        self.assertTrue(table_status.is_positioned({"layout_x": 152, "layout_y": 0}))


class TestServiceCharge(FrappeTestCase):
    """Xizmat haqi — foiz kodda emas, ERPNext shablonida (TZ §8)."""

    def test_rate_comes_from_tax_template_not_code(self):
        restaurant = frappe.db.get_value("URY Restaurant", {}, "name")
        if not restaurant:
            self.skipTest("URY Restaurant yo'q")

        config = cashier_billing.get_service_charge_config(restaurant)
        if not config["enabled"]:
            self.skipTest("Xizmat haqi sozlanmagan")

        rows = frappe.get_all(
            "Sales Taxes and Charges",
            filters={"parent": config["template"], "account_head": config["account"]},
            fields=["rate"],
        )
        self.assertEqual(config["rate"], rows[0].rate)

    def test_build_bill_reads_totals_never_recomputes(self):
        """`build_bill` summalarni O'ZI hisoblamaydi — hujjatdan oladi."""
        doc = frappe._dict(
            doctype="POS Invoice",
            name="TEST-INV",
            docstatus=0,
            creation=None,
            modified=None,
            customer="X",
            currency="UZS",
            net_total=100000,
            total=100000,
            grand_total=112000,
            rounded_total=112000,
            total_taxes_and_charges=12000,
            items=[],
            taxes=[],
            get=lambda key, default=None: None,
            precision=lambda field: 2,
        )
        bill = cashier_billing.build_bill(doc, include_kitchen=False)

        self.assertEqual(bill["subtotal"], 100000)
        self.assertEqual(bill["grand_total"], 112000)
        self.assertEqual(bill["total_taxes"], 12000)


class TestCashierPermissions(FrappeTestCase):
    """Server tomonidagi ruxsat nazorati (TZ §17)."""

    def test_user_without_role_is_rejected(self):
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": "rc-test-nobody@example.com",
                "first_name": "Ruxsatsiz",
                "send_welcome_email": 0,
            }
        ).insert(ignore_permissions=True)

        frappe.set_user(user.name)
        try:
            with self.assertRaises(frappe.PermissionError):
                cashier_permissions.require_cashier()
        finally:
            frappe.set_user("Administrator")

    def test_cashier_role_is_accepted(self):
        frappe.set_user("Administrator")
        cashier_permissions.require_cashier()  # xato bermasligi kerak

    def test_unknown_table_is_rejected(self):
        scope = cashier_permissions.resolve_scope()
        with self.assertRaises(Exception):
            cashier_permissions.assert_table_in_scope("MAVJUD-EMAS", scope)


class TestTableReleaseOnPayment(FrappeTestCase):
    """TZ §23 — stol faqat HAQIQATDA bo'shaganda bo'shatiladi.

    Bu — tuzatilgan xatoning regressiya testi. Ilgari `on_submit` stolni
    SHARTSIZ bo'shatardi, ya'ni hisob bo'lingan holatda birinchi chek
    to'langanida stol bo'shab qolardi.
    """

    def setUp(self):
        self.table = _free_table()
        if not self.table:
            self.skipTest("Ochiq cheksiz URY Table yo'q")
        self.branch = frappe.db.get_value("URY Table", self.table, "branch")
        self.created = []

    def tearDown(self):
        """`FrappeTestCase` rollback'i SINF darajasida ishlaydi (frappe/tests/utils.py:46),
        ya'ni bitta sinfdagi testlar bir-birining yozuvlarini ko'radi. Shuning
        uchun har bir test o'zidan keyin tozalaydi."""
        for name in self.created:
            frappe.db.delete("POS Invoice", {"name": name})
        frappe.db.set_value(
            "URY Table", self.table, "occupied", 0, update_modified=False
        )

    def _draft(self, printed=0):
        """Stolga bog'langan qoralama chek yozuvini yaratadi (yengil usul).

        To'liq `POS Invoice` hujjati kerak emas — `_open_invoices_for` faqat
        `docstatus`, `restaurant_table` va `custom_cancelled` ni o'qiydi.
        """
        name = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabPOS Invoice`
                (name, creation, modified, owner, modified_by, docstatus,
                 restaurant_table, branch, invoice_printed, custom_cancelled)
            values (%s, now(), now(), 'Administrator', 'Administrator', 0,
                 %s, %s, %s, 0)
            """,
            (name, self.table, self.branch, printed),
        )
        self.created.append(name)
        return name

    def test_table_stays_occupied_while_another_bill_is_open(self):
        other = self._draft(printed=1)
        paid = self._draft(printed=1)

        frappe.db.set_value("URY Table", self.table, "occupied", 1, update_modified=False)

        doc = frappe._dict(
            doctype="POS Invoice",
            name=paid,
            restaurant_table=self.table,
            custom_merged_tables=None,
            get=lambda key, default=None: {
                "restaurant_table": self.table,
                "custom_merged_tables": None,
            }.get(key, default),
        )
        pos_invoice_override._reconcile_tables(doc)

        self.assertEqual(
            frappe.db.get_value("URY Table", self.table, "occupied"),
            1,
            f"{other} hali to'lanmagan — stol band qolishi kerak",
        )

    def test_table_is_released_when_last_bill_is_paid(self):
        paid = self._draft(printed=1)
        frappe.db.set_value("URY Table", self.table, "occupied", 1, update_modified=False)

        doc = frappe._dict(
            doctype="POS Invoice",
            name=paid,
            restaurant_table=self.table,
            custom_merged_tables=None,
            get=lambda key, default=None: {
                "restaurant_table": self.table,
                "custom_merged_tables": None,
            }.get(key, default),
        )
        pos_invoice_override._reconcile_tables(doc)

        self.assertEqual(
            frappe.db.get_value("URY Table", self.table, "occupied"),
            0,
            "boshqa ochiq chek yo'q — stol bo'shashi kerak",
        )

    def test_cancelled_draft_does_not_hold_the_table(self):
        """Bekor qilingan qoralama stolni ushlab turmasligi kerak."""
        if not frappe.db.has_column("POS Invoice", "custom_cancelled"):
            self.skipTest("custom_cancelled maydoni yo'q")

        cancelled = self._draft(printed=0)
        frappe.db.set_value("POS Invoice", cancelled, "custom_cancelled", 1)
        paid = self._draft(printed=1)

        frappe.db.set_value("URY Table", self.table, "occupied", 1, update_modified=False)

        doc = frappe._dict(
            doctype="POS Invoice",
            name=paid,
            restaurant_table=self.table,
            custom_merged_tables=None,
            get=lambda key, default=None: {
                "restaurant_table": self.table,
                "custom_merged_tables": None,
            }.get(key, default),
        )
        pos_invoice_override._reconcile_tables(doc)

        self.assertEqual(frappe.db.get_value("URY Table", self.table, "occupied"), 0)


class TestActiveOrderAndBillPreview(FrappeTestCase):
    """Phase 2 — faol buyurtma va hisob ko'rinishi (TZ §1-§5, §8, §9)."""

    def setUp(self):
        self.scope = cashier_permissions.resolve_scope()
        self.table = _free_table(self.scope.branch)
        if not self.table:
            self.skipTest("Ochiq cheksiz URY Table yo'q")
        self.created = []
        self.original_occupied = frappe.db.get_value(
            "URY Table", self.table, "occupied"
        )

    def tearDown(self):
        for name in self.created:
            frappe.db.delete("POS Invoice", {"name": name})
        frappe.db.set_value(
            "URY Table",
            self.table,
            "occupied",
            self.original_occupied,
            update_modified=False,
        )

    # ── §8: OCCUPIED bayrog'i bor, buyurtma yo'q ──────────────────────

    def test_stale_occupied_flag_reports_issue_instead_of_empty_bill(self):
        from ozturkapp.ozturkapp.api.table import get_table_detail

        frappe.db.set_value(
            "URY Table", self.table, "occupied", 1, update_modified=False
        )

        detail = get_table_detail(self.table)

        self.assertIsNotNone(detail["issue"], "nomuvofiqlik haqida xabar yo'q")
        self.assertEqual(detail["issue"]["code"], "STALE_OCCUPIED_FLAG")
        self.assertTrue(detail["issue"]["message"])
        self.assertIsNone(detail["bill"], "jim bo'sh hisob ko'rsatilmasligi kerak")

    def test_no_order_is_created_by_viewing_a_table(self):
        """TZ §8/§12#10 — ko'rish hech narsa yaratmaydi."""
        from ozturkapp.ozturkapp.api.table import get_table_detail

        frappe.db.set_value(
            "URY Table", self.table, "occupied", 1, update_modified=False
        )
        before = frappe.db.count("POS Invoice")

        get_table_detail(self.table)
        get_table_detail(self.table)

        self.assertEqual(frappe.db.count("POS Invoice"), before)

    def test_available_table_has_no_issue(self):
        from ozturkapp.ozturkapp.api.table import get_table_detail

        frappe.db.set_value(
            "URY Table", self.table, "occupied", 0, update_modified=False
        )
        detail = get_table_detail(self.table)

        self.assertEqual(detail["status"], table_status.AVAILABLE)
        self.assertIsNone(detail["issue"])

    # ── §3-§4: hisob ko'rinishi tuzilmasi ─────────────────────────────

    def test_bill_preview_exposes_every_required_field(self):
        """TZ §4 da sanab o'tilgan kalitlarning hammasi bo'lishi kerak."""
        doc = frappe._dict(
            doctype="POS Invoice",
            name="TEST",
            docstatus=0,
            creation=None,
            modified=None,
            customer="X",
            currency="UZS",
            net_total=100000,
            total=100000,
            grand_total=112000,
            rounded_total=112000,
            total_taxes_and_charges=12000,
            items=[],
            taxes=[],
            get=lambda key, default=None: None,
            precision=lambda field: 2,
        )
        bill = cashier_billing.build_bill(doc, include_kitchen=False)

        for field in (
            "invoice", "table", "waiter", "customer", "pax", "items",
            "subtotal", "taxes", "service_charge", "grand_total", "currency",
        ):
            self.assertIn(field, bill, f"'{field}' maydoni yo'q")

    def test_bill_preview_is_read_only_by_construction(self):
        """`build_bill` hujjatga YOZMAYDI — faqat o'qiydi (TZ §5)."""
        import inspect

        source = inspect.getsource(cashier_billing.build_bill)
        for forbidden in (".save(", ".submit(", ".insert(", "db_set", "set_value"):
            self.assertNotIn(
                forbidden, source, f"build_bill ichida '{forbidden}' bo'lmasligi kerak"
            )

    def test_bill_preview_api_never_mutates(self):
        """`get_order_bill_preview` ham yozuv amali bajarmaydi."""
        import inspect

        from ozturkapp.ozturkapp.api.order import get_order_bill_preview

        source = inspect.getsource(get_order_bill_preview)
        for forbidden in (".save(", ".submit(", ".insert(", "db_set"):
            self.assertNotIn(forbidden, source)


class TestPaidOrderHistory(FrappeTestCase):
    """Kassa tarixi — o'tgan davrda to'langan cheklar ro'yxati (FAQAT O'QISH)."""

    def setUp(self):
        self.scope = cashier_permissions.resolve_scope()
        self.table = _free_table(self.scope.branch)
        if not self.table:
            self.skipTest("Ochiq cheksiz URY Table yo'q")
        self.invoices = []
        self.shifts = []

    def tearDown(self):
        for name in self.invoices:
            frappe.db.delete(
                "Sales Invoice Payment", {"parent": name, "parenttype": "POS Invoice"}
            )
            frappe.db.delete("POS Invoice", {"name": name})
        for name in self.shifts:
            frappe.db.delete("POS Closing Entry", {"pos_opening_entry": name})
            frappe.db.delete("POS Opening Entry", {"name": name})
        frappe.set_user("Administrator")
        for user in getattr(self, "users", []):
            frappe.delete_doc("User", user, force=True, ignore_permissions=True)

    def _cashier(self):
        """Oddiy kassir (`URY Cashier`, menejer emas) — hozirgi foydalanuvchi qilib qo'yiladi."""
        email = f"history-{frappe.generate_hash(length=6)}@example.com"
        frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Tarix",
                "send_welcome_email": 0,
                "roles": [{"role": "URY Cashier"}],
            }
        ).insert(ignore_permissions=True)
        self.users = getattr(self, "users", []) + [email]
        frappe.set_user(email)
        return email

    def _current_shift(self, name):
        """Kassa sahifasi "hozirgi ochiq smena" deb hisoblaydigan smenani belgilaydi."""
        return mock.patch.object(cashier_permissions, "open_shift_name", return_value=name)

    def _shift(self, start, end=None, status=None, profile=None):
        """Smena (POS Opening Entry) va `end` berilsa yakuniy POS Closing Entry.

        `db_insert` — sinov faqat vaqt oynasini o'qiydi, ERPNext'ning ochish/yopish
        qoidalari (bir vaqtda bitta ochiq smena va h.k.) bu yerda kerak emas.
        """
        profile = profile or self.scope.pos_profile
        name = "TEST-OPE-" + frappe.generate_hash(length=8)
        opening = frappe.get_doc(
            {
                "doctype": "POS Opening Entry",
                "name": name,
                "pos_profile": profile,
                "user": "Administrator",
                "company": self.scope.company,
                "period_start_date": start,
                "posting_date": str(start)[:10],
                "status": status or ("Closed" if end else "Open"),
                "docstatus": 1,
            }
        )
        opening.db_insert()
        self.shifts.append(name)

        if end:
            closing = frappe.get_doc(
                {
                    "doctype": "POS Closing Entry",
                    "name": "TEST-CLO-" + frappe.generate_hash(length=8),
                    "pos_opening_entry": name,
                    "pos_profile": profile,
                    "user": "Administrator",
                    "company": self.scope.company,
                    "period_start_date": start,
                    "period_end_date": end,
                    "posting_date": str(end)[:10],
                    "docstatus": 1,
                }
            )
            closing.db_insert()
        return name

    def _paid(
        self, posting_date, amount=100000, customer_name="Test mijoz", mode="Cash",
        table=None, waiter=None, posting_time="12:00:00",
    ):
        name = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabPOS Invoice`
                (name, creation, modified, owner, modified_by, docstatus,
                 restaurant_table, branch, invoice_printed, custom_cancelled,
                 customer_name, cashier, waiter, posting_date, posting_time,
                 grand_total, rounded_total, paid_amount)
            values (%s, now(), now(), 'Administrator', 'Administrator', 1,
                 %s, %s, 1, 0, %s, 'Administrator', %s, %s, %s,
                 %s, %s, %s)
            """,
            (
                name, table or self.table, self.scope.branch, customer_name,
                waiter, posting_date, posting_time, amount, amount, amount,
            ),
        )
        frappe.db.sql(
            """
            insert into `tabSales Invoice Payment`
                (name, creation, modified, owner, modified_by, docstatus,
                 parent, parenttype, parentfield, idx, mode_of_payment, amount)
            values (%s, now(), now(), 'Administrator', 'Administrator', 1,
                 %s, 'POS Invoice', 'payments', 1, %s, %s)
            """,
            (frappe.generate_hash(length=10), name, mode, amount),
        )
        self.invoices.append(name)
        return name

    def test_only_paid_orders_in_range_are_returned(self):
        from ozturkapp.ozturkapp.api.order import get_paid_orders

        today = frappe.utils.today()
        yesterday = frappe.utils.add_days(today, -1)

        today_inv = self._paid(today)
        old_inv = self._paid(yesterday)

        rows = get_paid_orders(date_from=today, date_to=today)
        names = {r["invoice"] for r in rows}
        self.assertIn(today_inv, names)
        self.assertNotIn(old_inv, names)

    def test_includes_payment_modes_and_amount(self):
        from ozturkapp.ozturkapp.api.order import get_paid_orders

        today = frappe.utils.today()
        inv = self._paid(today, amount=55000, mode="Cash")

        rows = get_paid_orders(date_from=today, date_to=today)
        row = next(r for r in rows if r["invoice"] == inv)
        self.assertEqual(row["amount"], 55000)
        self.assertEqual([p["mode_of_payment"] for p in row["payments"]], ["Cash"])

    def test_search_filters_by_invoice(self):
        from ozturkapp.ozturkapp.api.order import get_paid_orders

        today = frappe.utils.today()
        inv = self._paid(today)
        other = self._paid(today)

        rows = get_paid_orders(date_from=today, date_to=today, search=inv)
        names = {r["invoice"] for r in rows}
        self.assertIn(inv, names)
        self.assertNotIn(other, names)

    def test_table_filter(self):
        from ozturkapp.ozturkapp.api.order import get_paid_orders

        today = frappe.utils.today()
        here = self._paid(today, table=self.table)
        elsewhere = self._paid(today, table="Test-Table-Boshqa")

        rows = get_paid_orders(date_from=today, date_to=today, table=self.table)
        names = {r["invoice"] for r in rows}
        self.assertIn(here, names)
        self.assertNotIn(elsewhere, names)

    def test_waiter_filter(self):
        from ozturkapp.ozturkapp.api.order import get_paid_orders

        today = frappe.utils.today()
        this_waiter = self._paid(today, waiter="waiter-a@example.com")
        other_waiter = self._paid(today, waiter="waiter-b@example.com")

        rows = get_paid_orders(date_from=today, date_to=today, waiter="waiter-a@example.com")
        names = {r["invoice"] for r in rows}
        self.assertIn(this_waiter, names)
        self.assertNotIn(other_waiter, names)

    def test_filter_options_list_actual_tables_and_waiters(self):
        from ozturkapp.ozturkapp.api.order import get_paid_order_filter_options

        today = frappe.utils.today()
        self._paid(today, table="Test-Table-Options", waiter="waiter-c@example.com")

        options = get_paid_order_filter_options()
        self.assertIn("Test-Table-Options", options["tables"])
        self.assertIn(
            "waiter-c@example.com", [w["value"] for w in options["waiters"]]
        )

    # ── Smena oynasi: kassa ochilgandan yopilgunicha ──────────────────

    def _names(self, **kwargs):
        from ozturkapp.ozturkapp.api.order import get_paid_orders

        return {r["invoice"] for r in get_paid_orders(**kwargs)}

    def test_closed_shift_shows_only_invoices_between_opening_and_closing(self):
        today = frappe.utils.today()
        shift = self._shift(f"{today} 10:00:00", f"{today} 14:00:00")

        before = self._paid(today, posting_time="09:59:59")
        first = self._paid(today, posting_time="10:00:01")
        last = self._paid(today, posting_time="13:59:59")
        after = self._paid(today, posting_time="14:00:01")

        names = self._names(shift=shift)
        self.assertEqual(names & {before, first, last, after}, {first, last})

    def test_open_shift_has_no_upper_bound_and_crosses_midnight(self):
        today = frappe.utils.today()
        yesterday = frappe.utils.add_days(today, -1)
        shift = self._shift(f"{yesterday} 22:00:00")

        early = self._paid(yesterday, posting_time="21:59:59")
        late = self._paid(yesterday, posting_time="23:30:00")
        next_day = self._paid(today, posting_time="01:15:00")

        # Sana filtri bilan `bugun` ro'yxatida faqat oxirgisi bo'lardi.
        names = self._names(shift=shift)
        self.assertEqual(names & {early, late, next_day}, {late, next_day})
        self.assertEqual(self._names(date_from=today, date_to=today) & {late, next_day}, {next_day})

    def test_two_shifts_on_one_day_do_not_mix(self):
        today = frappe.utils.today()
        morning = self._shift(f"{today} 08:00:00", f"{today} 15:00:00")
        evening = self._shift(f"{today} 18:00:00", f"{today} 23:00:00")

        a = self._paid(today, posting_time="09:00:00")
        b = self._paid(today, posting_time="19:00:00")
        between = self._paid(today, posting_time="16:30:00")

        mine = {a, b, between}
        self.assertEqual(self._names(shift=morning) & mine, {a})
        self.assertEqual(self._names(shift=evening) & mine, {b})

    def test_shift_ignores_the_date_filter(self):
        today = frappe.utils.today()
        shift = self._shift(f"{today} 10:00:00", f"{today} 14:00:00")
        inv = self._paid(today, posting_time="11:00:00")

        long_ago = frappe.utils.add_days(today, -30)
        self.assertIn(inv, self._names(shift=shift, date_from=long_ago, date_to=long_ago))

    def test_shift_combines_with_table_and_waiter_filters(self):
        today = frappe.utils.today()
        shift = self._shift(f"{today} 10:00:00", f"{today} 14:00:00")

        wanted = self._paid(today, table=self.table, waiter="waiter-a@example.com", posting_time="11:00:00")
        other_table = self._paid(today, table="Test-Table-Boshqa", waiter="waiter-a@example.com", posting_time="11:00:00")
        other_waiter = self._paid(today, table=self.table, waiter="waiter-b@example.com", posting_time="11:00:00")

        names = self._names(shift=shift, table=self.table, waiter="waiter-a@example.com")
        self.assertEqual(names & {wanted, other_table, other_waiter}, {wanted})

    def test_closed_shift_without_closing_entry_ends_when_the_next_one_opens(self):
        """Ko'p kassirli rejimda `Sub POS Closing` smenani yakuniy hujjatsiz `Closed` qiladi."""
        today = frappe.utils.today()
        first = self._shift(f"{today} 08:00:00", status="Closed")
        self._shift(f"{today} 13:00:00")

        inside = self._paid(today, posting_time="10:00:00")
        outside = self._paid(today, posting_time="13:30:00")

        self.assertEqual(self._names(shift=first) & {inside, outside}, {inside})

    def test_shift_of_another_pos_profile_is_rejected(self):
        today = frappe.utils.today()
        foreign = self._shift(f"{today} 10:00:00", f"{today} 14:00:00", profile="Test-Boshqa-Profil")

        with self.assertRaises(cashier_permissions.CashierPermissionError):
            self._names(shift=foreign)

    def test_unknown_shift_is_rejected(self):
        with self.assertRaises(frappe.DoesNotExistError):
            self._names(shift="TEST-OPE-yo'q")

    def test_shift_name_must_be_plain_text(self):
        with self.assertRaises(frappe.DoesNotExistError):
            self._names(shift='["POS-OPE-2026-00001"]')

    def test_filter_options_list_shifts_newest_first_with_their_windows(self):
        from ozturkapp.ozturkapp.api.order import get_paid_order_filter_options

        today = frappe.utils.today()
        closed = self._shift(f"{today} 01:00:00", f"{today} 02:00:00")
        current = self._shift(f"{today} 03:00:00")

        shifts = get_paid_order_filter_options()["shifts"]
        by_name = {s["name"]: s for s in shifts}

        self.assertTrue(by_name[current]["open"])
        self.assertIsNone(by_name[current]["closed_at"])
        self.assertFalse(by_name[closed]["open"])
        self.assertTrue(by_name[closed]["closed_at"].startswith(f"{today} 02:00:00"))
        self.assertTrue(by_name[closed]["opened_at"].startswith(f"{today} 01:00:00"))

        names = [s["name"] for s in shifts]
        self.assertLess(names.index(current), names.index(closed))

    def test_shifts_of_other_pos_profiles_are_not_listed(self):
        from ozturkapp.ozturkapp.api.order import get_paid_order_filter_options

        today = frappe.utils.today()
        foreign = self._shift(f"{today} 10:00:00", f"{today} 14:00:00", profile="Test-Boshqa-Profil")

        listed = {s["name"] for s in get_paid_order_filter_options()["shifts"]}
        self.assertNotIn(foreign, listed)

    # ── Kassir oldingi smenalarni KO'RMAYDI ───────────────────────────

    def _two_shifts(self):
        """Yopilgan smena (01:00–02:00) va hozirgi ochiq smena (03:00 dan), har birida bittadan chek."""
        today = frappe.utils.today()
        old = self._shift(f"{today} 01:00:00", f"{today} 02:00:00")
        current = self._shift(f"{today} 03:00:00")
        old_inv = self._paid(today, table="Test-Table-Eski", waiter="waiter-old@example.com", posting_time="01:30:00")
        new_inv = self._paid(today, table="Test-Table-Yangi", waiter="waiter-new@example.com", posting_time="03:30:00")
        return today, old, current, old_inv, new_inv

    def test_cashier_sees_only_the_current_open_shift(self):
        today, old, current, old_inv, new_inv = self._two_shifts()
        self._cashier()

        with self._current_shift(current):
            names = self._names()
        self.assertEqual(names & {old_inv, new_inv}, {new_inv})

    def test_cashier_cannot_ask_for_a_previous_shift(self):
        today, old, current, old_inv, new_inv = self._two_shifts()
        self._cashier()

        with self._current_shift(current):
            with self.assertRaises(cashier_permissions.CashierPermissionError):
                self._names(shift=old)
            # O'z smenasini aniq so'rasa — o'tadi.
            self.assertEqual(self._names(shift=current) & {old_inv, new_inv}, {new_inv})

    def test_cashier_cannot_bypass_the_limit_with_dates(self):
        today, old, current, old_inv, new_inv = self._two_shifts()
        self._cashier()

        long_ago = frappe.utils.add_days(today, -365)
        with self._current_shift(current):
            names = self._names(date_from=long_ago, date_to=today)
        self.assertEqual(names & {old_inv, new_inv}, {new_inv})

    def test_cashier_sees_nothing_when_no_shift_is_open(self):
        from ozturkapp.ozturkapp.api.order import get_paid_order_filter_options

        self._two_shifts()
        self._cashier()

        with self._current_shift(""):
            self.assertEqual(self._names(), set())
            self.assertEqual(
                get_paid_order_filter_options(), {"tables": [], "waiters": [], "shifts": []}
            )

    def test_cashier_filter_options_are_limited_to_the_current_shift(self):
        from ozturkapp.ozturkapp.api.order import get_paid_order_filter_options

        today, old, current, old_inv, new_inv = self._two_shifts()
        self._cashier()

        with self._current_shift(current):
            options = get_paid_order_filter_options()

        self.assertEqual([s["name"] for s in options["shifts"]], [current])
        self.assertIn("Test-Table-Yangi", options["tables"])
        self.assertNotIn("Test-Table-Eski", options["tables"])
        waiters = [w["value"] for w in options["waiters"]]
        self.assertIn("waiter-new@example.com", waiters)
        self.assertNotIn("waiter-old@example.com", waiters)

    def test_manager_still_sees_previous_shifts(self):
        """Administrator (`System Manager`) — menejer huquqi: hamma smenalar."""
        from ozturkapp.ozturkapp.api.order import get_paid_order_filter_options

        today, old, current, old_inv, new_inv = self._two_shifts()

        with self._current_shift(current):
            self.assertEqual(self._names(shift=old) & {old_inv, new_inv}, {old_inv})
            options = get_paid_order_filter_options()

        listed = [s["name"] for s in options["shifts"]]
        self.assertIn(old, listed)
        self.assertIn(current, listed)
        self.assertIn("Test-Table-Eski", options["tables"])

    def test_never_mutates(self):
        """`get_paid_orders` / `get_paid_order_filter_options` — faqat o'qish."""
        import inspect

        from ozturkapp.ozturkapp.api.order import (
            get_paid_order_filter_options,
            get_paid_orders,
        )

        for fn in (get_paid_orders, get_paid_order_filter_options):
            source = inspect.getsource(fn)
            for forbidden in (".save(", ".submit(", ".insert(", "db_set", "set_value"):
                self.assertNotIn(forbidden, source)

    def test_requires_cashier_role(self):
        from ozturkapp.ozturkapp.api.order import get_paid_orders

        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": "history-test-nobody@example.com",
                "first_name": "Ruxsatsiz",
                "send_welcome_email": 0,
            }
        ).insert(ignore_permissions=True)
        frappe.set_user(user.name)
        try:
            with self.assertRaises(cashier_permissions.CashierPermissionError):
                get_paid_orders()
        finally:
            frappe.set_user("Administrator")
            frappe.delete_doc("User", user.name, force=True, ignore_permissions=True)


class TestCashierCannotEditOrder(FrappeTestCase):
    """TZ §2, §12#11 — kassa sahifasi buyurtma tahrirlash vositasi EMAS."""

    FORBIDDEN = ("add_item", "remove_item", "update_qty", "update_item", "set_rate")

    def test_no_item_mutation_endpoints_exist(self):
        from ozturkapp.ozturkapp.api import billing, cashier, order, table

        for module in (billing, cashier, order, table):
            for name in dir(module):
                self.assertNotIn(
                    name,
                    self.FORBIDDEN,
                    f"{module.__name__}.{name} — mahsulot tahrirlash endpoint'i",
                )

    def test_page_script_has_no_item_editing_calls(self):
        """Yadroda taom tahrirlash yo'q.

        Kassadan buyurtma qabul qilish (`cashier_orders`) — POS Profile
        bayrog'i bilan yoqiladigan ALOHIDA funksiya moduli (`features/`);
        u o'z testlari bilan qoplanadi va shu yerda tekshirilmaydi.
        """
        page = cashier_page()

        for needle in ("sync_order", "add_item", "remove_item", "cart"):
            self.assertNotIn(
                needle,
                page.script,
                f"kassa yadrosida '{needle}' bo'lmasligi kerak",
            )


class TestShiftManagement(FrappeTestCase):
    """Kassa smenasi — ochish va yopish (TZ: kassa ochish/yopish)."""

    def test_endpoints_exist(self):
        from ozturkapp.ozturkapp.api import cashier as cashier_api

        for fn in ("open_shift", "get_shift_closing_data", "close_shift"):
            self.assertTrue(hasattr(cashier_api, fn), f"'{fn}' yo'q")

    def test_logic_is_not_duplicated(self):
        """Smena mantig'i `desktop_pos` dan QAYTA ISHLATILADI."""
        import inspect

        from ozturkapp.ozturkapp.api import cashier as cashier_api

        self.assertIn("createPosOpening", inspect.getsource(cashier_api.open_shift))
        self.assertIn("createPosClosing", inspect.getsource(cashier_api.close_shift))
        self.assertIn(
            "getPosClosingData", inspect.getsource(cashier_api.get_shift_closing_data)
        )

    def test_cashier_never_sees_expected_amounts(self):
        """KO'R SANOQ — kassirga umumiy savdo va kutilgan summa berilmaydi."""
        from ozturkapp.ozturkapp.api import cashier as cashier_api

        if not frappe.db.exists("POS Opening Entry", {"status": "Open", "docstatus": 1}):
            self.skipTest("Ochiq smena yo'q")

        data = cashier_api.get_shift_closing_data()
        for leaked in ("grand_total", "net_total", "reconciliation", "expected_amount"):
            self.assertNotIn(leaked, data, f"'{leaked}' kassirga yuborilmasligi kerak")

        # Ruxsat etilgan yagona raqam.
        self.assertIn("total_invoices", data)
        self.assertIn("cash_modes", data)

    def test_only_cash_can_be_entered(self):
        """Kassir bank/karta summasini yubora olmasligi kerak."""
        from ozturkapp.ozturkapp.api.cashier import _cash_modes, _parse_counted_cash

        profile = cashier_permissions.resolve_scope().pos_profile
        cash = _cash_modes(profile)
        if not cash:
            self.skipTest("Naqd to'lov usuli yo'q")

        # Sanoq HAR BIR naqd usul uchun kiritiladi (POS Profile'da bir nechta naqd usul bo'lishi mumkin).
        counted = {mode: 1000 for mode in cash}
        self.assertEqual(_parse_counted_cash(counted, profile), {mode: 1000.0 for mode in cash})

        non_cash = [
            m["mode_of_payment"]
            for m in cashier_billing.get_payment_methods(profile)
            if m["mode_of_payment"] not in cash
        ]
        if non_cash:
            with self.assertRaises(frappe.ValidationError):
                _parse_counted_cash({**counted, non_cash[0]: 1000}, profile)

        with self.assertRaises(frappe.ValidationError):
            _parse_counted_cash({**counted, cash[0]: -1}, profile)

    def test_page_has_blind_count_with_countdown(self):
        page = cashier_page()

        self.assertIn("Cheklar soni", page.script)
        self.assertIn("countdownTimer", page.script)
        self.assertIn("modalLocked", page.script)

        # Kutilayotgan summa/savdo ekranda BO'LMASLIGI kerak.
        for leaked in ("Kutilgan", "Tushum", "expected_amount"):
            self.assertNotIn(leaked, page.script, f"'{leaked}' ko'rsatilmasligi kerak")

    def test_count_is_entered_twice(self):
        """Ikki bosqichli sanoq — xato raqam o'tib ketmasligi uchun."""
        page = cashier_page()

        self.assertIn("renderCountStep", page.script)
        self.assertIn("Davom etish", page.script)
        # Ikkala kiritish solishtirilishi shart.
        self.assertIn("mismatch", page.script)

    def test_mismatch_keeps_last_count_as_new_baseline(self):
        """Mos kelmagan sanoq oxirgi raqamni SAQLAB qoladi.

        Ilgari mos kelmasa hammasi bekor bo'lardi. Kassir 200 000 deb xato
        sanab, keyin TO'G'RI 180 000 ni kiritsa, o'sha to'g'ri raqam ham
        tashlab yuborilardi va uni yana IKKI marta kiritishga to'g'ri
        kelardi (jami 4 kiritish, 2 marta 60 soniya kutish).

        Endi oxirgi kiritish yangi taqqoslash asosi bo'ladi — KETMA-KET
        ikkita mos raqam yetarli:

            200 000 -> 180 000 -> 180 000 (yopiladi)
        """
        page = cashier_page()

        block = page.script[page.script.index("if (mismatch.length)") :]
        block = block[: block.index("return;")]

        self.assertIn(
            "renderCountStep(data, counted)",
            block,
            "mos kelmaganda oxirgi sanoq yangi taqqoslash asosi bo'lishi kerak",
        )
        self.assertNotIn(
            "renderCountStep(data, null)",
            block,
            "mos kelmaganda sanoq BOSHIDAN boshlanmasligi kerak",
        )

    def test_recount_still_waits_for_the_countdown(self):
        """Qayta urinishda ham 60 soniyalik sanoq saqlanadi.

        Ko'r sanoqning ma'nosi shunda: har bir raqam HAQIQIY sanashdan
        keyin kiritiladi. Sanoqsiz kassir bir xil raqamni ketma-ket ikki
        marta yozib yuborardi va nazorat ishlamay qolardi.

        Mos kelmagandan keyin `first` BO'SH EMAS holda qayta chiziladi,
        ya'ni `second` rost bo'ladi va sanoq shoxi yana ishga tushadi.
        """
        page = cashier_page()

        block = js_method(page.script, "renderCountStep(data, first) {")

        # Sanoq `second` (ya'ni `first !== null`) shoxida ishga tushadi.
        self.assertIn("const second = first !== null;", block)
        self.assertIn("if (second) {", block)
        self.assertIn("countdownTimer = setInterval", block)

    def test_modal_handlers_are_detached_before_rebinding(self):
        """Oyna qayta chizilganda ESKI hodisa ishlovchilari yechilishi shart.

        `modalBody` — doimiy element: `innerHTML` almashsa ham unga
        delegatsiyalangan ishlovchilar QOLADI. Ikki bosqichli sanoqda ular
        to'planib, bitta bosishda ikkalasi ham ishlab ketardi — 2-bosqich
        tugmasi sanoqni QAYTADAN boshlab yuborardi (ko'rilgan xato).
        """
        page = cashier_page()

        # Ishlovchi bog'laydigan har bir joy — ya'ni `const $body = ...` —
        # `.off()` bilan olinishi kerak. Faqat o'qish uchun ishlatilgan
        # joylar (masalan `.find(...).text(...)`) bunga kirmaydi.
        import re

        bindings = re.findall(r"const \$body = \$\(this\.el\.modalBody\)([^;]*);", page.script)
        self.assertTrue(bindings, "oyna ishlovchilari topilmadi")

        for suffix in bindings:
            self.assertIn(
                ".off()",
                suffix,
                "`$body` `.off()` bilan olinishi kerak — aks holda ishlovchilar to'planadi",
            )

    def test_countdown_starts_after_first_entry_not_before(self):
        """Sanoq 1-bosqichda EMAS, 2-bosqichda boshlanadi."""
        page = cashier_page()

        block = js_method(page.script, "renderCountStep(data, first) {")

        # Sanoq `if (second)` shoxida ishga tushishi kerak.
        self.assertIn("if (second) {", block)
        countdown_at = block.index("countdownTimer = setInterval")
        second_at = block.index("if (second) {")
        self.assertGreater(
            countdown_at, second_at, "sanoq 2-bosqichdan oldin boshlanmasligi kerak"
        )

    def test_closing_is_blocked_while_orders_are_open(self):
        """To'lanmagan buyurtma bo'lsa kassa yopilmaydi.

        ILGARI BU TEST MANBA MATNINI O'QIRDI (`assertIn('"docstatus": 0')`)
        va shu sababli haqiqiy xatoni o'tkazib yubordi: sanoq bekor
        qilingan cheklarni ham qo'shib hisoblardi, kassir esa
        «2 ta to'lanmagan buyurtma bor» xabarini ko'rib, ro'yxatdan hech
        narsa topa olmasdi. Endi tekshiruv HULQ-ATVOR darajasida.
        """
        from ozturkapp.ozturkapp.api import cashier as cashier_api

        scope = cashier_permissions.resolve_scope()
        if not cashier_api._get_shift(scope).get("open"):
            self.skipTest("Ochiq kassa smenasi yo'q")

        # Testlar Administrator nomidan ketadi, u esa POS Profile'ga
        # biriktirilgan kassir emas — `assert_shift_operator()` bizni
        # tekshirmoqchi bo'lgan qadamga yetkazmasdan to'xtatadi.
        # Shu bitta tekshiruvni vaqtincha chetlab o'tamiz.
        real_check = cashier_permissions.can_operate_shift
        cashier_permissions.can_operate_shift = lambda profile, user=None: True
        self.addCleanup(
            setattr, cashier_permissions, "can_operate_shift", real_check
        )

        table = _free_table(scope.branch)
        if not table:
            self.skipTest("Ochiq cheksiz URY Table yo'q")

        name = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabPOS Invoice`
                (name, creation, modified, owner, modified_by, docstatus,
                 restaurant_table, branch, invoice_printed, custom_cancelled)
            values (%s, now(), now(), 'Administrator', 'Administrator', 0,
                 %s, %s, 0, 0)
            """,
            (name, table, scope.branch),
        )

        try:
            with self.assertRaises(frappe.ValidationError) as caught:
                cashier_api.close_shift(json.dumps({}))
            self.assertIn("to'lanmagan buyurtma", str(caught.exception))
        finally:
            frappe.db.delete("POS Invoice", {"name": name})

    def test_page_has_shift_buttons(self):
        page = cashier_page()
        # Ochish — bloklovchi ekran orqali, yopish — modal orqali.
        self.assertIn("renderShiftGate", page.script)
        self.assertIn("closeShiftDialog", page.script)

    def test_closed_shift_blocks_the_whole_page(self):
        """Smena yopiq bo'lsa kassa oynasi ko'rsatilmaydi — faqat ochish ekrani.

        Modal bo'lganda uni yopib ishlashda davom etish mumkin edi; endi
        `data-state="shift"` butun ish maydonini berkitadi.
        """
        page = cashier_page()

        self.assertIn('this.setState("shift")', page.script)
        self.assertIn('.rc-root[data-state="shift"]', page.style or "")

    def test_close_shift_is_a_red_menu_item(self):
        """«Kassani yopish» — «⋯» menyusidagi QIZIL band (Desk sarlavhasi ko'rinib turadi, menyu esa «⋯» da)."""
        page = cashier_page()
        menu = _read(frappe.get_app_path("ozturkapp", "public", "js", "cashier", "ui", "menu.js"))

        block = js_slot_item(menu, "close-shift")
        self.assertIn('kind: "danger"', block)
        self.assertIn("screen.closeShiftDialog()", block)
        self.assertIn("--rc-occupied", re.search(r"\.rc-menu__item--danger[^}]*\}", page.style).group(0))

    def test_close_shift_is_offered_once_and_only_when_open(self):
        """Band bir marta e'lon qilinadi va faqat ochiq smenada, kassir uchun chiqadi."""
        menu = _read(frappe.get_app_path("ozturkapp", "public", "js", "cashier", "ui", "menu.js"))
        self.assertEqual(menu.count('id: "close-shift"'), 1)

        block = js_slot_item(menu, "close-shift")
        self.assertIn("shift.open && canOperate", block)

        # Sarlavha tugmalari va mobil menyu to'lib ketishi endi mumkin emas.
        script = cashier_page().script
        for call in ("this.page.add_button(", "set_primary_action(", "set_secondary_action(", "custom_actions"):
            self.assertNotIn(call, script)


class TestValuationRateGuard(FrappeTestCase):
    """Tannarxsiz tovar — kassa yopilmaydi, lekin sabab TUSHUNARLI aytiladi.

    ERPNext bu holatda konsolidatsiyada yiqiladi va asl xatoni `except`
    blokida yutib yuboradi (`pos_invoice_merge_log.py:600`), o'rniga
    chalg'ituvchi «Could not find Reference Name: POS-CLO-...» beradi —
    chunki rollback'dan keyin o'sha hujjatga izoh yozmoqchi bo'ladi.

    Shuning uchun tekshiruv ERPNext'dan OLDIN bajariladi.
    """

    def test_missing_costs_are_detected(self):
        from ozturkapp.ozturkapp.setup.item_costs import missing_costs

        gaps = missing_costs()
        for gap in gaps:
            self.assertTrue(
                frappe.db.get_value("Item", gap["item"], "is_stock_item"),
                "zaxira bo'lmagan tovar tannarx talab qilmaydi",
            )
            self.assertFalse(frappe.db.get_value("Item", gap["item"], "valuation_rate"))

    def test_close_shift_checks_costs_before_erpnext(self):
        import inspect

        from ozturkapp.ozturkapp.api import cashier as cashier_api

        source = inspect.getsource(cashier_api.close_shift)
        self.assertIn("missing_costs", source)

        # Tekshiruv ERPNext konsolidatsiyasidan OLDIN bo'lishi kerak.
        guard_at = source.index("missing_costs")
        consolidate_at = source.index("make_closing_entry_from_opening")
        self.assertLess(guard_at, consolidate_at)

    def test_context_warns_early(self):
        """Menejer buni smena OXIRIDA emas, BOSHIDA bilishi kerak."""
        import inspect

        from ozturkapp.ozturkapp.api import cashier as cashier_api

        self.assertIn(
            "NO_VALUATION_RATE", inspect.getsource(cashier_api._config_warnings)
        )


class TestInvoiceOwnership(FrappeTestCase):
    """Chek egasi — smenadagi KASSIR (ofitsant emas).

    ERPNext smena hisobotini `POS Invoice.owner` bo'yicha yig'adi
    (`pos_closing_entry.get_pos_invoices`). Egasi ofitsant bo'lib qolsa,
    uning buyurtmasi kassirning Z-hisobotiga tushmaydi.
    """

    def test_waiter_api_assigns_shift_user_as_owner(self):
        import inspect

        from ozturkapp.ozturkapp.api import waiter as waiter_api

        source = inspect.getsource(waiter_api.submit_order)
        self.assertIn("shift_user", source)
        self.assertIn("cashier=shift_user", source)
        self.assertIn("owner=shift_user", source)
        # Ofitsant alohida maydonda qolishi kerak.
        self.assertIn("waiter=frappe.session.user", source)

    def test_existing_invoices_have_cashier_owner(self):
        for row in frappe.get_all(
            "POS Invoice", filters={"docstatus": 0}, fields=["name", "owner", "waiter"]
        ):
            if not row.waiter:
                continue
            shift_user = frappe.db.get_value(
                "POS Opening Entry", {"status": "Open", "docstatus": 1}, "user"
            )
            if shift_user:
                self.assertEqual(
                    row.owner,
                    shift_user,
                    f"{row.name} egasi kassir bo'lishi kerak",
                )


class TestDefaultRoomSelection(FrappeTestCase):
    """Standart holat — BARCHA ZALLAR (kassir keyin o'zgartirib oladi)."""

    def test_cashier_defaults_to_all_rooms(self):
        page = cashier_page()

        # `default_room` ga qaytish MANTIG'I bo'lmasligi kerak.
        self.assertNotIn("restaurant || {}).default_room", page.script)
        self.assertIn("this.room = saved ? saved : null", page.script)

    def test_floor_plan_without_room_returns_every_room(self):
        """`room=None` bo'lsa filialdagi BARCHA stol qaytadi."""
        from ozturkapp.ozturkapp.api import table as table_api

        scope = cashier_permissions.resolve_scope()
        everything = table_api.get_floor_plan()
        total = frappe.db.count("URY Table", {"branch": scope.branch})

        self.assertEqual(len(everything["tables"]), total)

        rooms = {t["restaurant_room"] for t in everything["tables"]}
        if len(rooms) > 1:
            # Ko'p zal bo'lsa — bloklarga ajratilgan bo'lishi kerak.
            self.assertTrue(everything["room_bands"])


class TestCashierCannotOccupyTable(FrappeTestCase):
    """Biznes qoidasi: kassir stolni QO'LDA band qila olmaydi.

    Stol faqat BUYURTMA orqali band bo'ladi va buyurtma yopilganda
    avtomatik bo'shaydi. Kassirning stolga ta'siri faqat bron bilan
    cheklangan.
    """

    def test_seat_table_endpoint_no_longer_exists(self):
        from ozturkapp.ozturkapp.api import table as table_api

        self.assertFalse(
            hasattr(table_api, "seat_table"),
            "kassir stolni qo'lda band qila olmasligi kerak",
        )

    def test_reservation_endpoints_exist(self):
        from ozturkapp.ozturkapp.api import table as table_api

        self.assertTrue(hasattr(table_api, "reserve_table"))
        self.assertTrue(hasattr(table_api, "cancel_reservation"))

    def test_cashier_page_has_no_seat_action(self):
        page = cashier_page()
        self.assertNotIn('data-action="seat"', page.script)
        self.assertNotIn('id: "seat"', page.script)
        self.assertIn('id: "reserve"', page.script)
        self.assertIn('id: "unreserve"', page.script)

    def test_no_manual_release_button_in_normal_flow(self):
        """Stol faqat to'lovda bo'shaydi — oddiy panelda tugma yo'q.

        `release` amali FAQAT buzilgan holat (`STALE_OCCUPIED_FLAG`)
        panelida qoladi, aks holda bunday stolni tozalab bo'lmaydi.
        """
        page = cashier_page()
        self.assertEqual(
            page.script.count('id: "release"'),
            1,
            "bo'shatish tugmasi faqat xato panelida bo'lishi kerak",
        )
        block = js_slot_item(page.script, "release")
        self.assertIn("STALE_OCCUPIED_FLAG", block)
        self.assertIn("is_supervisor", block)

    def test_no_ury_pos_link(self):
        page = cashier_page()
        self.assertNotIn('data-action="pos"', page.script)

    def test_bill_is_given_not_just_opened(self):
        """«Hisobni berish» — chekni belgilaydi VA chop etadi."""
        page = cashier_page()
        self.assertIn('id: "give-bill"', page.script)
        self.assertIn("screen.giveBill(detail, button)", page.script)
        self.assertIn("this.printReceipt(detail)", js_method(page.script, "async giveBill(detail, button) {"))
        # Alohida "chop etish" (data-action="print") tugmasi bo'lmasligi kerak.
        self.assertNotIn('data-action="print"', page.script)
        self.assertNotIn('id: "print"', page.script)

    def test_errors_are_shown_by_the_page_not_erpnext(self):
        """ERPNext'ning o'z msgprint oynasi chiqmasligi kerak."""
        page = cashier_page()
        self.assertIn("silent: true", page.script)
        # jqXHR ichidagi haqiqiy xabar o'qilishi kerak ([object Object] emas).
        self.assertIn("responseJSON", page.script)

    def test_reserving_an_occupied_table_is_rejected(self):
        """Buyurtmasi bor stolni bron qilib bo'lmaydi."""
        from ozturkapp.ozturkapp.api import table as table_api

        occupied = None
        for row in frappe.get_all(
            "POS Invoice", filters={"docstatus": 0}, fields=["restaurant_table"]
        ):
            if row.restaurant_table:
                occupied = row.restaurant_table
                break
        if not occupied:
            self.skipTest("Band stol yo'q")

        with self.assertRaises(frappe.ValidationError):
            table_api.reserve_table(occupied, customer_name="Test")


class TestTableAutoReleaseOnPayment(FrappeTestCase):
    """To'lov yopilganda stol AVTOMATIK bo'shaydi."""

    def test_release_is_automatic_on_submit(self):
        """`on_submit` hook'i stolni o'zi bo'shatadi — qo'lda amal kerak emas."""
        import inspect

        source = inspect.getsource(pos_invoice_override.on_submit)
        self.assertIn("_reconcile_tables", source)

        hooks = frappe.get_hooks("doc_events").get("POS Invoice", {})
        self.assertIn(
            "ozturkapp.ozturkapp.overrides.pos_invoice.on_submit",
            hooks.get("on_submit", []),
        )


class TestCustomerReceipt(FrappeTestCase):
    """Mijoz cheki — o'zbekcha, ustunlar ajratilgan, 3 ta yakuniy qator."""

    FORMAT = "Ozturk Chek"

    def _html(self):
        return frappe.db.get_value("Print Format", self.FORMAT, "html") or ""

    def test_format_exists_and_is_attached(self):
        self.assertTrue(frappe.db.exists("Print Format", self.FORMAT))
        self.assertEqual(
            frappe.db.get_value("Print Format", self.FORMAT, "doc_type"), "POS Invoice"
        )

    def test_qty_and_rate_are_separate_columns(self):
        """Soni, narxi va summasi — har biri o'z ustunida, summalar umumiy formatlagich bilan."""
        html = self._html()
        # Standart ERPNext formatidagi "qty @ rate" birikmasi BO'LMASLIGI kerak.
        self.assertNotIn("@ {{ item.get_formatted", html)
        self.assertIn('<td class="num">{{ item.qty | int }}</td>', html)
        # Narx va summa `format_amount` orqali (probel bilan guruhlanadi) —
        # Frappe'ning `get_formatted()` i saytning `#,###.##` formatiga tayanadi.
        self.assertIn('<td class="num">{{ format_amount(item.rate) }}</td>', html)
        self.assertIn('<td class="num">{{ format_amount(item.amount) }}</td>', html)
        self.assertNotIn('item.get_formatted("rate")', html)

    def test_exactly_three_total_rows(self):
        """Jami · xizmat haqi · umumiy summa — boshqasi yo'q."""
        html = self._html()
        self.assertIn("Jami", html)
        self.assertIn("Umumiy summa", html)
        for removed in ("Total Excl. Tax", "Grand Total", "Rounded Total", "Total Taxes"):
            self.assertNotIn(removed, html, f"'{removed}' qatori olib tashlanishi kerak")

    def test_text_is_uzbek(self):
        html = self._html()
        self.assertIn("Tashrifingiz uchun rahmat", html)
        self.assertNotIn("Thank you", html)
        for english in ("Receipt No", "Customer:", ">Qty<", ">Item<"):
            self.assertNotIn(english, html)

    def test_no_customer_row_on_receipt(self):
        """Chekda «Mijoz» qatori bo'lmasligi kerak.

        POS'da mijoz har doim standart texnik yozuv («... klient») bo'ladi
        va chekda ma'no bermaydi.

        DIQQAT: shablon MANBASI emas, RENDER natijasi tekshiriladi — manbada
        bu so'z Jinja izohi ichida uchraydi, lekin chiqishda bo'lmaydi.
        """
        invoice = frappe.db.get_value("POS Invoice", {"docstatus": 0}, "name")
        if not invoice:
            self.skipTest("Chek yo'q")

        rendered = frappe.get_print("POS Invoice", invoice, print_format=self.FORMAT)

        # `<style>` va `<script>` bloklarini olib tashlaymiz — ular ichida
        # izoh sifatida yozilgan so'zlar tekshiruvni chalg'itadi.
        body = re.sub(r"<(style|script)[^>]*>.*?</\1>", "", rendered, flags=re.S)

        self.assertNotIn("Mijoz", body)
        self.assertIn("Chek raqami", body)
        self.assertIn("Tashrifingiz uchun rahmat", body)

    def test_browser_headers_are_suppressed(self):
        """Brauzer qo'shadigan URL / sana / sahifa raqami chiqmasligi kerak."""
        html = self._html()
        self.assertIn("margin: 0mm", html)
        # Sahifa sarlavhasi mijoz nomi emas, chek raqami bo'lishi kerak.
        self.assertIn("document.title", html)

    def test_standard_erpnext_format_untouched(self):
        """ERPNext'ning o'z formatiga TEGILMAGAN."""
        standard = frappe.db.get_value("Print Format", "POS Invoice", "html") or ""
        self.assertIn("Thank you, please visit again.", standard)


class TestPaymentValidation(FrappeTestCase):
    """To'lov tekshiruvi frontend'ga bog'liq emas (TZ §17)."""

    def setUp(self):
        self.scope = cashier_permissions.resolve_scope()
        self.doc = frappe._dict(
            rounded_total=112000,
            grand_total=112000,
            precision=lambda field: 2,
        )

        # To'lov usuli nomi QATTIQ yozilmaydi: har bir restoranda o'zicha
        # («Naqd», «Нахт», ...). POS Profile'ning standart usuli olinadi —
        # aks holda test «noma'lum usul» xatosini «to'lov yetarli emas»
        # deb o'ylab, noto'g'ri sabab bilan o'tib ketardi.
        methods = cashier_billing.get_payment_methods(self.scope.pos_profile)
        if not methods:
            self.skipTest("POS Profile'da to'lov usuli sozlanmagan")
        self.mode = next((m for m in methods if m.get("default")), methods[0])[
            "mode_of_payment"
        ]

    def _validate(self, payments):
        from ozturkapp.ozturkapp.api.billing import _validate_payments

        return _validate_payments(json.dumps(payments), self.doc, self.scope)

    def test_underpayment_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._validate([{"mode_of_payment": self.mode, "amount": 50000}])

    def test_unknown_mode_of_payment_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._validate([{"mode_of_payment": "Bitcoin", "amount": 112000}])

    def test_empty_payment_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._validate([])

    def test_negative_amount_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._validate(
                [
                    {"mode_of_payment": self.mode, "amount": 200000},
                    {"mode_of_payment": self.mode, "amount": -50000},
                ]
            )

    def test_exact_payment_is_accepted(self):
        rows = self._validate([{"mode_of_payment": self.mode, "amount": 112000}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["mode_of_payment"], self.mode)
        self.assertEqual(rows[0]["amount"], 112000)


class TestSellingRequiresOpenShift(FrappeTestCase):
    """Kassa smenasi ochilmaguncha sotuv mumkin emas.

    Smena ochilishida kassir kassadagi naqd pulni sanab kiritadi — bu
    summasiz smena oxiridagi solishtiruvni hisoblab bo'lmaydi. ERPNext
    ham buni majburlaydi, lekin inglizcha va ancha kechroq bosqichda
    (`POS Invoice.validate_pos_opening_entry`).
    """

    def test_guard_exists(self):
        from ozturkapp.ozturkapp.utils import cashier_permissions

        self.assertTrue(hasattr(cashier_permissions, "assert_shift_open"))

    def test_sale_paths_are_guarded(self):
        """Hisob ochish, to'lov va ofitsant buyurtmasi — uchalasi ham."""
        import inspect

        from ozturkapp.ozturkapp.api import billing, waiter

        for fn in (
            billing.open_bill,
            billing.submit_payment,
            billing.split_bill,
            waiter.submit_order,
        ):
            with self.subTest(fn=fn.__name__):
                self.assertIn("assert_shift_open", inspect.getsource(fn))

    def test_message_tells_the_cashier_what_to_do(self):
        """Xato matni «nima qilay?» degan savolni qoldirmasligi kerak."""
        import inspect

        from ozturkapp.ozturkapp.utils import cashier_permissions

        source = inspect.getsource(cashier_permissions.assert_shift_open)
        self.assertIn("naqd pul", source)
        self.assertIn("Kassani ochish", source)


class TestBillSplit(FrappeTestCase):
    """Kassada hisobni taqsimlash (bill split) — POS Profile darajasida yoqish/o'chirish.

    Mahsulotni ko'chirish, soliq/xizmat haqini ikkala chekda ham qayta
    hisoblash — bularning barchasini `ury.ury.doctype.ury_order.ury_order
    .split_bill()` bajaradi (allaqachon mavjud, sinalgan). Bu yerda faqat
    ozturkapp qo'shgan qatlam (ko'lam, smena, POS Profile bayrog'i)
    sinaladi — pul hisob-kitobi URY'niki, qayta tekshirilmaydi.
    """

    def setUp(self):
        self.scope = cashier_permissions.resolve_scope()
        self.table = _free_table(self.scope.branch)
        if not self.table:
            self.skipTest("Ochiq cheksiz URY Table yo'q")
        self.invoices = []

        # Bu testlar smena holatiga bog'liq bo'lmasligi kerak — real
        # POS Opening Entry yaratish o'rniga tekshiruvni vaqtincha
        # chetlab o'tamiz (`TestOrderCancellation._as_cashier` bilan bir xil usul).
        self._real_shift_check = cashier_permissions.assert_shift_open
        cashier_permissions.assert_shift_open = lambda scope=None: None

        self._original_toggle = frappe.db.get_value(
            "POS Profile", self.scope.pos_profile, "custom_enable_bill_split"
        )

    def tearDown(self):
        cashier_permissions.assert_shift_open = self._real_shift_check
        frappe.db.set_value(
            "POS Profile",
            self.scope.pos_profile,
            "custom_enable_bill_split",
            self._original_toggle,
            update_modified=False,
        )
        for name in self.invoices:
            frappe.db.delete("POS Invoice", {"name": name})
        frappe.db.set_value(
            "URY Table", self.table, "occupied", 0, update_modified=False
        )

    def _draft(self, cancelled=0):
        """Qoralama chek qatori — to'liq hujjat kerak emas (yengil usul)."""
        name = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabPOS Invoice`
                (name, creation, modified, owner, modified_by, docstatus,
                 restaurant_table, branch, pos_profile, invoice_printed,
                 custom_cancelled, grand_total, rounded_total)
            values (%s, now(), now(), 'Administrator', 'Administrator', 0,
                 %s, %s, %s, 0, %s, 100, 100)
            """,
            (name, self.table, self.scope.branch, self.scope.pos_profile, cancelled),
        )
        self.invoices.append(name)
        return name

    def _set_toggle(self, enabled):
        frappe.db.set_value(
            "POS Profile",
            self.scope.pos_profile,
            "custom_enable_bill_split",
            1 if enabled else 0,
            update_modified=False,
        )

    # ── POS Profile bayrog'i ────────────────────────────────────────────

    def test_toggle_is_respected(self):
        from ozturkapp.ozturkapp.setup import bill_split_setup

        self._set_toggle(False)
        self.assertFalse(bill_split_setup.is_enabled(self.scope.pos_profile))

        self._set_toggle(True)
        self.assertTrue(bill_split_setup.is_enabled(self.scope.pos_profile))

    def test_missing_column_is_tolerated(self):
        from ozturkapp.ozturkapp.setup import bill_split_setup

        self.assertFalse(bill_split_setup.is_enabled(""))

    def test_custom_field_exists_on_pos_profile(self):
        row = frappe.db.get_value(
            "Custom Field",
            {"dt": "POS Profile", "fieldname": "custom_enable_bill_split"},
            ["fieldname", "fieldtype"],
            as_dict=True,
        )
        self.assertIsNotNone(row)
        self.assertEqual(row.fieldtype, "Check")

    def test_context_exposes_the_toggle(self):
        self._set_toggle(True)
        self.assertTrue(cashier_api.get_cashier_context()["enable_bill_split"])

        self._set_toggle(False)
        self.assertFalse(cashier_api.get_cashier_context()["enable_bill_split"])

    # ── Ruxsat qatlami ───────────────────────────────────────────────────

    def test_split_blocked_when_profile_disables_it(self):
        from ozturkapp.ozturkapp.api import billing

        self._set_toggle(False)
        invoice = self._draft()

        with self.assertRaises(frappe.ValidationError):
            billing.split_bill(invoice, [])

    def test_split_blocked_for_cancelled_order(self):
        from ozturkapp.ozturkapp.api import billing

        self._set_toggle(True)
        invoice = self._draft(cancelled=1)

        with self.assertRaises(frappe.ValidationError):
            billing.split_bill(invoice, [])

    def test_split_reaches_ury_when_enabled(self):
        """To'g'ri sozlanganda so'rov URY'ning HAQIQIY funksiyasiga yetadi.

        Mahsulot yo'qligi sababli URY o'zining "kamida bitta mahsulot
        tanlang" xatosini beradi — bu shu bosqichgacha yetib kelgani va
        gate'lardan boshqa hech narsa to'xtatmaganini isbotlaydi.
        """
        from ozturkapp.ozturkapp.api import billing

        self._set_toggle(True)
        invoice = self._draft()

        with self.assertRaises(frappe.ValidationError):
            billing.split_bill(invoice, [])

    def test_delegates_to_ury_does_not_reimplement(self):
        """Mahsulot ko'chirish mantig'ini QAYTA YOZMAYDI — URY'ga topshiradi."""
        import inspect

        from ozturkapp.ozturkapp.api import billing

        source = inspect.getsource(billing.split_bill)
        self.assertIn("ury.ury.doctype.ury_order.ury_order", source)
        for forbidden in (
            "doc.remove(",
            "new_invoice.insert(",
            "calculate_taxes_and_totals",
        ):
            self.assertNotIn(forbidden, source)

    # ── `build_bill` qator manzili ────────────────────────────────────

    def test_bill_items_expose_row_name_for_addressing(self):
        """`build_bill` mahsulot qatorining `name`sini ham qaytarishi kerak —
        aks holda frontend qaysi qatorni ko'chirishni URY'ga ayta olmaydi."""
        row = frappe._dict(
            name="ROW-1", idx=1, item_code="X", item_name="X",
            qty=2, uom="Nos", rate=1000, amount=2000,
        )
        doc = frappe._dict(
            doctype="POS Invoice", name="TEST", docstatus=0,
            creation=None, modified=None, customer="X", currency="UZS",
            net_total=2000, total=2000, grand_total=2000, rounded_total=2000,
            total_taxes_and_charges=0,
            items=[row], taxes=[],
            precision=lambda field: 2,
        )
        bill = cashier_billing.build_bill(doc, include_kitchen=False)
        self.assertEqual(bill["items"][0]["name"], "ROW-1")


class TestOpeningIsCashOnly(FrappeTestCase):
    """Smena ochishda faqat NAQD pul kiritiladi.

    Bank/karta bo'yicha "boshlang'ich qoldiq" tushunchasi yo'q — u pul
    kassada emas, bankda turadi. ERPNext yopilishda uni o'zi qo'shadi
    (`make_closing_entry_from_opening`: ochilishda yo'q usul chek
    uchraganda `opening_amount = 0` bilan qatorga tushadi), shuning uchun
    naqd bilan cheklash hisobotni buzmaydi.
    """

    def setUp(self):
        self.profile = frappe.get_all("POS Profile", pluck="name")[0]
        self.cash = cashier_api._cash_modes(self.profile)

    def _build(self, rows):
        return cashier_api._opening_balance_details(json.dumps(rows), self.profile)

    def test_cash_mode_is_accepted(self):
        rows = self._build(
            [{"mode_of_payment": m, "opening_amount": 500000} for m in self.cash]
        )
        self.assertEqual([r["mode_of_payment"] for r in rows], self.cash)
        self.assertTrue(all(r["opening_amount"] == 500000 for r in rows))

    def test_bank_mode_is_rejected(self):
        bank = [
            m["mode_of_payment"]
            for m in cashier_billing.get_payment_methods(self.profile)
            if m["mode_of_payment"] not in self.cash
        ]
        if not bank:
            self.skipTest("Profilda naqd bo'lmagan usul yo'q")

        rows = [{"mode_of_payment": m, "opening_amount": 0} for m in self.cash]
        rows.append({"mode_of_payment": bank[0], "opening_amount": 999})

        with self.assertRaises(frappe.ValidationError):
            self._build(rows)

    def test_missing_cash_mode_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._build([])

    def test_negative_amount_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._build(
                [{"mode_of_payment": m, "opening_amount": -1} for m in self.cash]
            )

    def test_gate_form_uses_cash_modes_only(self):
        page = cashier_page()
        self.assertIn("this.ctx.cash_modes", page.script)


class TestClosingAtZero(FrappeTestCase):
    """Savdo bo'lmagan smena 0 bilan yopiladi.

    Ilgari yopish oynasidagi maydon BO'SH turardi va bo'sh maydon rad
    etilardi — ya'ni chek yozilmagan smenani umuman yopib bo'lmasdi.
    """

    def setUp(self):
        self.profile = frappe.get_all("POS Profile", pluck="name")[0]
        self.cash = cashier_api._cash_modes(self.profile)

    def test_server_accepts_zero(self):
        parsed = cashier_api._parse_counted_cash(
            json.dumps({m: 0 for m in self.cash}), self.profile
        )
        self.assertEqual(parsed, {m: 0.0 for m in self.cash})

    def test_zero_opening_is_accepted(self):
        rows = cashier_api._opening_balance_details(
            json.dumps([{"mode_of_payment": m, "opening_amount": 0} for m in self.cash]),
            self.profile,
        )
        self.assertTrue(all(r["opening_amount"] == 0 for r in rows))

    def test_form_prefills_zero_only_without_sales(self):
        """Savdo bo'lsa maydon bo'sh qoladi — ko'r sanoq buzilmasin.

        Maydon HECH QACHON tayyor qiymat bilan chizilmaydi (`0` faqat
        placeholder). Bo'sh maydon `0` deb qabul qilinishi esa FAQAT chek
        yozilmagan smenada (`allowEmpty`); savdo bo'lgan smenada bo'sh
        maydon rad etiladi va kassir sanashga majbur bo'ladi.
        """
        page = cashier_page()
        step = js_method(page.script, "renderCountStep(data, first) {")

        self.assertIn('value="" placeholder="0"', step)
        self.assertIn("const allowEmpty = !cint(data.total_invoices);", step)
        self.assertIn('String(input.value).trim() === "" && !allowEmpty', step)

    def test_error_message_mentions_zero(self):
        page = cashier_page()
        self.assertIn("0 yozing", page.script)


class TestCashierSeesKitchenUpdates(FrappeTestCase):
    """Kassa oshxona holatini REALTIME ko'radi.

    Oshpaz taom holatini `URY KOT Items` da o'zgartiradi va POS Invoice'ga
    TEGMAYDI — ya'ni `on_pos_invoice_change` ishga tushmaydi va
    `ozturk_cashier_order` chiqmaydi. Kassa esa chek panelida
    "🍳 Tayyorlanmoqda (1/3)" ni ko'rsatadi.

    Shu sababli kassa oshxona kanaliga ALOHIDA obuna bo'lishi shart —
    aks holda ko'rsatkich qo'lda yangilanmaguncha qotib qoladi.
    """

    def test_context_exposes_kitchen_channel(self):
        import inspect

        from ozturkapp.ozturkapp.api import cashier as cashier_api

        source = inspect.getsource(cashier_api.get_cashier_context)
        self.assertIn('"kitchen_item": EVENT_ITEM', source)

    def test_page_subscribes_to_kitchen_channel(self):
        page = cashier_page()
        self.assertIn("events.kitchen_item", page.script)

    def test_event_carries_branch_and_invoice(self):
        """Kassa filtrlari AYNAN shu ikki maydonga tayanadi."""
        import inspect

        from ozturkapp.ozturkapp.utils import kitchen_realtime

        source = inspect.getsource(kitchen_realtime.emit_item_change)
        self.assertIn('"branch": branch', source)   # isOurBranch()
        self.assertIn('"invoice": invoice', source)  # touchesSelection()


class TestOrderCancellation(FrappeTestCase):
    """Ofitsant xato zakaz olganda kassir uni bekor qiladi.

    QOIDA
    =====
        Oshxona hali BOSHLAMAGAN  ->  har qanday kassir
        Oshxona BOSHLAB YUBORGAN  ->  faqat menejer

    Qoida `utils/order_cancel.py` da, "boshlangan" degan fakt esa
    `kitchen_status.get_order_progress()` da — bu yerda ikkalasi ham
    HULQ-ATVOR darajasida sinaladi, manba matnini o'qib emas.
    """

    def setUp(self):
        self.scope = cashier_permissions.resolve_scope()
        self.table = _free_table(self.scope.branch)
        if not self.table:
            self.skipTest("Ochiq cheksiz URY Table yo'q")

        self.branch = self.scope.branch
        self.invoices, self.kots = [], []

        # Testlar Administrator nomidan ketadi, ya'ni u DOIM menejer.
        # Oddiy kassirni ko'rsatish uchun rol tekshiruvi vaqtincha
        # almashtiriladi — `tearDown` uni qaytaradi.
        self._real_supervisor_check = cashier_permissions.has_supervisor_role

    def tearDown(self):
        cashier_permissions.has_supervisor_role = self._real_supervisor_check

        for kot in self.kots:
            frappe.db.delete("URY KOT Items", {"parent": kot})
            frappe.db.delete("URY KOT", {"name": kot})
        for name in self.invoices:
            frappe.db.delete("POS Invoice", {"name": name})

        frappe.db.set_value(
            "URY Table", self.table, "occupied", 0, update_modified=False
        )

    # ── Yordamchilar ──────────────────────────────────────────────────

    def _as_cashier(self, supervisor: bool):
        """Menejer huquqi bor/yo'q holatini taqlid qiladi."""
        cashier_permissions.has_supervisor_role = lambda user=None: supervisor

    def _draft(self, table=None):
        """Qoralama chek qatori — to'liq hujjat kerak emas (yengil usul)."""
        table = self.table if table is None else table
        name = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabPOS Invoice`
                (name, creation, modified, owner, modified_by, docstatus,
                 restaurant_table, branch, invoice_printed, custom_cancelled,
                 grand_total, rounded_total)
            values (%s, now(), now(), 'Administrator', 'Administrator', 0,
                 %s, %s, 0, 0, 100, 100)
            """,
            (name, table or None, self.branch),
        )
        self.invoices.append(name)
        return name

    def _kot(self, invoice, statuses, kot_type="New Order"):
        """Chekka KOT va uning mahsulotlarini biriktiradi."""
        kot = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabURY KOT`
                (name, creation, modified, owner, modified_by, docstatus,
                 invoice, branch, type, order_status)
            values (%s, now(), now(), 'Administrator', 'Administrator', 1,
                 %s, %s, %s, 'Ready For Prepare')
            """,
            (kot, invoice, self.branch, kot_type),
        )
        self.kots.append(kot)

        for idx, status in enumerate(statuses, start=1):
            frappe.db.sql(
                """
                insert into `tabURY KOT Items`
                    (name, creation, modified, owner, modified_by, docstatus,
                     parent, parenttype, parentfield, idx, item, quantity,
                     custom_kitchen_status)
                values (%s, now(), now(), 'Administrator', 'Administrator', 1,
                     %s, 'URY KOT', 'kot_items', %s, %s, 1, %s)
                """,
                (
                    frappe.generate_hash(length=10),
                    kot,
                    idx,
                    f"TEST-TAOM-{idx}",
                    status,
                ),
            )
        return kot

    def _row(self, invoice):
        return frappe._dict(name=invoice)

    def _describe(self, invoice):
        """API qanday uzatsa, test ham shunday uzatadi (to'liq hujjat emas)."""
        return order_cancel.describe(
            frappe.db.get_value(
                "POS Invoice",
                invoice,
                ["name", "docstatus", "custom_cancelled"],
                as_dict=True,
            )
        )

    def _item_statuses(self, kot):
        return sorted(
            frappe.get_all(
                "URY KOT Items",
                filters={"parent": kot, "parenttype": "URY KOT"},
                pluck="custom_kitchen_status",
            )
        )

    # ── "Boshlangan" degan fakt ───────────────────────────────────────

    def test_start_time_prep_alone_does_not_mean_the_kitchen_started(self):
        """REGRESSIYA: `start_time_prep` ni bekor qilish shartiga bog'lab bo'lmaydi.

        `URY KOT.start_time_prep` DocType'da `default = "Now"` — u KOT
        YARATILGANDA to'ladi, oshpaz ishni boshlaganda emas. Unga tayangan
        tekshiruv "ish har doim boshlangan" deb javob berardi va kassir
        hech qachon bekor qila olmasdi.
        """
        invoice = self._draft()
        kot = self._kot(invoice, [kitchen_status.PENDING, kitchen_status.PENDING])

        # URY xuddi shunday yozadi.
        frappe.db.set_value(
            "URY KOT", kot, "start_time_prep", "12:00:00", update_modified=False
        )

        progress = kitchen_status.get_order_progress(invoice)

        self.assertTrue(progress["has_kot"])
        self.assertFalse(
            progress["started"],
            "hamma taom 'Kutilmoqda' — oshxona hali boshlamagan",
        )

    def test_one_item_beyond_pending_means_started(self):
        invoice = self._draft()
        self._kot(invoice, [kitchen_status.PENDING, kitchen_status.PREPARING])

        progress = kitchen_status.get_order_progress(invoice)

        self.assertTrue(progress["started"])
        self.assertEqual(progress["pending_count"], 1)
        self.assertEqual(
            [row["status"] for row in progress["started_items"]],
            [kitchen_status.PREPARING],
        )

    def test_cancelled_item_does_not_count_as_started(self):
        invoice = self._draft()
        self._kot(invoice, [kitchen_status.PENDING, kitchen_status.CANCELLED])

        self.assertFalse(kitchen_status.get_order_progress(invoice)["started"])

    def test_cancellation_kot_is_not_kitchen_work(self):
        """«Cancelled» KOT — ko'rsatma, ovqat emas (TZ §9)."""
        invoice = self._draft()
        self._kot(invoice, [kitchen_status.PREPARING], kot_type="Cancelled")

        self.assertFalse(kitchen_status.get_order_progress(invoice)["started"])

    def test_order_without_a_kot_can_be_cancelled(self):
        """KOT yaratilmagan (masalan oshxona sozlanmagan) chek ham bekor bo'ladi."""
        invoice = self._draft()

        progress = kitchen_status.get_order_progress(invoice)
        self.assertFalse(progress["has_kot"])
        self.assertFalse(progress["started"])

    # ── Kim bekor qila oladi ──────────────────────────────────────────

    def test_plain_cashier_may_cancel_before_the_kitchen_starts(self):
        invoice = self._draft()
        self._kot(invoice, [kitchen_status.PENDING, kitchen_status.PENDING])
        self._as_cashier(supervisor=False)

        state = self._describe(invoice)

        self.assertTrue(state["allowed"])
        self.assertFalse(state["requires_supervisor"])
        self.assertFalse(state["kitchen_started"])

    def test_plain_cashier_is_blocked_once_the_kitchen_starts(self):
        invoice = self._draft()
        self._kot(invoice, [kitchen_status.PREPARING])
        self._as_cashier(supervisor=False)

        state = self._describe(invoice)

        self.assertFalse(state["allowed"])
        self.assertTrue(state["requires_supervisor"])
        self.assertTrue(state["blocked_reason"], "sabab aytilishi kerak")

        with self.assertRaises(frappe.ValidationError):
            order_cancel.cancel_invoice(self._row(invoice), "xato zakaz", self.scope)

        self.assertEqual(
            frappe.db.get_value("POS Invoice", invoice, "custom_cancelled"),
            0,
            "rad etilgan urinish hech narsani o'zgartirmasligi kerak",
        )

    def test_manager_may_force_cancel_after_the_kitchen_started(self):
        invoice = self._draft()
        self._kot(invoice, [kitchen_status.PREPARING])
        self._as_cashier(supervisor=True)

        state = self._describe(invoice)

        self.assertTrue(state["allowed"])
        self.assertTrue(state["requires_supervisor"])
        self.assertTrue(state["warning"], "menejer ogohlantirilishi kerak")

        result = order_cancel.cancel_invoice(
            self._row(invoice), "mijoz voz kechdi", self.scope
        )

        self.assertTrue(result["kitchen_started"])
        self.assertEqual(
            frappe.db.get_value("POS Invoice", invoice, "custom_cancelled"), 1
        )

    def test_paid_order_can_never_be_cancelled(self):
        invoice = self._draft()
        frappe.db.set_value("POS Invoice", invoice, "docstatus", 1)

        state = self._describe(invoice)
        self.assertFalse(state["allowed"])
        self.assertFalse(state["requires_supervisor"])

    def test_reason_is_required(self):
        invoice = self._draft()
        self._as_cashier(supervisor=False)

        for empty in ("", "   ", "."):
            with self.assertRaises(frappe.ValidationError):
                order_cancel.cancel_invoice(self._row(invoice), empty, self.scope)

        self.assertEqual(
            frappe.db.get_value("POS Invoice", invoice, "custom_cancelled"), 0
        )

    # ── Bekor qilishning oqibatlari ───────────────────────────────────

    def test_cancelling_keeps_the_document_and_records_the_reason(self):
        invoice = self._draft()
        self._as_cashier(supervisor=False)

        order_cancel.cancel_invoice(
            self._row(invoice), "Ofitsant noto'g'ri stolga yozdi", self.scope
        )

        row = frappe.db.get_value(
            "POS Invoice",
            invoice,
            ["docstatus", "custom_cancelled", "cancel_reason"],
            as_dict=True,
        )
        self.assertEqual(row.docstatus, 0, "hujjat o'chirilmaydi va bekor qilinmaydi")
        self.assertEqual(row.custom_cancelled, 1)
        self.assertEqual(row.cancel_reason, "Ofitsant noto'g'ri stolga yozdi")

    def test_cancelling_closes_the_kitchen_ticket(self):
        """Busiz oshpaz bekor qilingan buyurtmani tayyorlab yuboradi."""
        invoice = self._draft()
        kot = self._kot(invoice, [kitchen_status.PENDING, kitchen_status.PENDING])
        self._as_cashier(supervisor=False)

        result = order_cancel.cancel_invoice(
            self._row(invoice), "xato zakaz", self.scope
        )

        self.assertEqual(result["cancelled_items"], 2)
        self.assertEqual(
            self._item_statuses(kot),
            [kitchen_status.CANCELLED, kitchen_status.CANCELLED],
        )
        self.assertNotEqual(
            frappe.db.get_value("URY KOT", kot, "order_status"),
            "Ready For Prepare",
            "URY Mosaic 'Ready For Prepare' bo'yicha filtrlaydi — chipta "
            "ekranda qolib ketmasligi kerak",
        )

    def test_served_items_are_left_alone(self):
        """Berilgan taom jismonan chiqib bo'lgan — uni bekor deb yozib bo'lmaydi."""
        invoice = self._draft()
        kot = self._kot(invoice, [kitchen_status.SERVED, kitchen_status.PENDING])
        self._as_cashier(supervisor=True)

        result = order_cancel.cancel_invoice(
            self._row(invoice), "mijoz qolganidan voz kechdi", self.scope
        )

        self.assertEqual(result["cancelled_items"], 1)
        self.assertEqual(
            self._item_statuses(kot),
            sorted([kitchen_status.CANCELLED, kitchen_status.SERVED]),
        )

    def test_cancelling_frees_the_table(self):
        invoice = self._draft()
        frappe.db.set_value(
            "URY Table", self.table, "occupied", 1, update_modified=False
        )
        self._as_cashier(supervisor=False)

        result = order_cancel.cancel_invoice(
            self._row(invoice), "xato zakaz", self.scope
        )

        self.assertIn(self.table, result["freed_tables"])
        self.assertEqual(frappe.db.get_value("URY Table", self.table, "occupied"), 0)

    def test_table_stays_occupied_while_another_bill_is_open(self):
        """Hisob bo'lingan bo'lsa stol band qolishi kerak (TZ §23)."""
        cancelled = self._draft()
        self._draft()  # o'sha stolda ikkinchi ochiq chek
        frappe.db.set_value(
            "URY Table", self.table, "occupied", 1, update_modified=False
        )
        self._as_cashier(supervisor=False)

        result = order_cancel.cancel_invoice(
            self._row(cancelled), "xato zakaz", self.scope
        )

        self.assertEqual(result["freed_tables"], [])
        self.assertEqual(frappe.db.get_value("URY Table", self.table, "occupied"), 1)

    def test_order_without_a_table_can_be_cancelled(self):
        """Desktop POS / olib ketish buyurtmalarida stol bo'lmaydi."""
        invoice = self._draft(table="")
        self._as_cashier(supervisor=False)

        result = order_cancel.cancel_invoice(
            self._row(invoice), "xato zakaz", self.scope
        )

        self.assertEqual(result["freed_tables"], [])
        self.assertEqual(
            frappe.db.get_value("POS Invoice", invoice, "custom_cancelled"), 1
        )

    def test_double_cancellation_is_rejected(self):
        invoice = self._draft()
        self._as_cashier(supervisor=False)
        order_cancel.cancel_invoice(self._row(invoice), "xato zakaz", self.scope)

        with self.assertRaises(frappe.ValidationError):
            order_cancel.cancel_invoice(self._row(invoice), "yana", self.scope)


class TestCancelledOrderDoesNotBlockClosing(FrappeTestCase):
    """REGRESSIYA: bekor qilingan chek kassani yopishga xalaqit bermaydi.

    ILGARI QANDAY BUZILGANDI
    ========================
    Bekor qilingan chek o'chirilmaydi — `docstatus = 0` bo'lib qoladi va
    faqat `custom_cancelled = 1` bilan belgilanadi. Smena yopish sanog'i
    esa xom `{"docstatus": 0, "branch": ...}` bo'yicha ishlardi, ya'ni
    ularni ham qo'shib yuborardi:

        «Filialda 2 ta to'lanmagan buyurtma bor» — lekin kassirning
        ro'yxati BO'SH edi (u `custom_cancelled = 0` bo'yicha
        filtrlangan). Kassir nimani yopishni topa olmay qolardi.
    """

    def setUp(self):
        self.scope = cashier_permissions.resolve_scope()
        self.table = _free_table(self.scope.branch)
        if not self.table:
            self.skipTest("Ochiq cheksiz URY Table yo'q")
        self.created = []

    def tearDown(self):
        for name in self.created:
            frappe.db.delete("POS Invoice", {"name": name})
        frappe.db.set_value(
            "URY Table", self.table, "occupied", 0, update_modified=False
        )

    def _draft(self, cancelled=0):
        name = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabPOS Invoice`
                (name, creation, modified, owner, modified_by, docstatus,
                 restaurant_table, branch, invoice_printed, custom_cancelled)
            values (%s, now(), now(), 'Administrator', 'Administrator', 0,
                 %s, %s, 0, %s)
            """,
            (name, self.table, self.scope.branch, cancelled),
        )
        self.created.append(name)
        return name

    def test_cancelled_draft_is_not_counted_as_open(self):
        from ozturkapp.ozturkapp.api import cashier as cashier_api

        before = cashier_api._open_order_count(self.scope)
        cancelled = self._draft(cancelled=1)

        self.assertEqual(
            cashier_api._open_order_count(self.scope),
            before,
            "bekor qilingan chek 'to'lanmagan buyurtma' sifatida sanalmasligi kerak",
        )
        self.assertTrue(
            frappe.db.exists("POS Invoice", cancelled),
            "hujjat audit uchun bazada qolishi kerak",
        )

    def test_live_draft_is_still_counted(self):
        from ozturkapp.ozturkapp.api import cashier as cashier_api

        before = cashier_api._open_order_count(self.scope)
        self._draft(cancelled=0)

        self.assertEqual(cashier_api._open_order_count(self.scope), before + 1)

    def test_count_and_list_come_from_the_same_source(self):
        """Sanoq bilan ro'yxat hech qachon bir-biriga zid bo'lmasligi kerak."""
        from ozturkapp.ozturkapp.api import cashier as cashier_api

        self._draft(cancelled=1)
        self._draft(cancelled=0)

        self.assertEqual(
            cashier_api._open_order_count(self.scope),
            len(table_status.get_open_orders(self.scope.branch)),
        )


class TestCashierPageCancelButton(FrappeTestCase):
    """Kassa sahifasida bekor qilish tugmasi va stolsiz buyurtma."""

    def setUp(self):
        self.script = cashier_page().script

    def test_page_has_a_cancel_action(self):
        self.assertIn('id: "cancel-order"', self.script)
        self.assertIn("screen.cancelOrder(detail, button)", self.script)
        self.assertIn("cancelOrder(detail) {", self.script)

    def test_cancel_asks_for_a_reason(self):
        """Sabab MAJBURIY: tayyor sabablardan biri yoki «Boshqa…» (matn)."""
        form = js_method(self.script, "cancelOrder(detail) {")
        self.assertIn("api.order.cancel_order", form)
        self.assertIn('name: "reason"', form)
        self.assertIn("required: true", form)
        self.assertIn("other:", form)
        self.assertIn('__("Boshqa…")', form)

        for preset in ("Mijoz fikridan qaytdi", "Ofitsant xatosi", "Uzoq kutdi"):
            self.assertIn(f'__("{preset}")', self.script)

    def test_button_state_comes_from_the_server(self):
        """Tugma holati frontendda HISOBLANMAYDI (TZ §17)."""
        self.assertIn("bill.cancellation", self.script)
        self.assertIn("cancellation.requires_supervisor", self.script)
        block = js_slot_item(self.script, "cancel-order")
        self.assertIn("cancellation.allowed", block)
        self.assertIn("blocked_reason", block)

    def test_orders_without_a_table_can_be_opened(self):
        self.assertIn("selectOrder(invoice)", self.script)
        self.assertIn('data-invoice="', self.script)


class TestShiftOperatorRestriction(FrappeTestCase):
    """Kassani faqat POS Profile'ga biriktirilgan kassir ocha/yopa oladi.

    NEGA
    ====
    ERPNext Z-hisobotni `where owner = <POS Opening Entry.user>` bilan
    yig'adi (`pos_closing_entry.get_pos_invoices`). Smena boshqa
    foydalanuvchi nomiga ochilsa, cheklar hisobotdan tushib qoladi va
    `consolidated_invoice` siz osilib, buxgalteriyaga hech qachon
    yetib bormaydi.
    """

    def setUp(self):
        from ozturkapp.ozturkapp.api import cashier as cashier_api

        self.api = cashier_api
        self.scope = cashier_permissions.resolve_scope()
        self.profile = self.scope.pos_profile
        self.listed = cashier_permissions.pos_profile_users(self.profile)
        if not self.listed:
            self.skipTest("POS Profile'da `applicable_for_users` bo'sh")

    # ── Qoida ─────────────────────────────────────────────────────────

    def test_listed_user_may_operate(self):
        self.assertTrue(
            cashier_permissions.can_operate_shift(self.profile, self.listed[0])
        )

    def test_unlisted_user_may_not_operate(self):
        self.assertFalse(
            cashier_permissions.can_operate_shift(self.profile, "hech-kim@example.com")
        )

    def test_empty_profile_list_means_no_restriction(self):
        """Sozlanmagan profil butun kassani to'xtatib qo'ymasligi kerak."""
        self.assertTrue(cashier_permissions.can_operate_shift("", "hech-kim@example.com"))

    def test_operator_names_are_human_readable(self):
        """Xato xabarida e-pochta emas, ism ko'rinishi kerak (TZ §19)."""
        names = cashier_permissions.shift_operator_names(self.profile)
        self.assertTrue(names)

    # ── Hujjat darajasi — HAR QANDAY yo'lni qamrab oladi ──────────────

    def test_opening_entry_rejects_an_unlisted_owner(self):
        """Desk, Desktop POS yoki kassa sahifasi — farqi yo'q."""
        if "Administrator" in self.listed:
            self.skipTest("Administrator ro'yxatda — sinov ma'nosiz")

        with self.assertRaises(frappe.ValidationError):
            self._opening(user="Administrator").insert()

    def test_opening_entry_accepts_a_listed_owner(self):
        doc = self._opening(user=self.listed[0])
        doc.insert()
        self.assertTrue(doc.name)
        self.assertEqual(doc.docstatus, 0)

    def _opening(self, user):
        doc = frappe.new_doc("POS Opening Entry")
        doc.update(
            {
                "period_start_date": frappe.utils.now_datetime(),
                "posting_date": frappe.utils.nowdate(),
                "user": user,
                "pos_profile": self.profile,
                "company": frappe.db.get_value("POS Profile", self.profile, "company"),
            }
        )
        for mode in self.api._cash_modes(self.profile):
            doc.append("balance_details", {"mode_of_payment": mode, "opening_amount": 0})
        return doc

    # ── API darajasi — kassa sahifasi ─────────────────────────────────

    def test_open_shift_api_rejects_an_unlisted_user(self):
        """Testlar Administrator nomidan ketadi — u ro'yxatda yo'q."""
        if frappe.session.user in self.listed:
            self.skipTest("Joriy foydalanuvchi ro'yxatda — sinov ma'nosiz")

        with self.assertRaises(frappe.PermissionError):
            self.api.open_shift(json.dumps([]))

    def test_close_shift_api_rejects_an_unlisted_user(self):
        if frappe.session.user in self.listed:
            self.skipTest("Joriy foydalanuvchi ro'yxatda — sinov ma'nosiz")

        with self.assertRaises(frappe.PermissionError):
            self.api.close_shift(json.dumps({}))

    def test_context_tells_the_page_whether_it_may_operate(self):
        ctx = self.api.get_cashier_context()

        self.assertIn("can_operate_shift", ctx["permissions"])
        self.assertEqual(
            ctx["permissions"]["can_operate_shift"],
            frappe.session.user in self.listed,
        )
        self.assertTrue(ctx["shift_operators"], "kim ochishi aytilishi kerak")


class TestShiftGateForUnauthorizedUser(FrappeTestCase):
    """Kassa YOPIQ bo'lganda kim nima ko'radi.

        KASSIR (ocha oladi)      -> bloklovchi ekran + ochish formasi
        ADMINISTRATOR / MENEJER  -> bloklanmaydi, zal va buyurtmalar
                                    ko'rinadi, holat esa yuqori paneldagi
                                    QIZIL «Kassa yopiq» yozuvida turadi

    Sotuv amallarini server baribir rad etadi (`assert_shift_open`),
    shuning uchun kuzatuvchini bloklashning ma'nosi yo'q.
    """

    def setUp(self):
        page = cashier_page()
        self.script = page.script

    def test_only_the_operator_is_blocked_by_the_gate(self):
        self.assertIn("!(this.ctx.shift || {}).open && canOperate", self.script)

    def test_status_is_shown_to_everyone_in_red(self):
        self.assertIn("rc-shift--closed", self.script)
        self.assertIn('__("Kassa yopiq")', self.script)

    def test_status_tells_who_may_open_the_register(self):
        self.assertIn("shift_operators", self.script)

    def test_close_button_is_hidden_for_unauthorized_user(self):
        menu = _read(frappe.get_app_path("ozturkapp", "public", "js", "cashier", "ui", "menu.js"))
        self.assertIn("shift.open && canOperate", js_slot_item(menu, "close-shift"))


class TestBulkClosingMatchesErpnext(FrappeTestCase):
    """Tez `make_closing_entry_from_opening()` ERPNext bilan AYNAN bir xil.

    NEGA BU TEST BOR
    ================
    `utils/pos_closing.py` ERPNext'ning ichki funksiyasini almashtiradi —
    chek-boshiga 11 ta so'rov o'rniga jami 3 ta bulk so'rov. Tezlik
    yaxshi, lekin bu PUL hisobi: bironta maydon tushib qolsa Z-hisobot
    jimgina noto'g'ri chiqadi va buni hech kim payqamaydi.

    Shuning uchun ikkala versiya HAR SAFAR maydonma-maydon
    solishtiriladi. ERPNext yangilanganda bu test birinchi bo'lib
    ogohlantiradi.
    """

    def setUp(self):
        self.scope = cashier_permissions.resolve_scope()
        self.profile = self.scope.pos_profile
        self.user = "Administrator"
        self.invoices = []

        self.opening = frappe._dict(
            name="TEST-OPE",
            period_start_date=frappe.utils.add_to_date(
                frappe.utils.now_datetime(), days=-1
            ),
            pos_profile=self.profile,
            user=self.user,
            company=frappe.db.get_value("POS Profile", self.profile, "company"),
            balance_details=[
                frappe._dict(mode_of_payment=mode, opening_amount=50000)
                for mode in cashier_api._cash_modes(self.profile)
            ],
        )

    def tearDown(self):
        for name in self.invoices:
            frappe.db.delete("Sales Taxes and Charges", {"parent": name})
            frappe.db.delete("Sales Invoice Payment", {"parent": name})
            frappe.db.delete("POS Invoice", {"name": name})

    # ── Fikstura ──────────────────────────────────────────────────────

    def _invoice(self, grand_total, qty, taxes=(), payments=()):
        """To'langan, hali konsolidatsiya qilinmagan chek."""
        name = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabPOS Invoice`
                (name, creation, modified, owner, modified_by, docstatus,
                 posting_date, posting_time, pos_profile, customer, branch,
                 grand_total, net_total, total_qty, consolidated_invoice)
            values (%s, now(), now(), %s, %s, 1,
                 curdate(), curtime(), %s, 'TEST-MIJOZ', %s, %s, %s, %s, '')
            """,
            (name, self.user, self.user, self.profile, self.scope.branch,
             grand_total, grand_total, qty),
        )
        self.invoices.append(name)

        for idx, (account, rate, amount) in enumerate(taxes, start=1):
            frappe.db.sql(
                """
                insert into `tabSales Taxes and Charges`
                    (name, creation, modified, owner, modified_by, docstatus,
                     parent, parenttype, parentfield, idx,
                     charge_type, account_head, rate, tax_amount)
                values (%s, now(), now(), %s, %s, 1, %s, 'POS Invoice', 'taxes', %s,
                     'On Net Total', %s, %s, %s)
                """,
                (frappe.generate_hash(length=10), self.user, self.user,
                 name, idx, account, rate, amount),
            )

        for idx, (mode, amount) in enumerate(payments, start=1):
            frappe.db.sql(
                """
                insert into `tabSales Invoice Payment`
                    (name, creation, modified, owner, modified_by, docstatus,
                     parent, parenttype, parentfield, idx, mode_of_payment, amount)
                values (%s, now(), now(), %s, %s, 1, %s, 'POS Invoice', 'payments',
                     %s, %s, %s)
                """,
                (frappe.generate_hash(length=10), self.user, self.user,
                 name, idx, mode, amount),
            )
        return name

    # ── Solishtiruv ───────────────────────────────────────────────────

    def _both(self):
        from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import (
            make_closing_entry_from_opening as erpnext_version,
        )

        from ozturkapp.ozturkapp.utils.pos_closing import (
            make_closing_entry_from_opening as bulk_version,
        )

        return erpnext_version(self.opening), bulk_version(self.opening)

    def _rows(self, doc, table, keys):
        return sorted(
            tuple(flt(row.get(k)) if isinstance(row.get(k), (int, float)) else row.get(k)
                  for k in keys)
            for row in doc.get(table) or []
        )

    def test_totals_match(self):
        self._invoice(120000, 3, payments=[("Нахт", 120000)])
        self._invoice(80000, 2, payments=[("Нахт", 80000)])

        theirs, ours = self._both()

        self.assertEqual(flt(ours.grand_total), flt(theirs.grand_total))
        self.assertEqual(flt(ours.net_total), flt(theirs.net_total))
        self.assertEqual(flt(ours.total_quantity), flt(theirs.total_quantity))
        self.assertGreater(flt(ours.grand_total), 0, "sinov ma'lumoti tushmagan")

    def test_transactions_match(self):
        self._invoice(120000, 3, payments=[("Нахт", 120000)])
        self._invoice(80000, 2, payments=[("Нахт", 80000)])

        theirs, ours = self._both()
        keys = ["pos_invoice", "customer", "grand_total"]

        self.assertEqual(
            self._rows(ours, "pos_transactions", keys),
            self._rows(theirs, "pos_transactions", keys),
        )
        self.assertEqual(len(ours.pos_transactions), 2)

    def test_payments_are_aggregated_the_same_way(self):
        """Bir xil usul bo'yicha jamlanadi, ochilish summasi saqlanadi."""
        self._invoice(120000, 3, payments=[("Нахт", 100000), ("Нахт", 20000)])
        self._invoice(80000, 2, payments=[("Нахт", 80000)])

        theirs, ours = self._both()
        keys = ["mode_of_payment", "opening_amount", "expected_amount"]

        self.assertEqual(
            self._rows(ours, "payment_reconciliation", keys),
            self._rows(theirs, "payment_reconciliation", keys),
        )

    def test_taxes_are_aggregated_the_same_way(self):
        """Soliqlar `(account_head, rate)` juftligi bo'yicha jamlanadi."""
        account = frappe.db.get_value("Account", {"company": self.opening.company}, "name")
        if not account:
            self.skipTest("Kompaniyada hisob topilmadi")

        self._invoice(120000, 3, taxes=[(account, 12, 12000)],
                      payments=[("Нахт", 120000)])
        self._invoice(80000, 2, taxes=[(account, 12, 8000)],
                      payments=[("Нахт", 80000)])

        theirs, ours = self._both()
        keys = ["account_head", "rate", "amount"]

        self.assertEqual(
            self._rows(ours, "taxes", keys),
            self._rows(theirs, "taxes", keys),
        )
        self.assertEqual(len(ours.taxes), 1, "bir xil hisob+stavka birlashishi kerak")

    def test_consolidated_invoices_are_excluded(self):
        paid = self._invoice(50000, 1, payments=[("Нахт", 50000)])
        frappe.db.set_value("POS Invoice", paid, "consolidated_invoice", "ACC-SINV-TEST")

        theirs, ours = self._both()

        self.assertEqual(len(ours.pos_transactions), len(theirs.pos_transactions))
        self.assertNotIn(
            paid, [row.pos_invoice for row in ours.pos_transactions]
        )

    def test_invoices_of_another_user_are_excluded(self):
        other = self._invoice(50000, 1, payments=[("Нахт", 50000)])
        frappe.db.set_value("POS Invoice", other, "owner", "kassa@gmail.com")

        theirs, ours = self._both()

        self.assertEqual(len(ours.pos_transactions), len(theirs.pos_transactions))
        self.assertNotIn(other, [row.pos_invoice for row in ours.pos_transactions])

    def test_invoices_outside_the_window_are_excluded(self):
        old = self._invoice(50000, 1, payments=[("Нахт", 50000)])
        frappe.db.set_value(
            "POS Invoice", old, "posting_date",
            frappe.utils.add_to_date(frappe.utils.nowdate(), days=-5),
        )

        theirs, ours = self._both()

        self.assertEqual(len(ours.pos_transactions), len(theirs.pos_transactions))
        self.assertNotIn(old, [row.pos_invoice for row in ours.pos_transactions])

    def test_bulk_version_uses_far_fewer_queries(self):
        """Tezlikning O'ZI ham regressiyadan himoyalanadi."""
        for _ in range(6):
            self._invoice(50000, 1, payments=[("Нахт", 50000)])

        counts = {}
        real_sql = frappe.db.sql

        for label, fn in (("erpnext", None), ("bulk", None)):
            counts[label] = 0

        def counted(label):
            def wrapper(*args, **kwargs):
                counts[label] += 1
                return real_sql(*args, **kwargs)
            return wrapper

        from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import (
            make_closing_entry_from_opening as erpnext_version,
        )
        from ozturkapp.ozturkapp.utils.pos_closing import (
            make_closing_entry_from_opening as bulk_version,
        )

        try:
            frappe.db.sql = counted("erpnext")
            erpnext_version(self.opening)
            frappe.db.sql = counted("bulk")
            bulk_version(self.opening)
        finally:
            frappe.db.sql = real_sql

        self.assertLess(
            counts["bulk"], counts["erpnext"] / 3,
            f"bulk={counts['bulk']} erpnext={counts['erpnext']} — tezlik yo'qolgan",
        )


class TestCancelledOrderReleasesTheTable(FrappeTestCase):
    """Bekor qilingan chek stolni ushlab turmasligi kerak.

    MUAMMO QANDAY BO'LGAN
    =====================
    Bekor qilingan chek `docstatus = 0` bo'lib qoladi. URY esa stoldagi
    "faol buyurtma"ni stol bog'lami bo'yicha qidiradi va bizning
    `custom_cancelled` bayrog'imizni BILMAYDI::

        ury_order.get_order_invoice():
            filters    {docstatus: 0, invoice_printed: 0}
            or_filters {restaurant_table: <stol>,
                        custom_merged_tables: like %<stol>%}

    Shu sababli Table-1 dagi zakaz bekor qilingandan keyin o'sha stolga
    yangi zakaz olinganda «Table-1 is already occupied» chiqardi
    (`ury_order.py:840`) — `URY Table.occupied = 0` bo'lsa ham. Stol
    amalda abadiy bloklanardi.
    """

    def setUp(self):
        self.scope = cashier_permissions.resolve_scope()
        self.table = _free_table(self.scope.branch)
        if not self.table:
            self.skipTest("Ochiq cheksiz URY Table yo'q")
        if not frappe.db.has_column("POS Invoice", "custom_cancelled_table"):
            self.skipTest("`custom_cancelled_table` maydoni hali yaratilmagan")

        self.created = []
        self._real_supervisor = cashier_permissions.has_supervisor_role
        cashier_permissions.has_supervisor_role = lambda user=None: False

    def tearDown(self):
        cashier_permissions.has_supervisor_role = self._real_supervisor
        for name in self.created:
            frappe.db.delete("POS Invoice", {"name": name})
        frappe.db.set_value(
            "URY Table", self.table, "occupied", 0, update_modified=False
        )

    def _draft(self, cancelled=0):
        name = frappe.generate_hash(length=10)
        frappe.db.sql(
            """
            insert into `tabPOS Invoice`
                (name, creation, modified, owner, modified_by, docstatus,
                 restaurant_table, branch, invoice_printed, custom_cancelled)
            values (%s, now(), now(), 'Administrator', 'Administrator', 0,
                 %s, %s, 0, %s)
            """,
            (name, self.table, self.scope.branch, cancelled),
        )
        self.created.append(name)
        return name

    def _ury_would_find(self):
        """URY'ning `get_order_invoice()` dagi AYNAN o'sha so'rovi."""
        return frappe.get_all(
            "POS Invoice",
            filters={"docstatus": 0, "invoice_printed": 0},
            or_filters={
                "restaurant_table": self.table,
                "custom_merged_tables": ["like", f"%{self.table}%"],
            },
            pluck="name",
        )

    # ── Qoida ─────────────────────────────────────────────────────────

    def test_live_order_is_still_found_by_ury(self):
        """Nazorat: faol zakaz TOPILISHI kerak, aks holda test ma'nosiz."""
        live = self._draft()
        self.assertIn(live, self._ury_would_find())

    def test_cancelling_detaches_the_table(self):
        invoice = self._draft()

        order_cancel.cancel_invoice(
            frappe._dict(name=invoice), "xato zakaz", self.scope
        )

        row = frappe.db.get_value(
            "POS Invoice", invoice,
            ["restaurant_table", "custom_merged_tables", "custom_cancelled_table"],
            as_dict=True,
        )
        self.assertIsNone(row.restaurant_table, "stol bog'lami uzilishi kerak")
        self.assertIsNone(row.custom_merged_tables)
        self.assertEqual(
            row.custom_cancelled_table, self.table,
            "qaysi stol bo'lgani audit uchun saqlanishi kerak",
        )

    def test_ury_no_longer_finds_a_cancelled_order(self):
        """REGRESSIYA: «Table-1 is already occupied» qaytmasligi kerak."""
        invoice = self._draft()
        self.assertIn(invoice, self._ury_would_find())

        order_cancel.cancel_invoice(
            frappe._dict(name=invoice), "xato zakaz", self.scope
        )

        self.assertNotIn(
            invoice, self._ury_would_find(),
            "bekor qilingan chek stolni bloklab turibdi",
        )

    def test_cancelled_order_stays_in_the_database(self):
        """Stol uziladi, LEKIN hujjat audit uchun qoladi."""
        invoice = self._draft()
        order_cancel.cancel_invoice(
            frappe._dict(name=invoice), "xato zakaz", self.scope
        )

        row = frappe.db.get_value(
            "POS Invoice", invoice,
            ["docstatus", "custom_cancelled", "cancel_reason"], as_dict=True,
        )
        self.assertEqual(row.docstatus, 0)
        self.assertEqual(row.custom_cancelled, 1)
        self.assertEqual(row.cancel_reason, "xato zakaz")

    def test_bill_still_shows_which_table_it_was(self):
        invoice = self._draft()
        order_cancel.cancel_invoice(
            frappe._dict(name=invoice), "xato zakaz", self.scope
        )

        bill = cashier_billing.build_bill(
            frappe.get_doc("POS Invoice", invoice), self.scope, include_kitchen=False
        )
        self.assertEqual(bill["table"], self.table)

    # ── Eski ma'lumotni tozalash ──────────────────────────────────────

    def test_legacy_cancelled_orders_are_detached_on_migrate(self):
        """Tuzatishdan OLDIN bekor qilinganlar ham tozalanishi kerak."""
        from ozturkapp.ozturkapp.setup.cancelled_orders import detach_tables

        stuck = self._draft(cancelled=1)  # eski usulda: stol bog'lami joyida
        self.assertIn(stuck, self._ury_would_find())

        detach_tables()

        self.assertNotIn(stuck, self._ury_would_find())
        self.assertEqual(
            frappe.db.get_value("POS Invoice", stuck, "custom_cancelled_table"),
            self.table,
        )

    def test_cleanup_is_idempotent(self):
        from ozturkapp.ozturkapp.setup.cancelled_orders import detach_tables

        stuck = self._draft(cancelled=1)
        detach_tables()
        remembered = frappe.db.get_value("POS Invoice", stuck, "custom_cancelled_table")

        detach_tables()  # ikkinchi marta

        self.assertEqual(
            frappe.db.get_value("POS Invoice", stuck, "custom_cancelled_table"),
            remembered,
            "takroriy yurgizish eslab qolingan nomni buzmasligi kerak",
        )


class TestMoneyFormatting(FrappeTestCase):
    """Summalar probel bilan guruhlanadi: `200000` -> `200 000`.

    NEGA
    ====
    `1080800` ni bir qarashda o'qib bo'lmaydi, `1 080 800` ni bo'ladi.
    Kassir ham, ofitsant ham ekranga bir soniyaga qaraydi — noto'g'ri
    o'qilgan raqam pul xatosiga aylanadi.

    Qoida UCH joyda bir xil bo'lishi shart, aks holda bitta son ikki
    ekranda turlicha ko'rinadi:

        server / chek     utils/money.py       format_amount()
        kassa oynasi      restaurant_cashier.js money()
        ofitsant ilovasi  lib/core/format.dart  Fmt.money()
    """

    def test_thousands_are_separated_by_a_space(self):
        from ozturkapp.ozturkapp.utils.money import format_amount

        self.assertEqual(format_amount(200000), "200 000")
        self.assertEqual(format_amount(1080800), "1 080 800")
        self.assertEqual(format_amount(646000), "646 000")

    def test_small_numbers_are_untouched(self):
        from ozturkapp.ozturkapp.utils.money import format_amount

        self.assertEqual(format_amount(0), "0")
        self.assertEqual(format_amount(500), "500")
        self.assertEqual(format_amount(999), "999")
        self.assertEqual(format_amount(1000), "1 000")

    def test_zero_kopeks_are_not_shown(self):
        """Summalar butun so'mda — `,00` faqat ekranni to'ldiradi."""
        from ozturkapp.ozturkapp.utils.money import format_amount

        self.assertEqual(format_amount(200000.0), "200 000")
        self.assertEqual(format_amount(200000.004), "200 000")

    def test_real_kopeks_are_shown_with_a_comma(self):
        """Tiyin bo'lsa YASHIRILMAYDI — probel bilan vergul juftlashadi."""
        from ozturkapp.ozturkapp.utils.money import format_amount

        self.assertEqual(format_amount(1234.5), "1 234,50")
        self.assertEqual(format_amount(1234.05), "1 234,05")

    def test_rounding_carries_into_the_whole_part(self):
        from ozturkapp.ozturkapp.utils.money import format_amount

        self.assertEqual(format_amount(1.999), "2")
        self.assertEqual(format_amount(999.999), "1 000")

    def test_negative_amounts_keep_the_sign(self):
        from ozturkapp.ozturkapp.utils.money import format_amount

        self.assertEqual(format_amount(-75000), "-75 000")

    def test_none_is_zero(self):
        from ozturkapp.ozturkapp.utils.money import format_amount

        self.assertEqual(format_amount(None), "0")

    # ── Uch tomon bir xil qoidaga tayanadi ────────────────────────────

    def test_cashier_page_does_not_use_frappe_currency_format(self):
        """`format_currency()` saytning `#,###.##` sozlamasiga tayanadi."""
        page = cashier_page()

        self.assertNotIn("format_currency(flt(value)", page.script)
        # Minglik ajratgich — probel.
        self.assertIn('(?=(\\d{3})+(?!\\d))/g,', page.script.replace("\n", ""))

    def test_receipt_uses_the_shared_formatter(self):
        """Mijoz cheki ham probel bilan chiqadi."""
        import inspect

        from ozturkapp.ozturkapp.setup import receipt_format

        source = inspect.getsource(receipt_format)
        self.assertIn("format_amount(doc.rounded_total", source)
        self.assertNotIn('get_formatted("rounded_total")', source)

    def test_formatter_is_available_to_print_formats(self):
        """Chek Jinja shabloni `format_amount()` ni ko'ra olishi kerak."""
        methods = frappe.get_hooks("jinja", {}).get("methods") or []
        self.assertIn("ozturkapp.ozturkapp.utils.money.format_amount", methods)

    def test_jinja_render_produces_spaces(self):
        """Uchdan-uchgacha: shablon haqiqatan probel bilan chizadi."""
        rendered = frappe.render_template(
            "{{ format_amount(value) }}", {"value": 1080800}
        )
        self.assertEqual(rendered, "1 080 800")


class TestAmountInputsAreGrouped(FrappeTestCase):
    """Kassir YOZAYOTGAN summa ham probel bilan guruhlanadi.

    NEGA
    ====
    Xato eng ko'p aynan yozayotganda bo'ladi: bitta ortiqcha nol bir
    qarashda ko'rinmaydi. `1080800` va `10808000` bir xilga o'xshaydi,
    `1 080 800` va `10 808 000` esa yo'q.

    Buning uchun maydon `type="number"` BO'LMASLIGI shart — brauzer unda
    probelga yo'l qo'ymaydi va qiymatni bo'sh deb hisoblaydi.
    """

    def setUp(self):
        page = cashier_page()
        self.script = page.script

    def test_no_amount_field_is_a_number_input(self):
        for field in ("rc-pay__input", "rc-count-input", "rc-shift-input"):
            block = self.script.split(field, 1)[1][:200]
            self.assertNotIn(
                'type="number"', block,
                f"{field} hali `type=number` — probel yozib bo'lmaydi",
            )

    def test_typed_amounts_are_parsed_back_to_numbers(self):
        """Probelli matn serverga son bo'lib ketishi shart."""
        self.assertIn("function parseAmount(", self.script)
        # To'lov, ko'r sanoq va kassa ochish — uchalasi ham.
        self.assertGreaterEqual(self.script.count("parseAmount("), 4)
        self.assertNotIn("flt($input.val())", self.script)

    def test_every_amount_field_is_bound_to_the_formatter(self):
        self.assertIn("function bindAmountInput(", self.script)
        self.assertGreaterEqual(self.script.count("bindAmountInput("), 4)

    def test_caret_is_restored_after_regrouping(self):
        """Kursor tiklanmasa kassir raqamni o'rtasidan to'g'irlay olmaydi."""
        self.assertIn("setSelectionRange", self.script)


class TestWorkspacesHaveNoDescription(FrappeTestCase):
    """Kassa va Oshxona workspace'larida ortiqcha izoh bo'lmasligi kerak."""

    def test_only_header_and_shortcut_remain(self):
        for name in ("Kassa", "Oshxona"):
            if not frappe.db.exists("Workspace", name):
                self.skipTest(f"'{name}' workspace hali o'rnatilmagan")

            blocks = json.loads(
                frappe.db.get_value("Workspace", name, "content") or "[]"
            )
            types = [block.get("type") for block in blocks]

            self.assertNotIn("paragraph", types, f"{name}: izoh olib tashlanishi kerak")
            self.assertIn("shortcut", types, f"{name}: sahifa yorlig'i qolishi kerak")
