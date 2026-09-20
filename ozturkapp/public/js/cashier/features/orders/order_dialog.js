/**
 * Buyurtma oynasi: kassadan yangi buyurtma ochish (`create`) va ochiq,
 * hali hisobi chiqarilmagan buyurtmaga taom qo'shish/olib tashlash (`edit`).
 *
 * BIR OYNA, IKKI REJIM
 * ====================
 * Chap tomonda menyu (kurs tugmalari, taom kartalari, qidiruv) ikkala rejimda
 * bir xil. O'ng tomonda:
 *
 *   create   buyurtma turi, stol/mehmonlar yoki yetkazish ma'lumoti, mijoz,
 *            savat, izoh -> `create_order`
 *   edit     buyurtmadagi taomlar (kamaytirish/olib tashlash -> `remove_item`),
 *            yangi taomlar savati -> `add_items`
 *
 * BIZNES MANTIQ YO'Q
 * ==================
 * Narx, xizmat haqi, soliq, stolning bo'shligi va oshxona qoidalari serverda.
 * Bu yerda faqat kiritish yig'iladi va server xabari ko'rsatiladi. Savatdagi
 * summa — menyu narxi × miqdor, «taxminiy» deb belgilangan, hech qachon
 * yuborilmaydi.
 *
 * DUBLIKATDAN HIMOYA
 * ==================
 * `create_order` uchun har urinishga `client_ref` (UUID) beriladi. Tarmoq
 * uzilib AYNI ma'lumot qayta yuborilsa o'sha `client_ref` ishlatiladi (server
 * avval yaratilgan buyurtmani qaytaradi); ma'lumot o'zgargan bo'lsa yangisi
 * olinadi — aks holda serverdagi eski buyurtma jimgina qaytib, kassir yangi
 * yozgan taomlar yo'qolardi. `add_items` ni optimistik qulf (`modified`)
 * himoya qiladi: takror yuborish «Konflikt» beradi.
 */

import { orderApi } from "./api.js";
import { Cart } from "./cart.js";
import { pickCustomer } from "./customer.js";
import { courseChoices, loadMenu, menuKey, menuParams, visibleItems } from "./menu.js";
import { askRemoval, chooseTable, editComment } from "./pickers.js";
import {
	COMMENT_LIMIT,
	DELIVERY,
	DINE_IN,
	TAKE_AWAY,
	esc,
	fmtQty,
	orderTypeLabel,
	orderTypes,
	ui,
	uuid,
} from "./shared.js";

const MAX_GUESTS = 99;

/**
 * Muvaffaqiyat xabari qisqa turadi: u ekran tagida, keyingi oynaning «Yuborish»
 * tugmasi ustida ham qolib, bosishni tutib olmasligi kerak.
 */
const TOAST_SECONDS = 3;

export class OrderDialog {
	/**
	 * @param {object} screen
	 * @param {object} options
	 * @param {"create"|"edit"} options.mode
	 * @param {string} [options.invoice]  edit rejimida — chek nomi
	 * @param {string} [options.table]    create rejimida — oldindan tanlangan (bo'sh) stol
	 */
	constructor(screen, { mode, invoice = null, table = null }) {
		this.screen = screen;
		this.mode = mode;
		this.invoice = invoice;
		this.creating = mode === "create";

		this.cart = new Cart();
		this.orderType = DINE_IN;
		this.table = table;
		this.pax = 1;
		this.customer = null;
		this.phone = "";
		this.address = "";
		this.comment = "";
		this.invalid = {};

		this.course = null;
		this.query = "";
		this.menu = { items: [], courses: [] };
		this.menuKey = "";
		this.menuSequence = 0;

		this.bill = null;
		this.lockTime = null;
		this.allTables = null;

		this.attempt = null;
		this.busy = false;
	}

	// ═══════════════════════════════════════════════════════════
	//  Ochish va yuklash
	// ═══════════════════════════════════════════════════════════

	async open() {
		this.dialog = ui.dialog({
			title: this.creating ? __("Yangi buyurtma") : __("Taom qo'shish"),
			size: "xl",
			dismissible: false,
			body: `<div class="rc-orders-loading">${esc(__("Yuklanmoqda…"))}</div>`,
			actions: [
				{ id: "cancel", label: __("Bekor qilish"), onClick: () => this.requestClose() },
				{
					id: "submit",
					label: this.submitLabel(),
					kind: "primary",
					disabled: true,
					onClick: () => this.submit(),
				},
			],
		});
		this.dialog.dialogEl.classList.add("rc-orders-dlg");
		this.addCloseButton();

		try {
			await this.load();
		} catch (error) {
			this.dialog.close(null);
			this.screen.alertError(error);
			return;
		}
		this.render();
	}

	/**
	 * Oyna `dismissible: false` — orqa fon yoki Esc bilan tasodifan yopilib,
	 * yig'ilgan savat yo'qolmasin. Yopish tugmasi (×) esa savat bo'lsa so'raydi.
	 */
	addCloseButton() {
		const button = document.createElement("button");
		button.type = "button";
		button.className = "rc-dialog__x";
		button.setAttribute("aria-label", __("Yopish"));
		button.textContent = "×";
		button.addEventListener("click", () => this.requestClose());
		this.dialog.overlay.querySelector(".rc-dialog__head").appendChild(button);
	}

	async requestClose() {
		if (this.busy) return;
		if (this.cart.size) {
			const sure = await ui.confirm({
				title: __("Oyna yopilsinmi?"),
				message: __("Tanlangan taomlar saqlanmaydi."),
				confirmLabel: __("Ha, yopish"),
				cancelLabel: __("Yo'q"),
				kind: "danger-solid",
			});
			if (!sure) return;
		}
		this.dialog.close(null);
	}

	async load() {
		const { screen } = this;

		if (this.creating) {
			// Kassir bitta zalni tanlagan bo'lsa zal rejasida boshqa zal stollari yo'q;
			// buyurtma esa har qanday bo'sh stolga ochilishi kerak.
			if (screen.room) this.allTables = (await orderApi.getFloorPlan(screen)).tables || [];

			// Bo'sh stol yo'q bo'lsa «Zalda» ni tanlatib qo'ymaymiz.
			if (!this.table && !this.freeTables().length) this.orderType = TAKE_AWAY;
		} else {
			this.bill = await orderApi.getBill(screen, this.invoice);
			this.lockTime = this.bill.modified;
			this.orderType = this.bill.order_type;
			this.table = this.bill.table;
		}

		const params = this.currentMenuParams();
		this.menu = await loadMenu(screen, params);
		this.menuKey = menuKey(params);
	}

	/** Menyu so'rovi: zalda — stolning zali, aks holda buyurtma turi. */
	currentMenuParams() {
		if (!this.creating) return menuParams(this.orderType, this.bill.room);
		return menuParams(this.orderType, this.roomOf(this.table));
	}

	tables() {
		return this.allTables || (this.screen.floor || {}).tables || [];
	}

	freeTables() {
		return this.tables().filter((table) => table.status === "AVAILABLE");
	}

	roomOf(name) {
		const row = this.tables().find((table) => table.name === name);
		return row ? row.restaurant_room : null;
	}

	/** Buyurtma hozir o'zgartirilishi mumkinmi (hisob chiqmagan, to'lanmagan, bekor emas). */
	editable() {
		const bill = this.bill;
		return !bill || (!bill.billed && !bill.paid && !bill.cancelled);
	}

	// ═══════════════════════════════════════════════════════════
	//  Karkas
	// ═══════════════════════════════════════════════════════════

	render() {
		const withComment = this.creating;
		this.dialog.bodyEl.innerHTML = `
			<div class="rc-orders-grid">
				<section class="rc-orders-menu" aria-label="${esc(__("Menyu"))}">
					<div class="rc-orders-bar">
						<input type="text" class="rc-input rc-orders-search" data-vk="text" autocomplete="off"
							placeholder="${esc(__("Qidirish"))}" aria-label="${esc(__("Taomni qidirish"))}"
							inputmode="${ui.virtualKeyboard ? "none" : "text"}">
						<div class="rc-orders-courses" role="group"></div>
					</div>
					<div class="rc-orders-items"></div>
				</section>
				<section class="rc-orders-cart" aria-label="${esc(__("Buyurtma"))}">
					<div class="rc-orders-setup"></div>
					<div class="rc-orders-existing" hidden></div>
					<div class="rc-orders-lines"></div>
					<div class="rc-orders-foot">
						${
							withComment
								? `<input type="text" class="rc-input rc-orders-comment" data-vk="text"
									autocomplete="off" maxlength="${COMMENT_LIMIT}"
									placeholder="${esc(__("Buyurtma izohi (ixtiyoriy)"))}"
									inputmode="${ui.virtualKeyboard ? "none" : "text"}">`
								: ""
						}
						<div class="rc-orders-total" title="${esc(
							__(
								"Xizmat haqi va soliq hisobga olinmagan. Aniq summani server hisoblaydi."
							)
						)}">
							<span>${esc(this.creating ? __("Taxminiy jami") : __("Yangi taomlar (taxminiy)"))}</span>
							<strong class="rc-orders-total__value">0</strong>
						</div>
					</div>
				</section>
			</div>`;

		const find = (selector) => this.dialog.bodyEl.querySelector(selector);
		this.el = {
			search: find(".rc-orders-search"),
			courses: find(".rc-orders-courses"),
			items: find(".rc-orders-items"),
			setup: find(".rc-orders-setup"),
			existing: find(".rc-orders-existing"),
			lines: find(".rc-orders-lines"),
			comment: find(".rc-orders-comment"),
			total: find(".rc-orders-total__value"),
		};

		this.bind();
		this.renderCourses();
		this.renderItems();
		this.renderSetup();
		this.renderExisting();
		this.renderLines();
		this.renderTotal();
		this.syncFooter();
	}

	bind() {
		const body = this.dialog.bodyEl;

		body.addEventListener("click", (event) => {
			const target = event.target.closest("[data-act]");
			if (target) this.act(target.dataset.act, target);
		});

		this.el.search.addEventListener("input", () => {
			this.query = this.el.search.value;
			// Qidiruv butun menyudan izlaydi — kurs tanlovi shu paytda ma'nosiz.
			if (this.query.trim()) this.course = null;
			this.renderCourses();
			this.renderItems();
		});

		if (this.el.comment) {
			this.el.comment.addEventListener("input", () => (this.comment = this.el.comment.value));
		}

		// Telefon va manzil: qayta chizilganda yo'qolmasligi uchun holatda saqlanadi.
		this.el.setup.addEventListener("input", (event) => {
			const field = event.target.dataset.field;
			if (!field) return;
			this[field] = event.target.value;
			delete this.invalid[field];
			event.target.classList.remove("rc-orders-field--invalid");
			this.dialog.setError("");
		});
	}

	act(action, target) {
		const item = target.dataset.item;
		const handlers = {
			add: () => this.addItem(item),
			inc: () => this.stepLine(item, 1),
			dec: () => this.stepLine(item, -1),
			comment: () => this.commentLine(item),
			course: () => this.setCourse(target.dataset.course || null),
			type: () => this.setType(target.dataset.type),
			table: () => this.pickTable(),
			"pax-dec": () => this.stepGuests(-1),
			"pax-inc": () => this.stepGuests(1),
			customer: () => this.pickCustomer(),
			"customer-clear": () => this.clearCustomer(),
			"ex-less": () => this.removeExisting(target.dataset.row, false),
			"ex-remove": () => this.removeExisting(target.dataset.row, true),
		};
		if (handlers[action]) handlers[action]();
	}

	// ═══════════════════════════════════════════════════════════
	//  Menyu (chap tomon)
	// ═══════════════════════════════════════════════════════════

	renderCourses() {
		const choices = courseChoices(this.menu);
		this.el.courses.hidden = !choices.length;
		const chip = (value, label, pressed) => `<button type="button" class="rc-orders-course"
			data-act="course" data-course="${esc(value)}" aria-pressed="${pressed}">${esc(label)}</button>`;

		this.el.courses.innerHTML = [
			chip("", __("Hammasi"), !this.course && !this.query.trim()),
			...choices.map((choice) =>
				chip(choice.value, choice.label, this.course === choice.value)
			),
		].join("");
	}

	setCourse(course) {
		this.course = course;
		this.query = "";
		this.el.search.value = "";
		this.renderCourses();
		this.renderItems();
		this.el.items.scrollTop = 0;
	}

	renderItems() {
		const items = visibleItems(this.menu, { course: this.course, query: this.query });
		const { screen } = this;

		if (!items.length) {
			this.el.items.innerHTML = `<div class="rc-empty"><p class="rc-empty__title">${esc(
				this.query.trim() ? __("Taom topilmadi") : __("Menyu bo'sh")
			)}</p></div>`;
			return;
		}

		this.el.items.innerHTML = items
			.map(
				(entry) => `<button type="button" class="rc-orders-card" data-act="add"
					data-item="${esc(entry.item)}" data-qty="${this.cart.qty(entry.item)}">
					<span class="rc-orders-card__qty" aria-hidden="true">${fmtQty(this.cart.qty(entry.item))}</span>
					<span class="rc-orders-card__name">${esc(entry.item_name)}</span>
					<span class="rc-orders-card__price">${esc(screen.money(entry.rate))}${
					entry.special
						? ` <span class="rc-orders-card__star" title="${esc(
								__("Maxsus taom")
						  )}" aria-label="${esc(__("Maxsus taom"))}">★</span>`
						: ""
				}</span>
				</button>`
			)
			.join("");
	}

	/** Karta ustidagi miqdor belgisini butun ro'yxatni qayta chizmasdan yangilaydi. */
	paintCard(item) {
		const qty = this.cart.qty(item);
		this.el.items.querySelectorAll(".rc-orders-card").forEach((card) => {
			if (card.dataset.item !== item) return;
			card.dataset.qty = qty;
			card.querySelector(".rc-orders-card__qty").textContent = fmtQty(qty);
		});
	}

	/** Buyurtma turi yoki stol almashganda (zal menyusi/tur menyusi) menyu qayta olinadi. */
	async reloadMenu() {
		const params = this.currentMenuParams();
		if (menuKey(params) === this.menuKey) return;

		const mine = (this.menuSequence += 1);
		this.el.items.classList.add("rc-orders-items--loading");
		try {
			const menu = await loadMenu(this.screen, params);
			if (mine !== this.menuSequence || this.dialog.closed) return;

			this.menu = menu;
			this.menuKey = menuKey(params);
			this.cart.reprice(menu.items);
			if (
				this.course &&
				!courseChoices(menu).some((choice) => choice.value === this.course)
			) {
				this.course = null;
			}
			this.renderCourses();
			this.renderItems();
			this.renderLines();
			this.renderTotal();
		} catch (error) {
			if (mine === this.menuSequence) this.dialog.setError(this.screen.errorText(error));
		} finally {
			if (mine === this.menuSequence)
				this.el.items.classList.remove("rc-orders-items--loading");
		}
	}

	addItem(item) {
		const entry = this.menu.items.find((candidate) => candidate.item === item);
		if (!entry) return;
		this.cart.add(entry);
		this.afterCartChange(item);
	}

	stepLine(item, delta) {
		this.cart.step(item, delta);
		this.afterCartChange(item, delta > 0);
	}

	afterCartChange(item, reveal = true) {
		this.dialog.setError("");
		this.paintCard(item);
		this.renderLines(reveal ? item : null);
		this.renderTotal();
		this.syncFooter();
	}

	async commentLine(item) {
		const line = this.cart.lines.get(item);
		if (!line) return;

		const comment = await editComment({ name: line.item_name, comment: line.comment });
		if (comment === null) return;
		this.cart.setComment(item, comment);
		this.renderLines();
	}

	// ═══════════════════════════════════════════════════════════
	//  Buyurtma sozlamalari (o'ng tepa)
	// ═══════════════════════════════════════════════════════════

	hasCustomerFeature() {
		return !!(this.screen.ctx.features || {}).customer_attach;
	}

	renderSetup() {
		if (!this.creating) {
			this.el.setup.innerHTML = `<div class="rc-orders-strip">
				<strong>${esc(orderTypeLabel(this.bill.order_type) || __("Buyurtma"))}</strong>
				<span>${esc(this.bill.table || this.bill.invoice)}</span>
			</div>`;
			return;
		}

		const invalid = (name) => (this.invalid[name] ? " rc-orders-field--invalid" : "");
		const types = orderTypes()
			.map(
				(type) => `<button type="button" class="rc-orders-type" data-act="type"
					data-type="${esc(type.value)}" aria-pressed="${this.orderType === type.value}">${esc(
					type.label
				)}</button>`
			)
			.join("");

		let detail = "";
		if (this.orderType === DINE_IN) {
			detail = `<div class="rc-orders-row">
				<button type="button" class="rc-orders-pick${invalid("table")}${
				this.table ? "" : " rc-orders-pick--empty"
			}" data-act="table">
					<small>${esc(__("Stol"))}</small>
					<strong>${esc(this.table || __("Tanlang"))}</strong>
				</button>
				<div class="rc-orders-stepper" role="group" aria-label="${esc(__("Mehmonlar soni"))}">
					<button type="button" data-act="pax-dec" aria-label="${esc(__("Kamaytirish"))}">−</button>
					<span><small>${esc(__("Mehmon"))}</small><strong>${this.pax}</strong></span>
					<button type="button" data-act="pax-inc" aria-label="${esc(__("Ko'paytirish"))}">+</button>
				</div>
			</div>`;
		} else if (this.orderType === DELIVERY) {
			const vk = ui.virtualKeyboard ? "none" : "";
			detail = `<div class="rc-orders-row rc-orders-row--delivery">
				<input type="text" class="rc-input rc-orders-phone${invalid(
					"phone"
				)}" data-field="phone" data-vk="tel"
					autocomplete="off" value="${esc(this.phone)}" placeholder="${esc(__("Telefon"))} *"
					aria-label="${esc(__("Telefon"))}" inputmode="${vk || "tel"}">
				<input type="text" class="rc-input rc-orders-address${invalid(
					"address"
				)}" data-field="address" data-vk="text"
					autocomplete="off" value="${esc(this.address)}" placeholder="${esc(__("Manzil"))} *"
					aria-label="${esc(__("Manzil"))}" inputmode="${vk || "text"}">
			</div>`;
		}

		const customer = this.hasCustomerFeature()
			? `<div class="rc-orders-row">
					<button type="button" class="rc-orders-pick" data-act="customer">
						<small>${esc(__("Mijoz"))}</small>
						<strong>${esc(
							this.customer
								? this.customer.customer_name || this.customer.name
								: __("Standart")
						)}</strong>
					</button>
					${
						this.customer
							? `<button type="button" class="rc-orders-clear" data-act="customer-clear"
								aria-label="${esc(__("Standart mijozga qaytish"))}">×</button>`
							: ""
					}
				</div>`
			: "";

		this.el.setup.innerHTML = `<div class="rc-orders-types" role="group">${types}</div>${detail}${customer}`;
	}

	setType(type) {
		if (!type || type === this.orderType) return;
		this.orderType = type;
		this.dialog.setError("");
		this.renderSetup();
		this.reloadMenu();
	}

	/**
	 * Zal filtri qo'yilgan bo'lsa bo'sh stollar ro'yxati `screen.floor` da emas, oyna ochilganda
	 * olingan nusxada turadi — u eskirib qolmasligi uchun stol tanlashdan oldin yangilanadi.
	 * Yangilab bo'lmasa eski ro'yxat qoladi: server band stolni baribir rad etadi.
	 */
	async refreshTables() {
		if (!this.allTables) return;
		try {
			this.allTables = (await orderApi.getFloorPlan(this.screen)).tables || [];
		} catch {
			// eski ro'yxat qoladi
		}
	}

	async pickTable() {
		await this.refreshTables();
		const table = await chooseTable({ tables: this.tables(), current: this.table });
		if (!table) return;
		this.table = table;
		delete this.invalid.table;
		this.dialog.setError("");
		this.renderSetup();
		this.reloadMenu();
	}

	stepGuests(delta) {
		this.pax = Math.min(MAX_GUESTS, Math.max(1, this.pax + delta));
		this.renderSetup();
	}

	async pickCustomer() {
		const customer = await pickCustomer(this.screen);
		if (!customer) return;
		this.customer = customer;
		// Yetkazishda mijoz telefoni tayyor bo'lsa qayta yozdirmaymiz.
		if (!this.phone.trim() && customer.mobile_number) this.phone = customer.mobile_number;
		this.renderSetup();
	}

	clearCustomer() {
		this.customer = null;
		this.renderSetup();
	}

	// ═══════════════════════════════════════════════════════════
	//  Buyurtmadagi taomlar (edit) va savat
	// ═══════════════════════════════════════════════════════════

	renderExisting() {
		const host = this.el.existing;
		host.hidden = this.creating;
		if (this.creating) return;

		const rows = this.bill.items || [];
		const editable = this.editable();

		host.innerHTML = `<div class="rc-orders-heading">${esc(__("Buyurtmada"))}</div>${
			editable
				? ""
				: `<p class="rc-hint">${esc(
						__("Hisob chiqarilgan yoki buyurtma yopilgan — taom qo'shib bo'lmaydi.")
				  )}</p>`
		}${rows
			.map((row) => {
				const kitchen = row.kitchen && row.kitchen.label;
				return `<div class="rc-orders-ex">
					<div class="rc-orders-ex__main">
						<div class="rc-orders-ex__name">${esc(row.item_name || row.item_code)}</div>
						<div class="rc-orders-ex__sub">${fmtQty(row.qty)} × ${esc(this.screen.money(row.rate))}${
					kitchen ? ` <span class="rc-tag">${esc(kitchen)}</span>` : ""
				}${row.comment ? ` · <em>${esc(row.comment)}</em>` : ""}</div>
					</div>
					${
						editable && flt(row.qty) > 1
							? `<button type="button" class="rc-btn" data-act="ex-less"
									data-row="${esc(row.name)}">${esc(__("Kamaytirish"))}</button>`
							: ""
					}
					${
						editable
							? `<button type="button" class="rc-btn rc-btn--danger" data-act="ex-remove"
									data-row="${esc(row.name)}">${esc(__("O'chirish"))}</button>`
							: ""
					}
				</div>`;
			})
			.join("")}`;
	}

	async removeExisting(rowName, whole) {
		const row = (this.bill.items || []).find((candidate) => candidate.name === rowName);
		if (!row) return;

		await askRemoval({
			row,
			whole,
			onSubmit: async (reason) => {
				const bill = await orderApi.removeItem(this.screen, {
					invoice: this.invoice,
					itemRow: row.name,
					qty: whole ? null : 1,
					reason,
				});
				this.applyBill(bill);
				const name = row.item_name || row.item_code;
				ui.toast(
					whole
						? __("{0} olib tashlandi", [name])
						: __("{0}: bitta porsiya kamaytirildi", [name]),
					{ indicator: "orange", seconds: TOAST_SECONDS }
				);
			},
		});
	}

	/** Server javobidagi yangi chek: ro'yxat va optimistik qulf shundan olinadi. */
	applyBill(bill) {
		this.bill = bill;
		this.lockTime = bill.last_modified_time || bill.modified;
		this.renderExisting();
		this.syncFooter();
		this.screen.scheduleRefresh({ floor: true, orders: true, panel: true });
	}

	renderLines(reveal = null) {
		const lines = [...this.cart.lines.values()];
		const { screen } = this;

		if (!lines.length) {
			this.el.lines.innerHTML = `<div class="rc-orders-empty">
				<span aria-hidden="true">🍽</span>
				<p>${esc(__("Chap tomondan taom tanlang"))}</p>
			</div>`;
			return;
		}

		this.el.lines.innerHTML = `${
			this.creating ? "" : `<div class="rc-orders-heading">${esc(__("Yangi taomlar"))}</div>`
		}${lines
			.map(
				(line) => `<div class="rc-orders-line" data-item="${esc(line.item)}">
					<button type="button" class="rc-orders-line__main" data-act="comment"
						data-item="${esc(line.item)}" aria-label="${esc(__("Izoh"))}">
						<span class="rc-orders-line__name">${esc(line.item_name)}</span>
						<span class="rc-orders-line__sub">${esc(screen.money(line.rate))} · ${
					line.comment
						? `<em>${esc(line.comment)}</em>`
						: `<span class="rc-orders-line__hint">＋ ${esc(__("izoh"))}</span>`
				}</span>
					</button>
					<div class="rc-orders-qty" role="group" aria-label="${esc(__("Miqdor"))}">
						<button type="button" data-act="dec" data-item="${esc(line.item)}"
							aria-label="${esc(__("Kamaytirish"))}">−</button>
						<span>${fmtQty(line.qty)}</span>
						<button type="button" data-act="inc" data-item="${esc(line.item)}"
							aria-label="${esc(__("Ko'paytirish"))}">+</button>
					</div>
				</div>`
			)
			.join("")}`;

		if (reveal) {
			const row = [...this.el.lines.querySelectorAll(".rc-orders-line")].find(
				(node) => node.dataset.item === reveal
			);
			if (row) row.scrollIntoView({ block: "nearest" });
		}
	}

	renderTotal() {
		this.el.total.textContent = this.screen.money(this.cart.estimate());
	}

	// ═══════════════════════════════════════════════════════════
	//  Yuborish
	// ═══════════════════════════════════════════════════════════

	submitLabel() {
		return this.creating ? __("Buyurtmani yuborish") : __("Buyurtmaga qo'shish");
	}

	syncFooter() {
		if (!this.dialog || this.dialog.closed) return;
		const button = this.dialog.footEl.querySelector('[data-action="submit"]');
		button.disabled = this.busy || !this.cart.size || !this.editable();
		button.textContent = this.cart.size
			? `${this.submitLabel()} · ${fmtQty(this.cart.quantity)}`
			: this.submitLabel();
	}

	setBusy(state) {
		this.busy = state;
		if (this.dialog.closed) return;
		this.dialog.setBusy(state);
		// `setBusy(false)` hamma tugmani yoqadi — savat bo'sh bo'lsa yuborish tugmasi yoqilmasin.
		if (!state) this.syncFooter();
	}

	/** Kiritishning ko'rinadigan kamchiligi (server baribir qayta tekshiradi). */
	validate() {
		this.invalid = {};
		if (!this.creating) return "";

		if (this.orderType === DINE_IN && !this.table) {
			this.invalid.table = true;
			return __("Stolni tanlang");
		}
		if (this.orderType === DELIVERY) {
			if (!this.phone.trim()) this.invalid.phone = true;
			if (!this.address.trim()) this.invalid.address = true;
			if (this.invalid.phone || this.invalid.address) {
				return __("Yetkazib berish uchun telefon va manzil kiritilishi shart");
			}
		}
		return "";
	}

	async submit() {
		if (this.busy) return;

		const problem = this.validate();
		this.renderSetup();
		if (problem) {
			this.dialog.setError(problem);
			return;
		}

		this.dialog.setError("");
		this.setBusy(true);
		let done = false;
		try {
			const bill = this.creating ? await this.createOrder() : await this.addItems();
			done = true;
			this.finish(bill);
		} catch (error) {
			this.dialog.setError(this.screen.errorText(error));
			await this.afterFailure(error);
		} finally {
			if (!done) this.setBusy(false);
		}
	}

	orderArgs() {
		const dine = this.orderType === DINE_IN;
		return {
			orderType: this.orderType,
			items: this.cart.payload(),
			table: dine ? this.table : null,
			customer: this.customer ? this.customer.name : null,
			pax: dine ? this.pax : null,
			comments: this.comment.trim(),
			delivery:
				this.orderType === DELIVERY
					? { phone: this.phone.trim(), address: this.address.trim() }
					: null,
		};
	}

	/**
	 * Har urinishga `client_ref`. Ma'lumot o'zgarmagan qayta urinishda o'sha
	 * ref ishlatiladi (tarmoq uzilgan bo'lsa buyurtma serverda allaqachon
	 * yaratilgan bo'lishi mumkin), o'zgargan bo'lsa — yangisi.
	 */
	clientRefFor(args) {
		const key = JSON.stringify(args);
		if (!this.attempt || this.attempt.key !== key) this.attempt = { key, ref: uuid() };
		return this.attempt.ref;
	}

	createOrder() {
		const args = this.orderArgs();
		return orderApi.createOrder(this.screen, { ...args, clientRef: this.clientRefFor(args) });
	}

	addItems() {
		return orderApi.addItems(this.screen, {
			invoice: this.invoice,
			items: this.cart.payload(),
			lastModifiedTime: this.lockTime,
		});
	}

	/**
	 * Server qabul qildi: oyna yopiladi, ekran yangilanadi. Yangilash yiqilsa
	 * (tarmoq) buyurtma baribir saqlangan — xato ko'rsatiladi, realtime esa
	 * ekranni o'zi to'g'rilaydi.
	 */
	finish(bill) {
		const { screen } = this;
		const quantity = this.cart.quantity;
		this.dialog.close(bill);

		if (this.creating) {
			ui.toast(
				__("Buyurtma yuborildi: {0}", [
					bill.table || orderTypeLabel(bill.order_type) || bill.invoice,
				]),
				{ seconds: TOAST_SECONDS }
			);
			this.showCreated(bill).catch((error) => screen.alertError(error));
		} else {
			ui.toast(__("Buyurtmaga {0} ta porsiya qo'shildi", [fmtQty(quantity)]), {
				seconds: TOAST_SECONDS,
			});
			screen
				.refresh({ floor: true, orders: true, panel: true })
				.catch((error) => screen.alertError(error));
		}
	}

	/** Yangi buyurtmani ekranda tanlab ko'rsatadi. */
	async showCreated(bill) {
		const { screen } = this;
		await screen.refresh({ floor: true, orders: true });
		if (bill.table) {
			await screen.selectTable(bill.table);
		} else {
			// Stolsiz buyurtma zal rejasida ko'rinmaydi — ro'yxatdan ochiladi.
			screen.setView("orders");
			await screen.selectOrder(bill.invoice);
		}
	}

	/**
	 * Yuborish yiqilgach: stol band bo'lib qolgan bo'lishi mumkin (zal rejasi
	 * yangilanadi), tahrirda esa chek boshqa joyda o'zgargan bo'lishi mumkin —
	 * yangi holat va qulf qayta olinadi, savat saqlanadi. Javob umuman
	 * kelmagan bo'lsa (tarmoq) qulf ATAYLAB eskisi qoladi: qayta yuborish
	 * o'zgarmagan chekka ikkinchi marta qo'shib yubormaydi.
	 */
	async afterFailure(error) {
		if (this.creating) {
			this.screen.scheduleRefresh({ floor: true });
			await this.refreshTables();
			return;
		}
		if (!error || error.status === 0) return;

		try {
			this.bill = await orderApi.getBill(this.screen, this.invoice);
			this.lockTime = this.bill.modified;
			this.renderExisting();
		} catch {
			// Asl xato allaqachon ko'rsatilgan; bu yerda qo'shimcha xabar kerak emas.
		}
	}
}
