/**
 * Kassa harakati — smena davomida g'aladonga pul kiritish (Kirim) yoki
 * undan chiqarish (Chiqim): xarajat, inkassatsiya, mayda pul qo'shish.
 *
 * KO'R SANOQ: bu oynada g'aladon qoldig'i, kutilgan summa yoki harakatlar
 * jami HECH QACHON ko'rsatilmaydi — faqat har bir harakatning o'z summasi.
 * `get_cash_movements()` javobidagi `total_in` / `total_out` ATAYLAB
 * o'qilmaydi: jami kirim-chiqim sotuvsiz ma'nosiz, lekin kassirga qoldiqni
 * taxmin qilishga yordam beradi.
 *
 * Pul va ruxsat mantig'i serverda (`api/cash_movements.py`): kirim DOIM,
 * chiqim limitdan oshsa menejer tasdig'i so'raladi (`ui.withApproval`).
 */

import { approvalArgs, esc, features, hhmm, shiftIsOpen, slots, ui } from "./shared.js";

/**
 * Yo'nalish -> tur. Server `categories` ni beradi; bo'lmasa (eski javob)
 * `Ozturk Cash Movement.category` variantlari bilan bir xil ro'yxat.
 */
const FALLBACK_CATEGORIES = {
	In: ["Kassaga qo'shish", "Boshqa"],
	Out: ["Xarajat", "Inkassatsiya", "Boshqa"],
};

/** Sabab tugmalari — yozishdan tezroq; «Boshqa…» erkin matn (ekran klaviaturasi). */
const REASON_PRESETS = {
	In: [__("Mayda pul qo'shildi"), __("Menejer pul berdi")],
	Out: [__("Mahsulot xaridi"), __("Yetkazib beruvchiga to'lov"), __("Menejerga topshirildi")],
};

function categoriesOf(data, kind) {
	const served = data && data.categories && data.categories[kind];
	return served && served.length ? served : FALLBACK_CATEGORIES[kind];
}

/** Naqd usullar: serverning yangi ro'yxati, bo'lmasa sahifa konteksti. */
function cashModesOf(screen, data) {
	const served = data && data.cash_modes;
	return served && served.length ? served : screen.ctx.cash_modes || [];
}

// ═══════════════════════════════════════════════════════════════
//  Ro'yxat
// ═══════════════════════════════════════════════════════════════

function emptyHtml(title) {
	return `<div class="rc-empty rc-empty--inline">
		<div class="rc-empty__icon" aria-hidden="true">🧾</div>
		<div class="rc-empty__title">${esc(title)}</div>
	</div>`;
}

function listHtml(screen, data) {
	if (!data.pos_opening_entry) return emptyHtml(__("Kassa smenasi yopiq"));
	const items = data.items || [];
	if (!items.length) return emptyHtml(__("Bu smenada kassa harakati yo'q"));

	// Yo'nalish FAQAT rang bilan emas: belgi + matn (▲ Kirim / ▼ Chiqim).
	return `<table class="rc-shift-mv__table">
		<thead><tr>
			<th>${esc(__("Vaqt"))}</th>
			<th>${esc(__("Harakat"))}</th>
			<th class="rc-shift-mv__num">${esc(__("Summa"))}</th>
			<th>${esc(__("Sabab"))}</th>
			<th>${esc(__("Tasdiqladi"))}</th>
		</tr></thead>
		<tbody>${items
			.map((item) => {
				const isIn = item.kind === "In";
				const tone = isIn ? "in" : "out";
				return `<tr>
					<td class="rc-shift-mv__time">${esc(hhmm(item.posting_datetime))}</td>
					<td><span class="rc-shift-mv__kind rc-shift-mv__kind--${tone}">${isIn ? "▲" : "▼"} ${esc(
					isIn ? __("Kirim") : __("Chiqim")
				)}</span>
						<span class="rc-shift-mv__cat">${esc(item.category)}</span></td>
					<td class="rc-shift-mv__num rc-shift-mv__amount--${tone}">${isIn ? "+" : "−"}${esc(
					screen.money(item.amount)
				)}</td>
					<td class="rc-shift-mv__reason">${esc(item.reason)}</td>
					<td class="rc-shift-mv__by" title="${esc(item.approved_by || "")}">${esc(
					item.approved_by_name || item.approved_by || ""
				)}</td>
				</tr>`;
			})
			.join("")}</tbody>
	</table>`;
}

// ═══════════════════════════════════════════════════════════════
//  Yangi harakat
// ═══════════════════════════════════════════════════════════════

/** Tasdiq oynasi va toast uchun: «Kassadan chiqarish 50 000». */
function actionLabel(screen, kind, amount) {
	return `${kind === "In" ? __("Kassaga kiritish") : __("Kassadan chiqarish")} ${screen.money(
		amount
	)}`;
}

/** Serverga yozadi; kerak bo'lsa menejer tasdig'ini so'raydi. */
function record(screen, args) {
	return ui.withApproval(
		(approval) =>
			screen.call("ozturkapp.ozturkapp.api.cash_movements.create_cash_movement", {
				kind: args.kind,
				amount: args.amount,
				category: args.category,
				reason: args.reason,
				...(args.mode ? { mode_of_payment: args.mode } : {}),
				...approvalArgs(approval),
			}),
		{ action: actionLabel(screen, args.kind, args.amount) }
	);
}

/** Tasdiq haqidagi eslatma — faqat ma'lumot: qaror serverda. */
function approvalNote(screen, kind) {
	if (kind === "In") return __("Kassaga kirim menejer tasdig'i bilan qayd etiladi.");

	const limit = (screen.ctx.feature_settings || {}).cash_payout_approval_limit;
	if (typeof limit !== "number") return "";
	return limit > 0
		? __("{0} dan katta chiqim menejer tasdig'i bilan qayd etiladi.", [screen.money(limit)])
		: __("Har bir chiqim menejer tasdig'i bilan qayd etiladi.");
}

/**
 * Kirim/chiqim formasi.
 *
 * @returns {Promise<object|null>}  yangilangan `get_cash_movements()` natijasi;
 *          bekor qilinsa `null`
 */
async function askMovement(screen, kind, data, reload) {
	const isIn = kind === "In";
	const categories = categoriesOf(data, kind);
	const modes = cashModesOf(screen, data);

	const fields = [
		{ type: "amount", name: "amount", label: __("Summa"), required: true },
		{
			type: "select",
			name: "category",
			label: __("Turi"),
			options: categories,
			columns: Math.min(categories.length, 3),
			required: true,
		},
		{
			type: "select",
			name: "reason",
			label: __("Sabab"),
			options: REASON_PRESETS[kind],
			columns: 2,
			other: { label: __("Boshqa…"), placeholder: __("Sababni yozing") },
			required: true,
		},
	];
	// Bitta naqd usul bo'lsa so'ralmaydi — server birinchisini oladi.
	if (modes.length > 1) {
		fields.push({
			type: "select",
			name: "mode_of_payment",
			label: __("Naqd usul"),
			options: modes,
			value: modes[0],
			columns: Math.min(modes.length, 3),
			required: true,
		});
	}

	let saved = null;
	const values = await ui.form({
		title: isIn ? __("Kassaga kirim (+)") : __("Kassadan chiqim (−)"),
		subtitle: approvalNote(screen, kind),
		kind: isIn ? "pay" : "danger-solid",
		submitLabel: isIn ? __("Kirimni qayd etish") : __("Chiqimni qayd etish"),
		cancelLabel: __("Bekor qilish"),
		fields,
		onSubmit: async (values) => {
			try {
				saved = await record(screen, {
					kind,
					amount: values.amount,
					category: values.category,
					reason: values.reason,
					mode: values.mode_of_payment,
				});
			} catch (error) {
				// Javob yo'qolgan bo'lsa harakat yozilgan bo'lishi mumkin («takror» xatosi):
				// ro'yxatni yangilab qo'yamiz, shunda kassir uni ko'radi.
				if (!error || !error.cancelled) reload();
				throw error;
			}
		},
	});

	if (!values || !saved) return null;
	// Qisqa: toast pastdagi Kirim/Chiqim tugmalarining ustiga tushadi.
	ui.toast(`${actionLabel(screen, kind, values.amount)} — ${__("qayd etildi")}`, { seconds: 3 });
	return saved;
}

// ═══════════════════════════════════════════════════════════════
//  Oyna
// ═══════════════════════════════════════════════════════════════

export function openMovements(screen) {
	if (ui.hasDialog() || !shiftIsOpen(screen)) return;

	const host = document.createElement("div");
	host.className = "rc-shift-mv";
	let data = null;

	const paint = (next) => {
		data = next;
		host.innerHTML = listHtml(screen, data);
	};

	const reload = async () => {
		try {
			paint(await screen.call("ozturkapp.ozturkapp.api.cash_movements.get_cash_movements"));
			dialog.setError("");
		} catch (error) {
			if (!data) host.innerHTML = emptyHtml(__("Ro'yxat yuklanmadi"));
			dialog.setError(screen.errorText(error));
		}
	};

	const add = async (kind) => {
		const saved = await askMovement(screen, kind, data, reload);
		if (saved) paint(saved);
	};

	host.innerHTML = emptyHtml(__("Yuklanmoqda…"));
	const dialog = ui.dialog({
		title: __("Kassa harakati"),
		subtitle: __("Joriy smena"),
		size: "lg",
		body: host,
		actions: [
			{ id: "in", label: __("Kirim (+)"), kind: "pay", onClick: () => add("In") },
			{ id: "out", label: __("Chiqim (−)"), kind: "danger-solid", onClick: () => add("Out") },
		],
	});

	reload();
}

features.register({
	key: "shift-movements",
	flag: "cash_movements",
	install() {
		return slots.contribute("topbar.menu", {
			id: "shift-movements",
			order: 410,
			label: __("Kassa harakati"),
			when: shiftIsOpen,
			onClick: (screen) => openMovements(screen),
		});
	},
});
