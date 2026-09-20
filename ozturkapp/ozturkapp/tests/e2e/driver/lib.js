// E2E haydovchi kutubxonasi: CDP ustida sahifa, Fetch->shim yo'naltirish, tasdiqlar, hisobot.
const fs = require("fs");
const path = require("path");
const { launch, sleep } = require("./cdp.js");

const BASE = "http://127.0.0.1:8000";
const API_PREFIX = "/api/method/ozturkapp.ozturkapp.api.";

/** Ma'lum, zararsiz konsol shovqini (socket.io va Desk'ning o'z so'rovlari). */
const IGNORED_CONSOLE = [/socket\.io/i, /WebSocket connection/i, /favicon/i, /ERR_CONNECTION_REFUSED.*9000/];

class Harness {
	constructor(env) {
		this.env = env;
		this.shimUrl = `http://127.0.0.1:${env.E2E_SHIM_PORT}`;
		this.workDir = env.E2E_WORK;
		this.shotDir = path.join(this.workDir, "shots");
		fs.mkdirSync(this.shotDir, { recursive: true });
		this.scenario = "";
		this.stepName = "";
		this.consoleErrors = [];
		this.failedRequests = [];
		this.browserRequests = [];
		this.checks = [];
		this.results = [];
		this.queue = Promise.resolve();
		this.logCursor = 0;
		this.expected = [];
		this.shared = {};
	}

	/** Shu qadamda KUTILGAN non-2xx javob (masalan PIN so'ralganda 403 ApprovalRequired). */
	expect(method, status, excType = "") {
		this.expected.push({ scenario: this.scenario, step: this.stepName, method, status, excType });
	}

	// ── Brauzer ─────────────────────────────────────────────────────────

	async start(width = 1366, height = 768) {
		const { chrome, cdp } = await launch({
			port: Number(this.env.E2E_CHROME_PORT),
			profile: path.join(this.workDir, "profile"),
			width,
			height,
			headed: !!this.env.E2E_HEADED,
		});
		this.chrome = chrome;
		this.cdp = cdp;
		for (const domain of ["Page", "Runtime", "Network", "Log"]) await cdp.send(`${domain}.enable`);
		await cdp.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
		await cdp.send("Fetch.enable", { patterns: [{ urlPattern: `*${API_PREFIX}*`, requestStage: "Request" }] });
		cdp.on((msg) => this.onEvent(msg));
	}

	async stop() {
		try {
			this.cdp && this.cdp.ws.close();
		} catch (e) {}
		if (this.chrome && !this.chrome.killed) {
			this.chrome.kill("SIGTERM");
			await sleep(500);
			if (!this.chrome.killed) this.chrome.kill("SIGKILL");
		}
	}

	onEvent(msg) {
		const p = msg.params;
		if (msg.method === "Fetch.requestPaused") {
			// Shim bitta DB ulanishi ustida ishlaydi: so'rovlar brauzer yuborgan tartibda ketma-ket.
			this.queue = this.queue.then(() => this.forward(p)).catch((e) => {
				// Sahifa so'rovni o'zi bekor qilgan (navigatsiya/abort) — javob berish shart emas.
				if (!String(e && e.message).includes("Invalid InterceptionId")) console.error("forward xatosi", e);
				else this.abandoned = (this.abandoned || 0) + 1;
			});
		} else if (msg.method === "Runtime.exceptionThrown") {
			const d = p.exceptionDetails;
			this.noteConsole("EXC", (d.exception && d.exception.description) || d.text);
		} else if (msg.method === "Runtime.consoleAPICalled" && p.type === "error") {
			this.noteConsole("console.error", p.args.map((a) => a.value || a.description || "").join(" "));
		} else if (msg.method === "Log.entryAdded" && p.entry.level === "error") {
			this.noteConsole("log", `${p.entry.text} ${p.entry.url || ""}`);
		} else if (msg.method === "Network.loadingFailed") {
			const err = p.errorText || "";
			if (!p.canceled) this.failedRequests.push({ step: this.label(), what: `loadingFailed ${err}`, id: p.requestId });
		} else if (msg.method === "Network.responseReceived") {
			const r = p.response;
			if (r.status >= 400 && !r.url.includes(API_PREFIX)) {
				this.failedRequests.push({ step: this.label(), what: `HTTP ${r.status} ${r.url.replace(BASE, "")}` });
			}
		}
	}

	label() {
		return `${this.scenario} / ${this.stepName}`;
	}

	noteConsole(kind, text) {
		text = String(text || "").slice(0, 400);
		if (IGNORED_CONSOLE.some((re) => re.test(text))) return;
		this.consoleErrors.push({ step: this.label(), kind, text });
	}

	async forward(p) {
		const request = p.request;
		let body = request.postData || null;
		if (!body && request.hasPostData) {
			try {
				body = (await this.cdp.send("Network.getRequestPostData", { requestId: p.networkId || p.requestId })).postData;
			} catch (e) {}
		}
		const reply = await this.shimPost("/__api", {
			method: request.method,
			url: request.url,
			headers: request.headers,
			body,
		});
		this.browserRequests.push({ step: this.label(), method: request.method, url: request.url.replace(BASE + API_PREFIX, "") });
		await this.cdp.send("Fetch.fulfillRequest", {
			requestId: p.requestId,
			responseCode: reply.status,
			responseHeaders: [{ name: "Content-Type", value: reply.headers["Content-Type"] || "application/json" }],
			body: Buffer.from(reply.body, "utf-8").toString("base64"),
		});
	}

	// ── Shim ────────────────────────────────────────────────────────────

	async shimPost(route, payload) {
		const res = await fetch(this.shimUrl + route, { method: "POST", body: JSON.stringify(payload) });
		const data = await res.json();
		if (data.error) throw new Error(`shim ${route}: ${data.error}\n${data.trace || ""}`);
		return data.result;
	}

	op(name, args = {}) {
		return this.shimPost("/__op", { op: name, args });
	}

	sql(query, values = []) {
		return this.op("sql", { query, values });
	}

	async one(query, values = []) {
		const rows = await this.sql(query, values);
		return rows[0] || null;
	}

	/** Brauzersiz, to'g'ridan-to'g'ri API chaqiruvi: FAQAT shu so'rov `user` nomidan (Frappe yo'li bilan). */
	async apiAs(user, method, args = {}, { http = "POST" } = {}) {
		const body = new URLSearchParams(
			Object.entries(args).map(([k, v]) => [k, typeof v === "object" ? JSON.stringify(v) : String(v)])
		).toString();
		let url = `${BASE}${API_PREFIX}${method}`;
		const headers = { "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", Accept: "application/json", "X-Requested-With": "XMLHttpRequest" };
		let payload = body;
		if (http === "GET") {
			url += "?" + body;
			payload = null;
		}
		const reply = await this.shimPost("/__api", { method: http, url, headers, body: payload, as: user });
		let data = {};
		try {
			data = JSON.parse(reply.body);
		} catch (e) {}
		return { status: reply.status, data, message: data.message, excType: data.exc_type || "" };
	}

	// ── Sahifa ──────────────────────────────────────────────────────────

	async eval(expression) {
		const r = await this.cdp.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
		if (r.exceptionDetails) {
			const d = r.exceptionDetails;
			throw new Error("eval: " + ((d.exception && d.exception.description) || d.text) + "\n  in: " + expression.slice(0, 240));
		}
		return r.result.value;
	}

	async waitFor(expression, timeout = 10000, label = "") {
		const t0 = Date.now();
		for (;;) {
			let value;
			try {
				value = await this.eval(`(() => { try { return !!(${expression}); } catch (e) { return false; } })()`);
			} catch (e) {
				value = false;
			}
			if (value) return true;
			if (Date.now() - t0 > timeout) throw new Error(`waitFor timeout (${timeout}ms): ${label || expression.slice(0, 200)}`);
			await sleep(100);
		}
	}

	async goto(url) {
		await this.cdp.send("Page.navigate", { url });
		await sleep(600);
		await this.waitFor(`document.readyState === "complete"`, 30000, "readyState");
	}

	async login() {
		await this.goto(`${BASE}/login`);
		const r = await this.eval(
			`fetch("/api/method/login", {method:"POST", headers:{"Content-Type":"application/x-www-form-urlencoded"}, body:new URLSearchParams({usr:${JSON.stringify(
				this.env.E2E_USER
			)}, pwd:${JSON.stringify(this.env.E2E_PASSWORD)}})}).then(r => r.status)`
		);
		if (r !== 200) throw new Error("login xatosi: " + r);
	}

	async openCashier() {
		await this.goto(`${BASE}/app/restaurant-cashier`);
		await this.waitFor(`window.ozturk && ozturk.cashier && ozturk.cashier.screen && ozturk.cashier.screen.state !== "loading"`, 40000, "kassa ekrani yuklandi");
		// Qurilma sozlamasi (localStorage) oxirgi ko'rinishni eslab qoladi: senariylar bir-biriga ta'sir qilmasin.
		await this.eval(`(() => { const s = ozturk.cashier.screen; if (s.state === "ready" && s.view !== "floor") s.setView("floor"); })()`);
		await sleep(400);
	}

	/** Esc bosib barcha kit oynalari va modalni yopadi (matn maydonidagi birinchi Esc faqat fokusni oladi). */
	async escapeAll(max = 6) {
		for (let i = 0; i < max; i++) {
			const open = await this.eval(`document.querySelectorAll(".rc-dialog").length + (document.querySelector(".rc-overlay") && !document.querySelector(".rc-overlay").hidden ? 1 : 0)`);
			if (!open) return;
			await this.key("Escape");
		}
	}

	async setViewport(width, height) {
		await this.cdp.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
		await sleep(500);
	}

	state() {
		return this.eval(`(() => { const s = ozturk.cashier.screen; return {state: s.state, view: s.view, table: s.selectedTable, invoice: s.selectedInvoice}; })()`);
	}

	// ── Elementlar ─────────────────────────────────────────────────────

	/**
	 * Ko'rinadigan elementni topadi: `selector` + ixtiyoriy matn (qism yoki RegExp manbasi),
	 * `scope` — qidiruv ildizi (CSS). Markaz nuqtasini va u AYNAN shu elementni bosishini qaytaradi.
	 */
	locate({ selector, text = null, scope = null, index = 0, regex = false, last = false, exact = false }) {
		return this.eval(`(() => {
			const roots = ${scope ? `[...document.querySelectorAll(${JSON.stringify(scope)})]` : "[document]"};
			const root = ${last ? "roots[roots.length - 1]" : "roots[0]"};
			if (!root) return {found: false, why: "scope yo'q: ${scope || ""}"};
			const norm = (s) => (s || "").replace(/\\s+/g, " ").trim();
			const want = ${JSON.stringify(text)};
			const re = ${regex} && want !== null ? new RegExp(want, "i") : null;
			const all = [...root.querySelectorAll(${JSON.stringify(selector)})].filter((el) => {
				const r = el.getBoundingClientRect();
				if (!(r.width > 0 && r.height > 0)) return false;
				if (want === null) return true;
				// innerText CSS text-transform: uppercase ni hisobga oladi - solishtirish harf registrisiz.
				const t = norm(el.innerText || el.textContent || el.value || el.getAttribute("aria-label"));
				return re ? re.test(t) : ${exact} ? t.toLowerCase() === want.toLowerCase() : t.toLowerCase().includes(want.toLowerCase());
			});
			const el = all[${index}];
			if (!el) return {found: false, why: "topilmadi", count: all.length};
			el.scrollIntoView({block: "center", inline: "center"});
			const r = el.getBoundingClientRect();
			const x = r.x + r.width / 2, y = r.y + r.height / 2;
			const top = document.elementFromPoint(x, y);
			const hit = !!top && (top === el || el.contains(top));
			return {found: true, x, y, w: r.width, h: r.height, hit, disabled: !!el.disabled, cover: hit ? "" : (top ? top.tagName + "." + top.className : "null"), text: norm(el.innerText).slice(0, 60)};
		})()`);
	}

	async click(selector, text = null, opts = {}) {
		const pos = await this.locate({ selector, text, ...opts });
		if (!pos.found) throw new Error(`click: ${selector} ${text === null ? "" : JSON.stringify(text)} ${pos.why} (${pos.count || 0})`);
		if (pos.disabled && !opts.allowDisabled) throw new Error(`click: o'chirilgan tugma ${selector} ${JSON.stringify(text)}`);
		if (!pos.hit && !opts.allowCovered) throw new Error(`click: ${selector} ${JSON.stringify(text)} ustini ${pos.cover} yopib turibdi`);
		await this.cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: pos.x, y: pos.y });
		await this.cdp.send("Input.dispatchMouseEvent", { type: "mousePressed", x: pos.x, y: pos.y, button: "left", clickCount: 1 });
		await this.cdp.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: pos.x, y: pos.y, button: "left", clickCount: 1 });
		await sleep(opts.settle === undefined ? 150 : opts.settle);
		return pos;
	}

	/** Eng ustki oyna (kit dialog) ichida bosish. */
	clickDialog(selector, text = null, opts = {}) {
		return this.click(selector, text, { scope: ".rc-dialog", last: true, ...opts });
	}

	/** Matn maydoniga yozadi (fokus + tanlash + `Input.insertText`). */
	async type(selector, value, { scope = null, index = 0, last = false } = {}) {
		const pos = await this.locate({ selector, scope, index, last });
		if (!pos.found) throw new Error(`type: ${selector} topilmadi ${pos.why}`);
		await this.cdp.send("Input.dispatchMouseEvent", { type: "mousePressed", x: pos.x, y: pos.y, button: "left", clickCount: 1 });
		await this.cdp.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: pos.x, y: pos.y, button: "left", clickCount: 1 });
		await this.eval(`(() => { const roots = ${scope ? `[...document.querySelectorAll(${JSON.stringify(scope)})]` : "[document]"}; const r = ${last ? "roots[roots.length-1]" : "roots[0]"}; const el = [...r.querySelectorAll(${JSON.stringify(selector)})].filter(e => e.getBoundingClientRect().width > 0)[${index}]; el.focus(); if (el.select) el.select(); })()`);
		if (value === "") {
			await this.cdp.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Delete", code: "Delete", windowsVirtualKeyCode: 46 });
			await this.cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Delete", code: "Delete", windowsVirtualKeyCode: 46 });
		} else {
			await this.cdp.send("Input.insertText", { text: String(value) });
		}
		await sleep(120);
	}

	/** Kit forma maydoni: yorlig'i (qism, registrsiz) bo'yicha `.rc-field` ichidagi input/textarea. */
	async typeField(label, value, opts = {}) {
		const found = await this.eval(`(() => {
			document.querySelectorAll("[data-e2e-target]").forEach((el) => el.removeAttribute("data-e2e-target"));
			const dialogs = [...document.querySelectorAll(".rc-dialog")];
			const d = dialogs[dialogs.length - 1] || document;
			const f = [...d.querySelectorAll(".rc-field")].find((el) => {
				const l = el.querySelector(".rc-field__label");
				return l && l.textContent.toLowerCase().includes(${JSON.stringify(label.toLowerCase())});
			});
			if (!f) return false;
			f.setAttribute("data-e2e-target", "1");
			return true;
		})()`);
		if (!found) throw new Error(`typeField: '${label}' maydoni yo'q`);
		await this.type(`[data-e2e-target] input, [data-e2e-target] textarea`, value, { scope: ".rc-dialog", last: true, ...opts });
		await this.eval(`document.querySelectorAll("[data-e2e-target]").forEach((el) => el.removeAttribute("data-e2e-target"))`);
	}

	async key(key, { code = key, vk = 0 } = {}) {
		const codes = { Escape: 27, Enter: 13, F9: 120, F2: 113, F3: 114, F4: 115, F5: 116, F6: 117, F7: 118, F8: 119, Backspace: 8, Tab: 9 };
		const windowsVirtualKeyCode = vk || codes[key] || 0;
		await this.cdp.send("Input.dispatchKeyEvent", { type: "keyDown", key, code, windowsVirtualKeyCode });
		await this.cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key, code, windowsVirtualKeyCode });
		await sleep(150);
	}

	text(selector, { last = false } = {}) {
		return this.eval(`(() => { const els = [...document.querySelectorAll(${JSON.stringify(selector)})]; const el = ${last ? "els[els.length-1]" : "els[0]"}; return el ? el.innerText.replace(/\\s+/g, " ").trim() : null; })()`);
	}

	allText(selector) {
		return this.eval(`[...document.querySelectorAll(${JSON.stringify(selector)})].map(e => e.innerText.replace(/\\s+/g, " ").trim())`);
	}

	dialogText() {
		return this.text(".rc-dialog", { last: true });
	}

	async dialogOpen() {
		return this.eval(`document.querySelectorAll(".rc-dialog").length`);
	}

	async waitDialog(match, timeout = 8000) {
		await this.waitFor(`(() => { const d = [...document.querySelectorAll(".rc-dialog")].pop(); return d && d.innerText.toLowerCase().includes(${JSON.stringify(match.toLowerCase())}); })()`, timeout, `oyna: ${match}`);
	}

	async waitNoDialog(timeout = 8000) {
		await this.waitFor(`document.querySelectorAll(".rc-dialog").length === 0`, timeout, "oyna yopilishi");
	}

	/** `except` (masalan buyurtma oynasi) dan tashqari HECH QANDAY kit oynasi qolmaguncha kutadi. */
	async waitNoDialogTop(except = ".rc-orders-dlg", timeout = 8000) {
		await this.waitFor(`!document.querySelector(".rc-dialog:not(${except})")`, timeout, "ustki oyna yopilishi");
	}

	/** Hech qanday kit oynasi qolmadi (buyurtma oynasi ham). */
	async waitNoDialogAny(timeout = 10000) {
		await this.waitFor(`document.querySelectorAll(".rc-dialog").length === 0`, timeout, "barcha oynalar yopildi");
	}

	async waitDialogCount(n, timeout = 10000) {
		await this.waitFor(`document.querySelectorAll(".rc-dialog").length === ${n}`, timeout, `${n} ta oyna qoldi`);
	}

	async waitIdle(ms = 300) {
		// so'rovlar navbati tugashini va UI yangilanishini kutadi
		await this.queue;
		await sleep(ms);
		await this.queue;
	}

	// ── Sinov hisobi ────────────────────────────────────────────────────

	check(label, ok, detail = "") {
		this.checks.push({ scenario: this.scenario, step: this.stepName, label, ok: !!ok, detail: String(detail) });
		if (!ok) console.log(`      XATO: ${label} ${detail}`);
		return !!ok;
	}

	eq(actual, expected, label) {
		const ok = JSON.stringify(actual) === JSON.stringify(expected);
		return this.check(label, ok, ok ? "" : `kutilgan=${JSON.stringify(expected)} haqiqiy=${JSON.stringify(actual)}`);
	}

	near(actual, expected, label, tolerance = 0.5) {
		const ok = Math.abs(Number(actual) - Number(expected)) <= tolerance;
		return this.check(label, ok, ok ? "" : `kutilgan=${expected} haqiqiy=${actual}`);
	}

	async shot(name) {
		try {
			const r = await this.cdp.send("Page.captureScreenshot", { format: "png" });
			const file = path.join(this.shotDir, `${name.replace(/[^\w.-]+/g, "_")}.png`);
			fs.writeFileSync(file, Buffer.from(r.data, "base64"));
			return file;
		} catch (e) {
			return "";
		}
	}

	async step(name, fn) {
		this.stepName = name;
		await this.op("step", { name: `${this.scenario} / ${name}` });
		const errBefore = this.consoleErrors.length;
		const failBefore = this.failedRequests.length;
		const checkBefore = this.checks.length;
		const t0 = Date.now();
		let error = null;
		try {
			await fn();
			await this.waitIdle(150);
		} catch (e) {
			error = e;
		}
		// Kutilgan xato javob (h.expect) Chrome konsoliga "Failed to load resource" va Frappe'ning
		// server traceback nusxasini yozadi — bu kutilgan shovqin.
		const expectedHere = this.expected.some((e) => e.scenario === this.scenario && e.step === name);
		const newErrors = this.consoleErrors.slice(errBefore).filter(
			(e) => !(expectedHere && /Failed to load resource|Traceback|frappe\.api|exc_type|ApprovalRequired|ValidationError/.test(e.text))
		);
		const newFailed = this.failedRequests.slice(failBefore);
		const failedChecks = this.checks.slice(checkBefore).filter((c) => !c.ok);
		const ok = !error && !newErrors.length && !newFailed.length && !failedChecks.length;
		const record = {
			scenario: this.scenario,
			step: name,
			ok,
			ms: Date.now() - t0,
			error: error ? String(error.message || error).slice(0, 600) : "",
			console: newErrors,
			failed: newFailed,
			failedChecks,
		};
		this.results.push(record);
		console.log(`  ${ok ? "OK  " : "FAIL"} ${this.scenario} / ${name} (${record.ms}ms)`);
		if (!ok) {
			if (error) console.log("      " + record.error.split("\n")[0]);
			newErrors.forEach((e) => console.log(`      konsol: ${e.kind} ${e.text.slice(0, 200)}`));
			newFailed.forEach((e) => console.log(`      tarmoq: ${e.what}`));
			record.shot = await this.shot(`${this.scenario}_${name}`);
		}
		if (error) {
			const fatal = new Error(record.error);
			fatal.stepFailed = true;
			throw fatal;
		}
		return record;
	}
}

module.exports = { Harness, BASE, API_PREFIX, sleep };
