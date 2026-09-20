/**
 * Buyurtma moduli uchun umumiy narsalar.
 *
 * Yadroga ES import bilan EMAS, `ozturk.cashier` nomlar fazosi orqali ulanamiz:
 * bu alohida yig'ma, yadro nusxasini o'zi bilan olib kelsa reestrlar bo'linib
 * ketardi (README §3). Bu fayl yig'ma yuklanganda yadro allaqachon tayyor.
 */

export const { features, slots, ui, util } = ozturk.cashier;
export const { esc, fmtQty } = util;

/** Serverdagi `order_type` qiymatlari (`api/cashier_orders.py`). */
export const DINE_IN = "Dine In";
export const TAKE_AWAY = "Take Away";
export const DELIVERY = "Delivery";

/**
 * Izoh maydonlari (buyurtma va taom izohi) Frappe `Data` turida — baza 140 belgidan
 * ortig'ini saqlamaydi. Kassir yozib bo'lgach xato ko'rmasligi uchun oldindan cheklanadi.
 */
export const COMMENT_LIMIT = 140;

/** Kassadan ochiladigan buyurtma turlari — server ham aynan shularni qabul qiladi. */
export function orderTypes() {
	return [
		{ value: DINE_IN, label: __("Zalda") },
		{ value: TAKE_AWAY, label: __("Olib ketish") },
		{ value: DELIVERY, label: __("Yetkazib berish") },
	];
}

/** Turning o'zbekcha nomi; noma'lum tur (masalan Desktop POS) — o'z nomi bilan. */
export function orderTypeLabel(type) {
	const known = orderTypes().find((entry) => entry.value === type);
	return known ? known.label : type || "";
}

/** Kassa smenasi ochiqmi. Buyurtma ochish (server ham) faqat ochiq smenada ishlaydi. */
export function shiftIsOpen(screen) {
	return !!(screen.ctx && screen.ctx.shift && screen.ctx.shift.open);
}

/** `disabled` slot qiymati: smena yopiq bo'lsa sabab matni, aks holda `false`. */
export function shiftClosedReason(screen) {
	return shiftIsOpen(screen) ? false : __("Kassa yopiq — avval smenani oching");
}

/** Stolsiz buyurtma (olib ketish, yetkazib berish, Desktop POS). */
export const isTableless = (order) => !order.table;

/**
 * UUID v4. `crypto.randomUUID` faqat xavfsiz kontekstda (HTTPS) bor, kassa esa
 * ichki tarmoqda HTTP orqali ham ochilishi mumkin — `getRandomValues` hamma
 * joyda ishlaydi.
 */
export function uuid() {
	if (window.crypto && typeof window.crypto.randomUUID === "function") {
		return window.crypto.randomUUID();
	}
	const bytes = window.crypto.getRandomValues(new Uint8Array(16));
	bytes[6] = (bytes[6] & 0x0f) | 0x40;
	bytes[8] = (bytes[8] & 0x3f) | 0x80;
	const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0"));
	return [
		hex.slice(0, 4).join(""),
		hex.slice(4, 6).join(""),
		hex.slice(6, 8).join(""),
		hex.slice(8, 10).join(""),
		hex.slice(10).join(""),
	].join("-");
}
