/**
 * Menyu: yuklash, qisqa muddatli kesh, kurs va qidiruv bo'yicha saralash.
 */

import { DINE_IN, TAKE_AWAY, DELIVERY } from "./shared.js";
import { orderApi } from "./api.js";

/** Kursi yo'q taomlar uchun maxsus filtr qiymati. */
const NO_COURSE = "__no_course__";

/** Menyu shuncha vaqt eslab qolinadi — ketma-ket ochilgan oynalar qayta so'ramaydi. */
const MENU_TTL_MS = 60 * 1000;

const cache = new Map();

/**
 * Menyu so'rovi parametrlari.
 *
 * URY zal menyusini buyurtma turi menyusidan USTUN qo'yadi (`getRestaurantMenu`):
 * zalda — stol zali, olib ketish/yetkazishda — buyurtma turi. Noma'lum tur
 * (Desktop POS, agregator) `get_menu` da xato beradi, shuning uchun yuborilmaydi.
 */
export function menuParams(orderType, room) {
	const known = [DINE_IN, TAKE_AWAY, DELIVERY].includes(orderType);
	return {
		orderType: known ? orderType : null,
		room: orderType === DINE_IN || !known ? room || null : null,
	};
}

export const menuKey = ({ orderType, room }) => `${orderType || ""}|${room || ""}`;

/** Menyuni yuklaydi; bir xil so'rov muddat ichida keshdan olinadi (va parallel so'rovlar birlashadi). */
export function loadMenu(screen, params) {
	const key = menuKey(params);
	const hit = cache.get(key);
	if (hit && Date.now() - hit.at < MENU_TTL_MS) return hit.promise;

	const promise = Promise.resolve(orderApi.getMenu(screen, params));
	cache.set(key, { at: Date.now(), promise });
	// Xato keshda qolib ketmasin — keyingi urinish qayta so'raydi.
	promise.catch(() => {
		if (cache.get(key) && cache.get(key).promise === promise) cache.delete(key);
	});
	return promise;
}

/** Kurs va qidiruv bo'yicha ko'rinadigan taomlar. Qidiruv butun menyudan izlaydi. */
export function visibleItems(menu, { course, query }) {
	const items = menu.items || [];
	const needle = (query || "").trim().toLocaleLowerCase();
	if (needle) {
		return items.filter(
			(entry) =>
				(entry.item_name || "").toLocaleLowerCase().includes(needle) ||
				(entry.item || "").toLocaleLowerCase().includes(needle)
		);
	}
	if (course === NO_COURSE) return items.filter((entry) => !entry.course);
	if (course) return items.filter((entry) => entry.course === course);
	return items;
}

/** Kurs tugmalari: «Hammasi» tashqarida; kursi yo'q taomlar bo'lsa «Boshqa». */
export function courseChoices(menu) {
	const courses = menu.courses || [];
	if (!courses.length) return [];
	const choices = courses.map((name) => ({ value: name, label: name }));
	if ((menu.items || []).some((entry) => !entry.course)) {
		choices.push({ value: NO_COURSE, label: __("Boshqa") });
	}
	return choices;
}
