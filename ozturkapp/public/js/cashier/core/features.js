/**
 * Funksiya reestri.
 *
 * Har bir qo'shimcha funksiya (aralash to'lov, chegirma, stol ko'chirish, ...)
 * o'zining modulida `features.register()` ni chaqiradi. Modul faqat POS
 * Profile'da bayrog'i YOQILGAN bo'lsa o'rnatiladi (`ctx.features[flag]`):
 * o'chiq funksiya ekranda umuman paydo bo'lmaydi.
 *
 * Bu — qulaylik. Himoya emas: server har amalda bayroqni qayta tekshiradi
 * (`cashier_features.assert_enabled`).
 */

const definitions = [];

export const features = {
	/**
	 * @param {object}   def
	 * @param {string}   def.key       noyob nom
	 * @param {string}   [def.flag]    `ctx.features` dagi kalit; berilmasa — doim yoqiq
	 * @param {Function} def.install   `install(screen)` — slotlarga element qo'shadi,
	 *                                 kerak bo'lsa `screen.listen(...)` bilan obuna
	 *                                 bo'ladi. Olib tashlovchi funksiya (yoki ularning
	 *                                 massivi) qaytarishi mumkin.
	 */
	register(def) {
		if (!def || !def.key || typeof def.install !== "function") {
			throw new Error("features.register: key va install() kerak");
		}
		if (definitions.some((item) => item.key === def.key)) {
			throw new Error(`features.register: '${def.key}' allaqachon bor`);
		}
		definitions.push(def);
	},

	list() {
		return [...definitions];
	},

	/**
	 * `screen.ctx` bo'yicha yoqilganlarini o'rnatadi, o'chganlarini olib tashlaydi.
	 * `boot()` har chaqirilganda ishlaydi (smena ochilgach kontekst qayta o'qiladi).
	 */
	sync(screen) {
		const enabled = screen.ctx.features || {};
		const installed = screen.installedFeatures;

		for (const def of definitions) {
			const wanted = !def.flag || !!enabled[def.flag];
			const present = installed.has(def.key);

			if (wanted && !present) {
				installed.set(def.key, def.install(screen));
			} else if (!wanted && present) {
				dispose(installed.get(def.key));
				installed.delete(def.key);
			}
		}
	},

	disposeAll(screen) {
		screen.installedFeatures.forEach(dispose);
		screen.installedFeatures.clear();
	},
};

function dispose(handle) {
	[].concat(handle || []).forEach((fn) => typeof fn === "function" && fn());
}
