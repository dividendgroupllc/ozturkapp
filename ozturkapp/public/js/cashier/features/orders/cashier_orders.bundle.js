/**
 * Kassadan buyurtma qabul qilish va mijoz — alohida yig'ma (`cashier_orders.bundle.js`).
 *
 * Har biri o'z POS Profile bayrog'i bilan yoqiladi (`features.register`); o'chiq
 * bo'lsa ekranda umuman ko'rinmaydi, server ham har amalda bayroqni qayta tekshiradi.
 *
 *   cashier_orders   `cashier_orders`   buyurtma ochish, taom qo'shish/olib tashlash
 *   customer_attach  `customer_attach`  chekka mijoz biriktirish
 *   order_view       (bayroqsiz)        stolsiz buyurtmalar filtri, panel ma'lumot bloki
 *
 * `order_view` faqat KO'RSATADI (Desktop POS ham stolsiz buyurtma yaratadi),
 * shuning uchun bayroq talab qilmaydi.
 *
 * Slot tartibi (`order`) 200-299 oralig'ida: to'lov 100-199, stollar 300-399,
 * smena 400-499. Uslub: `cashier_orders.bundle.css` (`.rc-orders-*`).
 *
 * Slot id lari (`orders-` prefiksi bilan): topbar.quick `orders-new`;
 * panel.primary `orders-open`; panel.more `orders-edit`, `orders-customer`;
 * panel.info `orders-info`; orders.tabs `orders-takeaway`.
 */

import { attachCustomer, renderInfo } from "./customer.js";
import { OrderDialog } from "./order_dialog.js";
import { features, isTableless, shiftClosedReason, slots } from "./shared.js";

const isBill = (detail) => detail.kind === "bill" && !!detail.bill;

/** Hisobi chiqmagan, to'lanmagan, bekor qilinmagan chek. */
const isOpenDraft = (detail) => {
	if (!isBill(detail)) return false;
	const { bill } = detail;
	return !bill.billed && !bill.paid && !bill.cancelled;
};

/** Stol bo'sh bo'lsagina oldindan tanlanadi (zal rejasidagi tanlov). */
function selectedFreeTable(screen) {
	const rows = (screen.floor || {}).tables || [];
	const table = rows.find((row) => row.name === screen.selectedTable);
	return table && table.status === "AVAILABLE" ? table.name : null;
}

const openOrderDialog = (screen, options) => new OrderDialog(screen, options).open();

features.register({
	key: "cashier_orders",
	flag: "cashier_orders",
	install() {
		return [
			slots.contribute("topbar.quick", {
				id: "orders-new",
				order: 200,
				label: __("Buyurtma"),
				icon: "+",
				kind: "primary",
				disabled: (current) => shiftClosedReason(current),
				onClick: (current) =>
					openOrderDialog(current, { mode: "create", table: selectedFreeTable(current) }),
			}),

			slots.contribute("panel.primary", {
				id: "orders-open",
				order: 200,
				kind: "pay",
				label: __("Buyurtma ochish"),
				when: (current, detail) => detail.kind === "available",
				disabled: (current) => shiftClosedReason(current),
				onClick: (current, detail) =>
					openOrderDialog(current, { mode: "create", table: detail.table }),
			}),

			slots.contribute("panel.more", {
				id: "orders-edit",
				order: 200,
				kind: "primary",
				label: __("Taom qo'shish / tahrirlash"),
				when: (current, detail) => isOpenDraft(detail),
				disabled: (current) => shiftClosedReason(current),
				onClick: (current, detail) =>
					openOrderDialog(current, { mode: "edit", invoice: detail.bill.invoice }),
			}),
		];
	},
});

features.register({
	key: "customer_attach",
	flag: "customer_attach",
	install() {
		return [
			slots.contribute("panel.more", {
				id: "orders-customer",
				order: 210,
				label: __("Mijoz"),
				when: (current, detail) =>
					isBill(detail) && !detail.bill.paid && !detail.bill.cancelled,
				disabled: (current) => shiftClosedReason(current),
				onClick: (current, detail) => attachCustomer(current, detail),
			}),
		];
	},
});

features.register({
	key: "order_view",
	install() {
		return [
			slots.contribute("orders.tabs", {
				id: "orders-takeaway",
				order: 210,
				label: __("Olib ketish / Yetkazish"),
				when: (orders) => orders.some(isTableless),
				filter: isTableless,
			}),

			slots.contribute("panel.info", {
				id: "orders-info",
				order: 200,
				when: (current, detail) => isBill(detail),
				render: (current, detail) => renderInfo(current, detail),
			}),
		];
	},
});
