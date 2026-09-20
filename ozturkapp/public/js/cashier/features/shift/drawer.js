/**
 * G'aladon (savdosiz ochish) — `topbar.quick` tugmasi va F9.
 *
 * Naqd to'lovdan keyingi ochilish SERVERDA (`billing` -> `kick_drawer`);
 * bu yerda faqat qo'lda ochish: u hech qaysi chekka bog'lanmaydi, shuning
 * uchun firibgarlikka eng moyil amal — sabab MAJBURIY va smena hisobotiga
 * «G'aladon (savdosiz)» bo'lib tushadi.
 */

import { esc, features, noOverlay, shiftIsOpen, slots, ui } from "./shared.js";

/** Tez sabablar; «Boshqa…» — erkin matn (ekran klaviaturasi bilan). */
const REASONS = [__("Qaytim"), __("Maydalash")];

/**
 * Bir zarba ketayotganda yoki hozirgina ketganda yangisi yuborilmaydi.
 *
 * Ikki marta bosish ikki `Drawer` topshirig'i, ya'ni ikki qo'ng'iroq demak;
 * agent oflayn bo'lsa ikkalasi ULANGACH birga ishlab ketadi. Qisqa muddat
 * ham shu uchun: kassir g'aladon ochilmadi deb qayta bosmasin.
 */
const COOLDOWN_MS = 4000;
let inFlight = false;
let lockedUntil = 0;

/**
 * Server javobini foydalanuvchiga tushunarli natijaga aylantiradi.
 *
 * `{queued, agent_online}` — topshiriq navbatga tushdi (agent oflayn bo'lsa
 * u ULANGACH ochiladi, server 60 soniyadan keyin eskirgan buyruqni bekor
 * qiladi: `print_queue.DRAWER_TTL`); `{queued: null, reason}` — tushmadi.
 */
function outcomeOf(result) {
	if (result && result.queued) {
		if (result.agent_online) {
			return { toast: __("G'aladon ochilmoqda"), indicator: "green", seconds: 4 };
		}
		return {
			toast: __(
				"Printer agenti oflayn. G'aladon agent ulangach ochiladi; 60 soniya ichida ulanmasa buyruq bekor bo'ladi. Qayta bosmang."
			),
			indicator: "orange",
			seconds: 10,
		};
	}
	if (result && result.reason === "no_printer") {
		return { error: __("Kassa printeri sozlanmagan — g'aladonni ochib bo'lmadi.") };
	}
	return {
		error: __(
			"G'aladon buyrug'i yaratilmadi. Qayta urinib ko'ring yoki menejerga xabar bering."
		),
	};
}

async function kick(screen, dialog, reasons) {
	const reason = String(reasons.value() || "").trim();
	if (!reason) {
		dialog.setError(__("Sababni tanlang yoki yozing"));
		return;
	}
	if (inFlight || Date.now() < lockedUntil) {
		dialog.setError(__("G'aladon buyrug'i hozirgina yuborilgan — bir necha soniya kuting."));
		return;
	}

	inFlight = true;
	dialog.setError("");
	dialog.setBusy(true);
	try {
		const result = await screen.call("ozturkapp.ozturkapp.api.printing.open_drawer", {
			reason,
		});
		const outcome = outcomeOf(result);
		if (outcome.error) {
			dialog.setError(outcome.error);
			return;
		}
		lockedUntil = Date.now() + COOLDOWN_MS;
		dialog.close(true);
		ui.toast(outcome.toast, { indicator: outcome.indicator, seconds: outcome.seconds });
	} catch (error) {
		dialog.setError(screen.errorText(error));
	} finally {
		inFlight = false;
		dialog.setBusy(false);
	}
}

export function openDrawerDialog(screen) {
	if (ui.hasDialog() || !shiftIsOpen(screen)) return;

	const reasons = ui.chips({
		options: REASONS,
		columns: 3,
		other: { label: __("Boshqa…"), placeholder: __("Sababni yozing") },
	});

	const body = document.createElement("div");
	body.innerHTML = `<div class="rc-field__label">${esc(__("Sabab"))}</div>`;
	body.appendChild(reasons.el);

	ui.dialog({
		title: __("G'aladonni ochish"),
		subtitle: __("Savdosiz ochish hisobotga yoziladi"),
		// Ekran klaviaturasi (10 tugmali qator) tor oynada 48px dan kichrayib ketadi.
		size: ui.virtualKeyboard ? "lg" : "md",
		body,
		actions: [
			{ id: "cancel", label: __("Yopish"), onClick: (dialog) => dialog.close(null) },
			{
				id: "open",
				label: __("Ochish"),
				kind: "primary",
				onClick: (dialog) => kick(screen, dialog, reasons),
			},
		],
	});
}

features.register({
	key: "shift-drawer",
	flag: "cash_drawer",
	install() {
		return [
			slots.contribute("topbar.quick", {
				id: "shift-drawer-quick",
				order: 410,
				label: __("G'aladon"),
				icon: "💵",
				kind: "default",
				when: shiftIsOpen,
				onClick: (screen) => openDrawerDialog(screen),
			}),
			slots.contribute("shortcuts", {
				id: "shift-drawer-key",
				order: 410,
				key: "F9",
				description: __("G'aladon"),
				when: (screen) => shiftIsOpen(screen) && noOverlay(screen),
				handler: (screen) => {
					screen.closeMenu();
					openDrawerDialog(screen);
				},
			}),
		];
	},
});
