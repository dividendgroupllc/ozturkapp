/**
 * Faol buyurtmalar ro'yxati (TZ §7) — ixcham qatorlar.
 *
 * Tartib: «hisob so'radi» birinchi (kassir avval shuni bajaradi), keyin eng
 * uzoq kutganlari. Filtr tugmalari `orders.tabs` slotidan kengaytiriladi:
 *
 *     { id, label, order, filter(order) -> bool, badge(orders) -> number }
 *
 * RO'YXAT QAYERDA CHIZILADI. Keng ekranda (`screen.wide`) va hech narsa tanlanmaganda
 * ro'yxat O'NG PANELDA turadi (`renderWorklistPanel`) — stol yoki qator tanlansa panel
 * hisobga almashadi, `‹` tugmasi ro'yxatga qaytaradi. Tor ekranda ro'yxat chap ustundagi
 * «Buyurtmalar» ko'rinishida (yuqori paneldagi almashtirgich bilan). Qatorlar, tartib
 * va filtrlar ikkala joyda BIR XIL.
 */

import { slots } from "../core/slots.js";
import { elapsedHtml, esc, orderTypeLabel } from "../util/format.js";

const BASE_TABS = [
	{ id: "all", label: __("Hammasi"), filter: () => true },
	{ id: "open", label: __("Ochiq"), filter: (order) => order.status === "open" },
	{ id: "billed", label: __("Hisob berilgan"), filter: (order) => order.status === "billed" },
];

export class OrdersMethods {
	orderTabs() {
		return [...BASE_TABS, ...slots.visible("orders.tabs", this.orders || [])];
	}

	/**
	 * Ro'yxat hozir qaysi joyga chiziladi: bo'sh o'ng panelga (keng ekran) yoki
	 * chap ustundagi ko'rinishga (tor ekran, yoki panel hisob bilan band).
	 */
	ordersHosts() {
		if (this.worklistOpen && this.panelOrders) return this.panelOrders;
		return { tabs: this.el.orderTabs, list: this.el.orderList };
	}

	renderOrderTabs() {
		const orders = this.orders || [];
		const tabs = this.orderTabs();
		const host = this.ordersHosts().tabs;

		// Tanlangan filtr (masalan o'chirilgan funksiya) endi yo'q bo'lsa — «Hammasi».
		if (!tabs.some((tab) => tab.id === this.orderFilter)) this.orderFilter = "all";

		host.innerHTML = tabs
			.map((tab) => {
				const count = tab.badge ? tab.badge(orders) : orders.filter(tab.filter).length;
				return `<button class="rc-tab" type="button" data-tab="${esc(tab.id)}"
					aria-pressed="${this.orderFilter === tab.id}" title="${esc(tab.label)}"><span class="rc-tab__label">${esc(
					tab.label
				)}</span>
					<span class="rc-tab__count">${cint(count)}</span></button>`;
			})
			.join("");

		$(host)
			.off("click")
			.on("click", ".rc-tab", (e) => {
				this.orderFilter = e.currentTarget.dataset.tab;
				this.renderOrderTabs();
				this.renderOrders();
			});
	}

	/** O'ng paneldagi ro'yxat sarlavhasi: faol buyurtmalar soni va «hisob so'radi» soni. */
	renderWorklistHeader() {
		const sub = this.el.panel.querySelector(".rc-worklist__sub");
		const badges = this.el.panel.querySelector(".rc-worklist__badges");
		if (!sub || !badges) return;

		const orders = this.orders || [];
		const bells = orders.filter((order) => order.bill_requested).length;
		sub.textContent = __("{0} ta faol", [orders.length]);
		badges.innerHTML = bells
			? `<span class="rc-bell rc-bell--panel"><span aria-hidden="true">🔔</span>${esc(
					__("Hisob so'radi")
			  )} · ${cint(bells)}</span>`
			: "";
	}

	/**
	 * Keng ekranda hech narsa tanlanmaganda o'ng panel = faol buyurtmalar ro'yxati.
	 * Bo'sh ekran o'rniga kassir uchun eng kerakli narsa: kim hisob so'radi, kim uzoq kutyapti.
	 */
	renderWorklistPanel() {
		this.el.panel.innerHTML = `
			<div class="rc-panel__head rc-panel__head--list">
				<div class="rc-panel__row">
					<div class="rc-panel__titles">
						<div class="rc-panel__table">${esc(__("Buyurtmalar"))}</div>
						<div class="rc-panel__sub rc-worklist__sub"></div>
					</div>
					<div class="rc-panel__badges rc-worklist__badges"></div>
				</div>
			</div>
			<div class="rc-orders__tabs" role="group"></div>
			<div class="rc-orders__list"></div>`;

		this.panelOrders = {
			tabs: this.el.panel.querySelector(".rc-orders__tabs"),
			list: this.el.panel.querySelector(".rc-orders__list"),
		};
		this.worklistOpen = true;
		this.renderOrders();
	}

	/** Ro'yxatdagi qatorlarda tanlangan chekni belgilaydi (ro'yxat qayta chizilmaydi). */
	syncOrderSelection() {
		const { list } = this.ordersHosts();
		list.querySelectorAll(".rc-order").forEach((row) => {
			row.setAttribute("aria-pressed", String(row.dataset.invoice === this.selectedInvoice));
		});
	}

	renderOrders() {
		this.renderViewTabs();
		this.renderOrderTabs();
		this.renderWorklistHeader();
		const host = this.ordersHosts().list;

		const tab = this.orderTabs().find((t) => t.id === this.orderFilter) || BASE_TABS[0];
		const orders = (this.orders || [])
			.filter(tab.filter)
			.sort(
				(a, b) =>
					Number(!!b.bill_requested) - Number(!!a.bill_requested) ||
					cint(b.elapsed_minutes) - cint(a.elapsed_minutes)
			);

		if (!orders.length) {
			host.innerHTML = `<div class="rc-empty">
				<div class="rc-empty__icon">✓</div>
				<p class="rc-empty__title">${esc(__("To'lanmagan buyurtma yo'q"))}</p>
				<p class="rc-empty__hint">${esc(__("Stolni tanlab yangi buyurtma oching."))}</p>
			</div>`;
			return;
		}

		host.innerHTML = orders
			.map(
				(order) => `<button type="button" class="rc-order rc-order--${esc(order.status)} ${
					order.bill_requested ? "rc-order--bill" : ""
				}" data-table="${esc(order.table || "")}" data-invoice="${esc(order.invoice)}"
					aria-pressed="${order.invoice === this.selectedInvoice}">
					<span class="rc-order__main">
						<span class="rc-order__table">${
							order.bill_requested
								? `<span class="rc-bell" title="${esc(
										__("Hisob so'radi")
								  )}"><span aria-hidden="true">🔔</span></span>`
								: ""
						}${esc(order.table || orderTypeLabel(order.order_type) || "—")}</span>
						<span class="rc-order__meta">${esc(order.waiter_name || "—")}${
					order.customer_name ? ` · ${esc(order.customer_name)}` : ""
				}</span>
					</span>
					<span class="rc-order__time">${elapsedHtml(order.elapsed_minutes, this.ordersLoadedAt)}</span>
					<span class="rc-order__tags">
						<span class="rc-tag ${order.billed ? "rc-tag--billed" : ""}">${esc(order.status_label)}</span>
						${(order.kitchen || {}).label ? `<span class="rc-tag">${esc(order.kitchen.label)}</span>` : ""}
					</span>
					<span class="rc-order__amount">${esc(this.money(order.amount))}</span>
				</button>`
			)
			.join("");

		$(host)
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
}
