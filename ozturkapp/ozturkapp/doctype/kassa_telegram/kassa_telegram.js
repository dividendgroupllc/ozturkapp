// Copyright (c) 2026, Ozturkapp
// License: MIT

frappe.ui.form.on("Kassa Telegram", {
	refresh(frm) {
		frm.add_custom_button(__("Sinov xabari yuborish"), () => {
			if (frm.is_dirty()) {
				frappe.show_alert({ message: __("Avval saqlang"), indicator: "orange" });
				return;
			}
			frappe.call({
				method: "ozturkapp.ozturkapp.doctype.kassa_telegram.kassa_telegram.send_test",
				freeze: true,
				callback: () => frappe.show_alert({ message: __("Yuborildi"), indicator: "green" }),
			});
		});
	},
});
