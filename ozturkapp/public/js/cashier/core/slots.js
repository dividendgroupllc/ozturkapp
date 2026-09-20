/**
 * Kengaytma nuqtalari (slot) — funksiya modullari asosiy fayllarni
 * TAHRIRLAMASDAN ekranga qo'shimcha qo'sha oladi.
 *
 * Har bir slot — nomlangan ro'yxat. Modul unga element qo'shadi
 * (`slots.contribute`), asosiy kod esa chizish paytida o'qiydi
 * (`slots.visible`). Asosiy tugma va menyu bandlari ham SHU YO'L bilan
 * qo'shilgan: o'zining tartibi (`order`) bilan, ya'ni funksiya moduli
 * ularning orasiga tushishi mumkin.
 *
 * Slotlar ro'yxati va element shakli `README.md` da.
 */

/** Ma'lum slotlar. Noma'lum nom — xato: imloviy xato jimgina ishlamay qolmasin. */
const SLOT_NAMES = [
	"topbar.menu",
	"topbar.quick",
	"panel.info",
	"panel.primary",
	"panel.secondary",
	"panel.more",
	"payment.beforeAmount",
	"payment.afterAmount",
	"payment.footer",
	"orders.tabs",
	"floor.tileBadges",
	"history.rowActions",
	"history.detailActions",
	"shortcuts",
	"realtime",
	"refresh",
];

const registry = new Map(SLOT_NAMES.map((name) => [name, []]));
let sequence = 0;

function slotOf(name) {
	const list = registry.get(name);
	if (!list) throw new Error(`Noma'lum slot: ${name}`);
	return list;
}

export const slots = {
	/**
	 * Slotga element qo'shadi.
	 *
	 * @param {string} name  slot nomi (`SLOT_NAMES`)
	 * @param {object} item  `id` (slot ichida noyob) va `order` (kichigi oldin,
	 *                       standart 100) bilan; qolgan maydonlar slotga xos
	 * @returns {Function}   elementni olib tashlaydigan funksiya
	 */
	contribute(name, item) {
		const list = slotOf(name);
		if (!item || !item.id) throw new Error(`${name}: elementda id yo'q`);
		if (list.some((entry) => entry.id === item.id)) {
			throw new Error(`${name}: '${item.id}' allaqachon qo'shilgan`);
		}

		const entry = { order: 100, ...item, _seq: (sequence += 1) };
		list.push(entry);
		return () => {
			const at = list.indexOf(entry);
			if (at !== -1) list.splice(at, 1);
		};
	},

	/** Slotdagi barcha elementlar — `order`, keyin qo'shilish tartibida. */
	items(name) {
		return [...slotOf(name)].sort((a, b) => a.order - b.order || a._seq - b._seq);
	},

	/** Faqat `when(...args)` rost (yoki yo'q) bo'lganlar. */
	visible(name, ...args) {
		return this.items(name).filter((item) => !item.when || item.when(...args));
	},
};
