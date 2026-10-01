// Copyright (c) 2026, Ozturkapp
// License: MIT

frappe.ui.form.on("Branch Stock Transfer", {
	refresh(frm) {
		if (frm.doc.docstatus === 0) {
			frm.dashboard.set_headline_alert(
				__("O'tkazma <b>TAN NARXDA</b> (valuation, ustamasiz) amalga oshiriladi."),
				"blue"
			);
			frm.add_custom_button(__("BOM'larni portlat"), () => explode_boms(frm));
		}

		if (frm.doc.sales_invoice) {
			frm.add_custom_button(
				__("Sales Invoice"),
				() => frappe.set_route("Form", "Sales Invoice", frm.doc.sales_invoice),
				__("Ko'rish")
			);
		}
		if (frm.doc.purchase_invoice) {
			frm.add_custom_button(
				__("Purchase Invoice"),
				() => frappe.set_route("Form", "Purchase Invoice", frm.doc.purchase_invoice),
				__("Ko'rish")
			);
		}
	},

	from_company(frm) {
		frm.set_value("from_warehouse", null);
	},

	to_company(frm) {
		frm.set_value("to_warehouse", null);
	},

	onload(frm) {
		frm.set_query("from_warehouse", (doc) => ({
			filters: { company: doc.from_company, is_group: 0 },
		}));
		frm.set_query("to_warehouse", (doc) => ({
			filters: { company: doc.to_company, is_group: 0 },
		}));
		// Dynamic Link: source_type='BOM' -> aktiv BOM; 'Item' -> stokli item
		frm.set_query("reference", "items", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			if (row.source_type === "BOM") {
				return { filters: { is_active: 1, docstatus: 1 } };
			}
			return { filters: { disabled: 0, is_stock_item: 1 } };
		});
	},
});

frappe.ui.form.on("Branch Stock Transfer Item", {
	source_type(frm, cdt, cdn) {
		// Tur o'zgarsa — tanlovni va narxni tozalaymiz
		frappe.model.set_value(cdt, cdn, "reference", null);
		frappe.model.set_value(cdt, cdn, "item_code", null);
		frappe.model.set_value(cdt, cdn, "rate", 0);
		frappe.model.set_value(cdt, cdn, "amount", 0);
	},

	reference(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.reference) {
			return;
		}
		if (row.source_type === "Item") {
			// Item: item_code = tanlangan tovar, narxni olamiz
			frappe.model.set_value(cdt, cdn, "item_code", row.reference);
			fetch_rate(frm, cdt, cdn, row.reference, row.qty || 1);
		} else {
			// BOM: narx portlaganda hisoblanadi
			frappe.model.set_value(cdt, cdn, "item_code", null);
			frappe.model.set_value(cdt, cdn, "rate", 0);
			frappe.show_alert({
				message: __("BOM tanlandi — Save yoki 'BOM'larni portlat' bosing."),
				indicator: "blue",
			});
		}
	},

	qty(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		row.amount = (row.qty || 0) * (row.rate || 0);
		frm.refresh_field("items");
	},
});

function fetch_rate(frm, cdt, cdn, item_code, qty) {
	if (!frm.doc.from_warehouse || !frm.doc.from_company) {
		return;
	}
	frappe.call({
		method:
			"ozturkapp.ozturkapp.doctype.branch_stock_transfer.branch_stock_transfer.get_item_rate",
		args: {
			item_code: item_code,
			from_warehouse: frm.doc.from_warehouse,
			from_company: frm.doc.from_company,
			qty: qty,
			posting_date: frm.doc.posting_date,
			posting_time: frm.doc.posting_time,
		},
		callback(r) {
			if (r.message) {
				frappe.model.set_value(cdt, cdn, "uom", r.message.uom);
				frappe.model.set_value(cdt, cdn, "rate", r.message.rate);
			}
		},
	});
}

function explode_boms(frm) {
	if (frm.is_dirty()) {
		frappe.show_alert({
			message: __("Avval saqlang, keyin portlating."),
			indicator: "orange",
		});
		return;
	}
	frappe.call({
		method:
			"ozturkapp.ozturkapp.doctype.branch_stock_transfer.branch_stock_transfer.explode_boms",
		args: { docname: frm.doc.name },
		freeze: true,
		freeze_message: __("BOM'lar portlatilmoqda..."),
		callback(r) {
			if (r.message) {
				frm.reload_doc();
				frappe.show_alert({
					message: __("BOM'lar portlatildi va narxlandi."),
					indicator: "green",
				});
			}
		},
	});
}
