// Copyright (c) 2026, Ozturkapp and contributors
// For license information, please see license.txt

// Guruh bo'yicha konsolidatsiyalangan P&L. Ichki aylanma (sklad → filial)
// DOIM chiqariladi; "Ички айланмани кўрсатиш" faqat ayirilgan summani alohida
// qator qilib ko'rsatadi. Batafsil: pl_obshi.py docstring.
frappe.query_reports["PL Obshi"] = {
	filters: [
		{
			// Guruh kompaniya — uning ishchi farzandlari birlashtiriladi.
			// Bo'sh qolsa server foydalanuvchi kompaniyasining ildiz guruhini oladi.
			fieldname: "company",
			label: __("Гуруҳ / компания"),
			fieldtype: "Link",
			options: "Company",
		},
		{
			fieldname: "from_date",
			label: __("Дан"),
			fieldtype: "Date",
			default: frappe.datetime.year_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("Гача"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "periodicity",
			label: __("Давр"),
			fieldtype: "Select",
			options: "Yearly\nHalf-Yearly\nQuarterly\nMonthly",
			default: "Monthly",
			reqd: 1,
		},
		{
			// Frappe belgilanmagan checkbox'ni so'rovga qo'shmaydi — shuning
			// uchun standart 0 = "ko'rsatilmasin".
			fieldname: "show_internal",
			label: __("Ички айланмани кўрсатиш"),
			fieldtype: "Check",
			default: 0,
		},
	],

	tree: true,
	name_field: "label",
	initial_depth: 1,

	formatter: function (value, row, column, data, default_formatter) {
		const rt = data ? data.row_type : null;
		if (!data || rt === "divider") return "";

		if (column.fieldname === "label") {
			let s = "white-space:nowrap;color:var(--text-color);";
			if (rt === "root") s += "font-weight:700;text-transform:uppercase;letter-spacing:.3px;";
			else if (rt === "result") s += "font-weight:800;";
			else if (rt === "sub") s += "font-weight:600;";
			else if (rt === "detail") s += "font-size:12.5px;";
			else if (rt === "percent") s += "font-style:italic;font-size:11.5px;color:var(--text-muted);";
			return `<span style="${s}">${frappe.utils.escape_html(data.label || "")}</span>`;
		}

		if (value === null || value === undefined || value === "") return "";
		const num = flt(value);
		if (rt === "percent") {
			return `<span style="font-style:italic;font-weight:600;color:var(--text-muted);">${Math.round(num)}%</span>`;
		}
		const rounded = Math.round(num);
		const w = rt === "root" || rt === "result" ? 800 : rt === "sub" ? 700 : 400;
		if (rounded < 0) {
			return `<span style="font-weight:${w};color:var(--alert-text-danger);">(${Math.abs(rounded).toLocaleString("ru-RU")})</span>`;
		}
		return `<span style="font-weight:${w};color:var(--text-color);">${rounded.toLocaleString("ru-RU")}</span>`;
	},
};
