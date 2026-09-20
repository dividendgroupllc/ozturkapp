/**
 * Savat — kassir tanlagan taomlar.
 *
 * NARX BU YERDA HISOBLANMAYDI (server yagona manba): `rate` faqat menyudagi
 * narxni ekranda ko'rsatish va «taxminiy» yig'indi uchun saqlanadi, serverga
 * HECH QACHON yuborilmaydi (`payload`). Haqiqiy hisob (xizmat haqi, soliq,
 * yaxlitlash) buyurtma yaratilgach serverdan keladi.
 *
 * Bir taom — bir qator: server ham (`add_items` da) bir taomning qatorlarini
 * bittaga jamlaydi, shuning uchun ikkita qatorga bo'lish ma'nosiz.
 */

export class Cart {
	constructor() {
		this.lines = new Map();
	}

	get size() {
		return this.lines.size;
	}

	/** Jami porsiyalar soni. */
	get quantity() {
		let total = 0;
		this.lines.forEach((line) => (total += line.qty));
		return total;
	}

	qty(item) {
		const line = this.lines.get(item);
		return line ? line.qty : 0;
	}

	add(menuItem) {
		const line = this.lines.get(menuItem.item);
		if (line) {
			line.qty += 1;
			return;
		}
		this.lines.set(menuItem.item, {
			item: menuItem.item,
			item_name: menuItem.item_name,
			rate: flt(menuItem.rate),
			qty: 1,
			comment: "",
		});
	}

	/** Miqdorni `delta` ga o'zgartiradi; 0 ga tushsa qator olib tashlanadi. */
	step(item, delta) {
		const line = this.lines.get(item);
		if (!line) return;
		line.qty += delta;
		if (line.qty <= 0) this.lines.delete(item);
	}

	setComment(item, text) {
		const line = this.lines.get(item);
		if (line) line.comment = text;
	}

	/** Menyu almashganda (buyurtma turi/zal) narxlar yangi menyudan olinadi. */
	reprice(menuItems) {
		const rates = new Map(menuItems.map((entry) => [entry.item, entry.rate]));
		this.lines.forEach((line) => {
			if (rates.has(line.item)) line.rate = flt(rates.get(line.item));
		});
	}

	/** Faqat ko'rsatish uchun: menyu narxi × miqdor. Aniq summani server beradi. */
	estimate() {
		let total = 0;
		this.lines.forEach((line) => (total += line.rate * line.qty));
		return total;
	}

	/** Serverga yuboriladigan qatorlar — narxsiz. */
	payload() {
		return [...this.lines.values()].map((line) => ({
			item: line.item,
			item_name: line.item_name,
			qty: line.qty,
			comment: line.comment,
		}));
	}
}
