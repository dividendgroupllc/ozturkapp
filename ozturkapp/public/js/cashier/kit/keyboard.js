/**
 * Ekran klaviaturasi.
 *
 * Kassa apparatida jismoniy klaviatura bo'lmasligi mumkin, brauzerning o'z
 * klaviaturasi esa sensorli monobloklarda ishonchsiz. Shuning uchun
 * sahifaning o'zida katta tugmali klaviatura chiziladi. U har kassada kerak
 * emas: POS Profile'da yoqilgan bo'lsagina ko'rinadi
 * (`ctx.features.virtual_keyboard`); o'chiq bo'lsa maydonlar faqat
 * brauzerning o'z klaviaturasi bilan ishlaydi.
 *
 * IKKI SHAKL
 * ==========
 *   `numpad()`       — oyna ichiga joylashtiriladigan raqam paneli (to'lov,
 *                      kassa sanog'i, PIN);
 *   `TextKeyboard`   — matn klaviaturasi (o'zbek lotin, kirill, raqam/belgi);
 *                      oynaning pastiga mahkamlanadi va fokusdagi maydonga yozadi.
 *
 * FOKUS QOLADI
 * ============
 * Tugma bosilganda kiritish maydoni fokusni YO'QOTMASLIGI kerak — aks holda
 * klaviatura har bosishda yopilib qolardi. Shuning uchun tugmalarda
 * `pointerdown` bekor qilinadi (fokus ko'chmaydi), `click` esa baribir keladi.
 */

import { esc } from "../util/format.js";

// ═══════════════════════════════════════════════════════════════
//  Maydonga yozish
// ═══════════════════════════════════════════════════════════════

/** Kursor turgan joyga (yoki tanlangan matn o'rniga) matn qo'shadi. */
export function insertText(input, text) {
	const start = input.selectionStart == null ? input.value.length : input.selectionStart;
	const end = input.selectionEnd == null ? start : input.selectionEnd;

	input.value = input.value.slice(0, start) + text + input.value.slice(end);
	const caret = start + text.length;
	input.setSelectionRange(caret, caret);
	input.dispatchEvent(new Event("input", { bubbles: true }));
}

/** Tanlangan matnni, tanlov bo'lmasa kursor oldidagi belgini o'chiradi. */
export function backspace(input) {
	const start = input.selectionStart == null ? input.value.length : input.selectionStart;
	const end = input.selectionEnd == null ? start : input.selectionEnd;

	const from = start === end ? Math.max(0, start - 1) : start;
	input.value = input.value.slice(0, from) + input.value.slice(end);
	input.setSelectionRange(from, from);
	input.dispatchEvent(new Event("input", { bubbles: true }));
}

function keepFocus(root) {
	root.addEventListener("pointerdown", (event) => {
		if (event.target.closest("button")) event.preventDefault();
	});
}

// ═══════════════════════════════════════════════════════════════
//  Raqam paneli
// ═══════════════════════════════════════════════════════════════

const NUMPAD_KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", ",", "0", "⌫"];

/**
 * Raqam paneli. Tugma bosilganda FOKUSDAGI (yoki oxirgi fokuslangan) maydonga
 * yoziladi; `input` hodisasi chaqiriladi, shu bilan `bindAmountInput()` dagi
 * guruhlash/qayta hisoblash o'zgarishsiz ishlaydi.
 *
 * Oldindan to'ldirilgan maydon (masalan to'lov summasi to'lanadigan summa bilan
 * boshlanadi) fokus bilan birga TANLANADI, `insertText()` esa tanlangan matnni
 * almashtiradi — birinchi bosilgan raqam eski summani o'chirib yozadi.
 *
 * DINAMIK MAYDONLAR
 * =================
 * Maydonlar keyin qo'shilishi mumkin (aralash to'lovda qator/tahrirlagich):
 * `el.bind(input)` yangi maydonni shu panelga ulaydi — ikkinchi panel qo'yish
 * shart emas. Panel faqat KO'RINIB TURGAN maydonga yozadi: boshqa rejimda
 * yashirilgan yoki o'chirilgan maydon (masalan oddiy summa maydoni aralash
 * rejimda) tashlab ketiladi va navbatdagi ko'rinadigan ulangan maydon olinadi.
 *
 * @param {object} [options]
 * @param {HTMLInputElement[]} [options.inputs]  boshlang'ich maydonlar
 * @returns {HTMLElement}  `bind(input)` metodi bilan
 */
export function numpad({ inputs = [] } = {}) {
	const el = document.createElement("div");
	el.className = "rc-numpad";
	el.innerHTML = NUMPAD_KEYS.map(
		(key) =>
			`<button class="rc-numpad__key${
				key === "⌫" ? " rc-numpad__key--del" : ""
			}" type="button" data-key="${esc(key)}">${esc(key)}</button>`
	).join("");

	const bound = new Set();
	let active = null;
	const usable = (input) =>
		input.isConnected && !input.disabled && input.getClientRects().length > 0;

	el.bind = (input) => {
		// DOM'dan olib tashlangan (qayta chizilgan qator) maydonlar ro'yxatni to'ldirmasin.
		bound.forEach((old) => old.isConnected || bound.delete(old));
		if (bound.has(input)) return;

		bound.add(input);
		input.addEventListener("focus", () => (active = input));
		if (!active) active = input;
	};
	inputs.forEach(el.bind);

	keepFocus(el);
	el.addEventListener("click", (event) => {
		const button = event.target.closest(".rc-numpad__key");
		if (!button) return;

		const target = active && usable(active) ? active : [...bound].find(usable);
		if (!target) return;

		const key = button.dataset.key;
		if (key === "⌫") backspace(target);
		else if (key === ",") {
			if (!/[.,]/.test(target.value)) insertText(target, ",");
		} else insertText(target, key);

		target.focus({ preventScroll: true });
	});

	return el;
}

// ═══════════════════════════════════════════════════════════════
//  Matn klaviaturasi
// ═══════════════════════════════════════════════════════════════

const SHIFT = { k: "shift", label: "⇧", cls: "rc-kb__key--fn" };
const BACK = { k: "back", label: "⌫", cls: "rc-kb__key--fn" };
const ENTER = { k: "enter", label: "↵", cls: "rc-kb__key--fn rc-kb__key--wide" };
const SPACE = { k: "space", label: "", cls: "rc-kb__key--space" };
const chars = (text) => text.split(" ").map((c) => ({ k: "char", v: c }));

/**
 * Tartiblar. `oʻ`, `gʻ` — o'zbek lotin harflari (U+02BB), `ʼ` — tutuq belgisi
 * (U+02BC). `sh`, `ch` — bitta bosishda yoziladigan juft harflar.
 */
const LAYOUTS = {
	uz: [
		chars("q w e r t y u i o p"),
		chars("a s d f g h j k l"),
		[SHIFT, ...chars("z x c v b n m"), BACK],
		[
			{ k: "sym", label: "123", cls: "rc-kb__key--fn" },
			{ k: "lang", label: "РУ", cls: "rc-kb__key--fn" },
			...chars("oʻ gʻ sh ch ʼ"),
			SPACE,
			ENTER,
		],
	],
	ru: [
		chars("й ц у к е н г ш щ з х ъ"),
		chars("ф ы в а п р о л д ж э"),
		[SHIFT, ...chars("я ч с м и т ь б ю"), BACK],
		[
			{ k: "sym", label: "123", cls: "rc-kb__key--fn" },
			{ k: "lang", label: "UZ", cls: "rc-kb__key--fn" },
			...chars("ў қ ғ ҳ ё"),
			SPACE,
			ENTER,
		],
	],
	sym: [
		chars("1 2 3 4 5 6 7 8 9 0"),
		chars("- / : ; ( ) . , ! ?"),
		chars("+ = % * # @ & ' \" _").concat([BACK]),
		[{ k: "abc", label: "ABC", cls: "rc-kb__key--fn" }, SPACE, ENTER],
	],
	num: [
		chars("1 2 3"),
		chars("4 5 6"),
		chars("7 8 9"),
		[{ k: "char", v: "," }, { k: "char", v: "0" }, BACK],
	],
	tel: [
		chars("1 2 3"),
		chars("4 5 6"),
		chars("7 8 9"),
		[{ k: "char", v: "+" }, { k: "char", v: "0" }, BACK],
	],
};

/** Harf tartiblari — bosh harf (shift) faqat shularda ishlaydi. */
const LETTER_LAYOUTS = ["uz", "ru"];

export class TextKeyboard {
	/**
	 * @param {HTMLElement} host   klaviatura chiziladigan element
	 * @param {object} [options]
	 * @param {Function} [options.onEnter]  bir qatorli maydonda «↵» bosilganda
	 */
	constructor(host, { onEnter = null } = {}) {
		this.host = host;
		this.onEnter = onEnter;
		this.layout = "uz";
		this.letters = "uz"; // «ABC» ga qaytish uchun oxirgi harf tartibi
		this.shift = false;
		this.target = null;

		keepFocus(host);
		host.addEventListener("click", (event) => {
			const button = event.target.closest(".rc-kb__key");
			if (button) this.press(button.dataset.k, button.dataset.v || "");
		});
	}

	/** Maydonga bog'lanadi va klaviaturani ko'rsatadi. */
	bind(input) {
		this.target = input;

		const mode = input.dataset.vk;
		if (mode === "numeric") this.layout = "num";
		else if (mode === "tel") this.layout = "tel";
		else if (this.layout === "num" || this.layout === "tel") this.layout = this.letters;

		this.autoShift();
		this.render();
		this.host.hidden = false;
	}

	unbind() {
		this.target = null;
		this.host.hidden = true;
	}

	/** Gap boshida (yoki bo'sh maydonda) bosh harf — ism va manzil yozishni tezlashtiradi. */
	autoShift() {
		if (!this.target || !LETTER_LAYOUTS.includes(this.layout)) return;
		const before = this.target.value.slice(0, this.target.selectionStart || 0);
		this.shift = before === "" || /[\s.!?]$/.test(before);
	}

	render() {
		const letters = LETTER_LAYOUTS.includes(this.layout);
		this.host.dataset.layout = this.layout;
		this.host.innerHTML = LAYOUTS[this.layout]
			.map(
				(row) =>
					`<div class="rc-kb__row">${row
						.map((key) => {
							const text = key.k === "char" ? this.cased(key.v, letters) : key.label;
							const pressed =
								key.k === "shift" && this.shift ? ' aria-pressed="true"' : "";
							return `<button type="button" class="rc-kb__key ${key.cls || ""}"
							data-k="${key.k}" data-v="${esc(key.v || "")}"${pressed}>${esc(text)}</button>`;
						})
						.join("")}</div>`
			)
			.join("");
	}

	cased(value, letters) {
		return letters && this.shift ? value.charAt(0).toUpperCase() + value.slice(1) : value;
	}

	press(kind, value) {
		const input = this.target;
		if (!input) return;

		if (kind === "char") {
			insertText(input, this.cased(value, LETTER_LAYOUTS.includes(this.layout)));
			// Bosh harf bir marta ishlaydi.
			if (this.shift) {
				this.shift = false;
				this.render();
			}
		} else if (kind === "back") {
			backspace(input);
		} else if (kind === "space") {
			insertText(input, " ");
		} else if (kind === "shift") {
			this.shift = !this.shift;
			this.render();
		} else if (kind === "lang") {
			this.letters = this.layout === "uz" ? "ru" : "uz";
			this.layout = this.letters;
			this.render();
		} else if (kind === "sym") {
			this.layout = "sym";
			this.render();
		} else if (kind === "abc") {
			this.layout = this.letters;
			this.autoShift();
			this.render();
		} else if (kind === "enter") {
			if (input.tagName === "TEXTAREA") insertText(input, "\n");
			else if (this.onEnter) this.onEnter(input);
		}

		input.focus({ preventScroll: true });
	}
}
