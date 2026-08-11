// Copyright (c) 2026, Ozturkapp and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["Expense Analysis"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("Бошланғич сана"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.now_date(), -1),
			reqd: 1,
			width: "80",
		},
		{
			fieldname: "to_date",
			label: __("Охирги сана"),
			fieldtype: "Date",
			default: frappe.datetime.now_date(),
			reqd: 1,
			width: "80",
		},
		{
			fieldname: "expense_account",
			label: __("Харажат счёти"),
			fieldtype: "Link",
			options: "Account",
			get_query: function () {
				return {
					filters: {
						root_type: "Expense",
						is_group: 0,
					},
				};
			},
		},


		{
			fieldname: "category",
			label: __("Категория"),
			fieldtype: "Select",
			options: [
				"",
				"Поставщики",
				"Сотрудники",
				"Покупатели",
				"Прочие",
			].join("\n"),
		},
		{
			fieldname: "cost_center",
			label: __("Харажат маркази"),
			fieldtype: "Link",
			options: "Cost Center",
		},
	],

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		// Sumani formatlash - minus bo'lsa yashil (xarajat qaytarilishi)
		if (column.fieldname === "amount" && data && data.amount !== undefined && data.amount !== null) {
			if (data.amount < 0) {
				value = `<span style="font-weight: 500; color: #2e7d32;" title="Xarajat qaytarilishi/kamayishi">${value}</span>`;
			} else if (data.amount > 0) {
				value = `<span style="font-weight: 500;">${value}</span>`;
			}
		}

		// Voucher type ni belgilash
		if (column.fieldname === "voucher_type" && value) {
			const colors = {
				"Payment Entry": "#1976d2",
				"Journal Entry": "#7b1fa2",
			};
			const c = colors[data.voucher_type] || "#666";
			value = `<span style="color: ${c}; font-size: 11px; font-weight: 600; text-transform: uppercase;">${value}</span>`;
		}

		// Izoh bo'sh bo'lsa
		if (column.fieldname === "remarks" && !value) {
			value = `<span style="color: #bbb; font-style: italic;">— изоҳ йўқ —</span>`;
		}

		return value;
	},
};
