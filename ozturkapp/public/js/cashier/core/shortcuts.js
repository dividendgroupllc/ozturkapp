/**
 * Klaviatura yorliqlari.
 *
 * Yorliqlar `shortcuts` slotidan o'qiladi (`slots.js`) — funksiya modullari
 * ham shu yo'l bilan o'zlariniki qo'shadi. Element shakli:
 *
 *     { id, key: "F2", description, when(screen), handler(screen, event),
 *       allowInInput: false }
 *
 * `key` — `F2`, `Escape`, `Ctrl+K`, `Shift+F3` ko'rinishida.
 *
 * MATN YOZILAYOTGANDA ISHLAMAYDI
 * ==============================
 * Kassir maydonga yozayotganda «F3» yoki boshqa tugma tasodifan to'lov yoki
 * hisobni ishga tushirib yubormasligi kerak. Shuning uchun fokus kiritish
 * maydonida bo'lsa yorliq o'tkazib yuboriladi — faqat `allowInInput: true`
 * bilan e'lon qilinganlar (masalan Esc) bundan mustasno.
 */

import { slots } from "./slots.js";

/** Fokus matn kiritish maydonidami? */
export function isTyping(target) {
	if (!target || !target.tagName) return false;
	if (target.isContentEditable) return true;

	const tag = target.tagName;
	if (tag === "TEXTAREA" || tag === "SELECT") return true;
	if (tag !== "INPUT") return false;

	return !["button", "checkbox", "radio", "submit", "reset"].includes(target.type);
}

/** Hodisadan `Ctrl+Alt+Shift+Kalit` ko'rinishidagi nom. */
function comboOf(event) {
	const parts = [];
	if (event.ctrlKey) parts.push("Ctrl");
	if (event.altKey) parts.push("Alt");
	if (event.shiftKey) parts.push("Shift");
	parts.push(event.key.length === 1 ? event.key.toUpperCase() : event.key);
	return parts.join("+");
}

export class ShortcutManager {
	constructor(screen) {
		this.screen = screen;
		this.onKeyDown = (event) => this.dispatch(event);
	}

	start() {
		document.addEventListener("keydown", this.onKeyDown);
	}

	stop() {
		document.removeEventListener("keydown", this.onKeyDown);
	}

	dispatch(event) {
		if (event.defaultPrevented || event.isComposing || !event.key) return;

		const combo = comboOf(event);
		const typing = isTyping(event.target);

		for (const item of slots.visible("shortcuts", this.screen)) {
			if (item.key !== combo) continue;
			if (typing && !item.allowInInput) continue;

			event.preventDefault();
			item.handler(this.screen, event);
			return;
		}
	}
}
