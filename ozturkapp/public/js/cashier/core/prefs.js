/**
 * Qurilmaga xos sozlamalar (localStorage).
 *
 * Bu server sozlamasi EMAS: tanlangan zal va ko'rinish kabi narsalar har bir kassa qurilmasida alohida eslab qolinadi.
 */

const PREFIX = "ozturk_cashier_";

export function readPreference(key) {
	try {
		return localStorage.getItem(`${PREFIX}${key}`);
	} catch (e) {
		return null;
	}
}

export function writePreference(key, value) {
	try {
		localStorage.setItem(`${PREFIX}${key}`, value);
	} catch (e) {
		/* localStorage o'chirilgan bo'lishi mumkin — muhim emas */
	}
}
