/**
 * UI to'plamining umumiy holati.
 *
 * To'plam (`kit/`) `screen` ga bog'liq emas — u faqat ikkita narsani
 * bilishi kerak: elementlar QAYERGA qo'yilishi (`.rc-root` ichiga — shunda
 * `--rc-*` o'zgaruvchilar ishlaydi) va ekran
 * klaviaturasi yoqilganmi. Ikkalasini `screen` yuklangach bir marta
 * beradi (`ui.configure`).
 */
export const kit = {
	root: null,
	virtualKeyboard: false,

	/** Elementlar qo'yiladigan joy; `screen` hali yo'q bo'lsa — `body`. */
	get host() {
		return this.root || document.body;
	},

	configure({ root, virtualKeyboard }) {
		this.root = root || null;
		this.virtualKeyboard = !!virtualKeyboard;
	},
};
