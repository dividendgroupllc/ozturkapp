// Copyright (c) 2026, Ozturkapp
// License: MIT

frappe.query_reports["Kunlik sotuv"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("Boshlanish sanasi"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("Tugash sanasi"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "company",
			label: __("Kompaniya"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "branch",
			label: __("Filial"),
			fieldtype: "Link",
			options: "Branch",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		const field = column.fieldname || "";
		if ((field === "discount" || field.startsWith("disc_")) && flt(data[field]) > 0) {
			return `<span style="color:#e24c4c">${value}</span>`;
		}
		if (field === "revenue") return `<b>${value}</b>`;
		return value;
	},
};
