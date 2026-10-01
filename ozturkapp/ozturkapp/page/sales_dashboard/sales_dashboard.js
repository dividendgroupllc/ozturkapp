/**
 * Sotuv dashboardi — tushum, mahsulotlar, to'lov turlari, chegirmalar, ofitsiantlar.
 *
 * Barcha hisob-kitob serverda (`api/sales_dashboard.py`). Bu fayl faqat
 * ko'rsatadi. Ikki daraja bor (batafsil — server modulining izohida):
 * mahsulot guruhi filtri faqat mahsulot darajasiga ta'sir qiladi, chek
 * darajasidagi bloklar (tushum, to'lovlar, chegirmalar, ofitsiantlar) esa
 * butun filial bo'yicha qoladi va buni belgi bilan ko'rsatadi.
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
		this.open_discounts = new Set();

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
			company: this.page.add_field({
				fieldname: "company",
				label: __("Kompaniya"),
				fieldtype: "Link",
				options: "Company",
				default: frappe.defaults.get_user_default("Company"),
				change: refresh,
			}),
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
			days30: [frappe.datetime.add_days(today, -29), today],
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
			company: this.fields.company.get_value() || null,
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
					<button class="sd-period" data-period="days30">${__("30 kun")}</button>
					<button class="sd-period" data-period="month">${__("Shu oy")}</button>
					<button class="sd-period" data-period="prev_month">${__("O'tgan oy")}</button>
					<span class="sd-compare text-muted"></span>
				</div>
				<div class="sd-alerts"></div>
				<div class="sd-kpis"></div>
				<div class="sd-grid">
					<div class="sd-card">
						<div class="sd-card-title">${__("Sotuv dinamikasi")} <span class="sd-timeline-note text-muted"></span></div>
						<div class="sd-chart-timeline"></div>
					</div>
					<div class="sd-card">
						<div class="sd-card-title">${__("To'lov turlari")} <span class="sd-scope"></span></div>
						<div class="sd-payments"></div>
					</div>
				</div>
				<div class="sd-card sd-discounts"></div>
				<div class="sd-card sd-waiters"></div>
				<div class="sd-card sd-table-card">
					<div class="sd-card-head">
						<div class="sd-card-title">${__("Mahsulotlar bo'yicha sotuv")}</div>
						<div class="sd-table-tools">
							<input type="search" class="form-control input-xs sd-search"
								placeholder="${__("Mahsulot qidirish...")}">
							<button class="btn btn-default btn-xs sd-export">
								${frappe.utils.icon("download", "xs")} ${__("Excel")}
							</button>
						</div>
					</div>
					<div class="sd-table-wrap sd-items"></div>
				</div>
			</div>
		`).appendTo(this.page.main);

		this.$root.on("click", ".sd-period", (e) => this.set_period($(e.currentTarget).data("period")));
		this.$root.on("input", ".sd-search", frappe.utils.debounce((e) => {
			this.search = (e.target.value || "").trim().toLowerCase();
			this.render_items();
		}, 150));
		this.$root.on("click", ".sd-export", () => this.export_excel());
		this.$root.on("click", ".sd-items th[data-sort]", (e) => {
			const field = $(e.currentTarget).data("sort");
			const dir = this.sort.field === field && this.sort.dir === "desc" ? "asc" : "desc";
			this.sort = { field, dir };
			this.render_items();
		});
		this.$root.on("click", ".sd-discount-row", (e) => {
			const invoice = $(e.currentTarget).data("invoice");
			this.open_discounts.has(invoice) ? this.open_discounts.delete(invoice) : this.open_discounts.add(invoice);
			$(e.currentTarget).toggleClass("open");
			this.$root.find(`.sd-discount-items[data-for="${CSS.escape(invoice)}"]`).toggle();
		});
		this.$root.on("click", ".sd-stale-toggle", () => this.$root.find(".sd-stale-list").toggle());
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
		const prev = this.data.previous;
		this.$root.find(".sd-compare").text(
			__("Solishtirish: {0} — {1}", [frappe.datetime.str_to_user(prev.from_date), frappe.datetime.str_to_user(prev.to_date)])
		);
		// Guruh filtri yoqilganda chek darajasidagi bloklar butun filial bo'yicha.
		this.$root.find(".sd-scope").html(this.scope_badge());

		this.render_alerts();
		this.render_kpis();
		this.render_timeline();
		this.render_payments();
		this.render_discounts();
		this.render_waiters();
		this.render_items();
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

	pct(v) {
		return `${format_number(flt(v), null, 1)}%`;
	}

	esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}

	date_time(date, time) {
		return `${frappe.datetime.str_to_user(date)}${time ? " " + time : ""}`;
	}

	// O'zgarish belgisi: `invert` — o'sish yomon (masalan chegirma).
	delta(cur, prev, invert = false) {
		cur = flt(cur);
		prev = flt(prev);
		if (!prev) return "";
		const change = ((cur - prev) / Math.abs(prev)) * 100;
		if (Math.abs(change) < 0.05) return `<span class="sd-delta">0%</span>`;
		const up = change > 0;
		const good = invert ? !up : up;
		return `<span class="sd-delta ${good ? "sd-good" : "sd-bad"}">${up ? "▲" : "▼"} ${format_number(Math.abs(change), null, 1)}%</span>`;
	}

	scope_badge() {
		return this.data.group_filter
			? `<span class="sd-badge" title="${__("Mahsulot guruhi filtri chek darajasidagi ko'rsatkichlarga ta'sir qilmaydi")}">${__("butun filial")}</span>`
			: "";
	}

	empty_state(text) {
		return `<div class="sd-empty">${frappe.utils.icon("chart", "lg")}<div>${text || __("Tanlangan davrda sotuv yo'q")}</div></div>`;
	}

	// Gorizontal ulush chiziqlari: [{label, value, hint}] — eng kattasi 100%.
	bar_list(rows, { tone = "primary", money = true } = {}) {
		if (!rows.length) return `<div class="sd-hint">${__("Ma'lumot yo'q")}</div>`;
		const max = Math.max(...rows.map((r) => Math.abs(flt(r.value)))) || 1;
		const total = rows.reduce((s, r) => s + flt(r.value), 0) || 1;
		return `<div class="sd-bars">${rows.map((r) => `
			<div class="sd-bar-row">
				<div class="sd-bar-top">
					<span class="sd-bar-label">${this.esc(r.label)}</span>
					<span class="sd-bar-value">${money ? this.money(r.value) : this.num(r.value)}
						<span class="text-muted">· ${this.pct((flt(r.value) / total) * 100)}</span></span>
				</div>
				<div class="sd-bar-track"><div class="sd-bar-fill sd-fill-${tone}" style="width:${(Math.abs(flt(r.value)) / max) * 100}%${r.color ? `;background:${r.color}` : ""}"></div></div>
				${r.hint ? `<div class="sd-bar-hint">${r.hint}</div>` : ""}
			</div>`).join("")}</div>`;
	}

	/* ─────────────────────────── Ogohlantirishlar ─────────────────────────── */

	render_alerts() {
		const o = this.data.open_orders;
		const $alerts = this.$root.find(".sd-alerts").empty();
		if (!o.stale_count) return;

		const list = o.stale.map((r) => `
			<tr>
				<td><a href="/app/pos-invoice/${encodeURIComponent(r.invoice)}">${this.esc(r.invoice)}</a></td>
				<td>${this.date_time(r.date, r.time)}</td>
				<td>${this.esc(r.table)}</td>
				<td>${this.esc(r.waiter_name)}</td>
				<td class="text-right">${this.money(r.amount)}</td>
			</tr>`).join("");

		$alerts.html(`
			<div class="sd-alert">
				<div>
					${frappe.utils.icon("es-line-alert-circle", "sm")}
					<b>${__("{0} ta buyurtma yopilmay qolgan", [o.stale_count])}</b>
					— ${__("jami {0}. Bular kechagi va undan oldingi to'lanmagan cheklar: to'lovini oling yoki bekor qiling.", [this.money(o.stale_amount)])}
					<a class="sd-stale-toggle">${__("Ro'yxat")}</a>
				</div>
				<table class="sd-table sd-stale-list" style="display:none">
					<thead><tr><th>${__("Chek")}</th><th>${__("Sana")}</th><th>${__("Stol")}</th><th>${__("Ofitsiant")}</th><th class="text-right">${__("Summa")}</th></tr></thead>
					<tbody>${list}</tbody>
				</table>
			</div>
		`);
	}

	/* ─────────────────────────── KPI ─────────────────────────── */

	render_kpis() {
		const c = this.data.checks, p = this.data.prev_checks;
		const t = this.data.totals, pt = this.data.prev_totals;
		const group = this.data.group_filter;
		const scope = this.scope_badge();

		const cards = [
			{
				label: __("Jami tushum"), tone: "primary", scope,
				value: this.money(c.revenue), delta: this.delta(c.revenue, p.revenue),
				hint: __("Mijozlar to'lagan: sof sotuv + xizmat haqi + choychaqa"),
			},
			group ? {
				label: __("Guruh sof sotuvi"), tone: "blue",
				value: this.money(t.net_amount), delta: this.delta(t.net_amount, pt.net_amount),
				hint: __("{0} dona, {1} xil mahsulot", [this.num(t.qty), t.items]),
			} : {
				label: __("Sof sotuv"), tone: "blue",
				value: this.money(c.net), delta: this.delta(c.net, p.net),
				hint: __("Taomlar, chegirmadan keyin · {0} dona", [this.num(t.qty)]),
			},
			{
				label: __("Xizmat haqi"), tone: "teal", scope,
				value: this.money(c.service), delta: this.delta(c.service, p.service),
				hint: c.tips ? __("Choychaqa alohida: {0}", [this.money(c.tips)]) : __("Ofitsiant xizmati uchun"),
			},
			{
				label: __("Chegirma"), tone: "red", scope,
				value: this.money(c.discount), delta: this.delta(c.discount, p.discount, true),
				hint: __("Yalpining {0} · {1} ta chekda", [this.pct(c.discount_percent), c.discounted_invoices]),
			},
			{
				label: __("Cheklar soni"), tone: "indigo", scope,
				value: this.num(c.invoices), delta: this.delta(c.invoices, p.invoices),
				hint: c.returns
					? __("Qaytarish: {0} ta, {1}", [c.returns, this.money(c.return_amount)])
					: __("Qaytarish yo'q"),
			},
			{
				label: __("O'rtacha chek"), tone: "orange", scope,
				value: this.money(c.avg_check), delta: this.delta(c.avg_check, p.avg_check),
				hint: __("Oldingi davr: {0}", [this.money(p.avg_check)]),
			},
		];

		this.$root.find(".sd-kpis").html(cards.map((k) => `
			<div class="sd-kpi sd-tone-${k.tone}">
				<div class="sd-kpi-label">${k.label} ${k.scope || ""}</div>
				<div class="sd-kpi-value">${k.value}</div>
				<div class="sd-kpi-delta">${k.delta || ""}</div>
				<div class="sd-kpi-hint">${k.hint}</div>
			</div>
		`).join(""));
	}

	/* ─────────────────────────── Grafiklar ─────────────────────────── */

	render_timeline() {
		const tl = this.data.timeline;
		const $el = this.$root.find(".sd-chart-timeline").empty();
		this.$root.find(".sd-timeline-note").text(tl.mode === "month" ? __("(oylar bo'yicha)") : "");
		if (!this.data.items.length) return $el.html(this.empty_state());

		const label = (key) => tl.mode === "month"
			? moment(key, "YYYY-MM").format("MM.YYYY")
			: frappe.datetime.str_to_user(key).slice(0, 5);

		new frappe.Chart($el[0], {
			type: "axis-mixed",
			height: 260,
			colors: ["#2490ef", "#e24c4c"],
			data: {
				labels: tl.rows.map((r) => label(r.key)),
				datasets: [
					{ name: __("Sof sotuv"), chartType: "bar", values: tl.rows.map((r) => flt(r.net_amount)) },
					{ name: __("Chegirma"), chartType: "line", values: tl.rows.map((r) => flt(r.discount)) },
				],
			},
			axisOptions: {
				// Bitta kun (bitta ustun) bo'lganda ham o'q 0 dan boshlanadi — aks holda
				// frappe-charts o'qni qiymat atrofidan boshlab, ustun legenda ustiga tushardi.
				yAxisRange: { min: 0 },
				xIsSeries: tl.rows.length > 1 ? 1 : 0,
				xAxisMode: "tick",
				shortenYAxisNumbers: 1,
				numberFormatter: (v) => this.short_money(v),
			},
			barOptions: { spaceRatio: 0.4 },
			lineOptions: { regionFill: 0, dotSize: 3 },
			tooltipOptions: { formatTooltipY: (v) => this.money(v) },
		});
	}

	render_payments() {
		const c = this.data.checks;
		const rows = this.data.payments.map((p) => ({
			label: p.mode, value: p.amount, hint: __("{0} ta chek", [p.invoices]),
		}));
		const paid = this.data.payments.reduce((s, p) => s + flt(p.amount), 0);
		this.$root.find(".sd-payments").html(`
			${this.bar_list(rows)}
			<div class="sd-total-line"><span>${__("Jami")}</span><b>${this.money(paid)}</b></div>
			${Math.abs(paid - flt(c.revenue)) > 1
				? `<div class="sd-hint">${__("Tushumdan farq: {0}", [this.money(paid - c.revenue)])}</div>` : ""}
		`);
	}

	/* ─────────────────────────── Chegirmalar ─────────────────────────── */

	render_discounts() {
		const d = this.data.discounts, c = this.data.checks;
		const $card = this.$root.find(".sd-discounts");
		const head = `
			<div class="sd-card-head">
				<div class="sd-card-title">${__("Chegirmalar")} ${this.scope_badge()}</div>
				<div class="sd-card-sub">${__("{0} ta chek · jami {1} · yalpining {2}", [d.rows.length, this.money(c.discount), this.pct(c.discount_percent)])}</div>
			</div>`;

		if (!d.rows.length) {
			$card.html(`${head}<div class="sd-hint">${__("Tanlangan davrda chegirma berilmagan.")}</div>`);
			return;
		}

		// Chegirma turlari (sabab) — har biri alohida qator va o'z rangida;
		// qatorda kim bergani va nechta chek ekani ham ko'rinadi.
		const types = {};
		for (const r of d.rows) {
			const t = (types[r.reason] ||= { reason: r.reason, discount: 0, count: 0, by: {} });
			t.discount += flt(r.discount);
			t.count += 1;
			t.by[r.user_name] = (t.by[r.user_name] || 0) + flt(r.discount);
		}
		const palette = ["#e24c4c", "#f59e0b", "#2490ef", "#10b981", "#8b5cf6", "#ec4899", "#14b8a6", "#64748b"];
		const discount_types = Object.values(types)
			.sort((a, b) => b.discount - a.discount)
			.map((t, i) => {
				const by = Object.entries(t.by).sort((a, b) => b[1] - a[1]);
				const who = by.length === 1
					? by[0][0]
					: by.map(([name, amount]) => `${name} ${this.short_money(amount)}`).join(", ");
				return {
					label: t.reason,
					value: t.discount,
					color: palette[i % palette.length],
					hint: `${__("{0} ta chek", [t.count])} · ${this.esc(who)}`,
				};
			});

		// Taomlar — hammasi, blok ichida vertikal scroll (yon blok kichik:
		// sahifalashdan ko'ra aylantirish tezroq va qulayroq).
		const dishes = d.by_item.map((r) => ({ label: r.item_name, value: r.discount, hint: __("{0} dona", [this.num(r.qty)]) }));

		const rows = d.rows.map((r) => {
			const open = this.open_discounts.has(r.invoice);
			const items = r.items.map((it) => `
				<tr>
					<td>${this.esc(it.item_name)}</td>
					<td class="text-right">${this.num(it.qty)}</td>
					<td class="text-right">${this.money(it.gross)}</td>
					<td class="text-right sd-neg">${Math.abs(it.discount) > 0.5 ? "−" + this.money(it.discount) : "—"}</td>
					<td class="text-right">${this.money(it.gross - it.discount)}</td>
				</tr>`).join("");
			return `
				<tr class="sd-discount-row ${open ? "open" : ""}" data-invoice="${this.esc(r.invoice)}">
					<td><span class="sd-caret">▸</span>
						<a href="/app/pos-invoice/${encodeURIComponent(r.invoice)}" onclick="event.stopPropagation()">${this.esc(r.invoice)}</a></td>
					<td>${this.date_time(r.date, r.time)}</td>
					<td><b>${this.esc(r.user_name)}</b>${r.given_at ? `<div class="sd-code">${this.esc(r.given_at.slice(11))}</div>` : ""}</td>
					<td>${this.esc(r.reason)}</td>
					<td class="text-right"><span class="sd-pill sd-pill-orange">${this.pct(r.percent)}</span></td>
					<td class="text-right">${this.money(r.gross)}</td>
					<td class="text-right sd-neg">−${this.money(r.discount)}</td>
					<td class="text-right"><b>${this.money(r.amount)}</b></td>
					<td>${this.esc(r.waiter_name)}${r.table ? `<div class="sd-code">${this.esc(r.table)}</div>` : ""}</td>
				</tr>
				<tr class="sd-discount-items" data-for="${this.esc(r.invoice)}" ${open ? "" : 'style="display:none"'}>
					<td colspan="9">
						<table class="sd-subtable">
							<thead><tr><th>${__("Taom")}</th><th class="text-right">${__("Soni")}</th><th class="text-right">${__("Narxi")}</th><th class="text-right">${__("Chegirma ulushi")}</th><th class="text-right">${__("Sof")}</th></tr></thead>
							<tbody>${items}</tbody>
						</table>
					</td>
				</tr>`;
		}).join("");

		$card.html(`
			${head}
			<div class="sd-discount-layout">
				<div class="sd-summary sd-summary-scroll">
					<div class="sd-summary-title">${__("Chegirma turlari")} <span class="text-muted">· ${discount_types.length}</span></div>
					<div class="sd-scroll">${this.bar_list(discount_types, { tone: "red" })}</div>
				</div>
				<div class="sd-summary sd-summary-scroll">
					<div class="sd-summary-title">${__("Taomlar")} <span class="text-muted">· ${dishes.length}</span></div>
					<div class="sd-scroll">${this.bar_list(dishes, { tone: "indigo" })}</div>
				</div>
			</div>
			<div class="sd-table-wrap">
				<table class="sd-table">
					<thead><tr>
						<th>${__("Chek")}</th><th>${__("Sana")}</th><th>${__("Kim berdi")}</th><th>${__("Sabab")}</th>
						<th class="text-right">${__("Foiz")}</th><th class="text-right">${__("Chegirmagacha")}</th>
						<th class="text-right">${__("Chegirma")}</th><th class="text-right">${__("To'langan")}</th>
						<th>${__("Ofitsiant / stol")}</th>
					</tr></thead>
					<tbody>${rows}</tbody>
				</table>
			</div>
			<div class="sd-hint">${__("Qatorni bosing — chegirma qaysi taomlarga taqsimlangani ko'rinadi.")}</div>
		`);
	}

	/* ─────────────────────────── Ofitsiantlar ─────────────────────────── */

	render_waiters() {
		const rows = this.data.waiters;
		const $card = this.$root.find(".sd-waiters");
		const head = `<div class="sd-card-title">${__("Ofitsiantlar")} ${this.scope_badge()}</div>`;
		if (!rows.length) return $card.html(`${head}<div class="sd-hint">${__("Ma'lumot yo'q")}</div>`);

		const total = rows.reduce((s, r) => s + flt(r.revenue), 0) || 1;
		$card.html(`
			${head}
			<div class="sd-table-wrap">
				<table class="sd-table">
					<thead><tr>
						<th>${__("Ofitsiant")}</th><th class="text-right">${__("Cheklar")}</th>
						<th class="text-right">${__("Tushum")}</th><th class="text-right">${__("Ulushi")}</th>
						<th class="text-right">${__("O'rtacha chek")}</th><th class="text-right">${__("Xizmat haqi")}</th>
						<th class="text-right">${__("Chegirma")}</th>
					</tr></thead>
					<tbody>${rows.map((r) => `
						<tr>
							<td><b>${this.esc(r.name)}</b></td>
							<td class="text-right">${this.num(r.invoices)}</td>
							<td class="text-right"><b>${this.money(r.revenue)}</b></td>
							<td class="text-right">${this.pct((flt(r.revenue) / total) * 100)}</td>
							<td class="text-right">${this.money(r.avg_check)}</td>
							<td class="text-right">${this.money(r.service)}</td>
							<td class="text-right">${flt(r.discount) ? `<span class="sd-neg">−${this.money(r.discount)}</span>` : `<span class="text-muted">—</span>`}</td>
						</tr>`).join("")}
					</tbody>
				</table>
			</div>
		`);
	}

	/* ─────────────────────────── Mahsulotlar jadvali ─────────────────────────── */

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
			{ field: "share", label: __("Ulushi"), sortable: false },
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

	render_items() {
		if (!this.data) return;
		const $wrap = this.$root.find(".sd-items");

		if (!this.data.items.length) {
			$wrap.html(this.empty_state());
			return;
		}

		const cols = this.columns();
		const rows = this.visible_rows();
		const all_net = flt(this.data.totals.net_amount) || 1;

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
					return `<a href="/app/item/${encodeURIComponent(r.item_code)}" class="sd-item">${this.esc(r.item_name || r.item_code)}</a>
						<div class="sd-code">${this.esc(r.item_code)}</div>`;
				case "item_group":
					return `<span class="sd-group">${this.esc(v || "")}</span>`;
				case "discounted_qty":
					return flt(v) ? `<span class="sd-pill sd-pill-orange">${this.num(v)}</span>` : `<span class="text-muted">—</span>`;
				case "discount":
					return flt(v) ? `<span class="sd-neg">−${this.money(v)}</span>` : `<span class="text-muted">—</span>`;
				case "net_amount":
					return `<b>${this.money(v)}</b>`;
				case "share": {
					const share = (flt(r.net_amount) / all_net) * 100;
					return `<div class="sd-share"><div class="sd-share-fill" style="width:${Math.max(0, Math.min(share, 100))}%"></div><span>${this.pct(share)}</span></div>`;
				}
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
			<td></td>
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
		// Fayl serverda quriladi (bir nechta varaq) — ekrandagi qidiruv va saralash bilan.
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
