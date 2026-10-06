// Copyright (c) 2026, Ozturkapp
// License: MIT

// PDF tugmasi — Jazira'dagi naqsh: Frappe onload'dan keyin inner toolbar'ni
// tozalashi mumkin, shuning uchun tugma jadval chizilgandan keyin ham qayta
// qo'yiladi (o'z belgisi bo'lsa qaytadan qo'shilmaydi). .hidden-md esa
// 992px dan tor oynada toolbar'ni yashiradi — hisobot desktop uchun.
function ozturk_pl_hisoboti_pdf_button(report) {
	if (!report || !report.page || !report.page.inner_toolbar) return;
	report.page.inner_toolbar.removeClass("hidden-xs hidden-md");
	if (report.page.inner_toolbar.find(".ozturk_pl_hisoboti_pdf_button").length) return;

	const $btn = report.page.add_inner_button(__("PDF"), function () {
		const filters = frappe.query_report.get_filter_values();
		if (!filters.company || !filters.from_date || !filters.to_date) {
			frappe.show_alert({ message: __("Аввал компания ва саналарни танланг"), indicator: "orange" });
			return;
		}
		window.open("/api/method/ozturkapp.ozturkapp.report.pl_hisoboti.pl_hisoboti_pdf.generate_pdf"
			+ "?filters=" + encodeURIComponent(JSON.stringify(filters)));
	});
	if ($btn && $btn.addClass) $btn.addClass("ozturk_pl_hisoboti_pdf_button").addClass("btn-primary");
}

frappe.query_reports["PL Hisoboti"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Компания"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1
		},
		{
			fieldname: "from_date",
			label: __("Дан"),
			fieldtype: "Date",
			default: frappe.datetime.year_start(),
			reqd: 1
		},
		{
			fieldname: "to_date",
			label: __("Гача"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1
		},
		{
			fieldname: "periodicity",
			label: __("Давр"),
			fieldtype: "Select",
			options: "Yearly\nHalf-Yearly\nQuarterly\nMonthly",
			default: "Monthly",
			reqd: 1
		}
	],

	tree: true,
	name_field: "label",
	initial_depth: 1,

	onload: function (report) {
		ozturk_pl_hisoboti_pdf_button(report);
	},
	after_datatable_render: function () {
		ozturk_pl_hisoboti_pdf_button(frappe.query_report);
	},

	formatter: function (value, row, column, data, default_formatter) {
		const rt = data ? data.row_type : null;
		if (!data || rt === "divider") return "";

		if (column.fieldname === "label") {
			let s = "white-space:nowrap;color:var(--text-color);";
			if (rt === "root") {
				s += "font-weight:700;text-transform:uppercase;letter-spacing:.3px;";
			} else if (rt === "result") {
				s += "font-weight:800;";
			} else if (rt === "sub" || rt === "qty") {
				s += "font-weight:600;";
			} else if (rt === "detail") {
				s += "font-size:12.5px;";
			} else if (rt === "percent" || rt === "ratio") {
				s += "font-style:italic;font-size:11.5px;color:var(--text-muted);";
			}
			return `<span style="${s}">${frappe.utils.escape_html(data.label || "")}</span>`;
		}

		if (value === null || value === undefined || value === "") return "";
		const rounded = Math.round(flt(value));

		if (rt === "percent") {
			return `<span style="font-style:italic;font-weight:600;color:var(--text-muted);">${rounded}%</span>`;
		}
		if (rt === "ratio") {
			return `<span style="font-style:italic;color:var(--text-muted);">${rounded.toLocaleString("ru-RU")}</span>`;
		}
		// РАЗНИЦА: 0 — belgi, aks holda qizil (muammo!)
		if ((data.label || "").indexOf("РАЗНИЦА") === 0) {
			if (rounded === 0) return `<span style="color:var(--text-muted);">0 ✓</span>`;
			return `<span style="font-weight:800;color:var(--alert-text-danger);">${rounded.toLocaleString("ru-RU")} ✗</span>`;
		}

		const w = (rt === "root" || rt === "result") ? 800 : ((rt === "sub" || rt === "qty") ? 700 : 400);
		if (rounded < 0) {
			return `<span style="font-weight:${w};color:var(--alert-text-danger);">(${Math.abs(rounded).toLocaleString("ru-RU")})</span>`;
		}
		return `<span style="font-weight:${w};color:var(--text-color);">${rounded.toLocaleString("ru-RU")}</span>`;
	}
};
