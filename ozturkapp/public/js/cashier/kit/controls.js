/**
 * Kichik boshqaruv elementlari: bildirishnoma, tanlov tugmalari, summa maydoni.
 */

import { bindAmountInput, esc, groupAmount, parseAmount } from "../util/format.js";
import { kit } from "./context.js";

// ═══════════════════════════════════════════════════════════════
//  Bildirishnoma (toast)
// ═══════════════════════════════════════════════════════════════

const MAX_TOASTS = 4;

/**
 * Qisqa vaqtga ko'rinadigan xabar.
 *
 * `frappe.show_alert` ISHLATILMAYDI: u Desk'ning yuqori o'ng burchagida
 * turadi va sensorli ekranda kichik.
 * Bu yerda xabar ekran tagida, katta va bosib yopiladigan.
 *
 * Matn HAR DOIM ekranlanadi (`textContent`) — serverdan kelgan xabarga
 * ishonmaymiz.
 *
 * @param {string} message
 * @param {object} [options]
 * @param {"green"|"red"|"orange"|"blue"} [options.indicator="green"]
 * @param {number} [options.seconds=5]
 * @param {string} [options.title]  qalin sarlavha (masalan «Hisob so'radi»)
 */
export function toast(message, { indicator = "green", seconds = 5, title = "" } = {}) {
	let host = kit.host.querySelector(":scope > .rc-toasts");
	if (!host) {
		host = document.createElement("div");
		host.className = "rc-toasts";
		host.setAttribute("aria-live", "polite");
		kit.host.appendChild(host);
	}

	while (host.children.length >= MAX_TOASTS) host.firstElementChild.remove();

	const node = document.createElement("div");
	node.className = `rc-toast rc-toast--${indicator}`;
	node.setAttribute("role", indicator === "red" ? "alert" : "status");

	if (title) {
		const strong = document.createElement("strong");
		strong.textContent = title;
		node.appendChild(strong);
	}
	const text = document.createElement("span");
	text.textContent = message;
	node.appendChild(text);

	const remove = () => node.remove();
	node.addEventListener("click", remove);
	host.appendChild(node);
	setTimeout(remove, Math.max(1, seconds) * 1000);
}

// ═══════════════════════════════════════════════════════════════
//  Tanlov tugmalari (chips)
// ═══════════════════════════════════════════════════════════════

const OTHER = "__other__";

/**
 * Tanlov tugmalari — bitta yoki bir nechta variant.
 *
 * Ro'yxatdan `<select>` o'rniga: sensorli ekranda ochiladigan ro'yxatni
 * bosish sekin va xatoga moyil, katta tugmalar esa bir bosishda.
 *
 * @param {object} options
 * @param {Array<string|{value, label}>} options.options
 * @param {*}        [options.value]     boshlang'ich tanlov (ko'pli bo'lsa — massiv)
 * @param {boolean}  [options.multiple]  ko'p tanlov
 * @param {number}   [options.columns]   ustunlar soni (0 — sig'gancha)
 * @param {Function} [options.onChange]  `onChange(value)`
 * @param {object}   [options.other]     `{label, placeholder}` — «Boshqa…» tugmasi:
 *                                       tanlansa matn maydoni ochiladi va qiymat
 *                                       o'sha matn bo'ladi (faqat bitta tanlovda)
 * @returns {{el: HTMLElement, value: Function, set: Function, input: HTMLElement|null}}
 */
export function chips({
	options = [],
	value = null,
	multiple = false,
	columns = 0,
	onChange = null,
	other = null,
} = {}) {
	const items = options.map((o) => (typeof o === "object" ? o : { value: o, label: String(o) }));
	if (other) items.push({ value: OTHER, label: other.label });

	const selected = new Set(multiple ? [].concat(value || []) : value === null ? [] : [value]);

	const el = document.createElement("div");
	el.className = "rc-chipset";
	el.innerHTML = `<div class="rc-chips" role="${multiple ? "group" : "radiogroup"}"
			style="${columns ? `--rc-chip-cols:${columns}` : ""}">
			${items
				.map(
					(item) => `<button type="button" class="rc-chip-opt"
						data-value="${esc(item.value)}" aria-pressed="false">${esc(item.label)}</button>`
				)
				.join("")}
		</div>
		${
			other
				? `<textarea class="rc-input rc-chipset__other" rows="2" hidden data-vk="text"
						autocomplete="off" placeholder="${esc(other.placeholder || "")}"></textarea>`
				: ""
		}`;

	const buttons = [...el.querySelectorAll(".rc-chip-opt")];
	const input = el.querySelector(".rc-chipset__other");

	const read = () => {
		if (multiple) return items.map((i) => i.value).filter((v) => selected.has(v));
		const [current] = [...selected];
		if (current === OTHER) return (input.value || "").trim();
		return current === undefined ? null : current;
	};

	const paint = () => {
		// `data-value` — matn; tanlov esa asl turda (son bo'lishi mumkin).
		const keys = new Set([...selected].map(String));
		buttons.forEach((button) =>
			button.setAttribute("aria-pressed", String(keys.has(button.dataset.value)))
		);
		if (input) input.hidden = !selected.has(OTHER);
	};

	el.addEventListener("click", (event) => {
		const button = event.target.closest(".rc-chip-opt");
		if (!button) return;

		const item = items.find((i) => String(i.value) === button.dataset.value);
		if (multiple) {
			if (selected.has(item.value)) selected.delete(item.value);
			else selected.add(item.value);
		} else {
			selected.clear();
			selected.add(item.value);
		}
		paint();
		if (input && selected.has(OTHER)) input.focus();
		if (onChange) onChange(read());
	});
	if (input) input.addEventListener("input", () => onChange && onChange(read()));

	paint();
	return {
		el,
		input,
		value: read,
		set(next) {
			selected.clear();
			[]
				.concat(next === null || next === undefined ? [] : next)
				.forEach((v) => selected.add(v));
			paint();
		},
	};
}

// ═══════════════════════════════════════════════════════════════
//  Summa maydoni
// ═══════════════════════════════════════════════════════════════

/**
 * Summa kiritish maydoni: probel bilan guruhlanadi (`1 080 800`) va kursor
 * o'z joyida qoladi — to'lov, kassa sanog'i va boshqa summa maydonlari bilan
 * BIR XIL mantiq (`bindAmountInput`).
 *
 * `type="number"` EMAS: brauzer unda probelga yo'l qo'ymaydi.
 *
 * @param {object} [options]
 * @param {number}   [options.value]        boshlang'ich summa (bo'sh — placeholder `0`)
 * @param {Function} [options.onChange]     `onChange(number)`
 * @param {string}   [options.label]        `aria-label`
 * @returns {{el: HTMLInputElement, value: Function, set: Function, focus: Function}}
 */
export function amountInput({ value = null, onChange = null, label = "" } = {}) {
	const el = document.createElement("input");
	el.type = "text";
	el.className = "rc-input rc-input--amount";
	el.placeholder = "0";
	el.autocomplete = "off";
	el.dataset.vk = "numeric";
	el.setAttribute("inputmode", kit.virtualKeyboard ? "none" : "numeric");
	if (label) el.setAttribute("aria-label", label);
	if (value) el.value = groupAmount(value);

	bindAmountInput($(el), () => onChange && onChange(parseAmount(el.value)));

	return {
		el,
		value: () => parseAmount(el.value),
		set(next) {
			el.value = next ? groupAmount(next) : "";
			if (onChange) onChange(parseAmount(el.value));
		},
		focus: () => el.focus(),
	};
}
