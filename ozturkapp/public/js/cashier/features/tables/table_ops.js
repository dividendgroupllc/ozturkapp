/**
 * Stolni ko'chirish, stollarni birlashtirish va ajratish (POS Profile bayrog'i
 * `table_transfer`).
 *
 * Amallar «Yana ⋯» varag'ida (`panel.more`) va faqat stoli bor hisobda
 * ko'rinadi: olib ketish / yetkazib berish buyurtmasida stol yo'q. Qoidalarni
 * (bo'sh stol, bir zal, bron egasi, hisob holati) SERVER hal qiladi
 * (`utils/order_transfer.py`); bu yerda faqat mos kelmaydigan kartalar
 * bosilmasligi uchun qulaylik bor, rad etilsa serverning matni oynada chiqadi.
 */

import { loadTables, METHODS, reservationIsFor, roomNames } from "./table_data.js";
import { tablePicker } from "./table_picker.js";

const { slots, ui, util } = ozturk.cashier;
const { hhmm } = util;

const isTableBill = (screen, detail) =>
	detail.kind === "bill" &&
	!!detail.table &&
	!!detail.bill &&
	!!detail.bill.table &&
	!detail.bill.paid &&
	!detail.bill.cancelled;

/** Smena yopiq bo'lsa server baribir rad etadi — sababni oldindan aytamiz. */
const shiftClosed = (screen) =>
	screen.ctx.shift && !screen.ctx.shift.open ? __("Kassa yopiq") : false;

/** Chek turgan (asosiy) stol va birlashtirilgan sheriklari. */
const clusterOf = (detail) => new Set(detail.cluster || [detail.bill.table]);

// ═══════════════════════════════════════════════════════════════
//  Umumiy: stol tanlash oynasi
// ═══════════════════════════════════════════════════════════════

/**
 * Stol tanlash oynasi. Tasdiqlash tugmasi tanlov bo'lmaguncha o'chiq;
 * `run(value)` serverni chaqiradi, muvaffaqiyatda oyna server javobi bilan
 * yopiladi, xatoda ochiq qoladi va matn oynada chiqadi.
 *
 * Kassir so'rov ketayotganda oynani yopib yuborsa (Esc, ×) amal baribir
 * bajariladi — natija yo'qolmasligi uchun kutib olinadi.
 *
 * @returns {Promise<object|null>}  server javobi; bekor qilinsa `null`
 */
function pickerDialog(screen, options) {
	const { title, subtitle, note, legend, submitLabel, kind = "primary", run } = options;
	let pending = null;

	const picker = tablePicker({ ...options.picker, onChange: (value) => sync(value) });

	const body = document.createElement("div");
	body.appendChild(picker.el);
	if (legend) {
		const text = document.createElement("p");
		text.className = "rc-tt__legend";
		text.textContent = legend;
		body.appendChild(text);
	}

	const dialog = ui.dialog({
		title,
		subtitle,
		note,
		size: "lg",
		body,
		actions: [
			{ id: "cancel", label: __("Yopish"), onClick: (d) => d.close(null) },
			{
				id: "submit",
				label: submitLabel(picker.value()),
				kind,
				disabled: true,
				onClick: async (d) => {
					d.setError("");
					d.setBusy(true);
					try {
						pending = run(picker.value());
						d.close(await pending);
					} catch (error) {
						if (d.closed) screen.alertError(error);
						else d.setError(screen.errorText(error));
					} finally {
						d.setBusy(false);
					}
				},
			},
		],
	});

	function sync(value) {
		const submit = dialog.footEl.querySelector('[data-action="submit"]');
		submit.disabled = !(Array.isArray(value) ? value.length : value);
		submit.textContent = submitLabel(value);
	}

	return dialog.result.then((value) => value || (pending && pending.catch(() => null)) || null);
}

/** Stollar ro'yxatini yuklaydi (tugma band turadi) va oynani ochadi. */
async function withTables(screen, button, open) {
	let tables;
	screen.busy(button, true);
	try {
		tables = await loadTables(screen);
	} catch (error) {
		screen.alertError(error);
		return;
	} finally {
		screen.busy(button, false);
	}
	await open(tables);
}

/**
 * Amaldan keyin ekranni yangilaydi. Buyurtma boshqa stolga o'tgan bo'lsa,
 * kassir uni yo'qotib qo'ymasligi uchun panel yangi stolga o'tadi.
 */
async function refreshAfter(screen, focus) {
	try {
		const visible = ((screen.floor || {}).tables || []).some((table) => table.name === focus);
		if (visible) await screen.selectTable(focus);
		await screen.refresh({ floor: true, orders: true, panel: !visible });
	} catch (error) {
		screen.alertError(error);
	}
}

/** Bron o'tirgan mehmonniki bo'lsa stol bosiladi, aks holda — o'chiq. */
function reservedEntry(table, bill) {
	const reservation = table.reservation || {};
	const guest = reservation.customer_name || __("Bron");
	const mine = reservationIsFor(reservation, bill);
	return {
		name: table.name,
		room: table.restaurant_room,
		tone: "reserved",
		mark: "◆",
		text: [hhmm(reservation.from_time), guest].filter(Boolean).join(" · "),
		disabled: !mine,
		reason: mine ? "" : __("{0} uchun bron qilingan", [guest]),
	};
}

function freeEntry(table) {
	return {
		name: table.name,
		room: table.restaurant_room,
		tone: "free",
		mark: "●",
		text: `${cint(table.no_of_seats)} ${__("o'rin")}`,
	};
}

/** Nishon bo'lishi mumkin bo'lgan stollar: bo'sh (yoki shu mehmon broni). Bandlari ko'rsatilmaydi. */
function targetEntries(tables, detail, sameRoomOnly) {
	const own = clusterOf(detail);
	const room = detail.room || detail.bill.room;

	return tables
		.filter((table) => !own.has(table.name) && table.status !== "OCCUPIED")
		.filter((table) => !sameRoomOnly || table.restaurant_room === room)
		.map((table) =>
			table.status === "RESERVED" ? reservedEntry(table, detail.bill) : freeEntry(table)
		);
}

const RESERVED_LEGEND = __(
	"◆ Bron qilingan stol boshqa mehmon uchun band — avval «⋯ ▸ Bronlar» orqali bronni bekor qiling."
);

// ═══════════════════════════════════════════════════════════════
//  Ko'chirish
// ═══════════════════════════════════════════════════════════════

async function transferTable(screen, detail, button) {
	const bill = detail.bill;

	await withTables(screen, button, async (tables) => {
		const entries = targetEntries(tables, detail, false);

		const result = await pickerDialog(screen, {
			title: __("Stolni ko'chirish"),
			subtitle: `${bill.invoice} · ${bill.table}`,
			note: __("Buyurtma qaysi stolga ko'chirilsin?"),
			legend: entries.some((entry) => entry.tone === "reserved") ? RESERVED_LEGEND : "",
			picker: {
				entries,
				rooms: roomNames(screen),
				room: detail.room || bill.room,
				emptyText: __("Bo'sh stol yo'q"),
			},
			submitLabel: (to) => (to ? __("{0} ga ko'chirish", [to]) : __("Ko'chirish")),
			run: (to_table) => screen.call(METHODS.transfer, { invoice: bill.invoice, to_table }),
		});
		if (!result) return;

		const lines = [__("{0} → {1} ko'chirildi", [result.from_table, result.to_table])];
		if ((result.freed_tables || []).length) {
			lines.push(__("Bo'shagan stol: {0}", [result.freed_tables.join(", ")]));
		}
		if (result.reservation) lines.push(__("Bron o'tirdi deb belgilandi"));
		ui.toast(lines.join("\n"));

		await refreshAfter(screen, result.to_table);
		if (result.billed) await offerReprint(screen, result);
	});
}

/**
 * Hisob mijozga allaqachon berilgan edi — chekda eski stol nomi qolgan.
 * Qayta chop etish kassir qo'lida: ba'zan chek qo'lda almashtiriladi.
 */
async function offerReprint(screen, result) {
	const again = await ui.confirm({
		title: __("Hisobni qayta chop etish"),
		message: __(
			"Hisob mijozga allaqachon berilgan edi. Stol {0} dan {1} ga o'zgargani uchun chekni qayta chop etasizmi?",
			[result.from_table, result.to_table]
		),
		confirmLabel: __("Chop etish"),
		cancelLabel: __("Keyinroq"),
	});
	if (!again) return;

	try {
		const printed = await screen.call(METHODS.printBill, { invoice: result.invoice });
		if (printed && printed.queued) {
			ui.toast(
				printed.agent_online
					? __("Chek printerga yuborildi")
					: __("Chek navbatga qo'yildi — print-agent hozir oflayn"),
				{ indicator: printed.agent_online ? "green" : "orange" }
			);
		} else {
			ui.toast(__("Filialga kassa printeri biriktirilmagan (Ozturk Printer)."), {
				indicator: "red",
			});
		}
	} catch (error) {
		screen.alertError(error);
	}
}

// ═══════════════════════════════════════════════════════════════
//  Birlashtirish
// ═══════════════════════════════════════════════════════════════

async function mergeTables(screen, detail, button) {
	const bill = detail.bill;
	const room = detail.room || bill.room;

	await withTables(screen, button, async (tables) => {
		const entries = targetEntries(tables, detail, true);

		const result = await pickerDialog(screen, {
			title: __("Stollarni birlashtirish"),
			subtitle: `${bill.invoice} · ${bill.table}`,
			note: __(
				"Zal: {0}. Faqat shu zaldagi bo'sh stollarni birlashtirish mumkin — hisob bitta bo'lib qoladi. Bir nechta stol tanlang.",
				[room]
			),
			legend: entries.some((entry) => entry.tone === "reserved") ? RESERVED_LEGEND : "",
			picker: {
				entries,
				rooms: roomNames(screen),
				multiple: true,
				emptyText: __("Bo'sh stol yo'q"),
			},
			submitLabel: (chosen) =>
				chosen.length ? __("Birlashtirish ({0})", [chosen.length]) : __("Birlashtirish"),
			run: (chosen) =>
				screen.call(METHODS.merge, {
					invoice: bill.invoice,
					tables: JSON.stringify(chosen),
				}),
		});
		if (!result) return;

		ui.toast(__("Stollar birlashtirildi: {0}", [result.cluster.join(" + ")]));
		await refreshAfter(screen, result.table);
	});
}

// ═══════════════════════════════════════════════════════════════
//  Ajratish
// ═══════════════════════════════════════════════════════════════

/**
 * Asosiy stol (chek turgan stol) ajratilmaydi — u ro'yxatda o'chirilgan
 * karta bo'lib turadi, kassir nima uchun tanlab bo'lmasligini ko'rsin.
 */
async function unmergeTable(screen, detail) {
	const bill = detail.bill;
	const primary = bill.table;
	const room = detail.room || bill.room;

	const entries = [...clusterOf(detail)].map((name) => ({
		name,
		room,
		tone: name === primary ? "primary" : "merged",
		mark: name === primary ? "★" : "⛓",
		text: name === primary ? __("Asosiy") : __("Birlashgan"),
		disabled: name === primary,
		reason: name === primary ? __("Asosiy stolni ajratib bo'lmaydi") : "",
	}));

	const result = await pickerDialog(screen, {
		title: __("Stolni ajratish"),
		subtitle: `${bill.invoice} · ${primary}`,
		note: __("Qaysi stol ajratilsin? Ajratilgan stol bo'shaydi."),
		legend: __(
			"★ Asosiy stol ({0}) — chek shu stolda turibdi, uni ajratib bo'lmaydi. Uni almashtirish uchun buyurtmani boshqa stolga ko'chiring.",
			[primary]
		),
		picker: { entries, rooms: [room] },
		kind: "danger-solid",
		submitLabel: (table) => (table ? __("{0} ni ajratish", [table]) : __("Ajratish")),
		run: (table) => screen.call(METHODS.unmerge, { invoice: bill.invoice, table }),
	});
	if (!result) return;

	ui.toast(__("{0} ajratildi va bo'shatildi", [result.released_table]));
	await refreshAfter(screen, result.table);
}

// ═══════════════════════════════════════════════════════════════
//  Ulash
// ═══════════════════════════════════════════════════════════════

export function installTransfer() {
	return [
		slots.contribute("panel.more", {
			id: "table-transfer",
			order: 310,
			label: __("Stolni ko'chirish"),
			when: isTableBill,
			disabled: (screen, detail) =>
				detail.is_merged
					? __("Avval birlashtirilgan stollarni ajrating")
					: shiftClosed(screen),
			onClick: (screen, detail, button) => transferTable(screen, detail, button),
		}),
		slots.contribute("panel.more", {
			id: "table-merge",
			order: 320,
			label: __("Stollarni birlashtirish"),
			when: isTableBill,
			disabled: (screen) => shiftClosed(screen),
			onClick: (screen, detail, button) => mergeTables(screen, detail, button),
		}),
		slots.contribute("panel.more", {
			id: "table-unmerge",
			order: 330,
			label: __("Ajratish"),
			when: (screen, detail) => isTableBill(screen, detail) && !!detail.is_merged,
			disabled: (screen) => shiftClosed(screen),
			onClick: (screen, detail) => unmergeTable(screen, detail),
		}),
	];
}
