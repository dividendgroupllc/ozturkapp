/**
 * Standart klaviatura yorliqlari: Esc — yopish, F2 — to'lov, F3 — hisob berish.
 *
 * Kassir matn yozayotganda F2/F3 ishlamaydi (`core/shortcuts.js`); Esc esa
 * o'sha holatda faqat maydondan chiqadi — yozilgan matn tasodifan yo'qolib
 * ketmasin, ikkinchi bosish oynani yopadi.
 */

import { isTyping } from "../core/shortcuts.js";
import { slots } from "../core/slots.js";
import { ui } from "../kit/index.js";

/** Oyna ochiq turganda to'lov/hisob yorliqlari o'chiq — ular ostidagi ekranga tegishli. */
const noOverlay = (screen) => screen.el.overlay.hidden && !ui.hasDialog();

slots.contribute("shortcuts", {
	id: "close",
	key: "Escape",
	description: __("Yopish"),
	allowInInput: true,
	handler: (screen, event) => {
		if (isTyping(event.target)) {
			event.target.blur();
			return;
		}
		if (!screen.el.menu.hidden) {
			screen.closeMenu();
			return;
		}
		if (ui.closeTop()) return;
		if (!screen.el.overlay.hidden) screen.closeModal();
	},
});

slots.contribute("shortcuts", {
	id: "pay",
	key: "F2",
	description: __("To'lov"),
	when: noOverlay,
	handler: (screen) => screen.triggerAction("pay"),
});

slots.contribute("shortcuts", {
	id: "give-bill",
	key: "F3",
	description: __("Hisob berish"),
	when: noOverlay,
	handler: (screen) => screen.triggerAction("give-bill"),
});
