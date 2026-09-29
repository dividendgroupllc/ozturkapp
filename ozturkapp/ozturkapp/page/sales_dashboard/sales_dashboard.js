/**
 * Sotuv dashboardi — mahsulotlar bo'yicha sotuv va chegirma.
 *
 * Barcha hisob-kitob serverda (`api/sales_dashboard.py`). Bu fayl faqat
 * ko'rsatadi: KPI kartalar, ikki grafik va saralanadigan jadval.
 */

frappe.provide("ozturk.sales_dashboard");

frappe.pages["sales-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Sotuv dashboardi"),
		single_column: true,
	});

	wrapper.dashboard = new ozturk.sales_dashboard.Dashboard(page);
};

ozturk.sales_dashboard.API = "ozturkapp.ozturkapp.api.sales_dashboard";

ozturk.sales_dashboard.Dashboard = class SalesDashboard {
	constructor(page) {
		this.page = page;
		this.data = null;
		this.search = "";
		this.sort = { field: "net_amount", dir: "desc" };

		this.make_filters();
		this.make_layout();
		this.set_period("month");
	}

	/* ─────────────────────────── Filtrlar ─────────────────────────── */

	make_filters() {
		const refresh = () => this.refresh();
		// Sana qo'lda o'zgartirilsa tezkor davr tugmasi endi mos emas.
		const on_date_change = () => {
			if (this._setting_period) return;
			const f = this.get_filters();
			const [from, to] = this._period_range || [];
			if (f.from_date !== from || f.to_date !== to) {
				this.$root && this.$root.find(".sd-period").removeClass("active");
			}
			this.refresh();
		};

		this.fields = {
			from_date: this.page.add_field({
				fieldname: "from_date",
				label: __("Boshlanish sanasi"),
				fieldtype: "Date",
				reqd: 1,
				change: on_date_change,
			}),
			to_date: this.page.add_field({
				fieldname: "to_date",
				label: __("Tugash sanasi"),
				fieldtype: "Date",
				reqd: 1,
				change: on_date_change,
			}),
			branch: this.page.add_field({
				fieldname: "branch",
				label: __("Filial"),
				fieldtype: "Link",
				options: "Branch",
				change: refresh,
			}),
			item_group: this.page.add_field({
				fieldname: "item_group",
				label: __("Mahsulot guruhi"),
				fieldtype: "Link",
				options: "Item Group",
				change: refresh,
			}),
		};

		this.page.set_primary_action(__("Yangilash"), refresh, "refresh");
		this.page.add_menu_item(__("Excel yuklab olish"), () => this.export_excel());
	}

	set_period(key) {
		const today = frappe.datetime.get_today();
		const ranges = {
			today: [today, today],
			yesterday: [frappe.datetime.add_days(today, -1), frappe.datetime.add_days(today, -1)],
			week: [frappe.datetime.add_days(today, -6), today],
			month: [frappe.datetime.month_start(), today],
			prev_month: [
				frappe.datetime.add_months(frappe.datetime.month_start(), -1),
				frappe.datetime.add_days(frappe.datetime.month_start(), -1),
			],
		};
		const [from_date, to_date] = ranges[key];
		this._period_range = ranges[key];

		// Ikkala sana o'rnatilguncha oraliq holat bilan so'rov ketmasin.
		this._setting_period = true;
		Promise.all([
			this.fields.from_date.set_value(from_date),
			this.fields.to_date.set_value(to_date),
		]).finally(() => {
			this._setting_period = false;
			this.$root.find(".sd-period").removeClass("active");
			this.$root.find(`.sd-period[data-period="${key}"]`).addClass("active");
			this.refresh();
		});
	}

	get_filters() {
		const from_date = this.fields.from_date.get_value();
		return {
			from_date,
			to_date: this.fields.to_date.get_value() || from_date,
			branch: this.fields.branch.get_value() || null,
			item_group: this.fields.item_group.get_value() || null,
		};
	}

	/* ─────────────────────────── Maket ─────────────────────────── */

	make_layout() {
		this.$root = $(`
			<div class="sd-root">
				<div class="sd-periods">
					<button class="sd-period" data-period="today">${__("Bugun")}</button>
					<button class="sd-period" data-period="yesterday">${__("Kecha")}</button>
					<button class="sd-period" data-period="week">${__("7 kun")}</button>
					<button class="sd-period" data-period="month">${__("Shu oy")}</button>
					<button class="sd-period" data-period="prev_month">${__("O'tgan oy")}</button>
				</div>
				<div class="sd-kpis"></div>
				<div class="sd-charts">
					<div class="sd-card">
						<div class="sd-card-title">${__("Kunlik sotuv va chegirma")} <span class="sd-daily-note text-muted"></span></div>
						<div class="sd-chart-daily"></div>
					</div>
					<div class="sd-card">
						<div class="sd-card-title">${__("Eng ko'p sotilgan 10 mahsulot")}</div>
						<div class="sd-chart-top"></div>
					</div>
				</div>
				<div class="sd-card sd-table-card">
					<div class="sd-table-head">
						<div class="sd-card-title">${__("Mahsulotlar bo'yicha sotuv")}</div>
						<div class="sd-table-tools">
							<input type="search" class="form-control input-xs sd-search"
								placeholder="${__("Mahsulot qidirish...")}">
							<button class="btn btn-default btn-xs sd-export">
								${frappe.utils.icon("download", "xs")} ${__("Excel")}
							</button>
						</div>
					</div>
					<div class="sd-table-wrap"></div>
				</div>
			</div>
		`).appendTo(this.page.main);

		this.$root.on("click", ".sd-period", (e) => this.set_period($(e.currentTarget).data("period")));
		this.$root.on("input", ".sd-search", frappe.utils.debounce((e) => {
			this.search = (e.target.value || "").trim().toLowerCase();
			this.render_table();
		}, 150));
		this.$root.on("click", ".sd-export", () => this.export_excel());
		this.$root.on("click", "th[data-sort]", (e) => {
			const field = $(e.currentTarget).data("sort");
			const dir = this.sort.field === field && this.sort.dir === "desc" ? "asc" : "desc";
			this.sort = { field, dir };
			this.render_table();
		});
	}

	/* ─────────────────────────── Ma'lumot ─────────────────────────── */

	refresh() {
		const filters = this.get_filters();
		if (!filters.from_date) return;
		// Sana yozilayotgan oraliq holat (masalan boshlanish > tugash) — so'rov yubormaymiz.
		if (filters.from_date > filters.to_date) return;
		// Bir xil filtr bilan ketma-ket kelgan `change` hodisalari qayta so'ramasin.
		const key = JSON.stringify(filters);
		if (key === this._last_key && this._busy) return;
		this._last_key = key;

		const token = (this._token = (this._token || 0) + 1);
		this._busy = true;
		this.$root.addClass("sd-loading");

		frappe
			.call({ method: `${ozturk.sales_dashboard.API}.get_dashboard`, args: filters })
			.then((r) => {
				// Eski so'rov kechikib kelsa yangisining ustiga yozmasin.
				if (token !== this._token) return;
				this.data = r.message;
				this.render();
			})
			.always(() => {
				if (token !== this._token) return;
				this._busy = false;
				this.$root.removeClass("sd-loading");
			});
	}

	render() {
		this.render_kpis();
		this.render_charts();
		this.render_table();
	}

	/* ─────────────────────────── Formatlash ─────────────────────────── */

	money(v) {
		return format_currency(flt(v), this.data.currency, 0);
	}

	// Grafik o'qi uchun: 3000000 → "3 mln", 250000 → "250 ming".
	short_money(v) {
		const n = flt(v), a = Math.abs(n);
		const fmt = (x) => String(+x.toFixed(1)).replace(".", ",");
		if (a >= 1e9) return fmt(n / 1e9) + " mlrd";
		if (a >= 1e6) return fmt(n / 1e6) + " mln";
		if (a >= 1e3) return fmt(n / 1e3) + " ming";
		return String(n);
	}

	num(v) {
		return format_number(flt(v), null, flt(v) % 1 ? 2 : 0);
	}

	/* ─────────────────────────── KPI ─────────────────────────── */

	render_kpis() {
		const t = this.data.totals;
		const cards = [
			{ label: __("Sof sotuv"), value: this.money(t.net_amount), hint: __("Mijoz to'lagan (chegirmadan keyin)"), tone: "primary" },
			{ label: __("Sotilgan mahsulot"), value: this.num(t.qty), hint: __("{0} xil mahsulot", [t.items]), tone: "blue" },
			{ label: __("Cheklar soni"), value: this.num(t.invoices), hint: __("O'rtacha chek: {0}", [this.money(t.avg_check)]), tone: "indigo" },
			{ label: __("Umumiy chegirma"), value: this.money(t.discount), hint: __("Yalpi sotuvning {0}%", [flt(t.discount_percent, 1)]), tone: "red" },
			{ label: __("Chegirmada sotilgan"), value: this.num(t.discounted_qty), hint: __("{0} ta chekda chegirma", [t.discounted_invoices]), tone: "orange" },
			{ label: __("Qaytarishlar"), value: this.money(t.return_amount), hint: __("{0} ta qaytarish cheki", [t.returns]), tone: "gray" },
		];

		this.$root.find(".sd-kpis").html(cards.map((c) => `
			<div class="sd-kpi sd-tone-${c.tone}">
				<div class="sd-kpi-label">${c.label}</div>
				<div class="sd-kpi-value">${c.value}</div>
				<div class="sd-kpi-hint">${c.hint}</div>
			</div>
		`).join(""));
	}

	/* ─────────────────────────── Grafiklar ─────────────────────────── */

	render_charts() {
		const daily = this.data.daily;
		const $daily = this.$root.find(".sd-chart-daily").empty();
		const $top = this.$root.find(".sd-chart-top").empty();

		// Uzun davrda ustunlar siqilib, sanalar ustma-ust tushadi — oxirgi 10 kun.
		const days = daily.slice(-10);
		this.$root.find(".sd-daily-note").text(
			this.data.items.length && daily.length > days.length ? __("(oxirgi {0} kun)", [days.length]) : ""
		);

		if (!this.data.items.length) {
			$daily.html(this.empty_state());
			$top.html(this.empty_state());
			return;
		}

		new frappe.Chart($daily[0], {
			type: "axis-mixed",
			height: 260,
			colors: ["#2490ef", "#e24c4c"],
			data: {
				labels: days.map((d) => frappe.datetime.str_to_user(d.date).slice(0, 5)),
				datasets: [
					{ name: __("Sof sotuv"), chartType: "bar", values: days.map((d) => flt(d.net_amount)) },
					{ name: __("Chegirma"), chartType: "line", values: days.map((d) => flt(d.discount)) },
				],
			},
			axisOptions: { xIsSeries: 1, xAxisMode: "tick", shortenYAxisNumbers: 1, numberFormatter: (v) => this.short_money(v) },
			barOptions: { spaceRatio: 0.4 },
			lineOptions: { regionFill: 0, dotSize: 3 },
			tooltipOptions: { formatTooltipY: (v) => this.money(v) },
		});

		const top = [...this.data.items].sort((a, b) => flt(b.qty) - flt(a.qty)).slice(0, 10);
		new frappe.Chart($top[0], {
			type: "bar",
			height: 260,
			colors: ["#29cd42"],
			data: {
				labels: top.map((r) => this.truncate(r.item_name || r.item_code, 14)),
				datasets: [{ name: __("Soni"), values: top.map((r) => flt(r.qty)) }],
			},
			barOptions: { spaceRatio: 0.35 },
			tooltipOptions: { formatTooltipY: (v) => this.num(v) },
		});
	}

	truncate(s, n) {
		return s.length > n ? s.slice(0, n - 1) + "…" : s;
	}

	empty_state() {
		return `<div class="sd-empty">${frappe.utils.icon("chart", "lg")}<div>${__("Tanlangan davrda sotuv yo'q")}</div></div>`;
	}

	/* ─────────────────────────── Jadval ─────────────────────────── */

	columns() {
		return [
			{ field: "idx", label: "#", align: "center", sortable: false },
			{ field: "item_name", label: __("Mahsulot"), align: "left" },
			{ field: "item_group", label: __("Guruh"), align: "left" },
			{ field: "qty", label: __("Sotilgan soni"), type: "num" },
			{ field: "avg_price", label: __("O'rtacha narx"), type: "money" },
			{ field: "gross_amount", label: __("Yalpi summa"), type: "money" },
			{ field: "discounted_qty", label: __("Chegirmada sotilgan"), type: "num" },
			{ field: "discount", label: __("Umumiy chegirma"), type: "money" },
			{ field: "net_amount", label: __("Sof summa"), type: "money" },
		];
	}

	visible_rows() {
		let rows = [...this.data.items];

		if (this.search) {
			rows = rows.filter((r) =>
				[r.item_name, r.item_code, r.item_group].some((v) => (v || "").toLowerCase().includes(this.search))
			);
		}

		const { field, dir } = this.sort;
		const sign = dir === "asc" ? 1 : -1;
		rows.sort((a, b) => {
			const x = a[field], y = b[field];
			if (typeof x === "string" || typeof y === "string") {
				return sign * String(x || "").localeCompare(String(y || ""));
			}
			return sign * (flt(x) - flt(y));
		});
		return rows;
	}

	render_table() {
		if (!this.data) return;
		const $wrap = this.$root.find(".sd-table-wrap");

		if (!this.data.items.length) {
			$wrap.html(this.empty_state());
			return;
		}

		const cols = this.columns();
		const rows = this.visible_rows();
		const esc = frappe.utils.escape_html;

		const head = cols.map((c) => {
			const sortable = c.sortable !== false;
			const arrow = this.sort.field === c.field ? (this.sort.dir === "asc" ? " ▲" : " ▼") : "";
			return `<th class="text-${c.align || "right"} ${sortable ? "sd-sortable" : ""}"
				${sortable ? `data-sort="${c.field}"` : ""}>${c.label}${arrow}</th>`;
		}).join("");

		const cell = (c, r, i) => {
			const v = r[c.field];
			switch (c.field) {
				case "idx":
					return i + 1;
				case "item_name":
					return `<a href="/app/item/${encodeURIComponent(r.item_code)}" class="sd-item">${esc(r.item_name || r.item_code)}</a>
						<div class="sd-code">${esc(r.item_code)}</div>`;
				case "item_group":
					return `<span class="sd-group">${esc(v || "")}</span>`;
				case "discounted_qty":
					return flt(v) ? `<span class="sd-pill sd-pill-orange">${this.num(v)}</span>` : `<span class="text-muted">—</span>`;
				case "discount":
					return flt(v) ? `<span class="sd-neg">−${this.money(v)}</span>` : `<span class="text-muted">—</span>`;
				case "net_amount":
					return `<b>${this.money(v)}</b>`;
			}
			return c.type === "money" ? this.money(v) : this.num(v);
		};

		const body = rows.map((r, i) => `<tr>${cols.map((c) =>
			`<td class="text-${c.align || "right"} sd-col-${c.field}">${cell(c, r, i)}</td>`).join("")}</tr>`).join("");

		// Jami qatori qidiruvga qarab — ko'rinib turgan qatorlar yig'indisi.
		const sum = (f) => rows.reduce((s, r) => s + flt(r[f]), 0);
		const foot = `<tr>
			<td></td>
			<td class="text-left" colspan="2">${__("Jami")} (${rows.length})</td>
			<td class="text-right">${this.num(sum("qty"))}</td>
			<td></td>
			<td class="text-right">${this.money(sum("gross_amount"))}</td>
			<td class="text-right">${this.num(sum("discounted_qty"))}</td>
			<td class="text-right sd-neg">${sum("discount") ? "−" + this.money(sum("discount")) : "—"}</td>
			<td class="text-right">${this.money(sum("net_amount"))}</td>
		</tr>`;

		$wrap.html(`
			<table class="sd-table">
				<thead><tr>${head}</tr></thead>
				<tbody>${body || `<tr><td colspan="${cols.length}" class="text-center text-muted">${__("Hech narsa topilmadi")}</td></tr>`}</tbody>
				<tfoot>${foot}</tfoot>
			</table>
		`);
	}

	/* ─────────────────────────── Eksport ─────────────────────────── */

	export_excel() {
		if (!this.data || !this.data.items.length) {
			frappe.show_alert({ message: __("Yuklab olish uchun ma'lumot yo'q"), indicator: "orange" });
			return;
		}
		// Fayl serverda quriladi (formatlar, jami qatori) — ekrandagi qidiruv va saralash bilan.
		const args = {
			...this.get_filters(),
			search: this.search,
			sort_field: this.sort.field,
			sort_dir: this.sort.dir,
		};
		Object.keys(args).forEach((k) => args[k] == null && delete args[k]);
		open_url_post(`/api/method/${ozturk.sales_dashboard.API}.download_excel`, args);
	}
};
