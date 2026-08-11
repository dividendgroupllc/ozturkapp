// Ozturkapp — hujjat qatorlarida item'ni Item Group bo'yicha filtrlash yordamchisi.
//
// Jazira'da guruh nomlari ("Сырьё", "Полуфабрикат") to'g'ridan-to'g'ri
// set_query ichida yozilgan edi. Agar saytda bunday guruh bo'lmasa, item
// ro'yxati BO'SH chiqib, hujjat to'ldirib bo'lmay qolardi.
//
// Bu yordamchi avval qaysi guruhlar haqiqatan mavjudligini tekshiradi va
// filtrni FAQAT mavjud guruhlarga qo'yadi. Hech biri topilmasa — filtr
// umuman qo'yilmaydi (standart xatti-harakat).

frappe.provide('ozturkapp.item_filter');

ozturkapp.item_filter._cache = {};

/**
 * Berilgan guruhlardan saytda mavjudlarini qaytaradi (natija keshlanadi).
 * @param {string[]} groups — tekshiriladigan Item Group nomlari
 * @returns {Promise<string[]>}
 */
ozturkapp.item_filter.existing_groups = function (groups) {
	const key = groups.join('|');
	if (ozturkapp.item_filter._cache[key]) {
		return ozturkapp.item_filter._cache[key];
	}

	const promise = frappe.db
		.get_list('Item Group', {
			filters: { name: ['in', groups] },
			fields: ['name'],
			limit: groups.length,
		})
		.then((rows) => (rows || []).map((r) => r.name))
		.catch(() => []);

	ozturkapp.item_filter._cache[key] = promise;
	return promise;
};

/**
 * `items` jadvalidagi item_code uchun Item Group filtrini o'rnatadi.
 * @param {object} frm
 * @param {string[]} groups
 */
ozturkapp.item_filter.apply = function (frm, groups) {
	ozturkapp.item_filter.existing_groups(groups).then((found) => {
		if (!found.length) {
			// Guruhlar hali yaratilmagan — filtrsiz ishlaymiz
			return;
		}
		frm.set_query('item_code', 'items', () => ({
			filters: { item_group: ['in', found] },
		}));
	});
};
