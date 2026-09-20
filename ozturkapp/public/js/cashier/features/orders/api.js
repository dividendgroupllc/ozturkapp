/**
 * Serverga so'rovlar — modulning BARCHA endpoint'lari shu yerda.
 *
 * Yagona joyda turgani uchun test ularni (nomi va argumentlari bo'yicha)
 * Python imzolari bilan solishtira oladi. Bo'sh qiymatlar (`null`, `""`) YUBORILMAYDI:
 * jQuery ularni bo'sh satr qilib jo'natadi, server esa `None` kutadi.
 *
 * NARX HECH QACHON yuborilmaydi — buyurtma narxini server `Item Price` dan oladi.
 */

const present = (args) =>
	Object.fromEntries(
		Object.entries(args).filter(
			([, value]) => value !== undefined && value !== null && value !== ""
		)
	);

export const orderApi = {
	getMenu: (screen, { orderType, room }) =>
		screen.call(
			"ozturkapp.ozturkapp.api.cashier_orders.get_menu",
			present({ order_type: orderType, room })
		),

	/** Barcha zallardagi stollar (kassir bitta zalni tanlagan bo'lsa ham). */
	getFloorPlan: (screen) => screen.call("ozturkapp.ozturkapp.api.table.get_floor_plan", {}),

	/** Buyurtmaning yangi holati — `modified` optimistik qulf uchun. */
	getBill: (screen, invoice) =>
		screen.call("ozturkapp.ozturkapp.api.order.get_order_bill_preview", { order: invoice }),

	createOrder: (
		screen,
		{ orderType, items, table, customer, pax, comments, delivery, clientRef }
	) =>
		screen.call(
			"ozturkapp.ozturkapp.api.cashier_orders.create_order",
			present({
				order_type: orderType,
				items,
				table,
				customer,
				pax,
				comments,
				delivery,
				client_ref: clientRef,
			})
		),

	addItems: (screen, { invoice, items, lastModifiedTime }) =>
		screen.call("ozturkapp.ozturkapp.api.cashier_orders.add_items", {
			invoice,
			items,
			last_modified_time: lastModifiedTime,
		}),

	removeItem: (screen, { invoice, itemRow, qty, reason }) =>
		screen.call(
			"ozturkapp.ozturkapp.api.cashier_orders.remove_item",
			present({ invoice, item_row: itemRow, qty, reason })
		),

	searchCustomers: (screen, { query, limit }) =>
		screen.call(
			"ozturkapp.ozturkapp.api.cashier_orders.search_customers",
			present({ query, limit })
		),

	createCustomer: (screen, { customerName, mobileNumber, address }) =>
		screen.call(
			"ozturkapp.ozturkapp.api.cashier_orders.create_customer",
			present({ customer_name: customerName, mobile_number: mobileNumber, address })
		),

	setCustomer: (screen, { invoice, customer }) =>
		screen.call("ozturkapp.ozturkapp.api.cashier_orders.set_customer", { invoice, customer }),
};
