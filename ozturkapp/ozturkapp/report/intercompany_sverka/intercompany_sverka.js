// Copyright (c) 2026, Ozturkapp and contributors
// For license information, please see license.txt

// Guruh kompaniyalari o'rtasidagi qarz va hujjatlarni solishtirish.
// Batafsil: intercompany_sverka.py docstring.
frappe.query_reports["Intercompany Sverka"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("Сана дан"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("Сана гача"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "company",
			label: __("Гуруҳ"),
			fieldtype: "Link",
			options: "Company",
			get_query: () => ({ filters: { is_group: 1 } }),
		},
		{
			fieldname: "seller",
			label: __("Сотувчи компания"),
			fieldtype: "Link",
			options: "Company",
			get_query: () => ({ filters: { is_group: 0 } }),
		},
		{
			fieldname: "buyer",
			label: __("Олувчи компания"),
			fieldtype: "Link",
			options: "Company",
			get_query: () => ({ filters: { is_group: 0 } }),
		},
		{
			fieldname: "only_differences",
			label: __("Фақат фарқи борларини кўрсатиш"),
			fieldtype: "Check",
			default: 0,
		},
	],

	tree: true,
	name_field: "label",
	initial_depth: 2,

	formatter: function (value, row, column, data, default_formatter) {
		const rt = data ? data.row_type : null;
		if (!data || rt === "divider") return "";

		if (column.fieldname === "label") {
			let s = "white-space:nowrap;color:var(--text-color);";
			if (rt === "section") s += "font-weight:800;text-transform:uppercase;letter-spacing:.3px;";
			else if (rt === "root") s += "font-weight:700;";
			else if (rt === "result") s += "font-weight:800;";
			else if (rt === "sub") s += "font-weight:600;";
			else if (rt === "detail") s += "font-size:12.5px;";
			else if (rt === "warn") s += "font-size:12.5px;color:var(--alert-text-danger);";
			return `<span style="${s}">${frappe.utils.escape_html(data.label || "")}</span>`;
		}

		if (column.fieldtype === "Link") {
			return default_formatter(value, row, column, data);
		}

		if (value === null || value === undefined || value === "") return "";
		const rounded = Math.round(flt(value));
		if (column.fieldname === "diff") {
			if (rounded === 0) return `<span style="color:var(--text-muted);">—</span>`;
			const sign = rounded < 0 ? "−" : "+";
			return `<span style="font-weight:700;color:var(--alert-text-danger);">${sign}${Math.abs(rounded).toLocaleString("ru-RU")}</span>`;
		}
		const w = rt === "root" || rt === "result" ? 800 : rt === "sub" ? 600 : 400;
		const txt = Math.abs(rounded).toLocaleString("ru-RU");
		if (rounded < 0) {
			return `<span style="font-weight:${w};color:var(--alert-text-danger);">(${txt})</span>`;
		}
		return `<span style="font-weight:${w};color:var(--text-color);">${txt}</span>`;
	},
};
