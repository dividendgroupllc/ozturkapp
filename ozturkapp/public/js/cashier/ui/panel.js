/**
 * Tanlangan stol/buyurtma paneli (TZ §21).
 *
 * TUZILISHI
 * =================
 *   .rc-panel__head  — QOTIRILGAN: stol, holat, katta jami, o'tgan vaqt
 *   .rc-panel__info  — QOTIRILGAN: 1-2 qatorli ma'lumot bloklari (`panel.info`);
 *                      bo'sh bo'lsa balandligi 0
 *   .rc-panel__body  — ICHKI aylanadigan: taomlar, soliqlar, izohlar
 *   .rc-panel__foot  — QOTIRILGAN: asosiy tugmalar HAR DOIM ko'rinadi
 *
 * Tugmalar `panel.primary` (katta, 2 tagacha), `panel.secondary` (qator) va
 * `panel.more` («Yana ⋯» varag'i) slotlaridan chiziladi. Asosiy tugmalar pastda
 * shu fayl oxirida qo'shilgan; funksiya modullari o'zlariniki `order` bilan
 * orasiga qo'shadi.
 */

import { slots } from "../core/slots.js";
import { ui } from "../kit/index.js";
import { elapsedHtml, esc, fmtQty, hhmm, orderTypeLabel, taxLabel } from "../util/format.js";

/**
 * Taomlar ro'yxatiga HAR DOIM qoladigan eng kam balandlik (px). Ma'lumot
 * bloklari (`panel.info`) shu chegaradan oshsa «+N yana» qatoriga yig'iladi:
 * kassir hisobni ko'rishdan to'xtamasligi kerak.
 */
const PANEL_MIN_LIST = 120;

/** Panel qaysi ko'rinishda: issue | bill | reserved | available. */
function panelKind(detail) {
	if (detail.issue) return "issue";
	if (detail.status === "OCCUPIED" && detail.bill) return "bill";
	if (detail.status === "RESERVED") return "reserved";
	return "available";
}

/**
 * To'lanadigan summa. Server `payable` bersa (chegirma/choychaqa hisobga
 * olingan) shu, aks holda `rounded_total`. Frontend hech narsani hisoblamaydi.
 */
export function dueOf(bill) {
	return bill.payable !== undefined && bill.payable !== null
		? flt(bill.payable)
		: flt(bill.rounded_total);
}

export class PanelMethods {
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
		this.detail = null;
		this.infoOpen = null;
		$(this.el.canvas)
			.find(".rc-table")
			.each((_, node) => node.setAttribute("aria-pressed", "false"));
		this.renderQuick();
		this.renderIdlePanel();
		this.syncOrderSelection();
	}

	/**
	 * Hech narsa tanlanmagan panel. Keng ekranda bo'sh joy o'rniga faol buyurtmalar
	 * ro'yxati (`renderWorklistPanel`); tor ekranda ro'yxat chap ustunda, panelda
	 * esa qisqa yo'l-yo'riq.
	 */
	renderIdlePanel() {
		if (this.wide) {
			this.renderWorklistPanel();
			return;
		}
		this.worklistOpen = false;
		this.panelOrders = null;
		this.el.panel.innerHTML = `<div class="rc-empty rc-empty--panel">
			<div class="rc-empty__icon">👆</div>
			<p class="rc-empty__title">${esc(__("Stolni tanlang"))}</p>
			<p class="rc-empty__hint">${esc(__("Zal rejasidan stolni bosing yoki «Buyurtmalar» dan chekni oching."))}</p>
		</div>`;
	}

	panelError(error) {
		this.detail = null;
		this.worklistOpen = false;
		this.panelOrders = null;
		this.renderQuick();
		this.el.panel.innerHTML = `<div class="rc-empty rc-empty--panel">
			<div class="rc-empty__icon">⚠</div>
			<p class="rc-empty__title">${esc(__("Ma'lumot yuklanmadi"))}</p>
			<p class="rc-empty__hint">${esc(this.errorText(error))}</p>
		</div>`;
	}

	async loadTableDetail(table) {
		// Panel so'rovlari tartibsiz qaytishi mumkin (realtime yangilash + kassirning
		// o'z tanlovi): faqat OXIRGI so'rov javobi chiziladi.
		const mine = (this.detailRequest = (this.detailRequest || 0) + 1);
		try {
			const detail = await this.call("ozturkapp.ozturkapp.api.table.get_table_detail", {
				table,
			});
			if (this.selectedTable !== table || mine !== this.detailRequest) return; // kassir boshqasini tanlab ulgurdi
			this.renderPanel(detail);
		} catch (error) {
			if (this.selectedTable !== table || mine !== this.detailRequest) return;
			this.panelError(error);
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
		const mine = (this.detailRequest = (this.detailRequest || 0) + 1);

		try {
			const bill = await this.call("ozturkapp.ozturkapp.api.order.get_order_bill_preview", {
				order: invoice,
			});
			if (this.selectedInvoice !== invoice || mine !== this.detailRequest) return; // kassir boshqasini tanladi

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
			if (this.selectedInvoice !== invoice || mine !== this.detailRequest) return;
			this.panelError(error);
		}
	}

	/** Buyurtmalar ro'yxatidagi shu chekning qatori (o'tgan vaqt, hisob so'rovi uchun). */
	orderRow(invoice) {
		return (this.orders || []).find((order) => order.invoice === invoice) || null;
	}

	renderPanel(detail) {
		const labels = {
			AVAILABLE: __("Bo'sh"),
			RESERVED: __("Bron qilingan"),
			OCCUPIED: __("Band"),
		};
		const kind = panelKind(detail);
		detail.kind = kind;

		// Funksiya modullari (`panel.*` slotlari, klaviatura yorliqlari) oxirgi
		// chizilgan tafsilotga `screen.detail` orqali murojaat qiladi.
		this.detail = detail;

		// Realtime qamrovini aniqlash uchun joriy chekni eslab qolamiz.
		this.selectedInvoice = (detail.bill && detail.bill.invoice) || null;
		this.worklistOpen = false;
		this.panelOrders = null;

		let body = "";
		if (kind === "issue") {
			// Jim bo'sh hisob KO'RSATILMAYDI — muammo aniq aytiladi (TZ §8).
			body = this.issueHtml(detail);
		} else if (kind === "bill") {
			body = this.billHtml(detail);
		} else if (kind === "reserved") {
			body = this.reservationHtml(detail);
		} else {
			body = this.availableHtml(detail);
		}

		// Stolsiz buyurtmada (olib ketish / Desktop POS) sarlavha stol nomi
		// emas, buyurtma turi bo'ladi — «o'rin» soni ham ma'nosiz.
		const bill = detail.bill || {};
		const title = detail.table || orderTypeLabel(bill.order_type) || __("Stolsiz buyurtma");
		const subtitle = detail.table
			? `${esc(detail.room || "")} · ${cint(detail.seats)} ${esc(__("o'rin"))}${
					detail.is_merged ? ` · ${esc(__("birlashtirilgan"))}` : ""
			  }`
			: esc(bill.invoice || "");

		const row = kind === "bill" ? this.orderRow(bill.invoice) : null;
		const elapsed = row ? elapsedHtml(row.elapsed_minutes, this.ordersLoadedAt) : "";
		const billRequested = !!(row && row.bill_requested) || !!bill.bill_requested;

		this.el.panel.innerHTML = `
			<div class="rc-panel__head">
				<div class="rc-panel__row">
					<div class="rc-panel__lead">
						<button type="button" class="rc-panel__back" data-action="panel-back"
							aria-label="${esc(__("Buyurtmalar ro'yxatiga qaytish"))}"
							title="${esc(__("Buyurtmalar ro'yxatiga qaytish"))}"><span aria-hidden="true">‹</span></button>
						<div class="rc-panel__titles">
							<div class="rc-panel__table">${esc(title)}</div>
							<div class="rc-panel__sub">${subtitle}</div>
						</div>
					</div>
					<div class="rc-panel__badges">
						${
							billRequested
								? `<span class="rc-bell rc-bell--panel"><span aria-hidden="true">🔔</span>${esc(
										__("Hisob so'radi")
								  )}</span>`
								: ""
						}
						<span class="rc-chip rc-chip--${esc(detail.status)}">${esc(labels[detail.status])}</span>
					</div>
				</div>
				${
					kind === "bill"
						? `<div class="rc-panel__total">
								<span class="rc-panel__total-label">${esc(__("Jami"))}${elapsed ? ` · ${elapsed}` : ""}</span>
								<span class="rc-panel__total-value">${esc(this.money(bill.rounded_total))}</span>
							</div>`
						: ""
				}
			</div>
			<div class="rc-panel__info" hidden></div>
			<div class="rc-panel__body">${body}</div>
			${this.actionsHtml(detail)}`;

		this.bindPanel(detail);
		this.mountPanelInfo(detail);
		this.renderQuick();
		this.syncOrderSelection();
	}

	// ═══════════════════════════════════════════════════════════
	//  Ma'lumot bloklari (`panel.info`)
	// ═══════════════════════════════════════════════════════════

	/**
	 * `panel.info` bloklarini sarlavha bilan ro'yxat orasiga qo'yadi.
	 *
	 * Har `renderPanel` da qayta chiziladi. Bitta modulning `render` xatosi
	 * paneldan qolgan hamma narsani buzmasligi kerak — xatoli blok tashlab
	 * ketiladi. Blok bo'sh (`null`/`""`) qaytarsa ham chizilmaydi: shunda
	 * ma'lumot yo'q holatda ham balandlik 0 bo'ladi.
	 */
	mountPanelInfo(detail) {
		const info = this.el.panel.querySelector(".rc-panel__info");
		if (!info) return;

		const blocks = [];
		slots.visible("panel.info", this, detail).forEach((item) => {
			let content;
			try {
				content = item.render(this, detail);
			} catch (error) {
				console.error(`panel.info: '${item.id}' xato berdi`, error);
				return;
			}
			if (!content) return;

			const block = document.createElement("div");
			block.className = "rc-info-block";
			block.dataset.info = item.id;
			if (typeof content === "string") block.innerHTML = content;
			else block.appendChild(content);
			blocks.push(block);
		});

		// Ochilgan «+N» holati faqat SHU stol/chek uchun eslab qolinadi.
		const key = detail.table || (detail.bill || {}).invoice || "";
		if (this.infoOpen && this.infoOpen.key !== key) this.infoOpen = null;

		if (!blocks.length) {
			info.hidden = true;
			info.textContent = "";
			return;
		}

		const toggle = document.createElement("button");
		toggle.type = "button";
		toggle.className = "rc-info__toggle";
		toggle.hidden = true;
		toggle.addEventListener("click", () => {
			const open = !(this.infoOpen && this.infoOpen.open);
			this.infoOpen = { key, open };
			this.fitPanelInfo();
		});

		info.textContent = "";
		info.append(...blocks, toggle);
		info.dataset.key = key;
		info.hidden = false;
		this.fitPanelInfo();
	}

	/**
	 * Ma'lumot bloklarini mavjud joyga sig'diradi.
	 *
	 * Byudjet = panel balandligi - sarlavha - pastki tugmalar - `PANEL_MIN_LIST`.
	 * Sig'gan bloklar ko'rinib turadi, qolganlari bitta «+N yana» qatoriga
	 * yig'iladi; qator bosilsa hammasi ochiladi (byudjet ichida ichkarida
	 * aylanadi) va «Yig'ish» bilan qaytariladi. Panel o'lchami o'zgarganda
	 * (`ResizeObserver`) qayta chaqiriladi.
	 */
	fitPanelInfo() {
		const panel = this.el.panel;
		const info = panel.querySelector(".rc-panel__info");
		if (!info || info.hidden) return;

		const blocks = [...info.querySelectorAll(":scope > .rc-info-block")];
		const toggle = info.querySelector(":scope > .rc-info__toggle");
		const head = panel.querySelector(".rc-panel__head");
		const foot = panel.querySelector(".rc-panel__foot");
		if (!blocks.length || !toggle) return;

		// Avval hammasini ochib, tabiiy balandliklarni o'lchaymiz.
		blocks.forEach((block) => (block.hidden = false));
		info.style.maxHeight = "";
		toggle.hidden = false;
		toggle.textContent = "+9";
		const toggleHeight = toggle.offsetHeight;
		toggle.hidden = true;

		const budget =
			panel.clientHeight -
			(head ? head.offsetHeight : 0) -
			(foot ? foot.offsetHeight : 0) -
			PANEL_MIN_LIST;
		const heights = blocks.map((block) => block.offsetHeight);
		const total = heights.reduce((sum, height) => sum + height, 0);

		if (total <= budget) return; // hammasi sig'di — «+N» kerak emas

		const open = !!(this.infoOpen && this.infoOpen.open);
		toggle.hidden = false;

		if (open) {
			toggle.textContent = __("Yig'ish");
			toggle.setAttribute("aria-expanded", "true");
			info.style.maxHeight = `${Math.max(budget, toggleHeight)}px`;
			return;
		}

		let used = 0;
		let visible = 0;
		for (let i = 0; i < blocks.length; i += 1) {
			const rest = blocks.length - i - 1;
			if (used + heights[i] + (rest ? toggleHeight : 0) > budget) break;
			used += heights[i];
			visible = i + 1;
		}

		blocks.forEach((block, i) => (block.hidden = i >= visible));
		toggle.textContent = __("+{0} yana", [blocks.length - visible]);
		toggle.setAttribute("aria-expanded", "false");
	}

	// ═══════════════════════════════════════════════════════════
	//  «Yana ⋯» varag'i (`panel.more`)
	// ═══════════════════════════════════════════════════════════

	/**
	 * `panel.more` amallarini katta qatorli varaqda ko'rsatadi.
	 *
	 * Qator bosilganda varaq YOPILADI va amal shundan keyin ishga tushadi —
	 * amal o'z oynasini (forma, PIN) ochsa, u varaq ustiga emas, o'zi turadi.
	 * `button` sifatida panel tagidagi «Yana ⋯» tugmasi uzatiladi: u varaqdan
	 * tashqarida turadi, shuning uchun `screen.busy(button, true)` ko'rinib turadi.
	 */
	openMoreSheet(detail, button) {
		const items = slots.visible("panel.more", this, detail);
		if (!items.length) return null;

		const list = document.createElement("div");
		list.className = "rc-more";
		list.innerHTML = items
			.map((item) => {
				const label =
					typeof item.label === "function" ? item.label(this, detail) : item.label;
				const disabled = item.disabled ? item.disabled(this, detail) : false;

				// Sabab matni qator ostida turadi: sensorli ekranda `title` ko'rinmaydi.
				return `<button type="button" class="rc-more__item rc-more__item--${esc(
					item.kind || "default"
				)}" data-id="${esc(item.id)}" ${disabled ? "disabled" : ""}>
					<span class="rc-more__label">${esc(label)}</span>
					${typeof disabled === "string" ? `<span class="rc-more__reason">${esc(disabled)}</span>` : ""}
				</button>`;
			})
			.join("");

		const sheet = ui.dialog({
			title: __("Yana amallar"),
			subtitle: detail.table || (detail.bill || {}).invoice || "",
			size: "sm",
			body: list,
		});

		list.addEventListener("click", (event) => {
			const row = event.target.closest(".rc-more__item");
			if (!row || row.disabled) return;

			const item = items.find((i) => i.id === row.dataset.id);
			sheet.close(null);
			if (item) item.onClick(this, detail, button);
		});

		return sheet;
	}

	/** Ma'lumot nomuvofiqligi — buyurtma topilmadi va h.k. (TZ §8). */
	issueHtml(detail) {
		const issue = detail.issue || {};

		// Xato kodi kassirga kerak emas — yordam so'ralganda ko'rsatish uchun
		// yig'iladigan bo'limda turadi.
		return `
			<div class="rc-issue" role="alert">
				<div class="rc-issue__icon" aria-hidden="true">⚠</div>
				<p class="rc-issue__title">${esc(__("Buyurtma topilmadi"))}</p>
				<p class="rc-issue__text">${esc(issue.message || "")}</p>
				<details class="rc-issue__details">
					<summary>${esc(__("Tafsilotlar"))}</summary>
					<p class="rc-issue__code">${esc(issue.code || "")}</p>
				</details>
			</div>`;
	}

	availableHtml(detail) {
		return `<div class="rc-empty rc-empty--panel">
			<div class="rc-empty__icon">🍽</div>
			<p class="rc-empty__title">${esc(__("Stol bo'sh"))}</p>
		</div>`;
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
				)}</div><div class="rc-fact__value">${esc(hhmm(r.from_time))}${
			r.to_time ? "–" + esc(hhmm(r.to_time)) : ""
		}</div></div>
				<div class="rc-fact"><div class="rc-fact__label">${esc(
					__("Bron holati")
				)}</div><div class="rc-fact__value">${esc(r.status || "—")}</div></div>
			</div>
			${r.notes ? `<p class="rc-hint">${esc(r.notes)}</p>` : ""}`;
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
								<div class="rc-item__qty">${fmtQty(item.qty)} × ${esc(this.money(item.rate))}</div>
							</div>
							<div class="rc-item__amount">${esc(this.money(item.amount))}</div>
							${item.comment ? `<div class="rc-item__note">${esc(item.comment)}</div>` : ""}
						</div>`
					)
					.join("")
			: `<div class="rc-empty rc-empty--inline">
					<p class="rc-empty__title">${esc(__("Taom qo'shilmagan"))}</p>
				</div>`;

		// Soliq/yig'im qatorlari serverdan tayyor keladi — bu yerda hisob yo'q.
		const taxes = (bill.taxes || [])
			.map(
				(tax) => `<div class="rc-total ${tax.is_service_charge ? "rc-total--service" : ""}">
					<span>${esc(taxLabel(tax))}</span>
					<span>${esc(this.money(tax.amount))}</span>
				</div>`
			)
			.join("");

		// Bekor qilish qoidasi SERVERDA hisoblanadi (`utils/order_cancel.py`).
		// Bu yerda faqat chiziladi — tugmani DevTools'dan yoqib qo'yish ham
		// hech narsa bermaydi, server o'sha qoidani qayta qo'llaydi.
		const cancellation = bill.cancellation || {};

		// Kassir hamma narsani bir qatorda ko'radi; bo'sh qiymatlar tushib qoladi.
		const meta = [
			bill.waiter_name ? `${__("Ofitsant")}: ${bill.waiter_name}` : "",
			cint(bill.pax) ? `${__("Mehmonlar")}: ${cint(bill.pax)}` : "",
			bill.customer_name ? `${__("Mijoz")}: ${bill.customer_name}` : "",
		].filter(Boolean);

		return `
			${
				meta.length
					? `<div class="rc-meta">${meta
							.map((m) => `<span>${esc(m)}</span>`)
							.join("")}</div>`
					: ""
			}
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
				<div class="rc-total"><span>${esc(__("Umumiy"))}</span><span>${esc(
			this.money(bill.subtotal)
		)}</span></div>
				${
					bill.discount
						? `<div class="rc-total"><span>${esc(__("Chegirma"))}</span><span>−${esc(
								this.money(bill.discount)
						  )}</span></div>`
						: ""
				}
				${taxes}
				${
					flt(bill.tip) > 0
						? `<div class="rc-total"><span>${esc(__("Choychaqa"))}</span><span>${esc(
								this.money(bill.tip)
						  )}</span></div>`
						: ""
				}
			</div>

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
					? `<div class="rc-other-orders">
							<p class="rc-hint">⚠ ${esc(
								__(
									"Bu stolda yana {0} ta to'lanmagan hisob bor — to'lash uchun tanlang:"
								).replace("{0}", detail.other_orders.length)
							)}</p>
							${detail.other_orders
								.map(
									(
										order
									) => `<button class="rc-btn rc-other-orders__item" type="button"
										data-action="open-other-order" data-invoice="${esc(order.invoice)}">
										<span>${esc(order.invoice)}</span>
										<span>${esc(this.money(order.amount))}</span>
									</button>`
								)
								.join("")}
						</div>`
					: ""
			}`;
	}

	/** Pastdagi qotirilgan tugmalar — slotlardan. */
	actionsHtml(detail) {
		const button = (item) => {
			const label = typeof item.label === "function" ? item.label(this, detail) : item.label;
			const disabled = item.disabled ? item.disabled(this, detail) : false;
			return `<button type="button" class="rc-btn rc-btn--${esc(item.kind || "default")}"
				data-action="${esc(item.id)}" ${disabled ? "disabled" : ""}
				${typeof disabled === "string" ? `title="${esc(disabled)}"` : ""}>${esc(label)}</button>`;
		};

		const primary = slots.visible("panel.primary", this, detail);
		const secondary = slots.visible("panel.secondary", this, detail);
		const hasMore = slots.visible("panel.more", this, detail).length > 0;

		// «Yana ⋯» ikkinchi qatorning OXIRIDA turadi: yadro tugmalari
		// (Bo'lish / Chop / Bekor) joyida qoladi, qo'shimcha qator ochilmaydi.
		const moreButton = `<button type="button" class="rc-btn rc-btn--more" data-action="panel-more"
			aria-haspopup="dialog" aria-label="${esc(__("Yana amallar"))}"><span class="rc-btn__label">${esc(
			__("Yana")
		)}</span> ⋯</button>`;
		const secondaryClass = [
			"rc-panel__secondary",
			hasMore ? "rc-panel__secondary--more" : "",
			hasMore && !secondary.length ? "rc-panel__secondary--only-more" : "",
		].join(" ");

		return `<div class="rc-panel__foot">
			${
				primary.length
					? `<div class="rc-panel__primary" style="--rc-cols:${Math.min(
							primary.length,
							2
					  )}">${primary.map(button).join("")}</div>`
					: ""
			}
			${
				secondary.length || hasMore
					? `<div class="${secondaryClass}" style="--rc-cols:${Math.min(
							secondary.length,
							4
					  )}">${secondary.map(button).join("")}${hasMore ? moreButton : ""}</div>`
					: ""
			}
		</div>`;
	}

	bindPanel(detail) {
		const items = [...slots.items("panel.primary"), ...slots.items("panel.secondary")];

		$(this.el.panel)
			.off("click")
			.on("click", "[data-action]", (e) => {
				const button = e.currentTarget;
				const action = button.dataset.action;

				if (action === "panel-back") {
					this.clearSelection();
					return;
				}
				if (action === "open-other-order") {
					this.selectOrder(button.dataset.invoice);
					return;
				}
				if (action === "panel-more") {
					this.openMoreSheet(detail, button);
					return;
				}

				const item = items.find((i) => i.id === action);
				if (item) item.onClick(this, detail, button);
			});
	}

	/** Klaviatura yorliqlari uchun: paneldagi (faol) tugmani bosadi. */
	triggerAction(id) {
		const button = this.el.panel.querySelector(`[data-action="${id}"]`);
		if (button && !button.disabled) button.click();
	}
}

// ═══════════════════════════════════════════════════════════════
//  Asosiy tugmalar
// ═══════════════════════════════════════════════════════════════

const isKind = (kind) => (screen, detail) => panelKind(detail) === kind;

// Bo'sh stol
slots.contribute("panel.primary", {
	id: "reserve",
	order: 10,
	kind: "primary",
	label: __("Bron qilish"),
	when: isKind("available"),
	onClick: (screen, detail) => screen.reserveTable(detail),
});

// Bron qilingan stol
slots.contribute("panel.primary", {
	id: "unreserve",
	order: 10,
	kind: "danger",
	label: __("Bronni bekor qilish"),
	when: isKind("reserved"),
	onClick: (screen, detail) => screen.cancelReservation(detail),
});

// Buzilgan holat
slots.contribute("panel.primary", {
	id: "reload",
	order: 10,
	label: __("Qayta yuklash"),
	when: isKind("issue"),
	onClick: (screen) => screen.refreshAll().catch((error) => screen.alertError(error)),
});

// Band stol: «Hisob berish» — chek chop etiladi va mijozga beriladi;
// «To'lov» shundan keyin ochiladi.
slots.contribute("panel.primary", {
	id: "give-bill",
	order: 10,
	kind: "primary",
	label: (screen, detail) => (detail.bill.billed ? __("Hisob berilgan") : __("Hisob berish")),
	when: isKind("bill"),
	disabled: (screen, detail) => !(detail.bill.item_count > 0 && !detail.bill.billed),
	onClick: (screen, detail, button) => screen.giveBill(detail, button),
});

slots.contribute("panel.primary", {
	id: "pay",
	order: 20,
	kind: "pay",
	label: (screen, detail) => `${__("To'lov")} · ${screen.money(detail.bill.rounded_total)}`,
	when: isKind("bill"),
	disabled: (screen, detail) => !(detail.bill.item_count > 0 && detail.bill.billed),
	onClick: (screen, detail) => screen.openPaymentModal(detail),
});

slots.contribute("panel.secondary", {
	id: "split-bill",
	order: 10,
	label: __("Bo'lish"),
	when: (screen, detail) => {
		const bill = detail.bill;
		return (
			panelKind(detail) === "bill" &&
			!!screen.ctx.enable_bill_split &&
			!bill.paid &&
			!bill.cancelled &&
			bill.item_count > 1
		);
	},
	onClick: (screen, detail) => screen.openSplitModal(detail),
});

slots.contribute("panel.secondary", {
	id: "reprint",
	order: 20,
	label: __("Chop"),
	when: isKind("bill"),
	disabled: (screen, detail) => (detail.bill.billed ? false : __("Avval hisobni bering")),
	onClick: (screen, detail) => screen.printReceipt(detail),
});

// Bekor qilish tugmasi — uch xil ko'rinishi bor va uchalasi ham SERVER
// aytgan holatga tayanadi (`bill.cancellation`):
//
//   oshxona boshlamagan          -> oddiy bekor qilish (har qanday kassir)
//   boshlagan + menejer          -> «Majburan bekor»
//   boshlagan + oddiy kassir     -> o'chirilgan tugma + sabab
//
// Tugma butunlay yashirilmaydi: kassir NEGA bekor qilolmayotganini ko'rishi
// kerak, aks holda u menejerni chaqirish o'rniga sahifani qayta yuklab vaqt
// yo'qotadi. To'langan yoki allaqachon bekor qilingan chekda tugma keraksiz.
const cancellationOf = (detail) => (detail.bill || {}).cancellation;

slots.contribute("panel.secondary", {
	id: "cancel-order",
	order: 90,
	kind: "danger",
	when: (screen, detail) => {
		const cancellation = cancellationOf(detail);
		return (
			panelKind(detail) === "bill" &&
			!!cancellation &&
			(cancellation.allowed || !!cancellation.kitchen_started)
		);
	},
	label: (screen, detail) =>
		cancellationOf(detail).allowed && cancellationOf(detail).requires_supervisor
			? __("Majburan bekor")
			: __("Bekor qilish"),
	disabled: (screen, detail) =>
		cancellationOf(detail).allowed ? false : cancellationOf(detail).blocked_reason || true,
	onClick: (screen, detail, button) => screen.cancelOrder(detail, button),
});

// Faqat buzilgan holat (`STALE_OCCUPIED_FLAG`) va faqat menejerga — busiz
// bunday stolni hech qachon tozalab bo'lmaydi.
slots.contribute("panel.secondary", {
	id: "release",
	order: 10,
	kind: "danger",
	label: __("Stolni bo'shatish"),
	when: (screen, detail) =>
		panelKind(detail) === "issue" &&
		detail.issue.code === "STALE_OCCUPIED_FLAG" &&
		!!screen.ctx.permissions.is_supervisor,
	onClick: (screen, detail) => screen.releaseTable(detail),
});
