/**
 * Yuqori panel (52px): zallar | holat filtri | smena, kassir, tez tugmalar, soat, «⋯».
 * Sig'masa kamroq muhim qismlar yig'iladi (`core/layout.js: syncCompact`).
 *
 * Restoran va filial nomi UMUMAN chizilmaydi (kassir ularni allaqachon biladi,
 * joyi esa qimmat) — faqat kassir nomining tooltip'ida.
 */

import { syncCompact } from "../core/layout.js";
import { slots } from "../core/slots.js";
import { ui } from "../kit/index.js";
import { esc } from "../util/format.js";

export class TopbarMethods {
	renderTopbar() {
		const restaurant = this.ctx.restaurant || {};
		const cashier = this.ctx.cashier || {};
		this.el.cashier.textContent = cashier.full_name || "";
		this.el.cashier.title = [restaurant.name, this.ctx.branch].filter(Boolean).join(" · ");

		const shift = this.ctx.shift || {};
		const canOperate = !!(this.ctx.permissions || {}).can_operate_shift;

		this.el.shift.className =
			"rc-shift " + (shift.open ? "rc-shift--open" : "rc-shift--closed");
		this.el.shift.textContent = shift.open ? __("Kassa ochiq") : __("Kassa yopiq");

		// Kassani ocholmaydigan foydalanuvchi (Administrator, menejer)
		// bloklovchi ekranni KO'RMAYDI — u uchun holatning yagona manbai
		// shu qizil yozuv. Kim ocha olishini ham shu yerda aytamiz, aks
		// holda u kimni chaqirishni bilmaydi.
		this.el.shift.title =
			!shift.open && !canOperate && this.ctx.shift_operators
				? __("Kassani faqat {0} ochadi.", [this.ctx.shift_operators])
				: __("Kassa smenasi");

		this.tickClock();
		this.renderQuick();
		this.fitTopbar();
	}

	/** Yuqori panel sig'maganda kamroq muhim qismlarni yig'adi (tarkib o'zgarganda chaqiriladi). */
	fitTopbar() {
		syncCompact(this.$root[0]);
	}

	// ═══════════════════════════════════════════════════════════
	//  Tez tugmalar (`topbar.quick`)
	// ═══════════════════════════════════════════════════════════

	/**
	 * `topbar.quick` tugmalarini «⋯» ning chap tomoniga chizadi.
	 *
	 * Tugma holati (`when`, `disabled`) smena, tanlov va bayroqlarga bog'liq,
	 * shuning uchun yuklash, yangilash va panel chizilganda qayta chaqiriladi.
	 * Natija o'zgarmagan bo'lsa DOM'ga TEGILMAYDI: aks holda bosilayotgan tugma
	 * ostidan almashib ketardi.
	 *
	 * `disabled` — `aria-disabled` bilan belgilanadi, HTML `disabled` bilan EMAS:
	 * o'chirilgan tugma sensorli ekranda bosishni umuman sezmaydi va kassir
	 * NEGA ishlamayotganini ko'rmaydi. Bu yerda bosilganda sabab xabar bo'lib
	 * chiqadi.
	 */
	renderQuick() {
		const host = this.el.quick;
		if (!host || !this.ctx) return;

		const items = slots.visible("topbar.quick", this);
		const html = items
			.map((item) => {
				const label = typeof item.label === "function" ? item.label(this) : item.label;
				const disabled = item.disabled ? item.disabled(this) : false;
				const icon = item.icon || String(label).trim().charAt(0).toUpperCase();
				const title = typeof disabled === "string" ? `${label} — ${disabled}` : label;

				// Yozuv <=1100px da yashiriladi; `aria-label`/`title` qoladi.
				return `<button type="button" class="rc-topquick__btn rc-topquick__btn--${esc(
					item.kind === "primary" ? "primary" : "default"
				)}" data-id="${esc(item.id)}" aria-label="${esc(label)}" title="${esc(title)}"
					${disabled ? 'aria-disabled="true"' : ""}>
					<span class="rc-topquick__icon" aria-hidden="true">${esc(icon)}</span>
					<span class="rc-topquick__label">${esc(label)}</span>
				</button>`;
			})
			.join("");

		if (this.quickHtml === html) return;
		this.quickHtml = html;
		host.innerHTML = html;
		host.hidden = !items.length;
		this.fitTopbar();
	}

	onQuickClick(button) {
		const item = slots.items("topbar.quick").find((i) => i.id === button.dataset.id);
		if (!item) return;

		const disabled = item.disabled ? item.disabled(this) : false;
		if (disabled) {
			if (typeof disabled === "string") ui.toast(disabled, { indicator: "orange" });
			return;
		}
		item.onClick(this, button);
	}

	/** Soat: `SS:DD` — kassirga soniya kerak emas, sana tooltip'da. */
	tickClock() {
		if (!this.el.clock) return;
		const now = new Date();
		this.el.clock.textContent = now.toLocaleTimeString([], {
			hour: "2-digit",
			minute: "2-digit",
			hour12: false,
		});
		this.el.clock.title = frappe.datetime.str_to_user(frappe.datetime.now_datetime());
	}

	/**
	 * Sozlash ogohlantirishlari. Bir qatorli — ish maydonini siqmaydi; kassir
	 * yopib qo'ysa shu ochilish davomida qaytib chiqmaydi.
	 */
	renderWarnings() {
		this.dismissedWarnings = this.dismissedWarnings || new Set();
		const warnings = (this.ctx.warnings || []).filter(
			(w) => !this.dismissedWarnings.has(w.message)
		);

		this.el.warnings.innerHTML = warnings
			.map(
				(w) =>
					`<div class="rc-warning"><span aria-hidden="true">⚠</span>
						<span class="rc-warning__text" title="${esc(w.message)}">${esc(w.message)}</span>
						<button type="button" class="rc-warning__x" data-message="${esc(w.message)}"
							aria-label="${esc(__("Yopish"))}">×</button></div>`
			)
			.join("");

		$(this.el.warnings)
			.off("click")
			.on("click", ".rc-warning__x", (e) => {
				this.dismissedWarnings.add(e.currentTarget.dataset.message);
				this.renderWarnings();
			});
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

		this.fitTopbar();

		$(this.el.rooms)
			.off("click")
			.on("click", ".rc-room", (e) => {
				this.room = e.currentTarget.dataset.room || null;
				this.writePreference("room", this.room || "");
				this.clearSelection();
				// Boshqa zalga o'tilganda tahrirlash rejimi ham yopiladi —
				// "Barcha zallar"da koordinata boshqacha hisoblanadi (TZ §4),
				// shu bilan sudrash matematikasi noto'g'ri bo'lib qolardi.
				this.layoutEditMode = false;
				this.renderRooms();
				this.renderFloorToolbar();
				// Xato bu yerda ko'rsatiladi: aks holda jim «unhandled rejection» bo'lib, kassir
				// yangi zal yuklanmaganini bilmasdi.
				this.refreshAll().catch((error) => this.alertError(error));
			});
	}

	// ═══════════════════════════════════════════════════════════
	//  Holat filtri (sonlar bilan)
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
						data-filter="${option.key}" aria-pressed="${this.statusFilter === option.key}"
						aria-label="${esc(option.label)}: ${cint(counts[option.key])}" title="${esc(option.label)}">
						<span class="rc-filter__mark" aria-hidden="true">${option.mark}</span>
						<span class="rc-filter__label">${esc(option.label)}</span>
						<span class="rc-filter__count">${cint(counts[option.key])}</span>
					</button>`
			)
			.join("");

		this.fitTopbar();

		// Buyurtmalar ko'rinishida holat filtri bosilsa — zal rejasiga
		// o'tamiz: filtr stollarga tegishli, o'lik tugma bo'lib qolmasin.
		$(this.el.filters)
			.off("click")
			.on("click", ".rc-filter", (e) => {
				this.statusFilter = e.currentTarget.dataset.filter;
				this.renderFilters();
				this.renderFloor();
				this.setView("floor");
			});
	}

	// ═══════════════════════════════════════════════════════════
	//  Ko'rinish: «Stollar | Buyurtmalar»
	// ═══════════════════════════════════════════════════════════

	renderViewTabs() {
		const orders = this.orders || [];
		const bells = orders.filter((order) => order.bill_requested).length;

		this.el.viewTabs.innerHTML = `
			<button type="button" class="rc-viewtab" data-view="floor" role="tab"
				aria-selected="${this.view === "floor"}">${esc(__("Stollar"))}</button>
			<button type="button" class="rc-viewtab" data-view="orders" role="tab"
				aria-selected="${this.view === "orders"}">${esc(__("Buyurtmalar"))}
				<span class="rc-viewtab__count">${cint(orders.length)}</span>
				${
					bells
						? `<span class="rc-bell rc-bell--tab" title="${esc(
								__("Hisob so'radi")
						  )}"><span aria-hidden="true">🔔</span>${cint(bells)}</span>`
						: ""
				}
			</button>`;

		$(this.el.viewTabs)
			.off("click")
			.on("click", ".rc-viewtab", (e) => this.setView(e.currentTarget.dataset.view));
	}

	setView(view) {
		this.view = view;
		this.writePreference("view", view);
		this.$root.find(".rc-left").attr("data-view", view);
		this.renderViewTabs();
		if (view === "floor") this.fitFloor();
		// Keng ekranda «Buyurtmalar» — o'ng paneldagi ro'yxat: tanlangan stol/chek bo'lsa unga qaytamiz.
		else if (this.wide && (this.selectedTable || this.selectedInvoice)) this.clearSelection();
	}
}
