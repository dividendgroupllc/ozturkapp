# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa frontendi — hujum (adversarial) sharhi regressiya testlari.

Har test SHARHDA TASDIQLANGAN nuqsonni qulflaydi. Ikki qatlam:

1. HAQIQIY BRAUZERDA (headless Chrome, `file://` sahifa, esbuild yig'ma) — xulq:
   `errorText` XSS, `parseAmount`/`money` saytning raqam formatidan mustaqilligi,
   `withApproval` tsikli, oyna fokusi, eskirgan javob himoyasi, qayta yangilash
   kechikishi. Chrome yoki esbuild yo'q bo'lsa bu testlar o'tkazib yuboriladi.
2. MANBA MATNI bo'yicha — brauzersiz tekshirib bo'lmaydigan yoki yopishqoq qoidalar
   (CSS zaxiralari, README, taqiqlangan API ro'yxati).

Ishga tushirish::

    bench --site ozturk.local run-tests \
        --module ozturkapp.ozturkapp.tests.test_cashier_review_frontend
"""

import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

import frappe
from frappe.tests.utils import FrappeTestCase

JS_ROOT = frappe.get_app_path("ozturkapp", "public", "js", "cashier")
CSS_ROOT = frappe.get_app_path("ozturkapp", "public", "css")


def _read(*parts):
    with open(os.path.join(JS_ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def _code(*parts):
    """Manba matni IZOHLARSIZ: «taqiqlangan narsa yo'q» tekshiruvi izohdagi tushuntirishga tushmasin."""
    source = _read(*parts)
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", source)


def _css_files():
    for folder in (CSS_ROOT, JS_ROOT):
        for base, _dirs, files in os.walk(folder):
            for name in sorted(files):
                if name.endswith((".css", ".scss")):
                    yield os.path.join(base, name)


def _esbuild():
    path = os.path.join(frappe.get_app_path("frappe"), "..", "node_modules", ".bin", "esbuild")
    return os.path.abspath(path) if os.path.exists(path) else None


def _js_sources():
    for base, _dirs, files in os.walk(JS_ROOT):
        for name in sorted(files):
            if name.endswith(".js"):
                yield os.path.join(base, name)


# ═══════════════════════════════════════════════════════════════════
#  Haqiqiy brauzerda xulq tekshiruvi
# ═══════════════════════════════════════════════════════════════════

# Brauzer sahifasidagi test kodi. Yadro modullari HAQIQIY manbadan esbuild bilan yig'iladi
# (`--target=chrome84` — bu ayni paytda «eng kam brauzer» kafolati). Stublar faqat Desk
# global'lari: `__`, `cint`, `flt` (`#.###,##` formatini TAQLID qiladi — Frappe'ning haqiqiy
# `flt("1234.5")` i shunda 12345 beradi), `frappe.call`.
ENTRY = r"""
import { errorText, maskApprovalInErrorReports } from "__ROOT__/core/api.js";
import { money, num, fmtQty, groupAmount, parseAmount, bindAmountInput } from "__ROOT__/util/format.js";
import { withApproval } from "__ROOT__/kit/approval.js";
import { ui } from "__ROOT__/kit/index.js";
import { closeTop, trapTab } from "__ROOT__/kit/dialog.js";
import { CashierScreen } from "__ROOT__/core/screen.js";
import { RealtimeMethods } from "__ROOT__/core/realtime.js";
import { PanelMethods } from "__ROOT__/ui/panel.js";

const out = {};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const jq = window.jQuery;

async function main() {
	// ── errorText: XSS ──────────────────────────────────────────
	window.__pwn = "";
	const hostile = (m) => ({ responseJSON: { _server_messages: JSON.stringify([JSON.stringify({ message: m })]) } });
	out.errXss = errorText(hostile("Mijoz <img src=x onerror=\"window.__pwn+='a'\"> bloklangan"));
	out.errXssScript = errorText(hostile("<script>window.__pwn+='s'</script>Matn <style>x{}</style>"));
	out.errXssException = errorText({ responseJSON: { exception: "frappe.exceptions.ValidationError: <img src=x onerror=\"window.__pwn+='e'\">Sabab" } });
	out.errHtmlText = errorText(hostile("<b>Qalin</b> matn"));
	await sleep(400); // rasm yuklanib `onerror` ishlashi uchun vaqt
	out.pwn = window.__pwn;

	// ── errorText: kassirga tushunarli matn ────────────────────
	out.errOffline = errorText({ status: 0, statusText: "error" });
	out.err500 = errorText({ status: 500, responseJSON: { exc_type: "TypeError", exc: "[\"Traceback\\nTypeError: x\"]" } });
	out.err504 = errorText({ status: 504, statusText: "Gateway Timeout" });
	out.err403 = errorText({ status: 403, responseJSON: { exc_type: "PermissionError" } });
	out.err200 = errorText({ status: 200, statusText: "OK" });
	out.err417 = errorText({ status: 417, responseJSON: { exc_type: "ValidationError", exception: "frappe.exceptions.ValidationError: Stol band" } });
	out.err417none = errorText({ status: 417, responseJSON: { exc_type: "ValidationError" } });
	out.errJs = errorText(new Error("Izoh juda uzun"));
	out.errObject = errorText({ foo: 1 });

	// ── Menejer PIN'i Frappe xato hisobotiga tushmasin ─────────
	window.frappe.request = { cleanup_request_opts(opts) { opts.args.password = "*****"; return opts; } };
	maskApprovalInErrorReports();
	maskApprovalInErrorReports();
	const cleaned = window.frappe.request.cleanup_request_opts({ args: { approval: JSON.stringify({ user: "m", pin: "482913" }), invoice: "X", password: "p" } });
	out.pinReport = { approval: cleaned.args.approval, invoice: cleaned.args.invoice, password: cleaned.args.password };

	// ── summa: saytning raqam formati (#.###,##) ga bog'liq emas ──
	out.parse = ["1234,5", "1 234,5", "1234.5", "1 080 800", "-5", "abc", "", "0005", "1,5,5"].map(parseAmount);
	out.parseHuge = parseAmount("123456789012345678901234");
	out.groupHuge = groupAmount("123456789012345678901234");
	out.groupExp = [groupAmount(1e21), groupAmount(1e-7), groupAmount(0.1 + 0.2), groupAmount(551040), groupAmount(1234.5)];
	out.money = [0.1 + 0.2, -0.004, 1080800.999, "1080800.5", NaN, null, 1234.5].map(money);
	out.fmtQty = [0.1 + 0.2, "1.5", 2, "abc"].map(fmtQty);
	out.numStr = [num("1234.5"), num("x"), num(null), num(3), num(Infinity)];

	// ── summa maydoni: manfiy belgi tashlanadi, 12 xonadan ortiq kiritilmaydi ──
	const input = document.createElement("input");
	document.body.appendChild(input);
	bindAmountInput(jq(input), () => {});
	const type = (v) => { input.value = v; input.dispatchEvent(new Event("input", { bubbles: true })); return input.value; };
	out.typedMinus = type("-5x0y0");
	out.typedHuge = type("123456789012345678901");
	out.typedComma = type("1234,567");

	// ── withApproval: menejer o'zi tasdiqlagan, server baribir so'raydi ──
	let calls = 0;
	window.frappe.call = () => Promise.resolve({ message: { self_approves: true, approvers: [] } });
	const required = () => { const e = new Error("ApprovalRequired"); e.responseJSON = { exc_type: "ApprovalRequired", _server_messages: JSON.stringify([JSON.stringify({ message: "Tasdiq kerak" })]) }; return e; };
	try {
		await withApproval(async () => { calls += 1; if (calls > 50) throw new Error("LOOP"); throw required(); });
		out.approval = "resolved";
	} catch (error) {
		out.approval = error.message;
	}
	out.approvalCalls = calls;

	// ── Dialog: Tab oyna ichida aylanadi, fokus qaytadi, band oyna Esc bilan yopilmaydi ──
	const opener = document.createElement("button");
	opener.textContent = "opener";
	const outside = document.createElement("button");
	outside.textContent = "outside";
	document.body.append(opener, outside);
	opener.focus();
	const dlg = ui.dialog({
		title: "T",
		fields: [{ type: "text", name: "a", label: "A" }],
		actions: [{ id: "a", label: "A" }, { id: "b", label: "B" }],
	});
	const items = [...dlg.dialogEl.querySelectorAll("button, input")];
	const first = items[0];
	const last = items[items.length - 1];
	const tab = (shiftKey = false) => {
		const ev = new KeyboardEvent("keydown", { key: "Tab", shiftKey, bubbles: true, cancelable: true });
		(document.activeElement || document.body).dispatchEvent(ev);
		return ev.defaultPrevented;
	};
	last.focus();
	out.trapForward = { prevented: tab(false), wrapped: document.activeElement === first };
	first.focus();
	out.trapBackward = { prevented: tab(true), wrapped: document.activeElement === last };

	dlg.setBusy(true);
	closeTop();
	out.busyKeepsOpen = !dlg.closed;
	dlg.overlay.querySelector(".rc-dialog__x").click();
	out.busyXKeepsOpen = !dlg.closed;
	dlg.setBusy(false);
	closeTop();
	out.closedAfterIdle = dlg.closed;
	out.focusRestored = document.activeElement === opener;

	// ── Eskirgan javob: zal so'rovlari tartibsiz qaytadi ─────────
	const pending = {};
	const defer = (key) => new Promise((resolve) => { pending[key] = resolve; });
	const ctx = {
		room: "A", floor: null, selectedTable: null, seen: [],
		call: (m, a) => defer(a.room),
		renderFilters() {}, renderFloor() { this.seen.push(this.floor.room); }, clearSelection() {},
	};
	const first_ = CashierScreen.prototype.loadFloor.call(ctx);
	ctx.room = "B";
	const second_ = CashierScreen.prototype.loadFloor.call(ctx);
	pending.B({ room: "B", tables: [] });
	await second_;
	pending.A({ room: "A", tables: [] });
	await first_;
	out.staleFloor = { finalRoom: ctx.floor.room, rendered: ctx.seen };

	const dctx = {
		selectedTable: "T1", rendered: [], calls: {},
		call: (m, a) => defer("detail" + Object.keys(pending).length + a.table),
		renderPanel(d) { this.rendered.push(d.v); }, panelError() { this.rendered.push("error"); },
	};
	const oldReq = PanelMethods.prototype.loadTableDetail.call(dctx, "T1");
	const newReq = PanelMethods.prototype.loadTableDetail.call(dctx, "T1");
	const keys = Object.keys(pending).filter((k) => k.startsWith("detail"));
	pending[keys[1]]({ v: "new" });
	await newReq;
	pending[keys[0]]({ v: "old" });
	await oldReq;
	out.staleDetail = dctx.rendered;

	// ── Qayta yangilash: signallar tinimsiz kelsa ham 2 soniyadan ortiq kechikmaydi ──
	const delays = [];
	const realSetTimeout = window.setTimeout;
	const realNow = Date.now;
	let now = 1000000;
	Date.now = () => now;
	window.setTimeout = (fn, ms) => { delays.push(ms); return 7; };
	const rctx = { pendingScope: null, pendingSince: 0, refreshTimer: null, destroyed: false, refresh: () => Promise.resolve(), markStale() {} };
	[0, 200, 1700, 1900, 2100].forEach((dt) => { now = 1000000 + dt; RealtimeMethods.prototype.scheduleRefresh.call(rctx, { orders: true }); });
	window.setTimeout = realSetTimeout;
	Date.now = realNow;
	out.debounceDelays = delays;

	// ── Modul obunalari boot() da yo'qolmaydi ──────────────────
	const offs = [];
	window.frappe.realtime = { on() {}, off: (e) => offs.push(e), socket: { connected: true } };
	const sctx = { subscriptions: [], subscribingCore: false };
	RealtimeMethods.prototype.listen.call(sctx, "module_event", () => {});
	sctx.subscribingCore = true;
	RealtimeMethods.prototype.listen.call(sctx, "core_event", () => {});
	sctx.subscribingCore = false;
	RealtimeMethods.prototype.unsubscribeCore.call(sctx);
	out.subscriptions = { left: sctx.subscriptions.map((s) => s[0]), removed: offs };

	document.getElementById("out").textContent = JSON.stringify(out);
}

main().catch((error) => { document.getElementById("out").textContent = JSON.stringify({ crash: String(error && error.stack || error) }); });
"""

HTML = """<!doctype html><html><head><meta charset="utf-8"></head><body><pre id="out">PENDING</pre>
<script src="jquery.min.js"></script>
<script>
window.__ = (s, r) => (r ? String(s).replace(/\\{(\\d+)\\}/g, (_, i) => r[i]) : s);
window.cint = (v) => parseInt(v, 10) || 0;
/* Frappe `flt()` ning `#.###,##` formatidagi xulqi: nuqta — GURUH ajratgichi (o'chiriladi), vergul — o'nlik. */
window.flt = (v) => { if (typeof v === "number") return v; const t = String(v).replace(/\\./g, "").replace(/,/g, "."); const n = parseFloat(t); return Number.isNaN(n) ? 0 : n; };
window.frappe = { session: { user: "t@example.com" }, datetime: { now_time: () => "10:00:00" }, boot: {}, call: () => Promise.resolve({ message: null }) };
window.ozturk = { cashier: {} };
</script>
<script src="bundle.js"></script></body></html>"""


class TestBrowserBehaviour(FrappeTestCase):
    """Yig'ilgan yadro modullari HAQIQIY Chrome'da ishlatiladi."""

    result = None
    skip_reason = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        chrome = shutil.which("google-chrome") or shutil.which("chromium")
        esbuild = _esbuild()
        if not chrome or not esbuild:
            cls.skip_reason = "Chrome yoki esbuild topilmadi"
            return

        with tempfile.TemporaryDirectory(prefix="ozturk_fe_review_", dir="/tmp") as work:
            entry = os.path.join(work, "entry.js")
            with open(entry, "w", encoding="utf-8") as handle:
                handle.write(ENTRY.replace("__ROOT__", JS_ROOT))
            with open(os.path.join(work, "index.html"), "w", encoding="utf-8") as handle:
                handle.write(HTML)
            shutil.copy(
                os.path.join(
                    frappe.get_app_path("frappe"), "public", "js", "lib", "jquery", "jquery.min.js"
                ),
                os.path.join(work, "jquery.min.js"),
            )

            build = subprocess.run(
                [
                    esbuild, entry, "--bundle", "--format=iife", "--target=chrome84",
                    f"--outfile={os.path.join(work, 'bundle.js')}", "--log-level=error",
                ],
                capture_output=True, text=True, timeout=120,
            )
            if build.returncode:
                raise AssertionError(f"esbuild (chrome84) yiqildi:\n{build.stderr}")

            profile = os.path.join(work, "profile")
            run = subprocess.run(
                [
                    chrome, "--headless=new", "--disable-gpu", "--no-first-run",
                    "--no-default-browser-check", f"--user-data-dir={profile}",
                    "--virtual-time-budget=6000", "--dump-dom",
                    f"file://{os.path.join(work, 'index.html')}",
                ],
                capture_output=True, text=True, timeout=120,
            )
            match = re.search(r'<pre id="out">(.*?)</pre>', run.stdout, re.S)
            if not match or match.group(1) == "PENDING":
                raise AssertionError(f"Chrome natija bermadi:\n{run.stdout[-800:]}\n{run.stderr[-400:]}")
            cls.result = json.loads(html.unescape(match.group(1)))

    def setUp(self):
        if self.skip_reason:
            raise unittest.SkipTest(self.skip_reason)
        self.assertNotIn("crash", self.result, self.result.get("crash"))

    # ── errorText ────────────────────────────────────────────────

    def test_error_text_never_executes_server_html(self):
        """`<img onerror>` server xabarida ishlab ketmasligi kerak (saqlangan XSS:
        kassir yaratgan mijoz nomi menejer sessiyasida skript bo'lib ishlardi)."""
        self.assertEqual(self.result["pwn"], "")
        self.assertEqual(self.result["errXss"], "Mijoz  bloklangan")
        self.assertEqual(self.result["errXssScript"], "Matn")
        self.assertEqual(self.result["errHtmlText"], "Qalin matn")
        self.assertEqual(self.result["errXssException"], "Sabab")

    def test_error_text_hides_technical_words_from_the_cashier(self):
        r = self.result
        self.assertIn("aloqa yo'q", r["errOffline"])  # «error» emas
        self.assertIn("Serverda xatolik", r["err500"])  # «TypeError» emas
        self.assertIn("javob bermadi", r["err504"])
        self.assertIn("Sessiya", r["err403"])  # «PermissionError» emas
        self.assertIn("sessiya tugagan", r["err200"])  # «OK» emas
        self.assertEqual(r["err417"], "Stol band")
        self.assertEqual(r["err417none"], "Noma'lum xato")  # «ValidationError» emas
        self.assertEqual(r["errJs"], "Izoh juda uzun")
        self.assertNotIn("[object Object]", r["errObject"])

    def test_manager_pin_never_reaches_the_frappe_error_report(self):
        """500 da Frappe «Copy error to clipboard» so'rov argumentlarini matnga yozadi:
        `approval` ichidagi PIN yashirilishi kerak (Frappe faqat `password` kalitini yashiradi)."""
        report = self.result["pinReport"]
        self.assertEqual(report["approval"], "*****")
        self.assertEqual(report["invoice"], "X")
        self.assertEqual(report["password"], "*****")  # Frappe'ning o'z niqobi buzilmagan

    # ── summa ────────────────────────────────────────────────────

    def test_amounts_do_not_depend_on_the_site_number_format(self):
        """`flt("1234.5")` `#.###,##` da 12345 beradi — kassa summasi 10 baravar o'zgarardi."""
        self.assertEqual(self.result["parse"][:4], [1234.5, 1234.5, 1234.5, 1080800])
        self.assertEqual(self.result["money"][3], "1 080 800,50")
        self.assertEqual(self.result["fmtQty"][1], "1.5")
        self.assertEqual(self.result["numStr"], [1234.5, 0, 0, 3, 0])

    def test_amount_parsing_edges(self):
        r = self.result
        self.assertEqual(r["parse"][4:], [-5, 0, 0, 5, 1.55])  # groupAmount bilan bir xil qoida
        # 20+ raqam 2^53 dan oshib aniqligini yo'qotmasin: 12 xonada kesiladi.
        self.assertEqual(r["groupHuge"], "123 456 789 012")
        self.assertEqual(r["parseHuge"], 123456789012)
        # «ilmiy» yozuv (1e21) raqamlar yig'indisiga aylanib qolmasin.
        self.assertEqual(r["groupExp"], ["999 999 999 999", "0", "0,3", "551 040", "1 234,5"])

    def test_money_display_has_no_noise(self):
        self.assertEqual(
            self.result["money"], ["0,30", "0", "1 080 801", "1 080 800,50", "0", "0", "1 234,50"]
        )
        self.assertEqual(self.result["fmtQty"][0], "0.3")

    def test_amount_input_rejects_minus_and_overlong_numbers(self):
        self.assertEqual(self.result["typedMinus"], "500")
        self.assertEqual(self.result["typedHuge"], "123 456 789 012")
        self.assertEqual(self.result["typedComma"], "1 234,56")

    # ── withApproval ─────────────────────────────────────────────

    def test_with_approval_does_not_hammer_the_server(self):
        """Menejer o'zi tasdiqlagan (`self_approves`), server baribir so'rasa — cheksiz
        qayta yuborish soniyasiga yuzlab so'rov bo'lardi."""
        self.assertEqual(self.result["approvalCalls"], 2)
        self.assertEqual(self.result["approval"], "ApprovalRequired")

    # ── oyna ─────────────────────────────────────────────────────

    def test_dialog_focus_is_trapped_and_restored(self):
        r = self.result
        self.assertTrue(r["trapForward"]["prevented"] and r["trapForward"]["wrapped"])
        self.assertTrue(r["trapBackward"]["prevented"] and r["trapBackward"]["wrapped"])
        self.assertTrue(r["focusRestored"])

    def test_busy_dialog_ignores_esc_and_close_button(self):
        """So'rov ketayotganda oyna yopilsa xato ko'rinmay qolardi."""
        r = self.result
        self.assertTrue(r["busyKeepsOpen"])
        self.assertTrue(r["busyXKeepsOpen"])
        self.assertTrue(r["closedAfterIdle"])

    # ── eskirgan javob va yangilash ──────────────────────────────

    def test_only_the_latest_floor_response_is_applied(self):
        """Zal almashtirilganda sekin eski javob yangisining ustiga yozilmasligi kerak."""
        stale = self.result["staleFloor"]
        self.assertEqual(stale["finalRoom"], "B")
        self.assertEqual(stale["rendered"], ["B"])

    def test_only_the_latest_panel_response_is_rendered(self):
        self.assertEqual(self.result["staleDetail"], ["new"])

    def test_refresh_is_never_starved_by_a_stream_of_events(self):
        """Faqat «oxirgi signaldan 250ms keyin» qoidasi tinimsiz signalda yangilashni to'xtatardi."""
        delays = self.result["debounceDelays"]
        self.assertEqual(delays[0], 250)
        self.assertTrue(all(delay <= 250 for delay in delays))
        self.assertEqual(delays[-1], 0)  # 2 soniyadan keyin kutilmaydi

    def test_module_subscriptions_survive_a_reboot(self):
        """`boot()` qayta ishlaganda faqat yadro obunalari yechiladi: modul `install()` da
        olgan `screen.listen()` obunasi bir marta chaqiriladi va qayta tiklanmaydi."""
        subs = self.result["subscriptions"]
        self.assertEqual(subs["left"], ["module_event"])
        self.assertEqual(subs["removed"], ["core_event"])


# ═══════════════════════════════════════════════════════════════════
#  Manba matni (yopishqoq qoidalar)
# ═══════════════════════════════════════════════════════════════════


class TestSourceRules(FrappeTestCase):
    def test_error_text_uses_an_inert_parser_not_jquery_html(self):
        source = _code("core", "api.js")
        self.assertIn("DOMParser", source)
        self.assertNotIn(".html(", source)

    def test_no_server_html_is_ever_turned_into_text_through_jquery(self):
        for path in _js_sources():
            body = re.sub(r"/\*.*?\*/", "", open(path, encoding="utf-8").read(), flags=re.S)
            self.assertNotRegex(
                body, r'\$\(\s*["\']<div>["\']\s*\)\s*\.html\(', f"{path}: $('<div>').html(...)"
            )

    def test_amount_helpers_do_not_call_frappe_flt(self):
        source = _read("util", "format.js")
        for name in ("money", "parseAmount", "fmtQty"):
            body = source[source.index(f"export function {name}(") :].split("\n}\n", 1)[0]
            self.assertNotIn("flt(", body, f"{name} flt() ga tayanmasligi kerak")

    def test_amount_fields_select_their_content_on_tap(self):
        """Tayyor summa turgan maydonga tegib raqam bosilsa u almashishi kerak (asl sahifada
        «fresh» qoidasi bor edi): «131 040» + «5» -> «1 310 405» bo'lib qolmasin."""
        format_js = _code("util", "format.js")
        binder = format_js[format_js.index("export function bindAmountInput") :]
        self.assertIn('$input.on("focus"', binder)
        self.assertIn("el.select()", binder)

    def test_string_amounts_read_from_the_dom_use_num_not_flt(self):
        for path in ("ui/split.js", "features/payment/tips.js", "features/payment/refunds.js"):
            body = _read(*path.split("/"))
            self.assertNotRegex(body, r"flt\([^)]*(dataset|slice\()", path)

    def test_realtime_core_subscriptions_are_separate_from_module_ones(self):
        source = _read("core", "realtime.js")
        self.assertIn("unsubscribeCore()", source)
        self.assertIn("subscribingCore", source)
        self.assertIn("REFRESH_MAX_WAIT", source)
        self.assertIn(".catch((error) => this.markStale(error))", source)
        self.assertIn("if (this.active) this.beep();", source)

    def test_loads_apply_only_the_latest_response(self):
        screen = _read("core", "screen.js")
        self.assertIn("this.floorRequest", screen)
        self.assertIn("this.ordersRequest", screen)
        self.assertIn("this.detailRequest", _read("ui", "panel.js"))
        history = _read("ui", "history.js")
        self.assertIn("loadRequest", history)
        self.assertIn("detailRequest", history)

    def test_bill_split_is_not_repeated_after_a_partial_failure(self):
        source = _read("ui", "split.js")
        self.assertIn("let created = null;", source)
        self.assertIn("if (!created) {", source)
        # `open_bill` yiqilganda qayta bosish `split_bill` ni ikkinchi marta chaqirmaydi.
        self.assertEqual(source.count("billing.split_bill"), 1)

    def test_payment_modal_is_locked_while_the_request_is_in_flight(self):
        source = _read("ui", "payment.js")
        at = source.index("api.billing.submit_payment")
        confirm = source[at - 900 : at + 1500]
        self.assertIn("this.setModalLocked(true)", confirm)
        self.assertIn("this.setModalLocked(false)", confirm)
        self.assertIn("if (this.modalLocked) return;", source)
        shift = _read("ui", "shift.js")
        self.assertIn("this.setModalLocked(true);", shift[shift.index("close_shift") - 400 :])

    def test_menu_outside_click_handler_cannot_leak(self):
        menu = _read("ui", "menu.js")
        body = menu[menu.index("openMenu()") : menu.index("closeMenu()")]
        self.assertNotIn("setTimeout", body)

    def test_bundle_installs_the_pin_mask_at_load(self):
        self.assertIn("maskApprovalInErrorReports();", _read("cashier.bundle.js"))

    def test_floor_is_not_redrawn_in_the_middle_of_a_table_drag(self):
        """Sudrash paytida realtime yangilash stolni DOM'dan olib tashlasa `pointerup` unga
        yetmaydi va surish jimgina yo'qoladi (haqiqiy sichqoncha bilan sinalgan)."""
        floor = _read("ui", "floor.js")
        self.assertIn("this.draggingTable", floor)
        self.assertIn("this.floorDirty = true;", floor)
        self.assertIn("redrawLater()", floor)

    def test_page_entry_never_stays_blank_when_the_bundle_fails_to_load(self):
        """`frappe.require` faqat resolve qiladi: fayl so'rovi yiqilsa va'da hech qachon hal
        bo'lmaydi va Desk sahifani `freeze()` holida qoldiradi."""
        entry = open(
            frappe.get_app_path("ozturkapp", "ozturkapp", "page", "restaurant_cashier", "restaurant_cashier.js"),
            encoding="utf-8",
        ).read()
        self.assertIn("LOAD_TIMEOUT_MS", entry)
        self.assertIn("Promise.race([loaded, timeout])", entry)
        self.assertIn("frappe.dom.unfreeze();", entry)
        self.assertIn("showLoadError(", entry)

    def test_offline_socket_falls_back_to_polling_without_a_retry_storm(self):
        realtime = _read("core", "realtime.js")
        self.assertIn("OFFLINE_POLL", realtime)
        screen = _read("core", "screen.js")
        # Urinish vaqti (muvaffaqiyat emas) yoziladi: tarmoq yo'q paytda har 250ms qayta urinilmaydi.
        self.assertRegex(screen, r"this\.lastRefreshAt = Date\.now\(\);\s*const jobs")

    def test_fire_and_forget_refreshes_report_their_failure(self):
        for path in (("ui", "menu.js"), ("ui", "topbar.js")):
            self.assertRegex(_code(*path), r"refreshAll\(\)\.catch\(")
        self.assertRegex(_code("ui", "panel.js"), r"screen\.refreshAll\(\)\.catch\(")

    def test_with_approval_stops_after_a_self_approval_is_refused(self):
        self.assertIn('approval.pin === ""', _read("kit", "approval.js"))

    def test_modal_has_a_real_label_and_traps_tab(self):
        self.assertIn('.attr("aria-label", title)', _read("ui", "modal.js"))
        self.assertIn("trapTab(", _read("core", "screen.js"))

    # ── CSS ──────────────────────────────────────────────────────

    def test_every_dvh_height_has_a_vh_fallback_before_it(self):
        """Chrome <108 `dvh` ni bilmaydi: zaxirasiz deklaratsiya butunlay tashlab yuboriladi."""
        for path in _css_files():
            css = open(path, encoding="utf-8").read()
            for match in re.finditer(r"([a-z-]*height|height)\s*:[^;]*100dvh[^;]*;", css):
                prop = match.group(1)
                before = css[max(0, match.start() - 160) : match.start()]
                self.assertRegex(
                    before, rf"{prop}\s*:[^;]*100vh[^;]*;\s*$", f"{path}: {match.group(0)}"
                )

    def test_css_avoids_features_newer_than_the_supported_floor(self):
        for path in _css_files():
            css = open(path, encoding="utf-8").read()
            self.assertNotRegex(css, r"[;{\s]inset\s*:", f"{path}: `inset` (Chrome 87)")
            self.assertNotRegex(css, r"aspect-ratio\s*:", f"{path}: aspect-ratio (88)")
            self.assertNotRegex(css, r":is\(", f"{path}: :is() (88)")

    def test_dark_theme_does_not_paint_dark_text_on_dark_surfaces(self):
        base = open(os.path.join(CSS_ROOT, "cashier", "base.css"), encoding="utf-8").read()
        self.assertIn(":where(.rc-root) button { color: inherit; }", base)
        self.assertIn('[data-theme="dark"] .rc-root', base)
        self.assertIn("--rc-accent-ink", base)
        # Desk'ning `--primary` i qorong'i mavzuda ham to'q: matn rangi sifatida ishlatilmaydi.
        for path in _css_files():
            css = open(path, encoding="utf-8").read()
            self.assertNotRegex(css, r"(?<![-\w])color:\s*var\(--rc-accent\)", path)

    def test_pinned_footers_keep_the_primary_action_visible(self):
        modal = open(os.path.join(CSS_ROOT, "cashier", "modal.css"), encoding="utf-8").read()
        self.assertIn(".rc-split__foot", modal)
        self.assertRegex(modal, r"position:\s*sticky;\s*bottom:\s*-14px")
        self.assertIn('class="rc-split__foot"', _read("ui", "split.js"))

    # ── Eng kam brauzer ──────────────────────────────────────────

    def test_readme_states_the_minimum_chrome(self):
        readme = _read("README.md")
        self.assertIn("Chrome 84", readme)
        self.assertIn("Brauzer talabi", readme)

    def test_sources_avoid_apis_newer_than_the_supported_floor(self):
        """`replaceChildren` (86), `replaceAll` (85), `.at()` (92), `structuredClone` (98),
        `Object.hasOwn` (93), `findLast` (97) Chrome 84 da yo'q."""
        banned = re.compile(
            r"\.replaceChildren\(|\.replaceAll\(|\.at\(-?\d|structuredClone\(|Object\.hasOwn\(|"
            r"\.findLast(Index)?\(|\.toSorted\("
        )
        for path in _js_sources():
            body = open(path, encoding="utf-8").read()
            self.assertIsNone(banned.search(body), f"{path}: {banned.search(body)}")

    def test_bundles_compile_for_the_supported_floor(self):
        """esbuild `chrome84` maqsadi: yangi sintaksis qo'llab-quvvatlanmasa xato beradi."""
        esbuild = _esbuild()
        if not esbuild:
            raise unittest.SkipTest("esbuild topilmadi")

        entries = [os.path.join(JS_ROOT, "cashier.bundle.js")]
        for name in sorted(os.listdir(os.path.join(JS_ROOT, "features"))):
            folder = os.path.join(JS_ROOT, "features", name)
            entries += [
                os.path.join(folder, f) for f in sorted(os.listdir(folder)) if f.endswith(".bundle.js")
            ]

        for entry in entries:
            result = subprocess.run(
                [esbuild, entry, "--bundle", "--target=chrome84", "--log-level=error", "--outfile=/dev/null"],
                capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(result.returncode, 0, f"{entry}: {result.stderr}")
