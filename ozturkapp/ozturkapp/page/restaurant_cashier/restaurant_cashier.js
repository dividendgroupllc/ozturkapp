/**
 * Kassa oynasi — sahifa boshqaruvchisi (TZ §26).
 *
 * ═══════════════════════════════════════════════════════════════
 *  BU FAYLDA BIZNES MANTIQ YO'Q
 * ═══════════════════════════════════════════════════════════════
 *  - Stol holati (AVAILABLE / RESERVED / OCCUPIED) — serverda hisoblanadi.
 *  - Oraliq summa, xizmat haqi, jami — serverdan TAYYOR holda keladi.
 *  - Ruxsatlar serverda tekshiriladi; bu yerdagi `disabled` faqat qulaylik.
 *
 *  Foiz (12%) bu faylda UMUMAN yo'q va bo'lmasligi ham kerak (TZ §8).
 *
 * ═══════════════════════════════════════════════════════════════
 *  YANGILANISH STRATEGIYASI (TZ §13, §25)
 * ═══════════════════════════════════════════════════════════════
 *  Sahifa ochilganda bir marta to'liq yuklanadi. Keyin realtime
 *  signali kelganda faqat kerakli qism qayta so'raladi. Qo'lda
 *  yangilash (F5) talab qilinmaydi.
 *
 *  O'z hodisalarimizdan tashqari URY va Desktop POS ning MAVJUD
 *  hodisalariga ham obuna bo'lamiz — yangisini yozmaymiz.
 */

frappe.provide("ozturk.cashier");

frappe.pages["restaurant-cashier"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Restoran kassasi"),
		single_column: true,
	});

	wrapper.cashier = new ozturk.cashier.Screen(page);
};

frappe.pages["restaurant-cashier"].on_page_show = function (wrapper) {
	wrapper.cashier && wrapper.cashier.resume();
};

frappe.pages["restaurant-cashier"].on_page_hide = function (wrapper) {
	wrapper.cashier && wrapper.cashier.suspend();
};

/**
 * Qurilma belgisi.
 *
 * Desk — bitta sahifali ilova (SPA): F5 bosilmasa eski skript xotirada
 * qolib ketadi va yangi kod umuman ishga tushmaydi. Bu belgi yuqori
 * panelda ko'rinadi — quyidagi qiymat bilan mos kelmasa, brauzer ESKI
 * kodni ishlatmoqda va sahifani to'liq yangilash kerak.
 */
ozturk.cashier.BUILD = "2026-08-19.1";

ozturk.cashier.Screen = class CashierScreen {
	/** Realtime signaldan keyin qayta so'rovni birlashtirish oynasi (ms). */
	static REFRESH_DEBOUNCE = 250;

	/** URY va Desktop POS ning mavjud hodisalari — qayta ishlatamiz. */
	static LEGACY_EVENTS = [
		"reload_ro",
		"pos_invoice_updated",
		"table_freed",
		"pending_order_cancelled",
	];

	constructor(page) {
		this.page = page;
		this.ctx = null;
		this.floor = null;

		this.room = null;
		this.statusFilter = "ALL";
		this.orderFilter = "all";
		this.selectedTable = null;

		this.selectedInvoice = null;

		this.subscriptions = [];
		this.timers = [];
		this.refreshTimer = null;
		this.pendingScope = null;
		this.destroyed = false;

		this.mount();
		this.boot();
	}

	// ═══════════════════════════════════════════════════════════
	//  Karkas
	// ═══════════════════════════════════════════════════════════

	mount() {
		$(this.page.main).html(frappe.render_template("restaurant_cashier", {}));
		this.$root = $(this.page.main).find(".rc-root");

		this.el = {
			restaurant: this.$root.find(".rc-restaurant")[0],
			branch: this.$root.find(".rc-branch")[0],
			rooms: this.$root.find(".rc-rooms")[0],
			shift: this.$root.find(".rc-shift")[0],
			cashier: this.$root.find(".rc-cashier")[0],
			clock: this.$root.find(".rc-clock")[0],
			build: this.$root.find(".rc-build")[0],
			live: this.$root.find(".rc-live")[0],
			warnings: this.$root.find(".rc-warnings")[0],
			filters: this.$root.find(".rc-filters")[0],
			canvas: this.$root.find(".rc-floor__canvas")[0],
			floorScroll: this.$root.find(".rc-floor__scroll")[0],
			floorEmpty: this.$root.find(".rc-floor__empty")[0],
			panel: this.$root.find(".rc-panel")[0],
			orderTabs: this.$root.find(".rc-orders__tabs")[0],
			orderList: this.$root.find(".rc-orders__list")[0],
			bootError: this.$root.find(".rc-boot--error .rc-boot__message")[0],
			gate: this.$root.find(".rc-gate__form")[0],
			overlay: this.$root.find(".rc-overlay")[0],
			modalBody: this.$root.find(".rc-modal__body")[0],
		};

		this.$root.on("click", ".rc-retry", () => this.boot());
		this.$root.on("click", ".rc-modal__close", () => this.closeModal());
		this.$root.on("click", ".rc-overlay", (e) => {
			if (e.target === this.el.overlay) this.closeModal();
		});

		this.page.set_primary_action(__("Yangilash"), () => this.refreshAll(), "refresh");

		this.timers.push(
			setInterval(() => this.tickClock(), 1000)
		);
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
			this.ctx = await this.call("ozturkapp.ozturkapp.api.cashier.get_cashier_context");

			// Standart holat — BARCHA ZALLAR (`room = null`).
			//
			// Kassir odatda butun restoranni bir ekranda ko'rishi kerak;
			// bitta zalga cheklash uning ishini qiyinlashtiradi. Zal
			// tanlansa, tanlov eslab qolinadi va keyingi safar o'sha
			// ochiladi — "barcha zallar" ham to'liq huquqli tanlov.
			const rooms = this.ctx.rooms || [];
			const saved = this.readPreference("room");

			// `null`  -> hech qachon tanlanmagan  -> barcha zallar
			// `""`    -> foydalanuvchi ATAYLAB "barcha zallar" ni tanlagan
			// `"..."` -> aniq zal
			this.room = saved ? saved : null;

			// Saqlangan zal o'chirilgan bo'lsa — barcha zallarga qaytamiz.
			if (this.room && !rooms.some((r) => r.name === this.room)) {
				this.room = null;
			}

			this.renderTopbar();
			this.renderWarnings();
			this.renderRooms();
			this.renderOrderTabs();
			this.subscribe();

			// ── Smena yopiq bo'lsa kim nima ko'radi ───────────────────
			//
			// KASSIR (kassani ocha oladigan) — bloklovchi ekran. Stol ham,
			// buyurtma ham ko'rsatilmaydi: sotuv smenasiz mumkin emas,
			// ya'ni uning yagona mumkin bo'lgan amali — kassani ochish.
			//
			// ADMINISTRATOR / MENEJER (ocholmaydigan) — bloklanmaydi. U
			// kuzatish uchun kiradi: zal, buyurtmalar va cheklar ko'rinadi.
			// Kassa yopiqligi yuqori paneldagi QIZIL «Kassa yopiq» yozuvida
			// turadi. Sotuv amallarini server baribir rad etadi
			// (`cashier_permissions.assert_shift_open`), shuning uchun
			// bloklovchi ekran unga faqat xalaqit berardi.
			const canOperate = !!(this.ctx.permissions || {}).can_operate_shift;

			if (!(this.ctx.shift || {}).open && canOperate) {
				this.renderShiftGate();
				this.setState("shift");
				return;
			}

			await this.refreshAll();
			this.setState("ready");
		} catch (error) {
			this.el.bootError.textContent = this.errorText(error);
			this.setState("error");
		}
	}

	/** To'liq yangilash — qo'lda "Yangilash" bosilganda va boshlanishida. */
	async refreshAll() {
		await this.refresh({ floor: true, orders: true, panel: true });
	}

	/**
	 * Tanlangan qismlarnigina qayta so'raydi (TZ §6).
	 *
	 * Realtime signali kelganda BUTUN bazani qayta o'qish shart emas:
	 * boshqa zaldagi stol o'zgarsa zal rejasi qayta chizilmaydi, faqat
	 * buyurtmalar ro'yxati yangilanadi.
	 */
	async refresh({ floor = false, orders = false, panel = false } = {}) {
		const jobs = [];
		if (floor) jobs.push(this.loadFloor());
		if (orders) jobs.push(this.loadOrders());
		await Promise.all(jobs);

		// Panelni zal rejasidan KEYIN yangilaymiz — stol yo'qolgan bo'lsa
		// `loadFloor` tanlovni bekor qilib ulguradi.
		if (panel && this.selectedTable) {
			await this.loadTableDetail(this.selectedTable);
		} else if (panel && this.selectedInvoice) {
			// Stolsiz buyurtma tanlangan — uni stol orqali topib bo'lmaydi.
			await this.loadOrderDetail(this.selectedInvoice);
		}
	}

	async loadFloor() {
		this.floor = await this.call("ozturkapp.ozturkapp.api.table.get_floor_plan", {
			room: this.room,
		});
		this.renderFilters();
		this.renderFloor();

		// Tanlangan stol ko'rinishdan chiqib ketgan bo'lsa — tanlovni bekor qilamiz.
		if (this.selectedTable) {
			const still = (this.floor.tables || []).some((t) => t.name === this.selectedTable);
			if (!still) this.clearSelection();
		}
	}

	async loadOrders() {
		this.orders = await this.call("ozturkapp.ozturkapp.api.order.get_active_orders", {
			room: this.room,
		});
		this.renderOrders();
	}

	// ═══════════════════════════════════════════════════════════
	//  Realtime (TZ §13)
	// ═══════════════════════════════════════════════════════════

	subscribe() {
		const events = this.ctx.events || {};

		// Xabar faqat identifikator tashiydi — ma'lumot API orqali QAYTA
		// so'raladi, chunki serverda ruxsat tekshiriladi (TZ §17).

		// Stol holati o'zgardi -> zal rejasi + buyurtmalar.
		this.listen(events.floor, (data) => {
			if (!this.isOurBranch(data)) return;
			if (!this.touchesVisibleTables(data)) return; // boshqa zal — e'tibor bermaymiz
			this.scheduleRefresh({
				floor: true,
				orders: true,
				panel: this.touchesSelection(data),
			});
		});

		// Buyurtma o'zgardi -> ro'yxat; zal rejasi faqat ko'rinadigan stol
		// bo'lsa; panel faqat AYNAN tanlangan stol bo'lsa.
		this.listen(events.order, (data) => {
			if (!this.isOurBranch(data)) return;
			this.scheduleRefresh({
				floor: this.touchesVisibleTables(data),
				orders: true,
				panel: this.touchesSelection(data),
			});
		});

		// Oshxona taom holatini o'zgartirdi -> chek panelidagi "🍳 ..."
		// ko'rsatkichi va buyurtmalar ro'yxatidagi teg.
		//
		// NEGA ALOHIDA KANAL KERAK
		// ========================
		// Oshpaz holatni `URY KOT Items` da o'zgartiradi, POS Invoice'ga
		// TEGMAYDI. Ya'ni `on_pos_invoice_change` ishga tushmaydi va
		// `ozturk_cashier_order` CHIQMAYDI. Bu kanalsiz kassa oshxona
		// holatini qo'lda yangilanmaguncha eski holicha ko'rsatardi.
		this.listen(events.kitchen_item, (data) => {
			if (!this.isOurBranch(data)) return;
			this.scheduleRefresh({
				// Zal rejasida oshxona holati ko'rsatilmaydi — uni bezovta qilmaymiz.
				floor: false,
				orders: true,
				panel: this.touchesSelection(data),
			});
		});

		// Ofitsant hisob so'radi -> KASSIRGA ko'rinadigan xabar.
		this.listen(events.notify, (data) => this.showNotification(data, "cashier"));

		// URY va Desktop POS ning eski hodisalari qamrovni bildirmaydi —
		// ular uchun to'liq yangilash qilamiz.
		CashierScreen.LEGACY_EVENTS.forEach((event) => {
			this.listen(event, () =>
				this.scheduleRefresh({ floor: true, orders: true, panel: true })
			);
		});

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

	/** Hodisa hozir EKRANDA turgan stollarga tegishlimi? */
	touchesVisibleTables(data) {
		const tables = (data && data.tables) || (data && data.table ? [data.table] : []);
		if (!tables.length) return true; // qamrov noma'lum — ehtiyot uchun yangilaymiz

		const visible = new Set((this.floor?.tables || []).map((t) => t.name));
		if (!visible.size) return true;

		return tables.some((name) => visible.has(name));
	}

	/** Hodisa AYNAN tanlangan stol yoki uning chekiga tegishlimi? */
	touchesSelection(data) {
		if (!data) return false;

		// Stolsiz buyurtmada (olib ketish, Desktop POS) `selectedTable`
		// bo'sh bo'ladi — tanlov faqat chek nomida yashaydi.
		if (!this.selectedTable && !this.selectedInvoice) return false;

		if (data.invoice && data.invoice === this.selectedInvoice) return true;

		if (this.selectedTable) {
			if (data.table === this.selectedTable) return true;
			if (Array.isArray(data.tables) && data.tables.includes(this.selectedTable)) {
				return true;
			}
		}

		// Qamrov ko'rsatilmagan bo'lsa — panelni ham yangilaymiz.
		return !data.table && !data.invoice && !(data.tables || []).length;
	}

	scheduleRefresh(scope = {}) {
		// Ketma-ket kelgan signallarni BITTA so'rovga birlashtiramiz va
		// qamrovlarini yig'amiz (biri panelni so'rasa — panel yangilanadi).
		this.pendingScope = {
			floor: (this.pendingScope?.floor || false) || !!scope.floor,
			orders: (this.pendingScope?.orders || false) || !!scope.orders,
			panel: (this.pendingScope?.panel || false) || !!scope.panel,
		};

		clearTimeout(this.refreshTimer);
		this.refreshTimer = setTimeout(() => {
			if (this.destroyed) return;
			const pending = this.pendingScope;
			this.pendingScope = null;
			this.refresh(pending);
		}, CashierScreen.REFRESH_DEBOUNCE);
	}

	updateLiveIndicator() {
		const connected = !!(frappe.realtime.socket && frappe.realtime.socket.connected);
		this.el.live.classList.toggle("rc-live--down", !connected);
		this.el.live.querySelector(".rc-live__text").textContent = connected
			? __("Jonli")
			: __("Ulanish yo'q");
	}

	suspend() {
		this.updateLiveIndicator();
	}

	resume() {
		// Sahifaga qaytilganda holat eskirgan bo'lishi mumkin — to'liq yangilash.
		if (this.ctx) {
			this.scheduleRefresh({ floor: true, orders: true, panel: true });
		}
	}

	destroy() {
		this.destroyed = true;
		this.subscriptions.forEach(([event, handler]) => frappe.realtime.off(event, handler));
		this.subscriptions = [];
		this.timers.forEach(clearInterval);
		this.timers = [];
		clearTimeout(this.refreshTimer);
	}

	// ═══════════════════════════════════════════════════════════
	//  Yuqori panel
	// ═══════════════════════════════════════════════════════════

	renderTopbar() {
		const restaurant = this.ctx.restaurant || {};
		this.el.restaurant.textContent = restaurant.name || "—";
		this.el.branch.textContent = this.ctx.branch || "";
		this.el.cashier.textContent = (this.ctx.cashier || {}).full_name || "";
		this.el.build.textContent = ozturk.cashier.BUILD;

		const shift = this.ctx.shift || {};
		const canOperate = !!(this.ctx.permissions || {}).can_operate_shift;

		this.el.shift.className =
			"rc-shift " + (shift.open ? "rc-shift--open" : "rc-shift--closed");
		this.el.shift.textContent = shift.open
			? __("Kassa ochiq")
			: __("Kassa yopiq");

		// Kassani ocholmaydigan foydalanuvchi (Administrator, menejer)
		// bloklovchi ekranni KO'RMAYDI — u uchun holatning yagona manbai
		// shu qizil yozuv. Kim ocha olishini ham shu yerda aytamiz, aks
		// holda u kimni chaqirishni bilmaydi.
		this.el.shift.title =
			!shift.open && !canOperate && this.ctx.shift_operators
				? __("Kassani faqat {0} ochadi.", [this.ctx.shift_operators])
				: __("Kassa smenasi");

		this.renderShiftButton(shift);

		this.tickClock();
	}

	/**
	 * Kassani yopish tugmasi — sahifaning «Yangilash» tugmasi yonida.
	 *
	 * `page.add_button()` ISHLATILMAYDI: u har chaqiruvda mobil menyuga ham
	 * yozuv qo'shadi, ya'ni smena holati o'zgargan sari menyu to'lib boradi.
	 * Shuning uchun tugma BIR MARTA yaratiladi va keyin faqat ko'rinishi
	 * yangilanadi.
	 *
	 * Smena YOPIQ bo'lsa tugma YASHIRILADI: ochish faqat bloklovchi ekran
	 * (`renderShiftGate`) orqali bo'ladi. Ikkita ochish yo'li bo'lsa,
	 * modalni yopib kassasiz ishlashda davom etish mumkin bo'lardi.
	 */
	renderShiftButton(shift) {
		const isOpen = !!shift.open;

		if (!this.$shiftBtn) {
			this.$shiftBtn = $('<button class="btn btn-sm ellipsis rc-shift-action btn-danger"></button>')
				.appendTo(this.page.custom_actions);
			this.page.custom_actions.removeClass("hide");

			this.$shiftBtn.on("click", () => {
				// Holatni bosilgan PAYTDA o'qiymiz — eskirgan qiymatga tayanmaymiz.
				if (this.ctx && this.ctx.shift && this.ctx.shift.open) {
					this.closeShiftDialog();
				}
			});
		}

		// Yopish ham faqat biriktirilgan kassirning ishi — u kassadagi
		// naqd pulni sanaydi. Boshqa foydalanuvchida tugma chiqmaydi
		// (server ham rad etadi: `assert_shift_operator`).
		const canOperate = !!(this.ctx.permissions || {}).can_operate_shift;

		this.$shiftBtn.text(__("Kassani yopish")).toggle(isOpen && canOperate);
	}

	/**
	 * Kassani ochish — BLOKLOVCHI ekran (modal emas).
	 *
	 * Kassir smena boshida kassadagi NAQD PULNI sanab kiritadi. Bu summa
	 * smena oxiridagi solishtiruvning boshlang'ich nuqtasi:
	 *
	 *     kutilayotgan naqd = ochilish summasi + naqd sotuvlar
	 *
	 * Modal ATAYLAB ishlatilmadi — modalni yopib ishlashda davom etish
	 * mumkin bo'lardi. Bu qadam o'tkazib yuborilmasligi kerak, chunki
	 * smenasiz sotuv umuman mumkin emas: server tomonda
	 * `cashier_permissions.assert_shift_open()` hisob ochish, to'lov va
	 * ofitsant buyurtmasini rad etadi.
	 */
	renderShiftGate() {
		// FAQAT NAQD. Bank/karta bo'yicha boshlang'ich qoldiq yo'q — u pul
		// kassada emas, bankda turadi va kassir uni sanay olmaydi. Server
		// ham shuni majburlaydi (`_opening_balance_details`).
		// DIQQAT: bu ekran FAQAT kassani ocha oladigan foydalanuvchiga
		// chiziladi — qaror `boot()` da qabul qilinadi. Ocholmaydigan
		// foydalanuvchi bu yergacha yetib kelmaydi, u to'g'ridan-to'g'ri
		// kuzatuv rejimida ishlaydi.
		const modes = this.ctx.cash_modes || [];
		const $form = $(this.el.gate).off();

		if (!modes.length) {
			$form.html(
				`<div class="rc-pay__error">${esc(
					__("POS Profile'da naqd to'lov usuli sozlanmagan — kassani ochib bo'lmaydi.")
				)}</div>`
			);
			return;
		}

		$form.html(`
			<table class="rc-shift-table">
				<thead>
					<tr>
						<th>${esc(__("Naqd pul"))}</th>
						<th class="rc-num">${esc(__("Kassadagi summa"))}</th>
					</tr>
				</thead>
				<tbody>
					${modes
						.map(
							(mode) => `<tr>
								<td>${esc(mode)}</td>
								<td class="rc-num">
									<input class="rc-shift-input" type="number" step="any" min="0"
										value="0" data-mode="${esc(mode)}"
										aria-label="${esc(mode)}">
								</td>
							</tr>`
						)
						.join("")}
				</tbody>
			</table>
			<div class="rc-pay__error" role="alert"></div>
			<div class="rc-actions">
				<button class="rc-btn rc-btn--pay" data-action="open-shift">${esc(
					__("Kassani ochish")
				)}</button>
			</div>`);

		const $error = $form.find(".rc-pay__error");

		$form.on("click", '[data-action="open-shift"]', async (e) => {
			const rows = $form
				.find(".rc-shift-input")
				.map((_, input) => ({
					mode_of_payment: input.dataset.mode,
					opening_amount: flt(input.value),
				}))
				.get();

			$error.text("");
			try {
				this.busy(e.currentTarget, true);
				await this.call("ozturkapp.ozturkapp.api.cashier.open_shift", {
					balance_details: JSON.stringify(rows),
				});
				frappe.show_alert({ message: __("Kassa ochildi"), indicator: "green" });
				await this.boot();
			} catch (error) {
				$error.text(this.errorText(error));
				this.busy(e.currentTarget, false);
			}
		});
	}

	tickClock() {
		if (!this.el.clock) return;
		this.el.clock.textContent = frappe.datetime.str_to_user(
			frappe.datetime.now_datetime()
		);
	}

	renderWarnings() {
		const warnings = this.ctx.warnings || [];
		this.el.warnings.innerHTML = warnings
			.map(
				(w) =>
					`<div class="rc-warning"><span aria-hidden="true">⚠</span><span>${esc(
						w.message
					)}</span></div>`
			)
			.join("");
	}

	renderRooms() {
		const rooms = this.ctx.rooms || [];
		this.el.rooms.innerHTML =
			`<button class="rc-room" type="button" data-room="" aria-pressed="${
				this.room === null
			}">${esc(__("Barcha zallar"))}</button>` +
			rooms
				.map(
					(room) =>
						`<button class="rc-room" type="button" data-room="${esc(room.name)}"
							aria-pressed="${room.name === this.room}">${esc(room.name)}
							<span class="rc-room__count">${cint(room.table_count)}</span></button>`
				)
				.join("");

		$(this.el.rooms)
			.off("click")
			.on("click", ".rc-room", (e) => {
				this.room = e.currentTarget.dataset.room || null;
				this.writePreference("room", this.room || "");
				this.clearSelection();
				this.renderRooms();
				this.refreshAll();
			});
	}

	// ═══════════════════════════════════════════════════════════
	//  Holat filtri
	// ═══════════════════════════════════════════════════════════

	renderFilters() {
		const counts = (this.floor || {}).counts || {};
		const options = [
			{ key: "ALL", label: __("Hammasi"), mark: "▣" },
			{ key: "AVAILABLE", label: __("Bo'sh"), mark: "●" },
			{ key: "RESERVED", label: __("Bron"), mark: "◆" },
			{ key: "OCCUPIED", label: __("Band"), mark: "■" },
		];

		this.el.filters.innerHTML = options
			.map(
				(option) =>
					`<button class="rc-filter rc-filter--${option.key}" type="button"
						data-filter="${option.key}" aria-pressed="${this.statusFilter === option.key}">
						<span class="rc-filter__mark" aria-hidden="true">${option.mark}</span>
						<span>${esc(option.label)}</span>
						<span class="rc-filter__count">${cint(counts[option.key])}</span>
					</button>`
			)
			.join("");

		$(this.el.filters)
			.off("click")
			.on("click", ".rc-filter", (e) => {
				this.statusFilter = e.currentTarget.dataset.filter;
				this.renderFilters();
				this.renderFloor();
			});
	}

	// ═══════════════════════════════════════════════════════════
	//  Zal rejasi (TZ §4, §20)
	// ═══════════════════════════════════════════════════════════

	renderFloor() {
		const tables = (this.floor || {}).tables || [];
		this.el.floorEmpty.hidden = tables.length > 0;

		if (!tables.length) {
			this.el.canvas.innerHTML = "";
			return;
		}

		const extent = this.floor.extent || { width: 0, height: 0 };
		this.el.canvas.style.width = `${extent.width}px`;
		this.el.canvas.style.height = `${extent.height}px`;

		// "Barcha zallar" ko'rinishida har bir zal alohida blok — server
		// ularni ustma-ust tushmaydigan qilib joylashtirgan va chegaralarini
		// `room_bands` da qaytargan.
		const bands = (!this.room && this.floor.room_bands) || [];

		this.el.canvas.innerHTML =
			bands.map((b) => this.roomBandHtml(b, extent)).join("") +
			tables.map((t) => this.tableHtml(t)).join("");
		this.fitFloor();

		$(this.el.canvas)
			.off("click")
			.on("click", ".rc-table", (e) => this.selectTable(e.currentTarget.dataset.table));
	}

	/** Zal blokining sarlavhasi va ajratuvchi chizig'i.
	 *
	 * Sarlavha server AJRATGAN `header_height` bo'shlig'i ichida turadi —
	 * stollar allaqachon shu balandlikka pastga surilgan, shuning uchun
	 * nom hech qachon stol ustiga tushmaydi.
	 */
	roomBandHtml(band, extent) {
		const header = band.header_height || 0;
		return `<div class="rc-band" style="top:${band.y}px;height:${band.height}px;width:${extent.width}px">
			<div class="rc-band__header" style="height:${header}px">
				<span class="rc-band__label">${esc(band.room)}
					<span class="rc-band__count">${cint(band.count)}</span>
				</span>
			</div>
		</div>`;
	}

	tableHtml(table) {
		const layout = table.layout || {};
		const order = table.order;
		const reservation = table.reservation;
		const dim = this.statusFilter !== "ALL" && table.status !== this.statusFilter;

		// Holat matni + belgi — rangga tayanmaslik uchun (TZ §20).
		const marks = { AVAILABLE: "●", RESERVED: "◆", OCCUPIED: "■" };
		const labels = {
			AVAILABLE: __("Bo'sh"),
			RESERVED: __("Bron"),
			OCCUPIED: __("Band"),
		};

		let meta = "";
		let amount = "";

		if (order) {
			amount = `<span class="rc-table__amount">${esc(
				this.money(order.amount)
			)}</span>`;
			const who = order.waiter ? order.waiter.split("@")[0] : "";
			meta = who ? `<span class="rc-table__meta">${esc(who)}</span>` : "";
		} else if (reservation) {
			meta = `<span class="rc-table__meta">${esc(
				(reservation.from_time || "").slice(0, 5)
			)} · ${esc(reservation.customer_name || __("Bron"))}</span>`;
		} else {
			meta = `<span class="rc-table__meta">${cint(table.no_of_seats)} ${esc(
				__("o'rin")
			)}</span>`;
		}

		const badges = [];
		if (table.is_merged) badges.push("⛓");
		if (cint(table.open_order_count) > 1) badges.push(`×${cint(table.open_order_count)}`);
		if (order && order.billed) badges.push("🧾");

		return `<button type="button"
			class="rc-table rc-table--${esc(table.status)} rc-table--${esc(
			table.table_shape || "Square"
		)} ${dim ? "rc-table--dim" : ""}"
			data-table="${esc(table.name)}"
			aria-pressed="${this.selectedTable === table.name}"
			aria-label="${esc(table.name)} — ${esc(labels[table.status])}"
			style="left:${layout.x}px;top:${layout.y}px;width:${layout.width}px;height:${layout.height}px">
			${badges.length ? `<span class="rc-table__badge">${esc(badges.join(" "))}</span>` : ""}
			<span class="rc-table__name">${esc(table.name)}</span>
			<span class="rc-table__status"><span aria-hidden="true">${marks[table.status]}</span>${esc(
			labels[table.status]
		)}</span>
			${amount}
			${meta}
		</button>`;
	}

	/** Zal rejasini konteynerga sig'dirish (kichraytirish, kattalashtirmaslik). */
	fitFloor() {
		const extent = (this.floor || {}).extent || {};
		const available = this.el.floorScroll.clientWidth - 40;
		if (!extent.width || !available || available <= 0) return;

		const scale = Math.min(1, available / extent.width);
		this.el.canvas.style.transform = `scale(${scale})`;
		this.el.floorScroll.style.height = `${extent.height * scale + 40}px`;
	}

	// ═══════════════════════════════════════════════════════════
	//  Tanlangan stol paneli (TZ §21)
	// ═══════════════════════════════════════════════════════════

	async selectTable(table) {
		this.selectedTable = table;
		$(this.el.canvas)
			.find(".rc-table")
			.each((_, node) => {
				node.setAttribute("aria-pressed", String(node.dataset.table === table));
			});
		await this.loadTableDetail(table);
	}

	clearSelection() {
		this.selectedTable = null;
		this.selectedInvoice = null;
		this.el.panel.innerHTML = `<div class="rc-empty">
			<div class="rc-empty__icon">👆</div>
			<p class="rc-empty__title">${esc(__("Stolni tanlang"))}</p>
			<p class="rc-empty__hint">${esc(
				__("Buyurtma, hisob va to'lov shu yerda ko'rinadi.")
			)}</p>
		</div>`;
	}

	async loadTableDetail(table) {
		try {
			const detail = await this.call(
				"ozturkapp.ozturkapp.api.table.get_table_detail",
				{ table }
			);
			if (this.selectedTable !== table) return; // kassir boshqasini tanlab ulgurdi
			this.renderPanel(detail);
		} catch (error) {
			this.el.panel.innerHTML = `<div class="rc-empty">
				<div class="rc-empty__icon">⚠</div>
				<p class="rc-empty__title">${esc(__("Ma'lumot yuklanmadi"))}</p>
				<p class="rc-empty__hint">${esc(this.errorText(error))}</p>
			</div>`;
		}
	}

	/**
	 * Stolsiz buyurtmani tanlash (olib ketish, yetkazib berish, Desktop POS).
	 *
	 * Zal rejasida bunday buyurtmaning stoli yo'q, ya'ni uni faqat
	 * buyurtmalar ro'yxatidan ochish mumkin. Panel esa o'sha-o'sha —
	 * `renderPanel()` `detail.table` bo'sh bo'lsa sarlavhani buyurtma
	 * turidan oladi.
	 */
	async selectOrder(invoice) {
		this.selectedTable = null;
		$(this.el.canvas)
			.find(".rc-table")
			.each((_, node) => node.setAttribute("aria-pressed", "false"));

		await this.loadOrderDetail(invoice);
	}

	async loadOrderDetail(invoice) {
		this.selectedInvoice = invoice;

		try {
			const bill = await this.call(
				"ozturkapp.ozturkapp.api.order.get_order_bill_preview",
				{ order: invoice }
			);
			if (this.selectedInvoice !== invoice) return; // kassir boshqasini tanladi

			this.renderPanel({
				table: "",
				status: "OCCUPIED",
				room: bill.room || "",
				seats: 0,
				is_merged: false,
				bill,
				other_orders: [],
				issue: null,
			});
		} catch (error) {
			this.el.panel.innerHTML = `<div class="rc-empty">
				<div class="rc-empty__icon">⚠</div>
				<p class="rc-empty__title">${esc(__("Ma'lumot yuklanmadi"))}</p>
				<p class="rc-empty__hint">${esc(this.errorText(error))}</p>
			</div>`;
		}
	}

	renderPanel(detail) {
		const labels = {
			AVAILABLE: __("Bo'sh"),
			RESERVED: __("Bron qilingan"),
			OCCUPIED: __("Band"),
		};

		// Realtime qamrovini aniqlash uchun joriy chekni eslab qolamiz.
		this.selectedInvoice = (detail.bill && detail.bill.invoice) || null;

		let body = "";
		if (detail.issue) {
			// Jim bo'sh hisob KO'RSATILMAYDI — muammo aniq aytiladi (TZ §8).
			body = this.issueHtml(detail);
		} else if (detail.status === "OCCUPIED" && detail.bill) {
			body = this.billHtml(detail);
		} else if (detail.status === "RESERVED") {
			body = this.reservationHtml(detail);
		} else {
			body = this.availableHtml(detail);
		}

		// Stolsiz buyurtmada (olib ketish / Desktop POS) sarlavha stol nomi
		// emas, buyurtma turi bo'ladi — «o'rin» soni ham ma'nosiz.
		const bill = detail.bill || {};
		const title = detail.table || bill.order_type || __("Stolsiz buyurtma");
		const subtitle = detail.table
			? `${esc(detail.room || "")} · ${cint(detail.seats)} ${esc(__("o'rin"))}${
					detail.is_merged ? ` · ${esc(__("birlashtirilgan"))}` : ""
			  }`
			: esc(bill.invoice || "");

		this.el.panel.innerHTML = `
			<div class="rc-panel__head">
				<div>
					<div class="rc-panel__table">${esc(title)}</div>
					<div class="rc-panel__sub">${subtitle}</div>
				</div>
				<span class="rc-chip rc-chip--${esc(detail.status)}">${esc(
			labels[detail.status]
		)}</span>
			</div>
			${body}`;

		this.bindPanel(detail);
	}

	/** Ma'lumot nomuvofiqligi — buyurtma topilmadi va h.k. (TZ §8). */
	issueHtml(detail) {
		const issue = detail.issue || {};
		const canRelease =
			issue.code === "STALE_OCCUPIED_FLAG" && this.ctx.permissions.is_supervisor;

		return `
			<div class="rc-issue" role="alert">
				<div class="rc-issue__icon" aria-hidden="true">⚠</div>
				<p class="rc-issue__title">${esc(__("Buyurtma topilmadi"))}</p>
				<p class="rc-issue__text">${esc(issue.message || "")}</p>
				<p class="rc-issue__code">${esc(issue.code || "")}</p>
			</div>
			<div class="rc-actions">
				<button class="rc-btn" data-action="reload">${esc(__("Qayta yuklash"))}</button>
				${
					canRelease
						? `<button class="rc-btn rc-btn--danger" data-action="release">${esc(
								__("Stolni bo'shatish")
						  )}</button>`
						: ""
				}
			</div>
			<p class="rc-hint">${esc(
				__("Buyurtma avtomatik yaratilmaydi — bu holat qo'lda hal qilinishi kerak.")
			)}</p>`;
	}

	availableHtml(detail) {
		return `
			<div class="rc-facts">
				<div class="rc-fact"><div class="rc-fact__label">${esc(
					__("O'rindiqlar")
				)}</div><div class="rc-fact__value">${cint(detail.seats)}</div></div>
				<div class="rc-fact"><div class="rc-fact__label">${esc(
					__("Shakl")
				)}</div><div class="rc-fact__value">${esc(detail.shape || "—")}</div></div>
			</div>
			<div class="rc-empty" style="padding:26px 0">
				<div class="rc-empty__icon">🍽</div>
				<p class="rc-empty__title">${esc(__("Stol bo'sh"))}</p>
			</div>
			<div class="rc-actions">
				<button class="rc-btn rc-btn--primary" data-action="reserve">${esc(
					__("Bron qilish")
				)}</button>
			</div>
			<p class="rc-hint">${esc(
				__("Stol faqat buyurtma qabul qilinganda band bo'ladi va buyurtma yopilganda avtomatik bo'shaydi.")
			)}</p>`;
	}

	reservationHtml(detail) {
		const r = detail.reservation || {};
		return `
			<div class="rc-facts">
				<div class="rc-fact"><div class="rc-fact__label">${esc(
					__("Mehmon")
				)}</div><div class="rc-fact__value">${esc(r.customer_name || "—")}</div></div>
				<div class="rc-fact"><div class="rc-fact__label">${esc(
					__("Telefon")
				)}</div><div class="rc-fact__value">${esc(r.phone || "—")}</div></div>
				<div class="rc-fact"><div class="rc-fact__label">${esc(
					__("Vaqt")
				)}</div><div class="rc-fact__value">${esc(
			(r.from_time || "").slice(0, 5)
		)}${r.to_time ? "–" + esc(r.to_time.slice(0, 5)) : ""}</div></div>
				<div class="rc-fact"><div class="rc-fact__label">${esc(
					__("Bron holati")
				)}</div><div class="rc-fact__value">${esc(r.status || "—")}</div></div>
			</div>
			${r.notes ? `<p class="rc-hint">${esc(r.notes)}</p>` : ""}
			<div class="rc-actions">
				<button class="rc-btn rc-btn--danger" data-action="unreserve"
					data-reservation="${esc(r.name || "")}">${esc(
			__("Bronni bekor qilish")
		)}</button>
			</div>
			<p class="rc-hint">${esc(
				__("Mehmon kelib buyurtma berganda stol o'zi band bo'ladi.")
			)}</p>`;
	}

	billHtml(detail) {
		const bill = detail.bill;
		const kitchen = bill.kitchen || {};

		const items = bill.items.length
			? bill.items
					.map(
						(item) => `<div class="rc-item">
							<div>
								<div class="rc-item__name">${esc(item.item_name || item.item_code)}</div>
								<div class="rc-item__qty">${fmtQty(item.qty)} × ${esc(
							this.money(item.rate)
						)}</div>
							</div>
							<div class="rc-item__amount">${esc(this.money(item.amount))}</div>
							${item.comment ? `<div class="rc-item__note">${esc(item.comment)}</div>` : ""}
						</div>`
					)
					.join("")
			: `<div class="rc-empty" style="padding:22px 0">
					<p class="rc-empty__title">${esc(__("Taom qo'shilmagan"))}</p>
					<p class="rc-empty__hint">${esc(
						__("Stol ochilgan, lekin hali buyurtma qabul qilinmagan.")
					)}</p>
				</div>`;

		// Soliq/yig'im qatorlari serverdan tayyor keladi — bu yerda hisob yo'q.
		const taxes = (bill.taxes || [])
			.map(
				(tax) => `<div class="rc-total ${
					tax.is_service_charge ? "rc-total--service" : ""
				}">
					<span>${esc(tax.description)}${
					tax.rate ? ` (${fmtQty(tax.rate)}%)` : ""
				}</span>
					<span>${esc(this.money(tax.amount))}</span>
				</div>`
			)
			.join("");

		const canPay = bill.item_count > 0;
		const needsBill = canPay && !bill.billed;

		// Bekor qilish qoidasi SERVERDA hisoblanadi (`utils/order_cancel.py`).
		// Bu yerda faqat chiziladi — tugmani DevTools'dan yoqib qo'yish ham
		// hech narsa bermaydi, server o'sha qoidani qayta qo'llaydi.
		const cancellation = bill.cancellation || {};

		return `
			<div class="rc-facts">
				<div class="rc-fact"><div class="rc-fact__label">${esc(
					__("Buyurtma")
				)}</div><div class="rc-fact__value">${esc(bill.invoice)}</div></div>
				<div class="rc-fact"><div class="rc-fact__label">${esc(
					__("Ofitsant")
				)}</div><div class="rc-fact__value">${esc(bill.waiter_name || "—")}</div></div>
				<div class="rc-fact"><div class="rc-fact__label">${esc(
					__("Mijoz")
				)}</div><div class="rc-fact__value">${esc(bill.customer_name || "—")}</div></div>
				<div class="rc-fact"><div class="rc-fact__label">${esc(
					__("Mehmonlar")
				)}</div><div class="rc-fact__value">${cint(bill.pax)}</div></div>
			</div>

			${
				kitchen.kot_count
					? `<div class="rc-kitchen" title="${esc(
							__("Oshxona holati — kassa uni o'zgartirmaydi")
					  )}">🍳 ${esc(kitchen.label)} (${cint(kitchen.served_count)}/${cint(
							kitchen.kot_count
					  )})</div>`
					: ""
			}

			<div class="rc-items">${items}</div>

			<div class="rc-totals">
				<div class="rc-total"><span>${esc(__("Oraliq summa"))}</span><span>${esc(
			this.money(bill.subtotal)
		)}</span></div>
				${
					bill.discount
						? `<div class="rc-total"><span>${esc(
								__("Chegirma")
						  )}</span><span>−${esc(this.money(bill.discount))}</span></div>`
						: ""
				}
				${taxes}
				<div class="rc-total rc-total--grand"><span>${esc(
					__("Jami")
				)}</span><span>${esc(this.money(bill.rounded_total))}</span></div>
			</div>

			<div class="rc-actions">
				${
					needsBill
						? `<button class="rc-btn rc-btn--primary" data-action="give-bill">${esc(
								__("Hisobni berish")
						  )}</button>`
						: `<button class="rc-btn" data-action="reprint">${esc(
								__("Hisobni qayta chop etish")
						  )}</button>`
				}
				<button class="rc-btn rc-btn--pay" data-action="pay" ${
					canPay && bill.billed ? "" : "disabled"
				}>${esc(__("To'lov"))} · ${esc(this.money(bill.rounded_total))}</button>
				${this.cancelButtonHtml(cancellation)}
			</div>

			${
				needsBill
					? `<p class="rc-hint">${esc(
							__("«Hisobni berish» — chek chop etiladi va mijozga beriladi. To'lov shundan keyin qabul qilinadi.")
					  )}</p>`
					: `<p class="rc-hint">${esc(
							__("Hisob mijozga berilgan. To'lovni qabul qiling.")
					  )}</p>`
			}
			${
				cancellation.kitchen_started && cancellation.blocked_reason
					? `<p class="rc-hint">🔒 ${esc(cancellation.blocked_reason)}</p>`
					: ""
			}
			${
				cancellation.kitchen_started && cancellation.warning
					? `<p class="rc-hint">⚠ ${esc(cancellation.warning)}</p>`
					: ""
			}
			${
				detail.other_orders && detail.other_orders.length
					? `<p class="rc-hint">⚠ ${esc(
							__("Bu stolda yana {0} ta to'lanmagan hisob bor.").replace(
								"{0}",
								detail.other_orders.length
							)
					  )}</p>`
					: ""
			}`;
	}

	/**
	 * «Buyurtmani bekor qilish» tugmasi.
	 *
	 * Uch xil ko'rinishi bor va uchalasi ham SERVER aytgan holatga tayanadi
	 * (`bill.cancellation`):
	 *
	 *   oshxona boshlamagan          -> oddiy bekor qilish (har qanday kassir)
	 *   boshlagan + menejer          -> «Majburan bekor qilish»
	 *   boshlagan + oddiy kassir     -> o'chirilgan tugma + sabab
	 *
	 * Tugma butunlay yashirilmaydi: kassir NEGA bekor qilolmayotganini
	 * ko'rishi kerak, aks holda u menejerni chaqirish o'rniga sahifani
	 * qayta yuklab vaqt yo'qotadi.
	 */
	cancelButtonHtml(cancellation) {
		if (!cancellation || (!cancellation.allowed && !cancellation.kitchen_started)) {
			// To'langan yoki allaqachon bekor qilingan chek — tugma keraksiz.
			return "";
		}

		if (!cancellation.allowed) {
			return `<button class="rc-btn rc-btn--danger" disabled title="${esc(
				cancellation.blocked_reason || ""
			)}">${esc(__("Bekor qilish"))}</button>`;
		}

		return `<button class="rc-btn rc-btn--danger" data-action="cancel-order">${esc(
			cancellation.requires_supervisor
				? __("Majburan bekor qilish")
				: __("Buyurtmani bekor qilish")
		)}</button>`;
	}

	bindPanel(detail) {
		$(this.el.panel)
			.off("click")
			.on("click", "[data-action]", (e) => {
				const button = e.currentTarget;
				const action = button.dataset.action;

				if (action === "reserve") this.reserveTable(detail);
				else if (action === "unreserve") this.cancelReservation(detail);
				else if (action === "give-bill") this.giveBill(detail, button);
				else if (action === "reprint") this.printReceipt(detail);
				else if (action === "pay") this.openPaymentModal(detail);
				else if (action === "cancel-order") this.cancelOrder(detail, button);
				else if (action === "reload") this.refreshAll();
				else if (action === "release") this.releaseTable(detail);
				else if (action === "print") this.printReceipt(detail);
			});
	}

	// ═══════════════════════════════════════════════════════════
	//  Amallar
	// ═══════════════════════════════════════════════════════════

	/** Bo'sh stolni bron qilish. Stol BAND BO'LMAYDI — faqat RESERVED. */
	reserveTable(detail) {
		// Vaqt "Time" maydoniga qo'lda yozilmaydi: soat va daqiqa ro'yxatdan
		// tanlanadi — sensorli ekranda tezroq va xatosizroq.
		const slot = defaultTimeSlot();

		frappe.prompt(
			[
				{
					fieldname: "customer_name",
					fieldtype: "Data",
					label: __("Mehmon ismi"),
					reqd: 1,
				},
				{ fieldname: "phone", fieldtype: "Data", label: __("Telefon") },
				{ fieldtype: "Section Break", label: __("Bron vaqti") },
				{
					fieldname: "hour",
					fieldtype: "Select",
					label: __("Soat"),
					options: HOUR_OPTIONS,
					default: slot.hour,
					reqd: 1,
				},
				{ fieldtype: "Column Break" },
				{
					fieldname: "minute",
					fieldtype: "Select",
					label: __("Daqiqa"),
					options: MINUTE_OPTIONS,
					default: slot.minute,
					reqd: 1,
				},
				{ fieldtype: "Section Break" },
				{ fieldname: "notes", fieldtype: "Small Text", label: __("Izoh") },
			],
			async (values) => {
				try {
					await this.call("ozturkapp.ozturkapp.api.table.reserve_table", {
						table: detail.table,
						customer_name: values.customer_name,
						phone: values.phone,
						from_time: `${values.hour}:${values.minute}:00`,
						notes: values.notes,
					});
					frappe.show_alert({
						message: __("{0} bron qilindi", [detail.table]),
						indicator: "orange",
					});
					await this.refreshAll();
				} catch (error) {
					this.alertError(error);
				}
			},
			__("{0} — bron qilish", [detail.table]),
			__("Bron qilish")
		);
	}

	/** Bronni yechish — stol yana bo'sh bo'ladi. */
	cancelReservation(detail) {
		const reservation = (detail.reservation || {}).name;
		frappe.confirm(
			__("{0} stolidagi bron bekor qilinsinmi?", [detail.table]),
			async () => {
				try {
					await this.call("ozturkapp.ozturkapp.api.table.cancel_reservation", {
						table: detail.table,
						reservation,
					});
					frappe.show_alert({
						message: __("Bron bekor qilindi"),
						indicator: "green",
					});
					await this.refreshAll();
				} catch (error) {
					this.alertError(error);
				}
			}
		);
	}

	/**
	 * Hisobni mijozga berish.
	 *
	 * Bitta amalda ikkita ish bajariladi:
	 *   1. `open_bill` — chek "berilgan" deb belgilanadi (`invoice_printed=1`).
	 *      URY buni TALAB QILADI: `ury/hooks/ury_pos_invoice.py:validate_invoice_print`
	 *      stolga bog'langan chekni bu bayroqsiz submit qilishga yo'l qo'ymaydi.
	 *   2. chek chop etiladi va mijozga beriladi.
	 *
	 * Ya'ni "hisobni berish" — mijozga chekni taqdim etish amali.
	 */
	async giveBill(detail, button) {
		try {
			this.busy(button, true);
			await this.call("ozturkapp.ozturkapp.api.billing.open_bill", {
				invoice: detail.bill.invoice,
			});
			this.printReceipt(detail);
			frappe.show_alert({ message: __("Hisob berildi"), indicator: "blue" });
			await this.refreshAll();
		} catch (error) {
			this.alertError(error);
		} finally {
			this.busy(button, false);
		}
	}

	/**
	 * Buyurtmani bekor qilish — ofitsant xato zakaz olib qo'yganda.
	 *
	 * QOIDA (serverda majburlanadi, `utils/order_cancel.py`)
	 * ======================================================
	 *     Oshxona hali BOSHLAMAGAN  ->  har qanday kassir
	 *     Oshxona BOSHLAB YUBORGAN  ->  faqat menejer
	 *
	 * "Boshlangan" = kamida bitta taom `URY KOT Items.custom_kitchen_status`
	 * da «Kutilmoqda» dan chiqib ketgan. `URY KOT.start_time_prep` ga
	 * QARALMAYDI — u KOT yaratilganda to'ladigan maydon.
	 *
	 * Chek O'CHIRILMAYDI: `custom_cancelled = 1` bo'lib bazada qoladi,
	 * sabab esa hisobotga tushadi. Shuning uchun sabab majburiy.
	 */
	cancelOrder(detail, button) {
		const bill = detail.bill || {};
		const cancellation = bill.cancellation || {};
		const fields = [];

		// Menejer majburan bekor qilayotgan bo'lsa — taom allaqachon
		// pishayotganini AYTIB turamiz, u chiqindiga ketadi.
		if (cancellation.warning) {
			fields.push({
				fieldname: "warning",
				fieldtype: "HTML",
				options: `<div class="rc-hint" style="margin:0 0 10px">⚠ ${esc(
					cancellation.warning
				)}</div>`,
			});
		}

		fields.push({
			fieldname: "reason",
			fieldtype: "Small Text",
			label: __("Bekor qilish sababi"),
			description: __("Sabab chekda saqlanadi va hisobotga tushadi."),
			reqd: 1,
		});

		frappe.prompt(
			fields,
			async (values) => {
				try {
					this.busy(button, true);
					const result = await this.call(
						"ozturkapp.ozturkapp.api.order.cancel_order",
						{ order: bill.invoice, reason: values.reason }
					);

					frappe.show_alert({
						message: __("{0} bekor qilindi", [bill.invoice]),
						indicator: "orange",
					});

					// Oshxonaga chipta ketgan bo'lsa — kassir buni bilishi
					// kerak: oshpaz ekranida ham chipta yopildi.
					if (result && cint(result.cancelled_items)) {
						frappe.show_alert({
							message: __("Oshxonaga xabar berildi ({0} taom)", [
								cint(result.cancelled_items),
							]),
							indicator: "blue",
						});
					}

					this.clearSelection();
					await this.refreshAll();
				} catch (error) {
					this.alertError(error);
				} finally {
					this.busy(button, false);
				}
			},
			cancellation.requires_supervisor
				? __("Majburan bekor qilish")
				: __("Buyurtmani bekor qilish"),
			__("Bekor qilish")
		);
	}

	/**
	 * Stolni majburan bo'shatish — FAQAT buzilgan holat uchun.
	 *
	 * Oddiy ish jarayonida bu KERAK EMAS: stol to'lov qilinganda avtomatik
	 * bo'shaydi. Bu metod faqat `STALE_OCCUPIED_FLAG` holatida (stol band,
	 * lekin buyurtma yo'q) menejerga ko'rsatiladi — busiz bunday stolni
	 * hech qachon tozalab bo'lmaydi.
	 */
	releaseTable(detail) {
		frappe.prompt(
			[
				{
					fieldname: "reason",
					fieldtype: "Small Text",
					label: __("Bo'shatish sababi"),
					reqd: 1,
				},
			],
			async (values) => {
				try {
					await this.call("ozturkapp.ozturkapp.api.table.release_table", {
						table: detail.table,
						reason: values.reason,
					});
					frappe.show_alert({
						message: __("{0} bo'shatildi", [detail.table]),
						indicator: "green",
					});
					await this.refreshAll();
				} catch (error) {
					this.alertError(error);
				}
			},
			__("Stolni bo'shatish"),
			__("Bo'shatish")
		);
	}

	/**
	 * Mijoz uchun chekni chop etish.
	 *
	 * Frappe'ning STANDART chop etish ko'rinishi ochiladi va brauzerning
	 * chop etish oynasi darhol chiqadi (`trigger_print=1`). Shu tufayli
	 * hech qanday printer sozlamasi TALAB QILINMAYDI — kassir istalgan
	 * ulangan printerga (jumladan chek printeriga) chiqara oladi.
	 *
	 * URY'ning QZ/network printer oqimiga TEGILMAYDI — u oshxona cheki
	 * (KOT) uchun va o'z holicha ishlashda davom etadi.
	 */
	printReceipt(detail) {
		const invoice = (detail.bill || {}).invoice;
		if (!invoice) return;

		const params = new URLSearchParams({
			doctype: "POS Invoice",
			name: invoice,
			format: this.ctx.print_format || "POS Invoice",
			no_letterhead: "0",
			_lang: frappe.boot.lang || "uz",
			trigger_print: "1",
		});

		window.open(`/printview?${params.toString()}`, "_blank", "noopener");
	}

	// ═══════════════════════════════════════════════════════════
	//  Kassa smenasi — ochish / yopish
	// ═══════════════════════════════════════════════════════════

	/**
	 * Kassani yopish — IKKI BOSQICHLI KO'R SANOQ.
	 *
	 *     1-bosqich: naqd pulni sanab kiriting  ->  [Davom etish]
	 *                        |
	 *                60 soniya teskari sanoq (pulni QAYTA sanang)
	 *                        |
	 *     2-bosqich: yana bir marta kiriting    ->  [Kassani yopish]
	 *
	 * Ikkinchi kiritish 1-bosqichdagi qiymat bilan SOLISHTIRILADI. Mos
	 * kelmasa yopilmaydi va sanoq boshidan boshlanadi — shu tariqa
	 * shoshib yozilgan xato raqam o'tib ketmaydi.
	 *
	 * Birinchi kiritilgan summa 2-bosqichda KO'RSATILMAYDI, aks holda
	 * kassir uni ko'chirib yozib qo'yardi va nazorat ma'nosini yo'qotardi.
	 *
	 * Kassirga umumiy savdo va kutilayotgan summa HECH QACHON
	 * ko'rsatilmaydi (ko'r sanoq) — server ularni yubormaydi ham.
	 */
	async closeShiftDialog() {
		let data;
		try {
			data = await this.call("ozturkapp.ozturkapp.api.cashier.get_shift_closing_data");
		} catch (error) {
			this.alertError(error);
			return;
		}

		if (cint(data.open_orders) > 0) {
			frappe.msgprint({
				title: __("Yopilmagan buyurtmalar"),
				indicator: "red",
				message: __("{0} ta to'lanmagan buyurtma bor — kassa yopilmaydi.", [
					cint(data.open_orders),
				]),
			});
			return;
		}

		this.showModal(__("Kassani yopish"));
		this.renderCountStep(data, null);
	}

	/**
	 * Sanoq bosqichini chizadi.
	 *
	 * @param {object} data   server bergan yopish ma'lumoti
	 * @param {object|null} first  1-bosqichda kiritilgan summalar
	 *        (`null` bo'lsa — birinchi bosqich)
	 */
	renderCountStep(data, first) {
		const second = first !== null;

		// Bosqich holati YOPILMA (closure) da emas, INSTANSDA saqlanadi.
		// Sabab: `modalBody` doimiy element bo'lgani uchun eski ishlovchi
		// qandaydir yo'l bilan qolib ketsa, u o'zining ESKI `first`/`second`
		// qiymatlarini ko'rar va noto'g'ri tarmoqqa kirardi. Endi ishlovchi
		// bosqichni BOSILGAN TUGMADAN, birinchi sanoqni esa instansdan
		// o'qiydi — ya'ni qaysi ishlovchi ishga tushishidan qat'i nazar
		// natija bir xil bo'ladi.
		this.countFirst = first;
		const modes = data.cash_modes || [];

		// SAVDO BO'LMAGAN SMENA
		// =====================
		// Chek yozilmagan bo'lsa sanaydigan narsa yo'q — maydon 0 bilan
		// to'ldiriladi va kassir shunchaki tasdiqlaydi. Ilgari maydon bo'sh
		// turardi va "Sanalgan naqd pul summasini kiriting" xatosi kassani
		// yopishga qo'ymasdi: bo'sh smenani yopib bo'lmasdi.
		//
		// Savdo BO'LGAN bo'lsa maydon ataylab BO'SH qoladi. Tayyor turgan 0
		// ko'r sanoqning ma'nosini yo'qotardi — kassir sanamasdan ikki marta
		// tasdiqlab yuborishi mumkin bo'lardi.
		const preset = cint(data.total_invoices) ? "" : "0";

		this.el.modalBody.innerHTML = `
			<div class="rc-count__head">
				<div class="rc-count__label">${esc(__("Cheklar soni"))}</div>
				<div class="rc-count__value">${cint(data.total_invoices)}</div>
			</div>

			<div class="rc-steps">
				<span class="rc-step ${second ? "rc-step--done" : "rc-step--active"}">1</span>
				<span class="rc-steps__line"></span>
				<span class="rc-step ${second ? "rc-step--active" : ""}">2</span>
				<span class="rc-steps__text">${esc(
					second ? __("Ikkinchi sanoq") : __("Birinchi sanoq")
				)}</span>
			</div>

			<div class="rc-pay__label">${esc(
				second
					? __("Pulni qayta sanang va summani YANA kiriting")
					: __("Qo'lingizdagi naqd pulni sanang")
			)}</div>

			${modes
				.map(
					(mode) => `<div class="rc-count__row">
						<span>${esc(mode)}</span>
						<input class="rc-pay__input rc-count-input" type="number" step="any" min="0"
							value="${preset}" placeholder="0" data-mode="${esc(mode)}"
							aria-label="${esc(mode)}">
					</div>`
				)
				.join("")}

			<p class="rc-hint">${esc(
				__("Bank va karta summalarini kiritish shart emas — ular avtomatik hisoblanadi.")
			)}</p>

			${
				second
					? `<div class="rc-countdown">
						<div class="rc-countdown__ring"><span class="rc-countdown__num">--</span></div>
						<div class="rc-countdown__text">${esc(
							__("Pulni qayta sanang. Shu vaqt tugagach tasdiqlash ochiladi.")
						)}</div>
					</div>`
					: ""
			}

			<div class="rc-pay__error" role="alert"></div>
			<div class="rc-actions">
				<button class="rc-btn ${
					second ? "rc-btn--danger" : "rc-btn--primary"
				}" data-action="submit" data-step="${second ? 2 : 1}" ${
			second ? "disabled" : ""
		}>${esc(
			second ? __("Kassani yopish") : __("Davom etish")
		)}</button>
			</div>`;

		// XATO EDI: `modalBody` — DOIMIY element, `innerHTML` uni almashtirsa
		// ham unga OSILGAN delegatsiyalangan ishlovchilar QOLADI. Oyna ikki
		// bosqichli bo'lgani uchun ular to'planib, bitta bosishda IKKALASI
		// ham ishlab ketardi (2-bosqich tugmasi sanoqni qaytadan boshlardi).
		// Shuning uchun har safar eski ishlovchilar yechiladi.
		const $body = $(this.el.modalBody).off();
		const $error = $body.find(".rc-pay__error");
		const $submit = $body.find('[data-action="submit"]');

		// ── 2-bosqichda oyna QULFLANADI va sanoq ketadi ──────────────
		clearInterval(this.countdownTimer);
		this.countdownTimer = null;

		if (second) {
			this.setModalLocked(true);
			let left = cint(data.countdown_seconds) || 60;
			const $num = $body.find(".rc-countdown__num");

			const tick = () => {
				$num.text(left);
				if (left > 0) {
					left -= 1;
					return;
				}
				clearInterval(this.countdownTimer);
				this.countdownTimer = null;
				this.setModalLocked(false);
				$body.find(".rc-countdown").addClass("rc-countdown--done");
				$body.find(".rc-countdown__text").text(__("Endi tasdiqlashingiz mumkin."));
				$submit.prop("disabled", false);
			};

			tick();
			this.countdownTimer = setInterval(tick, 1000);
			this.timers.push(this.countdownTimer);
		} else {
			this.setModalLocked(false);
		}

		setTimeout(() => $body.find(".rc-count-input").first().trigger("focus"), 60);

		$body.on("click", '[data-action="submit"]', async (e) => {
			const counted = {};
			let missing = false;

			$body.find(".rc-count-input").each((_, input) => {
				if (String(input.value).trim() === "") missing = true;
				counted[input.dataset.mode] = flt(input.value);
			});

			if (missing) {
				// 0 — TO'LIQ HAQLI javob (masalan hamma to'lov karta bilan
				// bo'lgan). Xato matni buni aytib turishi kerak, aks holda
				// kassir 0 ni "qabul qilinmaydi" deb o'ylaydi.
				$error.text(
					__("Sanalgan naqd pul summasini kiriting. Kassa bo'sh bo'lsa — 0 yozing.")
				);
				return;
			}

			// Bosqichni BOSILGAN TUGMADAN o'qiymiz — yopilmadagi eskirgan
			// qiymatga tayanmaymiz.
			const isSecond = e.currentTarget.dataset.step === "2";
			const firstCount = this.countFirst;

			// ── 1-bosqich: eslab qolamiz va sanoqni boshlaymiz ───────
			if (!isSecond) {
				this.renderCountStep(data, counted);
				return;
			}

			// ── 2-bosqich: ikkala sanoq mos kelishi SHART ────────────
			if (!firstCount) {
				// Holat yo'qolgan — boshidan boshlaymiz (bu yerga tushmasligi kerak).
				this.renderCountStep(data, null);
				return;
			}

			const mismatch = Object.keys(counted).filter(
				(mode) => Math.abs(counted[mode] - flt(firstCount[mode])) > 0.004
			);

			if (mismatch.length) {
				frappe.show_alert(
					{
						message: __("Ikki sanoq mos kelmadi — qaytadan sanang"),
						indicator: "red",
					},
					7
				);
				// Boshidan: summalar KO'RSATILMAYDI, sanoq qayta boshlanadi.
				this.renderCountStep(data, null);
				$(this.el.modalBody)
					.find(".rc-pay__error")
					.text(
						__("Ikki marta kiritilgan summa bir xil bo'lishi kerak. Qaytadan sanang.")
					);
				return;
			}

			$error.text("");
			try {
				this.busy(e.currentTarget, true);
				await this.call("ozturkapp.ozturkapp.api.cashier.close_shift", {
					counted_cash: JSON.stringify(counted),
				});
				this.setModalLocked(false);
				this.closeModal();
				frappe.show_alert({ message: __("Kassa yopildi"), indicator: "green" });
				await this.boot();
			} catch (error) {
				$error.text(this.errorText(error));
				this.busy(e.currentTarget, false);
			}
		});
	}

	/** Oynani yopib bo'lmaydigan qilish (sanoq davomida). */
	setModalLocked(locked) {
		this.modalLocked = locked;
		this.$root.find(".rc-modal__close").toggle(!locked);
	}

	showModal(title, { locked = false } = {}) {
		this.setModalLocked(locked);
		this.$root.find(".rc-modal__title").text(title);
		this.el.overlay.hidden = false;
	}

	// ═══════════════════════════════════════════════════════════
	//  To'lov oynasi (TZ §22)
	// ═══════════════════════════════════════════════════════════

	openPaymentModal(detail) {
		const bill = detail.bill;
		const methods = this.ctx.payment_methods || [];
		if (!methods.length) {
			frappe.msgprint(__("POS Profile'da to'lov usuli sozlanmagan"));
			return;
		}

		const due = bill.rounded_total;
		const selected = (methods.find((m) => m.default) || methods[0]).mode_of_payment;

		this.el.modalBody.innerHTML = `
			<div class="rc-pay__due">
				<div class="rc-pay__due-label">${esc(__("To'lanadi"))}</div>
				<div class="rc-pay__due-value">${esc(this.money(due))}</div>
				<div class="rc-pay__breakdown">
					${esc(bill.invoice)} · ${esc(detail.table)} ·
					${esc(__("Oraliq"))} ${esc(this.money(bill.subtotal))}
					${
						bill.service_charge
							? ` + ${esc(bill.service_charge.description)} ${esc(
									this.money(bill.service_charge.amount)
							  )}`
							: ""
					}
				</div>
			</div>

			<div class="rc-pay__label">${esc(__("To'lov usuli"))}</div>
			<div class="rc-pay__modes">
				${methods
					.map(
						(m) =>
							`<button class="rc-mode" type="button" data-mode="${esc(
								m.mode_of_payment
							)}" aria-pressed="${m.mode_of_payment === selected}">${esc(
								m.mode_of_payment
							)}</button>`
					)
					.join("")}
			</div>

			<div class="rc-pay__label">${esc(__("Qabul qilingan summa"))}</div>
			<input class="rc-pay__input" type="number" inputmode="decimal" step="any"
				value="${due}" aria-label="${esc(__("Qabul qilingan summa"))}">
			<div class="rc-pay__quick"></div>

			<div class="rc-pay__change"><span>${esc(
				__("Qaytim")
			)}</span><span class="rc-pay__change-value">${esc(this.money(0))}</span></div>
			<div class="rc-pay__error" role="alert"></div>

			<div class="rc-actions">
				<button class="rc-btn rc-btn--pay" data-action="confirm">${esc(
					__("To'lovni tasdiqlash")
				)}</button>
			</div>`;

		// XATO EDI: `modalBody` — DOIMIY element, `innerHTML` uni almashtirsa
		// ham unga OSILGAN delegatsiyalangan ishlovchilar QOLADI. Oyna ikki
		// bosqichli bo'lgani uchun ular to'planib, bitta bosishda IKKALASI
		// ham ishlab ketardi (2-bosqich tugmasi sanoqni qaytadan boshlardi).
		// Shuning uchun har safar eski ishlovchilar yechiladi.
		const $body = $(this.el.modalBody).off();
		const $input = $body.find(".rc-pay__input");
		const $change = $body.find(".rc-pay__change");
		const $error = $body.find(".rc-pay__error");

		// Tez tanlash tugmalari — kassirning yozishini kamaytiradi.
		const suggestions = quickAmounts(due);
		$body.find(".rc-pay__quick").html(
			suggestions
				.map(
					(value) =>
						`<button class="rc-quick" type="button" data-amount="${value}">${esc(
							this.money(value)
						)}</button>`
				)
				.join("")
		);

		const recalc = () => {
			// Bu FAQAT ko'rsatish uchun. Haqiqiy tekshiruv serverda (TZ §17).
			const paid = flt($input.val());
			const change = paid - due;
			$change.toggleClass("rc-pay__change--short", change < 0);
			$change
				.find(".rc-pay__change-value")
				.text(change < 0 ? this.money(change) : this.money(change));
		};

		$body.on("click", ".rc-mode", (e) => {
			$body.find(".rc-mode").attr("aria-pressed", "false");
			e.currentTarget.setAttribute("aria-pressed", "true");
		});
		$body.on("click", ".rc-quick", (e) => {
			$input.val(e.currentTarget.dataset.amount);
			recalc();
		});
		$input.on("input", recalc);
		$body.on("click", '[data-action="confirm"]', async (e) => {
			const mode = $body.find('.rc-mode[aria-pressed="true"]').data("mode");
			const amount = flt($input.val());
			const button = e.currentTarget;

			$error.text("");
			try {
				this.busy(button, true);
				const result = await this.call(
					"ozturkapp.ozturkapp.api.billing.submit_payment",
					{
						invoice: bill.invoice,
						payments: JSON.stringify([
							{ mode_of_payment: mode, amount },
						]),
					}
				);
				this.closeModal();
				frappe.show_alert({
					message: __("To'lov qabul qilindi — {0}", [result.invoice]),
					indicator: "green",
				});
				if (result.change_amount) {
					frappe.msgprint({
						title: __("Qaytim"),
						message: __("Qaytim: {0}", [this.money(result.change_amount)]),
						indicator: "blue",
					});
				}
				await this.refreshAll();
			} catch (error) {
				// Xato bo'lsa oyna YOPILMAYDI va stol band qoladi (TZ §23).
				$error.text(this.errorText(error));
			} finally {
				this.busy(button, false);
			}
		});

		recalc();
		this.showModal(__("To'lov"));
		setTimeout(() => $input.trigger("focus").trigger("select"), 50);
	}

	closeModal() {
		// Qulflangan oyna (kassa yopish sanog'i) yopilmaydi.
		if (this.modalLocked) return;

		clearInterval(this.countdownTimer);
		this.countdownTimer = null;
		this.el.overlay.hidden = true;
		this.$root.find(".rc-modal__close").show();
		$(this.el.modalBody).off().empty();
	}

	// ═══════════════════════════════════════════════════════════
	//  Faol buyurtmalar (TZ §7)
	// ═══════════════════════════════════════════════════════════

	renderOrderTabs() {
		const tabs = [
			{ key: "all", label: __("Hammasi") },
			{ key: "open", label: __("Ochiq") },
			{ key: "billed", label: __("Hisob berilgan") },
		];
		this.el.orderTabs.innerHTML = tabs
			.map(
				(tab) =>
					`<button class="rc-tab" type="button" data-tab="${tab.key}"
						aria-pressed="${this.orderFilter === tab.key}">${esc(tab.label)}</button>`
			)
			.join("");

		$(this.el.orderTabs)
			.off("click")
			.on("click", ".rc-tab", (e) => {
				this.orderFilter = e.currentTarget.dataset.tab;
				this.renderOrderTabs();
				this.renderOrders();
			});
	}

	renderOrders() {
		let orders = this.orders || [];
		if (this.orderFilter !== "all") {
			orders = orders.filter((order) => order.status === this.orderFilter);
		}

		if (!orders.length) {
			this.el.orderList.innerHTML = `<div class="rc-empty">
				<div class="rc-empty__icon">✓</div>
				<p class="rc-empty__title">${esc(__("To'lanmagan buyurtma yo'q"))}</p>
			</div>`;
			return;
		}

		this.el.orderList.innerHTML = orders
			.map(
				(order) => `<button type="button" class="rc-order rc-order--${esc(
					order.status
				)}" data-table="${esc(order.table || "")}" data-invoice="${esc(
					order.invoice
				)}">
					<div class="rc-order__top">
						<span class="rc-order__table">${esc(order.table || order.order_type || "—")}</span>
						<span class="rc-order__amount">${esc(this.money(order.amount))}</span>
					</div>
					<div class="rc-order__meta">
						${esc(order.invoice)}<br>
						${esc(order.waiter_name || "—")} · ${cint(order.elapsed_minutes)} ${esc(__("daq"))}
						${order.customer_name ? ` · ${esc(order.customer_name)}` : ""}
					</div>
					<div class="rc-order__tags">
						<span class="rc-tag ${order.billed ? "rc-tag--billed" : ""}">${esc(
					order.status_label
				)}</span>
						${
							(order.kitchen || {}).label
								? `<span class="rc-tag">${esc(order.kitchen.label)}</span>`
								: ""
						}
					</div>
				</button>`
			)
			.join("");

		$(this.el.orderList)
			.off("click")
			.on("click", ".rc-order", (e) => {
				const { table, invoice } = e.currentTarget.dataset;

				// Olib ketish, yetkazib berish va Desktop POS'dan kelgan
				// buyurtmalarda stol BO'LMAYDI. Ilgari bunday qatorni
				// bosganda hech narsa ochilmasdi — ya'ni kassir uni ko'rib
				// tursa ham hisobini ocholmasdi va bekor ham qilolmasdi.
				if (table) this.selectTable(table);
				else if (invoice) this.selectOrder(invoice);
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
	 * Summani PROBEL bilan guruhlaydi: `1080800` -> `1 080 800`.
	 *
	 * NEGA `format_currency()` EMAS
	 * =============================
	 * U saytning raqam formatiga tayanadi (`#,###.##`) va shu saytda
	 * `лв 1,080,800.00` chiqaradi. Uchta muammo bor:
	 *
	 *   1. vergul — ingliz yozuvi; o'zbek/rus yozuvida u O'NLIK
	 *      ajratgich sifatida o'qiladi, ya'ni chalg'itadi;
	 *   2. `,00` — summalar butun so'mda, nol tiyin faqat ekranni
	 *      to'ldiradi;
	 *   3. `лв` — bolgar levi belgisi, UZS uchun noto'g'ri (Currency
	 *      yozuvida shunday sozlangan).
	 *
	 * Saytning global sozlamasini o'zgartirish butun ERPNext'ga ta'sir
	 * qiladi, shuning uchun qoida O'ZIMIZDA:
	 * `utils/money.py: format_amount()` va ofitsant ilovasidagi
	 * `Fmt.money()` — uchalasi bir xil natija berishi shart.
	 */
	money(value) {
		const number = flt(value);
		const negative = number < 0;
		const abs = Math.abs(number);

		const whole = Math.trunc(abs);
		let fraction = Math.round((abs - whole) * 100);

		// Yaxlitlash butun songa o'tkazgan bo'lishi mumkin: 1.999 -> 2,00.
		const carried = fraction >= 100;
		const grouped = String(carried ? whole + 1 : whole).replace(
			/\B(?=(\d{3})+(?!\d))/g,
			" "
		);
		if (carried) fraction = 0;

		// Tiyin FAQAT nolga teng bo'lmasa ko'rsatiladi.
		const text = fraction
			? `${grouped},${String(fraction).padStart(2, "0")}`
			: grouped;

		return negative ? `-${text}` : text;
	}

	busy(button, state) {
		if (!button) return;
		button.classList.toggle("rc-btn--busy", !!state);
		button.disabled = !!state;
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

	alertError(error) {
		frappe.show_alert({ message: this.errorText(error), indicator: "red" }, 7);
	}

	readPreference(key) {
		try {
			return localStorage.getItem(`ozturk_cashier_${key}`);
		} catch (e) {
			return null;
		}
	}

	writePreference(key, value) {
		try {
			localStorage.setItem(`ozturk_cashier_${key}`, value);
		} catch (e) {
			/* localStorage o'chirilgan bo'lishi mumkin — muhim emas */
		}
	}
};

// ═══════════════════════════════════════════════════════════════
//  Sof yordamchi funksiyalar
// ═══════════════════════════════════════════════════════════════

/** Bron vaqtini tanlash uchun ro'yxatlar — yozishdan ko'ra tanlash tezroq. */
const HOUR_OPTIONS = Array.from({ length: 24 }, (_, i) => pad2(i));
const MINUTE_OPTIONS = ["00", "15", "30", "45"];

function pad2(value) {
	return String(value).padStart(2, "0");
}

/** Hozirgi vaqtni keyingi 15 daqiqalik qadamga yaxlitlaydi. */
function defaultTimeSlot() {
	const parts = String(frappe.datetime.now_time() || "").split(":");
	let hour = cint(parts[0]);
	let minute = Math.ceil(cint(parts[1]) / 15) * 15;

	if (minute > 45) {
		minute = 0;
		hour = (hour + 1) % 24;
	}

	return { hour: pad2(hour), minute: pad2(minute) };
}

/** HTML'ga qo'yishdan oldin ekranlash — serverdan kelgan matnga ishonmaymiz. */
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

function fmtQty(value) {
	const number = flt(value);
	return Number.isInteger(number) ? String(number) : String(number);
}

/** To'lov uchun tez tanlash summalari (faqat qulaylik, hisob emas). */
function quickAmounts(due) {
	const steps = [1000, 5000, 10000, 50000, 100000];
	const values = new Set([Math.ceil(due)]);
	steps.forEach((step) => {
		const rounded = Math.ceil(due / step) * step;
		if (rounded > due) values.add(rounded);
	});
	return Array.from(values).slice(0, 4);
}
