/**
 * X-hisobot — smena ochiq turganda oraliq hisobot (ko'rish va chop etish).
 *
 * KO'R SANOQ (`utils/shift_report.py`)
 * ====================================
 * Kassir (yoki smenani o'zi ochgan menejer) uchun server `restricted: true`
 * qaytaradi va quyidagilarni `None` qiladi:
 *
 *     sales.*                                  savdo jami (SALES_LINES)
 *     payments[].sales_amount / refund_amount / net_amount   NAQD usullar uchun
 *     cash.expected / cash.difference          kutilgan summa va farq
 *
 * (`cash.counted` ham `None` — X da sanoq yo'q; Z dan keyingina to'ladi.)
 *
 * `None` "0" degani EMAS: yashirilgan qiymat chizilmaydi — qator ham,
 * katak ham bo'sh qoladi. Buning UCHUN summa FAQAT `amount()` orqali
 * chiziladi (bu faylda `money()` ning yagona chaqiruvi shu yerda), u esa
 * `None` da `null` qaytaradi. Menejer `restricted: false` bilan hamma
 * qiymatni oladi va ular shu yo'l bilan ko'rinadi.
 */

import { esc, features, hhmm, isHidden, shiftIsOpen, slots, ui } from "./shared.js";

const SALES_LINES = [
	{ key: "gross_sales", label: __("Yalpi savdo") },
	{ key: "discounts", label: __("Chegirmalar"), negative: true },
	{ key: "service_charge", label: __("Xizmat haqi") },
	{ key: "tips", label: __("Choychaqa") },
	{ key: "other_taxes", label: __("Boshqa soliqlar") },
	{ key: "rounding", label: __("Yaxlitlash") },
	{ key: "sales_total", label: __("Sotuv jami"), strong: true },
	{ key: "returns_total", label: __("Qaytarishlar"), negative: true },
	{ key: "net_total", label: __("Sof savdo"), strong: true },
];

const CASH_LINES = [
	{ key: "opening", label: __("Boshlang'ich (ochilishda sanalgan)") },
	{ key: "counted", label: __("Sanalgan") },
	{ key: "expected", label: __("Kutilgan") },
	{ key: "difference", label: __("Farq"), strong: true },
];

const COUNTS = [
	{ key: "invoices", label: __("Cheklar") },
	{ key: "returns", label: __("Qaytarishlar") },
	{ key: "cancelled_orders", label: __("Bekor qilingan buyurtmalar") },
	{ key: "drawer_no_sale", label: __("G'aladon (savdosiz)") },
];

/** Summa matni; yashirilgan (`None`) qiymat uchun `null` — hech qachon «0». */
function amount(screen, value) {
	return isHidden(value) ? null : screen.money(value);
}

/** Jadval katagi uchun: yashirilgan qiymat bo'sh katak. */
function cell(screen, value) {
	const text = amount(screen, value);
	return text === null ? "" : esc(text);
}

/** «Yorliq ........ summa» qatori; qiymat yashirilgan bo'lsa qator UMUMAN chizilmaydi. */
function line(screen, source, { key, label, negative = false, strong = false }) {
	const value = source ? source[key] : undefined;
	const text = amount(screen, value);
	if (text === null) return "";

	const sign = negative && flt(value) ? "−" : "";
	return `<div class="rc-shift-xr__line${strong ? " rc-shift-xr__line--strong" : ""}">
		<span>${esc(label)}</span><b>${esc(sign + text)}</b></div>`;
}

function section(title, inner, modifier = "") {
	if (!inner) return "";
	return `<section class="rc-shift-xr__sec ${modifier}">
		<h3 class="rc-shift-xr__h">${esc(title)}</h3>${inner}</section>`;
}

const none = () => `<p class="rc-shift-xr__none">${esc(__("Yo'q"))}</p>`;

function paymentsHtml(screen, payments) {
	if (!payments.length) return "";

	return `<table class="rc-shift-xr__table">
		<thead><tr>
			<th>${esc(__("Usul"))}</th>
			<th class="rc-shift-xr__num">${esc(__("Cheklar"))}</th>
			<th class="rc-shift-xr__num">${esc(__("Sotuv"))}</th>
			<th class="rc-shift-xr__num">${esc(__("Qaytarish"))}</th>
			<th class="rc-shift-xr__num">${esc(__("Sof"))}</th>
		</tr></thead>
		<tbody>${payments
			.map(
				(pay) => `<tr>
				<td>${esc(pay.mode_of_payment)}${
					pay.is_cash ? ` <span class="rc-tag">${esc(__("Naqd"))}</span>` : ""
				}</td>
				<td class="rc-shift-xr__num">${cint(pay.sales_count)}</td>
				<td class="rc-shift-xr__num">${cell(screen, pay.sales_amount)}</td>
				<td class="rc-shift-xr__num">${cell(screen, pay.refund_amount)}</td>
				<td class="rc-shift-xr__num rc-shift-xr__strong">${cell(screen, pay.net_amount)}</td>
			</tr>`
			)
			.join("")}</tbody>
	</table>`;
}

function movementsHtml(screen, movements) {
	const items = (movements && movements.items) || [];
	if (!items.length) return none();

	return items
		.map((item) => {
			const isIn = item.kind === "In";
			return `<div class="rc-shift-xr__row">
				<span class="rc-shift-xr__time">${esc(hhmm(item.posting_datetime))}</span>
				<span class="rc-shift-xr__kind rc-shift-xr__kind--${isIn ? "in" : "out"}">${isIn ? "▲" : "▼"} ${esc(
				isIn ? __("Kirim") : __("Chiqim")
			)}</span>
				<span>${esc(item.category)}</span>
				<b class="rc-shift-xr__num">${isIn ? "+" : "−"}${cell(screen, item.amount)}</b>
				<span class="rc-shift-xr__why">${esc(item.reason)}</span>
			</div>`;
		})
		.join("");
}

function drawerHtml(openings, total) {
	if (!openings.length) return none();

	// Server ro'yxatni qisqartiradi (`MAX_DRAWER_ROWS`), sanoq esa to'liq.
	const more = cint(total) - openings.length;
	return (
		openings
			.map(
				(item) => `<div class="rc-shift-xr__row">
				<span class="rc-shift-xr__time">${esc(hhmm(item.time))}</span>
				<span>${esc(item.user_name || item.user)}</span>
				<span class="rc-shift-xr__why">${esc(item.reason)}</span>
			</div>`
			)
			.join("") +
		(more > 0 ? `<p class="rc-shift-xr__none">${esc(__("va yana {0} ta", [more]))}</p>` : "")
	);
}

/** Hisobotning to'liq HTML'i. Barcha server matnlari ekranlanadi. */
function reportHtml(screen, report) {
	const counts = report.counts || {};
	const cashier = report.cashier || {};
	const cashLines = CASH_LINES.map((def) => line(screen, report.cash, def)).join("");
	const salesLines = SALES_LINES.map((def) => line(screen, report.sales, def)).join("");
	// Kassirda «Savdo» butunlay yo'q — bo'sh ustun qoldirmaymiz.
	const inner = section(__("Savdo"), salesLines) + section(__("Naqd pul"), cashLines);
	const columns = inner ? `<div class="rc-shift-xr__cols">${inner}</div>` : "";

	return `
		<div class="rc-shift-xr__meta">
			<span>${esc(__("Kassir"))}: <b>${esc(cashier.full_name || cashier.user)}</b></span>
			<span>${esc(__("Ochilgan"))}: <b>${esc(String(report.period_start || "").slice(0, 16))}</b></span>
			<span>${esc(__("Hozir"))}: <b>${esc(String(report.generated_at || "").slice(0, 16))}</b></span>
		</div>
		<div class="rc-shift-xr__tiles">${COUNTS.map(
			(def) => `<div class="rc-shift-xr__tile">
				<b>${cint(counts[def.key])}</b><span>${esc(def.label)}</span></div>`
		).join("")}</div>
		${
			report.restricted
				? `<p class="rc-shift-xr__note">${esc(
						__("Kutilayotgan summa va farq faqat menejerga ko'rinadi")
				  )}</p>`
				: ""
		}
		${section(__("To'lov usullari"), paymentsHtml(screen, report.payments || []))}
		${columns}
		${section(__("Kassa harakati"), movementsHtml(screen, report.cash_movements))}
		${section(
			__("G'aladon (savdosiz)"),
			drawerHtml(report.drawer_openings || [], counts.drawer_no_sale)
		)}`;
}

/**
 * Server javobini natijaga aylantiradi. `{queued, kind, agent_online}` —
 * navbatga tushdi; `{queued: null, reason}` — printer yo'q.
 */
function printOutcomeOf(result) {
	if (result && result.queued) {
		if (result.agent_online) {
			return { toast: __("X-hisobot chop etilmoqda"), indicator: "green", seconds: 4 };
		}
		return {
			toast: __("Printer agenti oflayn — hisobot agent ulangach chop etiladi."),
			indicator: "orange",
			seconds: 8,
		};
	}
	if (result && result.reason === "no_printer") {
		return { error: __("Kassa printeri sozlanmagan — hisobotni chop etib bo'lmadi.") };
	}
	return { error: __("Hisobot chop etish navbatiga tushmadi. Qayta urinib ko'ring.") };
}

export function openReport(screen) {
	if (ui.hasDialog() || !shiftIsOpen(screen)) return;

	const host = document.createElement("div");
	host.className = "rc-shift-xr";
	host.innerHTML = `<p class="rc-shift-xr__none">${esc(__("Yuklanmoqda…"))}</p>`;
	let busy = false;
	let loaded = false;

	// Tugmalar ish paytida band: ikki marta bosish ikki qog'oz yoki ikki so'rov bo'lmasin.
	const run = async (task) => {
		if (busy || dialog.closed) return;
		busy = true;
		dialog.setError("");
		dialog.setBusy(true);
		try {
			await task();
		} catch (error) {
			dialog.setError(screen.errorText(error));
		} finally {
			busy = false;
			dialog.setBusy(false);
		}
	};

	const load = () =>
		run(async () => {
			try {
				const report = await screen.call(
					"ozturkapp.ozturkapp.api.cashier.get_shift_report",
					{
						kind: "X",
					}
				);
				if (dialog.closed) return;
				host.innerHTML = reportHtml(screen, report);
				loaded = true;
			} catch (error) {
				// Birinchi yuklash yiqilsa «Yuklanmoqda…» qolib ketmasin; yangilash yiqilsa
				// oxirgi hisobot turadi (xato oynaning o'zida ko'rinadi).
				if (!loaded) host.textContent = "";
				throw error;
			}
		});

	const print = () =>
		run(async () => {
			const result = await screen.call(
				"ozturkapp.ozturkapp.api.printing.print_shift_report",
				{ kind: "X" }
			);
			const outcome = printOutcomeOf(result);
			if (outcome.error) dialog.setError(outcome.error);
			else
				ui.toast(outcome.toast, { indicator: outcome.indicator, seconds: outcome.seconds });
		});

	const dialog = ui.dialog({
		title: __("X-hisobot"),
		subtitle: __("Oraliq hisobot — smena ochiq"),
		size: "lg",
		body: host,
		actions: [
			{ id: "refresh", label: __("Yangilash"), onClick: load },
			{ id: "print", label: __("Chop etish"), kind: "primary", onClick: print },
		],
	});

	load();
}

features.register({
	key: "shift-reports",
	flag: "shift_reports",
	install() {
		return slots.contribute("topbar.menu", {
			id: "shift-x-report",
			order: 420,
			label: __("X-hisobot"),
			when: shiftIsOpen,
			onClick: (screen) => openReport(screen),
		});
	},
});
