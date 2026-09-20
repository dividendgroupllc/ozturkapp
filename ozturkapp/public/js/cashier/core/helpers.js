/**
 * `screen` uchun qisqa yo'llar: so'rov, summa formati, xato matni, sozlamalar.
 *
 * Asl mantiq `api.js`, `prefs.js` va `util/format.js` da — bu yerda faqat
 * `screen.call(...)` ko'rinishida chaqirish qulayligi (funksiya modullari
 * `screen` bilan ishlaydi).
 */

import { call, errorText } from "./api.js";
import { readPreference, writePreference } from "./prefs.js";
import { money } from "../util/format.js";
import { ui } from "../kit/index.js";

export class HelperMethods {
	call(method, args) {
		return call(method, args);
	}

	money(value) {
		return money(value);
	}

	/**
	 * Tugmani so'rov ketayotgan (band) holatga o'tkazadi va qaytaradi.
	 *
	 * `data-locked="1"` — tashqaridan «hozir yoqib bo'lmaydi» deb belgilangan
	 * tugma (`PaymentSession.setConfirmEnabled(false)`): so'rov tugagach `busy(false)`
	 * uni qayta YOQMAYDI. Aks holda xato bilan tugagan so'rovdan keyin shartlari
	 * bajarilmagan «To'lovni tasdiqlash» yoqilib qolardi.
	 */
	busy(button, state) {
		if (!button) return;
		button.classList.toggle("rc-btn--busy", !!state);
		button.disabled = !!state || button.dataset.locked === "1";
	}

	errorText(error) {
		return errorText(error);
	}

	/** Xatoni ko'rinadigan bildirishnoma qiladi. Rad etilgan tasdiq — xato EMAS. */
	alertError(error) {
		const text = errorText(error);
		if (text) ui.toast(text, { indicator: "red", seconds: 7 });
	}

	readPreference(key) {
		return readPreference(key);
	}

	writePreference(key, value) {
		writePreference(key, value);
	}
}
