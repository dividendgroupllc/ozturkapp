# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa oynasi dizayni: palitra kontrasti, fokus, zal joylashuvi, keng ekran ro'yxati.

Brauzersiz tekshiruvlar. Ko'rinishning o'zi (masshtab, tabletkalar, qatorlar) `bench build`
dan keyin haqiqiy Chrome'da (1536x838, 1920x1080, 1440x900, 1366x768, 1280x720, 1024x768)
ko'zdan kechirilgan; bu testlar uning REGRESSIYASINI ushlaydi:

  - palitra: matn >= 4.5:1, boshqaruv chegarasi/to'ldirishi >= 3:1 (yorug' VA qorong'i mavzu);
  - fokus halqasi faqat klaviaturada (`:focus-visible`), tanlangan holat qora emas;
  - zal masshtabi tabiiy o'lchamga yaqin (<= 1.25), bloklar yonma-yon joylanadi (`packBlocks`);
  - keng ekranda bo'sh o'ng panel = faol buyurtmalar ro'yxati, tor ekranda almashtirgich.
"""

import json
import os
import re
import shutil
import subprocess

import frappe
from frappe.tests.utils import FrappeTestCase

SURFACE = {"light": (255, 255, 255), "dark": (23, 23, 23)}


def _css(name):
    path = frappe.get_app_path("ozturkapp", "public", "css", "cashier", name)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _js(*parts):
    path = frappe.get_app_path("ozturkapp", "public", "js", "cashier", *parts)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _block(css, selector):
    """`selector { ... }` bloki (birinchi mos keluvchi)."""
    start = css.index(selector + " {")
    return css[start : css.index("}", start)]


def _tokens(block):
    return {name: value.strip() for name, value in re.findall(r"--rc-([a-z-]+):\s*([^;]+);", block)}


def _color(value):
    """`#rgb`, `#rrggbb`, `rgba(r, g, b, a)` -> (r, g, b, a)."""
    value = value.strip()
    if value.startswith("#"):
        digits = value[1:]
        if len(digits) == 3:
            digits = "".join(ch * 2 for ch in digits)
        return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16), 1.0)
    numbers = [float(n) for n in re.findall(r"[\d.]+", value)]
    return (numbers[0], numbers[1], numbers[2], numbers[3] if len(numbers) > 3 else 1.0)


def _over(top, bottom):
    alpha = top[3]
    return tuple(top[i] * alpha + bottom[i] * (1 - alpha) for i in range(3))


def _luminance(rgb):
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(a, b):
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


class TestPalette(FrappeTestCase):
    """`--rc-*` o'zgaruvchilari: matn >= 4.5:1, boshqaruv elementlari >= 3:1 (WCAG 2.1 AA)."""

    def setUp(self):
        base = _css("base.css")
        light = _tokens(_block(base, ".rc-root"))
        dark_tokens = _tokens(_block(base, '[data-theme="dark"] .rc-root'))
        self.themes = {"light": light, "dark": {**light, **dark_tokens}}

    def _tone(self, theme, name):
        value = self.themes[theme][name]
        return _color(value)

    def _backgrounds(self, theme, role):
        """Matn turishi mumkin bo'lgan fonlar: sirt va shu rolning tus (soft) foni."""
        surface = SURFACE[theme]
        return {
            "surface": surface,
            f"{role}-soft": _over(self._tone(theme, f"{role}-soft"), surface),
        }

    def test_status_text_is_readable_on_surface_and_tint_in_both_themes(self):
        for theme in ("light", "dark"):
            for role in ("available", "reserved", "occupied", "accent"):
                ink = self._tone(theme, f"{role}-ink")
                for where, background in self._backgrounds(theme, role).items():
                    ratio = _ratio(_over(ink, background), background)
                    self.assertGreaterEqual(
                        ratio, 4.5, f"{theme}: --rc-{role}-ink {where} ustida {ratio:.2f}:1"
                    )

    def test_filled_buttons_have_readable_white_text_in_both_themes(self):
        white = (255, 255, 255)
        for theme in ("light", "dark"):
            for token in ("accent", "available-fill", "occupied"):
                fill = _over(self._tone(theme, token), SURFACE[theme])
                ratio = _ratio(white, fill)
                self.assertGreaterEqual(ratio, 4.5, f"{theme}: oq matn --rc-{token} ustida {ratio:.2f}:1")

    def test_control_borders_and_status_outlines_have_three_to_one(self):
        for theme in ("light", "dark"):
            surface = SURFACE[theme]
            for token in ("available", "reserved", "occupied", "accent", "line"):
                border = _over(self._tone(theme, token), surface)
                ratio = _ratio(border, surface)
                self.assertGreaterEqual(ratio, 3.0, f"{theme}: --rc-{token} chegarasi {ratio:.2f}:1")

    def test_muted_text_falls_back_to_a_readable_grey(self):
        muted = _tokens(_block(_css("base.css"), ".rc-root"))["muted"]
        fallback = re.search(r"var\(--text-muted, (#[0-9a-fA-F]{3,6})\)", muted).group(1)
        self.assertGreaterEqual(_ratio(_color(fallback), SURFACE["light"]), 4.5)

    def test_status_text_uses_ink_tokens_never_the_fill_colours(self):
        """Rangli MATN `-ink` dan: to'ldirish rangi qorong'i fonda o'qilmaydi (3.5:1 edi)."""
        for name in ("base.css", "layout.css", "floor.css", "panel.css", "modal.css", "kit.css"):
            css = _css(name)
            for line in css.splitlines():
                if "__mark" in line:  # ●◆■ belgisi — matn emas, grafik element (>= 3:1 yetarli)
                    continue
                match = re.search(r"(?<![-\w])color:\s*var\(--rc-(available|occupied|reserved)\)", line)
                if match:
                    self.fail(f"{name}: matn rangi `{match.group(0)}` — `-ink` ishlating")

    def test_selected_state_is_accent_tint_not_a_solid_black_pill(self):
        layout = _css("layout.css")
        active = re.search(
            r'\.rc-room\[aria-pressed="true"\],[^{]*\{([^}]*)\}', layout
        ).group(1)
        self.assertIn("var(--rc-accent-soft)", active)
        self.assertIn("var(--rc-accent-ink)", active)
        self.assertNotRegex(active, r"background:\s*var\(--rc-(accent|text)\);")
        # Ko'rinish almashtirgichi va buyurtma filtri ham xuddi shunday (bitta qoida).
        self.assertIn(".rc-tab[aria-pressed=\"true\"]", layout.split(".rc-room[aria-pressed=\"true\"]")[1][:200])
        self.assertIn(".rc-viewtab[aria-selected=\"true\"]", layout.split(".rc-room[aria-pressed=\"true\"]")[1][:200])


class TestFocusRing(FrappeTestCase):
    def test_focus_ring_is_keyboard_only(self):
        base = _css("base.css")
        # Sichqoncha/barmoq bosganda ko'k halqa QOLMAYDI ...
        self.assertRegex(base, r"\.rc-root button:focus[^{]*\{[^}]*outline:\s*none[^}]*box-shadow:\s*none")
        # ... klaviaturada (`:focus-visible`) aniq halqa bor.
        self.assertRegex(base, r"\.rc-root button:focus-visible[^{]*\{[^}]*outline:\s*2px solid var\(--rc-accent-ink\)")
        floor = _css("floor.css")
        self.assertIn(".rc-table:focus-visible", floor)
        self.assertNotRegex(floor, r"\.rc-table:focus\s*\{")


class TestFloorLayout(FrappeTestCase):
    def test_upscale_is_capped_near_natural_size(self):
        floor = _js("ui", "floor.js")
        cap = float(re.search(r"FLOOR_MAX_SCALE = ([\d.]+);", floor).group(1))
        floor_min = float(re.search(r"FLOOR_MIN_SCALE = ([\d.]+);", floor).group(1))
        self.assertLessEqual(cap, 1.25, "stollar «semiz» kattalashib ketmasin (1.5 edi)")
        self.assertGreaterEqual(cap, 1.0)
        self.assertEqual(floor_min, 0.7)

    def test_band_header_and_separator_span_the_block_width(self):
        css = _css("floor.css")
        self.assertRegex(css, r"\.rc-band__header\s*\{[^}]*width:\s*100%[^}]*border-bottom")
        floor = _js("ui", "floor.js")
        # Qatordagi bloklar bo'sh joyni teng bo'lishadi va tuval mavjud enga cho'ziladi.
        self.assertIn("Math.floor(availableWidth / packed.scale)", floor)
        self.assertIn("const extra = Math.max(0, canvasWidth - rowNatural) / row.length", floor)

    def test_server_layout_is_never_mutated_by_the_arrangement(self):
        """`floor.tables[].layout` (sudrash, `touchesVisibleTables`) o'zgarmaydi: qo'shimcha joylashuv alohida."""
        arrange = _js("ui", "floor.js")
        start = arrange.index("	arrangeFloor() {")
        body = arrange[start : arrange.index("	positionFloor(arrangement) {")]
        self.assertNotRegex(body, r"\.layout\.(x|y|width|height)\s*=[^=]")
        self.assertNotRegex(body, r"\.layout\s*=[^=]")

    def test_layout_edit_stays_in_the_single_room_view_with_raw_coordinates(self):
        floor = _js("ui", "floor.js")
        self.assertIn('mode: "plain"', floor)
        # Bitta zalda stol o'zining saqlangan koordinatasida chiziladi (sudrash matematikasi shunga tayanadi).
        table_html = floor[floor.index("	tableHtml(table, position) {") :]
        self.assertIn("position ? position.x : layout.x", table_html)
        self.assertIn("if (!this.room) return;", floor[floor.index("	toggleLayoutEdit() {") :][:200])

    def test_tile_anatomy_is_identical_for_every_status_and_shape(self):
        floor = _js("ui", "floor.js")
        table_html = floor[floor.index("	tableHtml(table, position) {") :]
        order = [
            table_html.index('class="rc-table__age"'),
            table_html.index('class="rc-table__name"'),
            table_html.index('class="rc-table__status"'),
            table_html.index("${detail}"),
            table_html.index("${who}"),
            table_html.index('class="rc-table__flags"'),
        ]
        self.assertEqual(order, sorted(order))
        css = _css("floor.css")
        # O'rinlar soni yo'q stol ham boshqalar bilan bir tekis turadi.
        self.assertIn(".rc-table__meta:empty::before", css)
        # Tabletkalar stol chegarasida turadi — aylana va to'rtburchakda bir xil.
        self.assertRegex(css, r"\.rc-table__age\s*\{\s*top:\s*0;")
        self.assertRegex(css, r"\.rc-table__flags\s*\{\s*bottom:\s*0;")

    def _pack(self, calls):
        node = shutil.which("node")
        modules = os.path.join(frappe.get_app_path("frappe"), "..", "node_modules")
        if not node or not os.path.isdir(os.path.join(modules, "esbuild")):
            self.skipTest("node/esbuild yo'q")

        entry = frappe.get_app_path("ozturkapp", "public", "js", "cashier", "ui", "floor.js")
        script = """
            const esbuild = require("esbuild");
            const out = esbuild.buildSync({ entryPoints: [process.argv[1]], bundle: true, write: false,
                format: "cjs", platform: "neutral", target: "chrome84" }).outputFiles[0].text;
            const mod = { exports: {} };
            new Function("module", "exports", "frappe", "cint", "flt", "__", "$", out)(
                mod, mod.exports, {}, (v) => parseInt(v, 10) || 0, (v) => parseFloat(v) || 0, (s) => s, () => {});
            const calls = JSON.parse(process.argv[2]);
            console.log(JSON.stringify(calls.map(([blocks, w, h]) => mod.exports.packBlocks(
                blocks.map(([width, height]) => ({ width, height })), w, h))));
        """
        result = subprocess.run(
            [node, "-e", script, entry, json.dumps(calls)],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "NODE_PATH": os.path.abspath(modules)},
        )
        if result.returncode:
            raise AssertionError(result.stderr)
        return json.loads(result.stdout)

    def test_rooms_are_packed_side_by_side_when_they_fit(self):
        """Haqiqiy natijalar: kichik zallar yonma-yon, katta zallar joyga qarab qatorlarga."""
        sparse, busy, narrow, single = self._pack(
            [
                ([[283, 168], [120, 158]], 900, 600),  # foydalanuvchining 3 stoli, 1536x838
                ([[742, 332], [586, 332], [586, 332]], 890, 585),  # 26 stol, 3 zal, 1536x838
                ([[742, 332], [586, 332], [586, 332]], 593, 529),  # xuddi shu, 1024x768
                ([[300, 200]], 900, 600),
            ]
        )
        self.assertEqual(sparse["rows"], [[0, 1]])
        self.assertAlmostEqual(sparse["scale"], 1.15)
        # Busy: bitta katta zal + ikkitasi yonma-yon — hammasi aylantirishsiz, 0.7 dan katta masshtabda.
        self.assertEqual(busy["rows"], [[0], [1, 2]])
        self.assertGreater(busy["scale"], 0.7)
        # Tor ekran: yonma-yon bo'lsa gorizontal aylantirish kerak — shuning uchun bitta ustun.
        self.assertEqual(narrow["rows"], [[0], [1], [2]])
        self.assertFalse(narrow["overflow"])
        self.assertEqual(single["rows"], [[0]])


class TestWideWorklist(FrappeTestCase):
    def test_wide_screen_shows_the_worklist_in_the_idle_panel(self):
        layout = _js("core", "layout.js")
        self.assertRegex(layout, r"WIDE_MIN_WIDTH = 1200;")
        orders = _js("ui", "orders.js")
        panel = _js("ui", "panel.js")
        self.assertIn("renderWorklistPanel() {", orders)
        self.assertIn("this.renderWorklistPanel();", panel[panel.index("renderIdlePanel() {") :][:200])
        # Bir xil qatorlar va tartib (hisob so'radi birinchi) ikkala joyda; orders.tabs slotlari ham.
        self.assertIn("ordersHosts() {", orders)
        self.assertIn('slots.visible("orders.tabs"', orders)
        self.assertIn("Number(!!b.bill_requested) - Number(!!a.bill_requested)", orders)
        # Ro'yxatga qaytish tugmasi (keng ekranda) va tanlangan qator belgisi.
        self.assertIn('data-action="panel-back"', panel)
        self.assertIn("this.clearSelection();", panel[panel.index('if (action === "panel-back")') :][:120])
        self.assertIn("this.syncOrderSelection();", js_between(panel, "renderPanel(detail) {", "mountPanelInfo(detail) {"))

    def test_narrow_screen_keeps_the_view_tabs(self):
        layout = _css("layout.css")
        self.assertIn('.rc-root[data-wide="1"] .rc-viewtabs { display: none; }', layout)
        self.assertIn('.rc-root[data-wide="1"] .rc-view--orders { display: none; }', layout)
        topbar = _js("ui", "topbar.js")
        set_view = topbar[topbar.index("	setView(view) {") :]
        # Keng ekranda «Buyurtmalar» = ro'yxatga qaytish; «Stollar» esa tanlovga tegmaydi.
        self.assertIn("else if (this.wide && (this.selectedTable || this.selectedInvoice)) this.clearSelection();", set_view)

    def test_the_worklist_never_shows_money_totals(self):
        """Ko'r sanoq: ro'yxat sarlavhasida faqat SON — jami/kutilgan naqd yo'q."""
        header = js_between(_js("ui", "orders.js"), "renderWorklistHeader() {", "renderWorklistPanel() {")
        for forbidden in ("amount", "expected", "Kutilgan", "Tushum", "money("):
            self.assertNotIn(forbidden, header)

    def test_layout_classes_and_page_scoped_full_width_are_documented(self):
        readme = _js("README.md")
        for term in ("data-wide", "data-compact", "data-short", "core/layout.js", "packBlocks", "--rc-available-ink"):
            self.assertIn(term, readme, f"README'da `{term}` yo'q")
        # Sahifa sarlavhasi va navbar joyida; faqat shu sahifaning ish maydoni to'liq enga ochiladi.
        layout_css = _css("layout.css")
        self.assertIn("#page-restaurant-cashier .container { max-width: none; }", layout_css)


def js_between(source, start_marker, end_marker):
    start = source.index(start_marker)
    return source[start : source.index(end_marker, start)]
