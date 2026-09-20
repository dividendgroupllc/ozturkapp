/**
 * Buyurtma oynasi ustida ochiladigan kichik tanlov oynalari: stol, izoh, sabab.
 */

import { COMMENT_LIMIT, esc, fmtQty, ui } from "./shared.js";

// ═══════════════════════════════════════════════════════════════
//  Stol tanlash
// ═══════════════════════════════════════════════════════════════

/**
 * Bo'sh stollarni zallar bo'yicha guruhlab ko'rsatadi.
 *
 * Faqat BO'SH stol (`AVAILABLE`) tanlanadi: band stolga yangi buyurtma ochib
 * bo'lmaydi (unga taom qo'shiladi), bronli stolni esa kassir tasodifan
 * buzmasligi kerak. Server baribir tekshiradi.
 *
 * @returns {Promise<string|null>}  tanlangan stol nomi; yopilsa `null`
 */
export function chooseTable({ tables, current }) {
	const free = (tables || []).filter((table) => table.status === "AVAILABLE");

	const groups = new Map();
	free.forEach((table) => {
		const room = table.restaurant_room || "";
		if (!groups.has(room)) groups.set(room, []);
		groups.get(room).push(table);
	});

	const body = document.createElement("div");
	body.className = "rc-orders-tables";
	body.innerHTML = free.length
		? [...groups]
				.map(
					([room, list]) => `<section class="rc-orders-tables__group">
						<h3 class="rc-orders-tables__room">${esc(room || __("Zal ko'rsatilmagan"))}
							<span>${list.length}</span></h3>
						<div class="rc-orders-tables__grid">
							${list
								.map(
									(
										table
									) => `<button type="button" class="rc-chip-opt rc-orders-tables__table"
										data-table="${esc(table.name)}"
										aria-pressed="${table.name === current}">
										<strong>${esc(table.name)}</strong>
										<small>${cint(table.no_of_seats)} ${esc(__("o'rin"))}</small>
									</button>`
								)
								.join("")}
						</div>
					</section>`
				)
				.join("")
		: `<div class="rc-empty"><p class="rc-empty__title">${esc(
				__("Bo'sh stol yo'q")
		  )}</p></div>`;

	const dialog = ui.dialog({
		title: __("Stolni tanlang"),
		subtitle: __("Faqat bo'sh stollar ko'rsatilgan"),
		size: "md",
		body,
		actions: [{ id: "close", label: __("Yopish"), onClick: (d) => d.close(null) }],
	});

	body.addEventListener("click", (event) => {
		const button = event.target.closest("[data-table]");
		if (button) dialog.close(button.dataset.table);
	});

	return dialog.result;
}

// ═══════════════════════════════════════════════════════════════
//  Taom izohi
// ═══════════════════════════════════════════════════════════════

/** Eng ko'p uchraydigan izohlar — bir bosishda; qolganini kassir yozadi. */
function commentPresets() {
	return [
		__("Piyozsiz"),
		__("Achchiq"),
		__("Achchiq emas"),
		__("Kamroq tuz"),
		__("Tuzsiz"),
		__("Muzsiz"),
	];
}

/**
 * Taom izohini tahrirlaydi: tayyor izohlar (bir nechtasini tanlash mumkin) +
 * erkin matn. Natija vergul bilan birlashtirilgan bitta satr (KOT'da shunday chiqadi).
 *
 * @returns {Promise<string|null>}  yangi izoh (bo'sh satr — izohsiz); bekor qilinsa `null`
 */
export async function editComment({ name, comment }) {
	const presets = commentPresets();
	const parts = (comment || "")
		.split(",")
		.map((part) => part.trim())
		.filter(Boolean);
	const selected = parts.filter((part) => presets.includes(part));
	const extra = parts.filter((part) => !presets.includes(part)).join(", ");

	let result = null;
	const values = await ui.form({
		title: name,
		subtitle: __("Taom izohi"),
		fields: [
			{
				type: "chips",
				name: "presets",
				multiple: true,
				options: presets,
				columns: 3,
				value: selected,
			},
			{
				type: "text",
				name: "extra",
				label: __("Boshqa izoh"),
				placeholder: __("Masalan: sousni alohida"),
				value: extra,
			},
		],
		submitLabel: __("Saqlash"),
		onSubmit: (input) => {
			const text = [...input.presets, input.extra].filter(Boolean).join(", ");
			if (text.length > COMMENT_LIMIT) {
				throw new Error(__("Izoh juda uzun ({0} belgigacha)", [COMMENT_LIMIT]));
			}
			result = text;
		},
	});

	return values ? result : null;
}

// ═══════════════════════════════════════════════════════════════
//  Taomni olib tashlash sababi
// ═══════════════════════════════════════════════════════════════

function removalReasons() {
	return [__("Mijoz fikridan qaytdi"), __("Xato kiritilgan"), __("Oshxonada tugagan")];
}

/**
 * Buyurtmadan taom olib tashlashdan oldin sabab so'raydi.
 *
 * Sabab chekning izohlar tarixiga yoziladi (kim, nimani, nega) — shuning
 * uchun majburiy. `onSubmit` xato tashlasa oyna YOPILMAYDI va server xabari
 * oynada ko'rinadi (masalan oshxona boshlagan taom yoki oxirgi taom).
 *
 * @param {object} options
 * @param {object} options.row       `bill.items[]` qatori
 * @param {boolean} options.whole    butun qator (aks holda bitta porsiya)
 * @param {Function} options.onSubmit  `async (reason) => void`
 */
export function askRemoval({ row, whole, onSubmit }) {
	const name = row.item_name || row.item_code;

	return ui.form({
		title: whole ? __("{0} — olib tashlash", [name]) : __("{0} — kamaytirish", [name]),
		note: whole
			? __("Qatorning hammasi ({0} ta) olib tashlanadi.", [fmtQty(row.qty)])
			: __("Bitta porsiya olib tashlanadi."),
		fields: [
			{
				type: "select",
				name: "reason",
				label: __("Sabab"),
				required: true,
				options: removalReasons(),
				columns: 1,
				other: { label: __("Boshqa…"), placeholder: __("Sababni yozing") },
			},
		],
		submitLabel: whole ? __("Olib tashlash") : __("Kamaytirish"),
		kind: "danger-solid",
		onSubmit: (values) => onSubmit(values.reason),
	});
}
