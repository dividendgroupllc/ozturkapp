/**
 * Oshxona KDS — sahifa boshqaruvchisi (TZ §3, §29, §30, §32).
 *
 * ═══════════════════════════════════════════════════════════════
 *  BU FAYLDA BIZNES MANTIQ YO'Q
 * ═══════════════════════════════════════════════════════════════
 *  - Holat o'tishlari (Pending -> Preparing -> Ready -> Served)
 *    FAQAT serverda tekshiriladi (`utils/kitchen_status.py`).
 *  - Bu yerdagi tugmalar server bergan `next` maydonidan quriladi —
 *    JS o'zi keyingi holatni HISOBLAMAYDI.
 *  - Xato bo'lsa UI JIM YANGILANMAYDI: xabar ko'rsatiladi va
 *    o'sha KOT serverdan qayta o'qiladi (TZ §31).
 *
 * ═══════════════════════════════════════════════════════════════
 *  YANGILANISH (TZ §32)
 * ═══════════════════════════════════════════════════════════════
 *  Butun baza so'ralmaydi. Realtime signali kelganda faqat
 *  ta'sirlangan KOT qayta o'qiladi; ro'yxat esa yangi KOT
 *  paydo bo'lgandagina to'liq yangilanadi.
 */

frappe.provide("ozturk.kitchen");

frappe.pages["restaurant-kitchen"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Oshxona KDS"),
		single_column: true,
	});

	wrapper.kitchen = new ozturk.kitchen.Screen(page);
};

frappe.pages["restaurant-kitchen"].on_page_show = function (wrapper) {
	wrapper.kitchen && wrapper.kitchen.resume();
};

ozturk.kitchen.Screen = class KitchenScreen {
	static REFRESH_DEBOUNCE = 250;

	/** Shu daqiqadan oshsa chipta "kechikkan" deb belgilanadi. */
	static LATE_MINUTES = 15;

	constructor(page) {
		this.page = page;
		this.ctx = null;
		this.kots = [];

		this.station = null;
		this.statusFilter = "ALL";

		this.subscriptions = [];
		this.timers = [];
		this.refreshTimer = null;
		this.reloadKots = new Set();
		this.destroyed = false;

		this.mount();
		this.boot();
	}

	// ═══════════════════════════════════════════════════════════
	//  Karkas
	// ═══════════════════════════════════════════════════════════

	mount() {
		$(this.page.main).html(frappe.render_template("restaurant_kitchen", {}));
		this.$root = $(this.page.main).find(".kds-root");

		this.el = {
			branch: this.$root.find(".kds-branch")[0],
			stations: this.$root.find(".kds-stations")[0],
			user: this.$root.find(".kds-user")[0],
			clock: this.$root.find(".kds-clock")[0],
			live: this.$root.find(".kds-live")[0],
			filters: this.$root.find(".kds-filters")[0],
			board: this.$root.find(".kds-board")[0],
			empty: this.$root.find(".kds-empty")[0],
			bootError: this.$root.find(".kds-boot__message")[0],
		};

		this.$root.on("click", ".kds-retry", () => this.boot());
		this.page.set_primary_action(__("Yangilash"), () => this.loadKots(), "refresh");

		// Soat va o'tgan vaqt hisoblagichlari — bazaga so'rov YUBORILMAYDI,
		// vaqt mijozda sanaladi (TZ §20, §32).
		this.timers.push(setInterval(() => this.tickClock(), 1000));
		this.timers.push(setInterval(() => this.tickElapsed(), 1000));
	}

	setState(state) {
		this.$root.attr("data-state", state);
	}

	// ═══════════════════════════════════════════════════════════
	//  Yuklash
	// ═══════════════════════════════════════════════════════════

	async boot() {
		this.setState("loading");
		try {
			this.ctx = await this.call(
				"ozturkapp.ozturkapp.api.kitchen.get_kitchen_context"
			);

			const preferred = this.readPreference("station");
			const stations = this.ctx.stations || [];
			this.station =
				(stations.find((s) => s.name === preferred) || {}).name || null;

			this.el.branch.textContent = this.ctx.branch || "";
			this.el.user.textContent = this.ctx.full_name || this.ctx.user || "";

			this.renderStations();
			this.renderFilters();
			this.subscribe();
			this.tickClock();

			await this.loadKots();
			this.setState("ready");
		} catch (error) {
			this.el.bootError.textContent = this.errorText(error);
			this.setState("error");
		}
	}

	async loadKots() {
		this.kots = await this.call("ozturkapp.ozturkapp.api.kitchen.get_active_kots", {
			station: this.station,
		});
		this.renderFilters();
		this.renderBoard();
	}

	/** Bitta KOT ni qayta o'qib, faqat o'sha kartani almashtiradi (TZ §32). */
	async reloadKot(kotName) {
		let fresh = null;
		try {
			fresh = await this.call("ozturkapp.ozturkapp.api.kitchen.get_kot", {
				kot: kotName,
			});
		} catch (error) {
			// KOT o'chirilgan/bekor qilingan bo'lishi mumkin — ro'yxatdan olamiz.
			fresh = null;
		}

		const index = this.kots.findIndex((k) => k.kot === kotName);

		if (!fresh) {
			if (index >= 0) this.kots.splice(index, 1);
			this.renderFilters();
			this.renderBoard();
			return;
		}

		if (index >= 0) this.kots[index] = fresh;
		else this.kots.push(fresh);

		this.renderFilters();
		this.renderBoard();
	}

	// ═══════════════════════════════════════════════════════════
	//  Realtime (TZ §12, §13)
	// ═══════════════════════════════════════════════════════════

	subscribe() {
		const events = this.ctx.events || {};

		// Yangi KOT -> ro'yxatni to'liq yangilaymiz (yangi karta qo'shiladi).
		this.listen(events.kot, (data) => {
			if (!this.isOurBranch(data)) return;
			if (!this.isOurStation(data)) return;
			this.scheduleRefresh({ full: true });
		});

		// Mahsulot holati -> faqat o'sha KOT ni qayta o'qiymiz.
		this.listen(events.item, (data) => {
			if (!this.isOurBranch(data)) return;
			if (!this.isOurStation(data)) return;
			this.scheduleRefresh({ kot: data.kot });
		});

		// Yangi buyurtma -> OSHPAZGA ko'rinadigan xabar.
		this.listen(events.notify, (data) => this.showNotification(data, "kitchen"));

		this.updateLiveIndicator();
		this.timers.push(setInterval(() => this.updateLiveIndicator(), 5000));
	}

	/**
	 * KO'RINADIGAN bildirishnoma — banner + ovoz.
	 *
	 * NEGA JIM YANGILASH YETMAYDI
	 * ===========================
	 * Qolgan realtime hodisalari ekranni jim yangilaydi. Xodim aynan
	 * o'sha joyga qarab turmasa o'zgarishni SEZMAYDI — hisob so'rovi ham,
	 * yangi buyurtma ham e'tibordan chetda qolardi.
	 *
	 * Ovoz `try` ichida: brauzer foydalanuvchi sahifaga tegmaguncha
	 * audio'ni bloklaydi, va bu holda banner baribir ko'rinishi kerak.
	 */
	showNotification(data, audience) {
		if (!this.isOurBranch(data)) return;
		if (!data || data.audience !== audience) return;

		frappe.show_alert(
			{
				message: `<b>${esc(data.title || "")}</b><br>${esc(data.body || "")}`,
				indicator: data.kind === "BILL_REQUESTED" ? "orange" : "green",
			},
			15
		);
		this.beep();
	}

	beep() {
		try {
			const Ctx = window.AudioContext || window.webkitAudioContext;
			if (!Ctx) return;
			this.audio = this.audio || new Ctx();
			const osc = this.audio.createOscillator();
			const gain = this.audio.createGain();
			osc.connect(gain);
			gain.connect(this.audio.destination);
			osc.frequency.value = 880;
			gain.gain.setValueAtTime(0.15, this.audio.currentTime);
			gain.gain.exponentialRampToValueAtTime(0.001, this.audio.currentTime + 0.35);
			osc.start();
			osc.stop(this.audio.currentTime + 0.35);
		} catch (e) {
			// Ovoz ishlamasa ham banner ko'rinadi — to'xtatmaymiz.
		}
	}

	listen(event, handler) {
		if (!event) return;
		frappe.realtime.on(event, handler);
		this.subscriptions.push([event, handler]);
	}

	isOurBranch(data) {
		return !(data && data.branch && data.branch !== this.ctx.branch);
	}

	isOurStation(data) {
		if (!this.station) return true; // "Barcha stansiyalar"
		if (!data || !data.station) return true; // qamrov noma'lum
		return data.station === this.station;
	}

	scheduleRefresh(scope = {}) {
		if (scope.full) this.pendingFull = true;
		if (scope.kot) this.reloadKots.add(scope.kot);

		clearTimeout(this.refreshTimer);
		this.refreshTimer = setTimeout(async () => {
			if (this.destroyed) return;

			const full = this.pendingFull;
			const kots = Array.from(this.reloadKots);
			this.pendingFull = false;
			this.reloadKots.clear();

			if (full) await this.loadKots();
			else for (const kot of kots) await this.reloadKot(kot);
		}, KitchenScreen.REFRESH_DEBOUNCE);
	}

	updateLiveIndicator() {
		const connected = !!(frappe.realtime.socket && frappe.realtime.socket.connected);
		this.el.live.classList.toggle("kds-live--down", !connected);
		this.el.live.querySelector(".kds-live__text").textContent = connected
			? __("Jonli")
			: __("Ulanish yo'q");
	}

	resume() {
		if (this.ctx) this.scheduleRefresh({ full: true });
	}

	destroy() {
		this.destroyed = true;
		this.subscriptions.forEach(([e, h]) => frappe.realtime.off(e, h));
		this.subscriptions = [];
		this.timers.forEach(clearInterval);
		this.timers = [];
		clearTimeout(this.refreshTimer);
	}

	// ═══════════════════════════════════════════════════════════
	//  Filtrlar
	// ═══════════════════════════════════════════════════════════

	renderStations() {
		const stations = this.ctx.stations || [];
		if (!stations.length) {
			this.el.stations.innerHTML = "";
			return;
		}

		this.el.stations.innerHTML =
			`<button class="kds-station" type="button" data-station=""
				aria-pressed="${this.station === null}">${esc(
				__("Barcha stansiyalar")
			)}</button>` +
			stations
				.map(
					(s) =>
						`<button class="kds-station" type="button" data-station="${esc(
							s.name
						)}" aria-pressed="${s.name === this.station}">${esc(
							s.production || s.name
						)}</button>`
				)
				.join("");

		$(this.el.stations)
			.off("click")
			.on("click", ".kds-station", (e) => {
				this.station = e.currentTarget.dataset.station || null;
				this.writePreference("station", this.station || "");
				this.renderStations();
				this.loadKots();
			});
	}

	renderFilters() {
		const counts = { ALL: this.kots.length };
		(this.ctx.statuses || []).forEach((s) => {
			counts[s.key] = this.kots.filter((k) => k.status === s.key).length;
		});

		const options = [{ key: "ALL", label: __("Hammasi"), mark: "▣" }].concat(
			(this.ctx.statuses || []).map((s) => ({
				key: s.key,
				label: s.label,
				mark: { Pending: "○", Preparing: "◐", Ready: "●" }[s.key] || "•",
			}))
		);

		this.el.filters.innerHTML = options
			.map(
				(o) =>
					`<button class="kds-filter kds-filter--${esc(o.key)}" type="button"
						data-filter="${esc(o.key)}" aria-pressed="${this.statusFilter === o.key}">
						<span aria-hidden="true">${o.mark}</span>
						<span>${esc(o.label)}</span>
						<span class="kds-filter__count">${cint(counts[o.key])}</span>
					</button>`
			)
			.join("");

		$(this.el.filters)
			.off("click")
			.on("click", ".kds-filter", (e) => {
				this.statusFilter = e.currentTarget.dataset.filter;
				this.renderFilters();
				this.renderBoard();
			});
	}

	// ═══════════════════════════════════════════════════════════
	//  Doska
	// ═══════════════════════════════════════════════════════════

	visibleKots() {
		if (this.statusFilter === "ALL") return this.kots;
		return this.kots.filter((k) => k.status === this.statusFilter);
	}

	renderBoard() {
		const kots = this.visibleKots();
		this.el.empty.hidden = kots.length > 0;
		this.el.board.innerHTML = kots.map((k) => this.ticketHtml(k)).join("");

		$(this.el.board)
			.off("click")
			.on("click", "[data-next]", (e) => this.advance(e.currentTarget));

		this.tickElapsed();
	}

	ticketHtml(kot) {
		const late =
			kot.elapsed_seconds > KitchenScreen.LATE_MINUTES * 60 &&
			kot.status !== "Served";

		const head = `
			<div class="kds-ticket__head">
				<div class="kds-ticket__top">
					<span class="kds-ticket__table">${esc(
						kot.table || (kot.takeaway ? __("Saboy") : __("Buyurtma"))
					)}</span>
					<span class="kds-ticket__kot">${esc(kot.kot)}</span>
				</div>
				<div class="kds-ticket__meta">
					${kot.order_no ? `<span>#${esc(kot.order_no)}</span>` : ""}
					${kot.waiter ? `<span>${esc(kot.waiter)}</span>` : ""}
					${kot.station ? `<span>${esc(kot.station)}</span>` : ""}
					<span class="kds-elapsed ${late ? "kds-elapsed--late" : ""}"
						data-since="${cint(kot.elapsed_seconds)}">${esc(
			fmtDuration(kot.elapsed_seconds)
		)}</span>
				</div>
			</div>`;

		// Bekor qilish KOT'i — bu ovqat buyurtmasi EMAS (TZ §9).
		if (kot.is_cancellation) {
			return `<article class="kds-ticket kds-ticket--Cancelled">
				${head}
				<div class="kds-cancel-banner">${esc(
					kot.type === "Cancelled"
						? __("Buyurtma bekor qilindi")
						: __("Qisman bekor qilindi")
				)}</div>
				<div class="kds-items">
					${kot.items
						.map(
							(it) => `<div class="kds-item">
								<div class="kds-item__top">
									<span class="kds-item__name">${esc(it.item_name)}</span>
									<span class="kds-item__qty">×${cint(
										it.cancelled_qty || it.qty
									)}</span>
								</div>
								${
									it.comments
										? `<div class="kds-item__comment">${esc(it.comments)}</div>`
										: ""
								}
							</div>`
						)
						.join("")}
				</div>
			</article>`;
		}

		return `<article class="kds-ticket kds-ticket--${esc(kot.status)} ${
			late ? "kds-ticket--late" : ""
		}" data-kot="${esc(kot.kot)}">
			${head}
			${kot.comments ? `<div class="kds-ticket__note">${esc(kot.comments)}</div>` : ""}
			<div class="kds-items">
				${kot.items.map((it) => this.itemHtml(kot, it)).join("")}
			</div>
		</article>`;
	}

	itemHtml(kot, item) {
		// Tugma server bergan `next` dan quriladi — JS keyingi holatni
		// O'ZI hisoblamaydi (TZ §23).
		const next = item.next;
		const btnClass = {
			Preparing: "kds-btn--start",
			Ready: "kds-btn--ready",
			Served: "kds-btn--serve",
		}[next && next.status] || "kds-btn--primary";

		return `<div class="kds-item" data-item="${esc(item.id)}">
			<div class="kds-item__top">
				<span class="kds-item__name">${esc(item.item_name)}</span>
				<span class="kds-item__qty">×${cint(item.qty)}</span>
			</div>
			${
				item.course && item.indicate_course
					? `<div class="kds-item__course">${esc(item.course)}</div>`
					: ""
			}
			${
				item.comments
					? `<div class="kds-item__comment">⚠ ${esc(item.comments)}</div>`
					: ""
			}
			<div class="kds-item__foot">
				<span class="kds-badge kds-badge--${esc(item.status)}">${esc(
			item.status_label
		)}</span>
				${
					item.preparing_seconds !== null && item.preparing_seconds !== undefined
						? `<span class="kds-item__timer" data-since="${cint(
								item.preparing_seconds
						  )}">${esc(fmtDuration(item.preparing_seconds))}</span>`
						: ""
				}
			</div>
			${
				next
					? `<div class="kds-item__action">
						<button class="kds-btn ${btnClass}" type="button"
							data-next="${esc(next.status)}" data-item="${esc(item.id)}"
							data-kot="${esc(kot.kot)}">${esc(next.label)}</button>
					</div>`
					: ""
			}
			<div class="kds-item__error" hidden></div>
		</div>`;
	}

	// ═══════════════════════════════════════════════════════════
	//  Holat o'zgartirish
	// ═══════════════════════════════════════════════════════════

	async advance(button) {
		const { item, next, kot } = button.dataset;
		const $error = $(button).closest(".kds-item").find(".kds-item__error");

		$error.attr("hidden", true).text("");
		button.classList.add("kds-btn--busy");
		button.disabled = true;

		try {
			await this.call("ozturkapp.ozturkapp.api.kitchen.update_kot_item_status", {
				kot_item: item,
				status: next,
			});
			// Muvaffaqiyat: server holatini o'qib olamiz (mahalliy taxmin emas).
			await this.reloadKot(kot);
		} catch (error) {
			// Jim yangilanmaydi — xabar ko'rsatiladi va KOT qayta o'qiladi (TZ §31).
			$error
				.text(
					__("Holatni o'zgartirib bo'lmadi. Buyurtma o'zgargan bo'lishi mumkin.") +
						"\n" +
						this.errorText(error)
				)
				.removeAttr("hidden");
			button.classList.remove("kds-btn--busy");
			button.disabled = false;
			await this.reloadKot(kot);
		}
	}

	// ═══════════════════════════════════════════════════════════
	//  Vaqt
	// ═══════════════════════════════════════════════════════════

	tickClock() {
		if (!this.el.clock) return;
		this.el.clock.textContent = frappe.datetime.str_to_user(
			frappe.datetime.now_datetime()
		);
	}

	/** O'tgan vaqtni mijozda sanaymiz — serverga so'rov yubormaymiz (TZ §20). */
	tickElapsed() {
		$(this.el.board)
			.find("[data-since]")
			.each((_, node) => {
				const seconds = cint(node.dataset.since) + 1;
				node.dataset.since = seconds;
				node.textContent = fmtDuration(seconds);

				if (node.classList.contains("kds-elapsed")) {
					node.classList.toggle(
						"kds-elapsed--late",
						seconds > KitchenScreen.LATE_MINUTES * 60
					);
				}
			});
	}

	// ═══════════════════════════════════════════════════════════
	//  Yordamchilar
	// ═══════════════════════════════════════════════════════════

	call(method, args) {
		// `silent: true` — ERPNext'ning O'Z msgprint oynasi CHIQMAYDI
		// (`frappe/public/js/frappe/request.js:459`). Xatoni sahifaning
		// o'zi joyida, tushunarli qilib ko'rsatadi.
		return frappe
			.call({ method, args: args || {}, freeze: false, silent: true })
			.then((r) => r.message);
	}

	/**
	 * Serverdan kelgan xatoni O'QILADIGAN matnga aylantiradi.
	 *
	 * `frappe.call` promise'ni **jqXHR** bilan rad etadi, ya'ni xabar
	 * `error.responseJSON._server_messages` ichida turadi — to'g'ridan-to'g'ri
	 * `error._server_messages` da EMAS. Buni hisobga olmasak `String(error)`
	 * ishlab ketadi va ekranda `[object Object]` chiqadi (ko'rilgan xato).
	 */
	errorText(error) {
		if (!error) return __("Noma'lum xato");
		if (typeof error === "string") return error;

		const body = error.responseJSON || error;

		// 1) `frappe.throw()` xabarlari — eng tushunarlisi shu.
		const raw = body._server_messages;
		if (raw) {
			try {
				const list = typeof raw === "string" ? JSON.parse(raw) : raw;
				const messages = (Array.isArray(list) ? list : [list])
					.map((m) => {
						try {
							const parsed = typeof m === "string" ? JSON.parse(m) : m;
							return parsed && parsed.message ? parsed.message : m;
						} catch (e) {
							return m;
						}
					})
					.filter((m) => typeof m === "string" && m.trim())
					.map((m) => $("<div>").html(m).text().trim());
				if (messages.length) return messages.join("\n");
			} catch (e) {
				/* keyingi manbaga o'tamiz */
			}
		}

		// 2) Oddiy matnli maydonlar.
		for (const key of ["message", "exception", "exc_type", "statusText"]) {
			const value = body[key];
			if (typeof value === "string" && value.trim()) {
				return $("<div>").html(value.replace(/^[\w.]+Error:\s*/, "")).text().trim();
			}
		}

		return __("Noma'lum xato");
	}

	readPreference(key) {
		try {
			return localStorage.getItem(`ozturk_kitchen_${key}`);
		} catch (e) {
			return null;
		}
	}

	writePreference(key, value) {
		try {
			localStorage.setItem(`ozturk_kitchen_${key}`, value);
		} catch (e) {
			/* localStorage o'chirilgan bo'lishi mumkin */
		}
	}
};

// ═══════════════════════════════════════════════════════════════
//  Sof yordamchi funksiyalar
// ═══════════════════════════════════════════════════════════════

function esc(value) {
	if (value === null || value === undefined) return "";
	return String(value).replace(
		/[&<>"']/g,
		(ch) =>
			({
				"&": "&amp;",
				"<": "&lt;",
				">": "&gt;",
				'"': "&quot;",
				"'": "&#39;",
			}[ch])
	);
}

/** Soniyani `MM:SS` yoki `H:MM:SS` ko'rinishiga keltiradi. */
function fmtDuration(seconds) {
	const total = Math.max(0, cint(seconds));
	const h = Math.floor(total / 3600);
	const m = Math.floor((total % 3600) / 60);
	const s = total % 60;
	const pad = (n) => String(n).padStart(2, "0");
	return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}
