/**
 * Bronlar: «⋯» menyusidagi «Bronlar» oynasi (kun bo'yicha ro'yxat, bekor qilish,
 * yangi bron) va zal rejasidagi «⏰ SS:DD» belgisi.
 *
 * Funksiya POS Profile bayrog'iga bog'liq emas: bronni har bir kassir ko'radi.
 * Bron qoidalari (bugungi band stol, ikki bron to'qnashuvi, o'tgan sana)
 * serverda; rad etilsa serverning matni oynada chiqadi.
 */

import { dayOptions, loadTables, longDay, METHODS, minutesUntil, roomNames } from "./table_data.js";
import { tablePicker } from "./table_picker.js";

const { slots, ui, util } = ozturk.cashier;
const { esc, hhmm } = util;

/** Bron holatlari: matn + belgi (rangga tayanmaslik uchun). */
const STATUSES = {
	Pending: { label: __("Kutilmoqda"), mark: "◇" },
	Confirmed: { label: __("Tasdiqlangan"), mark: "◆" },
	Seated: { label: __("O'tirgan"), mark: "●" },
	"No Show": { label: __("Kelmadi"), mark: "✕" },
	Completed: { label: __("Yakunlangan"), mark: "✓" },
};

/** Faqat kutilayotgan bronni bekor qilish mantiqli: o'tirgan mehmonning broni tugagan. */
const CANCELLABLE = new Set(["Pending", "Confirmed"]);

/** Zal rejasidagi «⏰» belgisi: boshlanishiga shuncha daqiqa qolganda chiqadi... */
const BADGE_AHEAD_MINUTES = 60;
/** ...va mehmon kechikkan bo'lsa ham shuncha daqiqa saqlanadi (hali kutilyapti). */
const BADGE_LATE_MINUTES = 30;
const BADGE_MARK = "⏰";
const BADGE_CHECK_MS = 60 * 1000;

const MAX_GUESTS = 99;

// ═══════════════════════════════════════════════════════════════
//  Ro'yxat oynasi
// ═══════════════════════════════════════════════════════════════

function reservationRow(row) {
	const status = STATUSES[row.status] || { label: row.status, mark: "•" };
	const until = row.to_time ? `–${hhmm(row.to_time)}` : "";
	const meta = [
		row.phone ? `📞 ${row.phone}` : "",
		`${cint(row.pax)} ${__("kishi")}`,
		row.notes || "",
	].filter(Boolean);

	return `<div class="rc-tres__row">
		<div class="rc-tres__time">
			<strong>${esc(hhmm(row.from_time))}</strong>
			<span>${esc(until)}</span>
		</div>
		<div class="rc-tres__main">
			<div class="rc-tres__who">
				<span class="rc-tres__table">${esc(row.table)}</span>
				<span class="rc-tres__guest">${esc(row.guest || "—")}</span>
			</div>
			<div class="rc-tres__meta" title="${esc(meta.join(" · "))}">${esc(meta.join(" · "))}</div>
		</div>
		<span class="rc-tres__status rc-tres__status--${esc(String(row.status).replace(" ", ""))}">
			<span aria-hidden="true">${esc(status.mark)}</span> ${esc(status.label)}
		</span>
		${
			CANCELLABLE.has(row.status)
				? `<button type="button" class="rc-btn rc-btn--danger rc-tres__cancel"
					data-reservation="${esc(row.name)}">${esc(__("Bekor qilish"))}</button>`
				: `<span class="rc-tres__spacer"></span>`
		}
	</div>`;
}

export function openReservations(screen) {
	const days = dayOptions();
	let day = days[0].value;
	let token = 0;
	let rows = [];

	const root = document.createElement("div");
	root.className = "rc-tres";
	root.innerHTML = `<div class="rc-tres__days"></div>
		<div class="rc-tres__summary"></div>
		<div class="rc-tres__list"></div>`;
	const summary = root.querySelector(".rc-tres__summary");
	const list = root.querySelector(".rc-tres__list");

	const dayChips = ui.chips({
		options: days,
		value: day,
		columns: days.length,
		onChange: (value) => {
			day = value;
			load();
		},
	});
	root.querySelector(".rc-tres__days").appendChild(dayChips.el);

	async function load() {
		const mine = (token += 1);
		summary.textContent = longDay(day);
		list.innerHTML = `<p class="rc-tres__state">${esc(__("Yuklanmoqda…"))}</p>`;

		try {
			const result = await screen.call(METHODS.reservations, { date: day });
			if (mine !== token) return; // kassir boshqa kunni tanlab ulgurdi
			rows = result || [];
			summary.textContent = `${longDay(day)} · ${__("{0} ta bron", [rows.length])}`;
			list.innerHTML = rows.length
				? rows.map(reservationRow).join("")
				: `<p class="rc-tres__state">${esc(__("Bu kunga bron yo'q"))}</p>`;
		} catch (error) {
			if (mine !== token) return;
			list.innerHTML = `<p class="rc-tres__state rc-tres__state--error">${esc(
				screen.errorText(error)
			)}</p>`;
		}
	}

	async function cancel(reservation, button) {
		const yes = await ui.confirm({
			title: __("Bronni bekor qilish"),
			message: __("{0} stoli · {1} · {2} — bron bekor qilinsinmi?", [
				reservation.table,
				hhmm(reservation.from_time),
				reservation.guest || "—",
			]),
			confirmLabel: __("Ha, bekor qilish"),
			cancelLabel: __("Yo'q"),
			kind: "danger-solid",
		});
		if (!yes) return;

		screen.busy(button, true);
		try {
			await screen.call(METHODS.cancelReservation, {
				table: reservation.table,
				reservation: reservation.name,
			});
			// Qisqa: toast oyna tugmalari ustida turadi va bosishni ushlab qoladi.
			ui.toast(__("Bron bekor qilindi"), { seconds: 2 });
			screen.scheduleRefresh({ floor: true, panel: true });
			await load();
		} catch (error) {
			screen.alertError(error);
		} finally {
			screen.busy(button, false);
		}
	}

	list.addEventListener("click", (event) => {
		const button = event.target.closest("[data-reservation]");
		if (!button) return;
		const reservation = rows.find((row) => row.name === button.dataset.reservation);
		if (reservation) cancel(reservation, button);
	});

	async function create(button) {
		let created;
		screen.busy(button, true);
		try {
			created = await openReservationForm(screen, day);
		} catch (error) {
			screen.alertError(error);
			return;
		} finally {
			screen.busy(button, false);
		}
		if (!created) return;

		// Yangi bron qaysi kunga yozilgan bo'lsa, ro'yxat o'sha kunni ko'rsatadi.
		day = created.date;
		dayChips.set(day);
		await load();
	}

	ui.dialog({
		title: __("Bronlar"),
		size: "lg",
		body: root,
		actions: [
			{ id: "close", label: __("Yopish"), onClick: (d) => d.close(null) },
			{
				id: "new",
				label: __("+ Yangi bron"),
				kind: "primary",
				onClick: (d, button) => create(button),
			},
		],
	});

	return load();
}

// ═══════════════════════════════════════════════════════════════
//  Yangi bron
// ═══════════════════════════════════════════════════════════════

/** Mehmonlar soni: − son + ; stol sig'imidan oshsa ogohlantirish (server ham ogohlantiradi, rad etmaydi). */
function guestStepper(onChange) {
	let count = 2;

	const el = document.createElement("div");
	el.className = "rc-tstep";
	el.innerHTML = `<button type="button" class="rc-btn rc-tstep__btn" data-step="-1"
			aria-label="${esc(__("Kamaytirish"))}">−</button>
		<output class="rc-tstep__value" aria-live="polite"></output>
		<button type="button" class="rc-btn rc-tstep__btn" data-step="1"
			aria-label="${esc(__("Ko'paytirish"))}">+</button>
		<span class="rc-tstep__hint"></span>`;

	const output = el.querySelector("output");
	const hint = el.querySelector(".rc-tstep__hint");
	output.textContent = String(count);

	el.addEventListener("click", (event) => {
		const button = event.target.closest("[data-step]");
		if (!button) return;
		count = Math.min(MAX_GUESTS, Math.max(1, count + cint(button.dataset.step)));
		output.textContent = String(count);
		onChange();
	});

	return {
		el,
		value: () => count,
		/** Tanlangan stol sig'imi (0 — stol tanlanmagan). */
		showSeats(seats) {
			const over = seats > 0 && count > seats;
			hint.classList.toggle("rc-tstep__hint--warn", over);
			hint.textContent = !seats
				? ""
				: over
				? __("⚠ Stolda {0} o'rin bor", [seats])
				: __("Stolda {0} o'rin", [seats]);
		},
	};
}

function tableEntry(table) {
	const base = { name: table.name, room: table.restaurant_room, seats: cint(table.no_of_seats) };

	if (table.status === "OCCUPIED") {
		return { ...base, tone: "busy", mark: "■", text: __("Band") };
	}
	if (table.status === "RESERVED") {
		const reservation = table.reservation || {};
		return {
			...base,
			tone: "reserved",
			mark: "◆",
			text: [hhmm(reservation.from_time), reservation.customer_name]
				.filter(Boolean)
				.join(" · "),
		};
	}
	return { ...base, tone: "free", mark: "●", text: `${base.seats} ${__("o'rin")}` };
}

/**
 * Yangi bron formasi. Stollar zal bo'yicha; bugungi holat (bo'sh/bron/band)
 * kartada ko'rinadi, lekin hech qaysi stol o'chirilmaydi: band stolga kelgusi
 * kunga bron qilish mumkin, bugunga esa server rad etadi va sababini aytadi.
 *
 * @param {string} day  boshlang'ich sana (`YYYY-MM-DD`)
 * @returns {Promise<object|null>}  server javobi (`table`, `reservation`, `date`); bekor qilinsa `null`
 */
export async function openReservationForm(screen, day) {
	const tables = await loadTables(screen);
	let pending = null;

	const stepper = guestStepper(() => syncSeats());
	const picker = tablePicker({
		entries: tables.map(tableEntry),
		rooms: roomNames(screen),
		// Bitta zalning stollari ekranga sig'adi; boshqa zal — bir bosish narida.
		room: screen.room || roomNames(screen)[0] || "",
		emptyText: __("Stollar topilmadi"),
		onChange: () => {
			syncSeats();
			dialog.setError(""); // «Stolni tanlang» yoki eski server xatosi qolib ketmasin
		},
	});

	function syncSeats() {
		const entry = picker.entry(picker.value());
		stepper.showSeats(entry ? entry.seats : 0);
	}

	const body = document.createElement("div");
	body.innerHTML = `<div class="rc-field__label">${esc(
		__("Stol")
	)}<span class="rc-field__req"> *</span></div>
		<div data-slot="table"></div>
		<p class="rc-tt__legend">${esc(
			__("■ Band va ◆ bron qilingan stolga faqat kelgusi kunlarga bron qilish mumkin.")
		)}</p>
		<div class="rc-field__label rc-tres__label">${esc(__("Mehmonlar soni"))}</div>
		<div data-slot="guests"></div>`;
	body.querySelector('[data-slot="table"]').appendChild(picker.el);
	body.querySelector('[data-slot="guests"]').appendChild(stepper.el);

	const dialog = ui.dialog({
		title: __("Yangi bron"),
		size: "lg",
		body,
		fields: [
			{
				type: "select",
				name: "date",
				label: __("Sana"),
				required: true,
				options: dayOptions(),
				value: day,
				columns: 7,
			},
			{ type: "time", name: "time", label: __("Vaqt") },
			{
				type: "text",
				name: "customer_name",
				label: __("Mehmon ismi"),
				required: true,
			},
			{ type: "tel", name: "phone", label: __("Telefon") },
			{ type: "textarea", name: "notes", label: __("Izoh") },
		],
		actions: [
			{ id: "cancel", label: __("Yopish"), onClick: (d) => d.close(null) },
			{
				id: "submit",
				label: __("Bron qilish"),
				kind: "primary",
				onClick: async (d) => {
					d.setError("");
					const table = picker.value();
					if (!table) {
						d.setError(__("Stolni tanlang"));
						return;
					}
					if (!d.validate()) return;

					const values = d.values();
					d.setBusy(true);
					try {
						pending = screen.call(METHODS.reserve, {
							table,
							customer_name: values.customer_name,
							phone: values.phone,
							guests: stepper.value(),
							from_time: `${values.time}:00`,
							reservation_date: values.date,
							notes: values.notes,
						});
						const result = await pending;
						ui.toast(
							__("{0} stoli bron qilindi — {1}, {2}", [
								table,
								longDay(values.date),
								values.time,
							]),
							{ indicator: "orange", seconds: 3 }
						);
						screen.scheduleRefresh({ floor: true, panel: true });
						d.close(result);
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

	// Birinchi matn maydoniga avtomatik fokus ekran klaviaturasini darhol ochib,
	// stol ro'yxatini yopib qo'yardi: avval stol tanlanadi, matn keyin yoziladi.
	dialog.focus();
	dialog.bodyEl.scrollTop = 0;

	// So'rov ketayotganda oyna yopilib ketsa ham (Esc, ×) bron yozilgan bo'ladi —
	// ro'yxat uni ko'rsatishi uchun natija kutib olinadi.
	return dialog.result.then((value) => value || (pending && pending.catch(() => null)) || null);
}

// ═══════════════════════════════════════════════════════════════
//  Zal rejasidagi belgi
// ═══════════════════════════════════════════════════════════════

/** Yaqinlashayotgan bron uchun «⏰ SS:DD»; aks holda `null`. */
function imminentBadge(table, screen) {
	const reservation = table.reservation;
	if (table.status !== "RESERVED" || !reservation) return null;

	const until = minutesUntil(screen, reservation);
	if (until === null || until > BADGE_AHEAD_MINUTES || until < -BADGE_LATE_MINUTES) {
		return null;
	}
	return `${BADGE_MARK} ${hhmm(reservation.from_time)}`;
}

/**
 * Belgi chizish paytidagi vaqtga qarab qo'yiladi, zal esa faqat voqealar
 * bo'lganda qayta chiziladi. Tinch soatda bron yaqinlashsa belgi o'zi
 * chiqmasdi — shuning uchun har daqiqa belgi holati tekshiriladi va faqat
 * o'zgargan bo'lsagina zal qayta chiziladi (tarmoq so'rovi yo'q).
 */
function watchBadges(screen) {
	const timer = setInterval(() => {
		if (!screen.active || screen.state !== "ready" || screen.layoutEditMode || !screen.floor) {
			return;
		}

		const wanted = new Set(
			(screen.floor.tables || [])
				.filter((table) => imminentBadge(table, screen))
				.map((table) => table.name)
		);
		const shown = new Set(
			[...screen.el.canvas.querySelectorAll(".rc-table[data-table]")]
				.filter((node) => node.textContent.includes(BADGE_MARK))
				.map((node) => node.dataset.table)
		);

		const same = wanted.size === shown.size && [...wanted].every((name) => shown.has(name));
		if (!same) screen.renderFloor();
	}, BADGE_CHECK_MS);

	return () => clearInterval(timer);
}

export function installReservations(screen) {
	return [
		slots.contribute("topbar.menu", {
			id: "reservations",
			order: 310,
			label: __("Bronlar"),
			onClick: (s) => openReservations(s),
		}),
		slots.contribute("floor.tileBadges", {
			id: "reservation-soon",
			order: 310,
			when: (table) => table.status === "RESERVED" && !!table.reservation,
			badge: imminentBadge,
		}),
		watchBadges(screen),
	];
}
