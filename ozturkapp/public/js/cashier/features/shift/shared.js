/**
 * Smena moduli (g'aladon, kassa harakati, X-hisobot) uchun umumiy narsalar.
 *
 * Yadroga ES import bilan EMAS, `ozturk.cashier` nomlar fazosi orqali ulanamiz:
 * bu alohida yig'ma, yadro nusxasini o'zi bilan olib kelsa reestrlar bo'linib
 * ketardi (README §3). Bu fayl yig'ma yuklanganda yadro allaqachon tayyor.
 */

export const { features, slots, ui, util } = ozturk.cashier;
export const { esc, hhmm } = util;

/** Kassa smenasi ochiqmi. Hamma smena amali (server ham) faqat ochiq smenada ishlaydi. */
export function shiftIsOpen(screen) {
	return !!(screen.ctx && screen.ctx.shift && screen.ctx.shift.open);
}

/** Boshqa oyna ochiq emasmi — yorliq ostidagi ekranga tegishli amalni ishga tushirmasin. */
export function noOverlay(screen) {
	return screen.el.overlay.hidden && !ui.hasDialog();
}

/**
 * Server qiymatni YASHIRGANMI (ko'r sanoq).
 *
 * Kassirga kutilgan summa, farq, savdo jami va naqd summalar `None` bo'lib
 * keladi. `None` "0" degani EMAS — "sizga ko'rsatilmaydi": nol deb chizib
 * bo'lmaydi, aks holda kassir yashirilgan raqamni 0 deb o'ylaydi.
 */
export function isHidden(value) {
	return value === null || value === undefined;
}

/** `withApproval` bergan tasdiqni serverga uzatiladigan ko'rinishga aylantiradi. */
export function approvalArgs(approval) {
	return approval ? { approval: JSON.stringify(approval) } : {};
}
