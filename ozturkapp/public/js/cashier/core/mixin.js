/**
 * Sinf metodlarini boshqa sinfga ko'chiradi.
 *
 * NEGA
 * ====
 * `CashierScreen` ~90 ta metodli — bitta faylda o'qib bo'lmaydi. Har bir
 * mas'uliyat o'z faylida sinf bo'lib yoziladi va metodlari shu yerda
 * `CashierScreen.prototype` ga qo'shiladi. `this` hammasida BIR XIL —
 * ya'ni `screen` obyekti.
 *
 * Nomlar to'qnashsa XATO tashlanadi: jimgina ustidan yozib yuborish ikki
 * modul bir-birining metodini buzib qo'yishiga olib kelardi va topish
 * qiyin bo'lardi.
 */
export function mixin(target, ...sources) {
	for (const source of sources) {
		for (const name of Object.getOwnPropertyNames(source.prototype)) {
			if (name === "constructor") continue;

			if (Object.prototype.hasOwnProperty.call(target.prototype, name)) {
				throw new Error(`${source.name}.${name} — ${target.name} da allaqachon bor`);
			}
			Object.defineProperty(
				target.prototype,
				name,
				Object.getOwnPropertyDescriptor(source.prototype, name)
			);
		}
	}
}
