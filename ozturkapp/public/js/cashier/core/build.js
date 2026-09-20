/**
 * Yig'ma (bundle) belgisi.
 *
 * Desk — bitta sahifali ilova (SPA): F5 bosilmasa eski skript xotirada
 * qolib ketadi va yangi kod umuman ishga tushmaydi. Belgi `assets.json`
 * dagi hash'dan olinadi (`cashier.bundle.ABCD1234.js`) — `bench build`
 * har safar kod o'zgarganda uni o'zi yangilaydi, qo'lda oshirish kerak
 * emas va unutib bo'lmaydi. «⋯» menyusida ko'rinadi.
 */
export function bundleVersion() {
	const path = (frappe.boot.assets_json || {})["cashier.bundle.js"] || "";
	const match = path.match(/cashier\.bundle\.([A-Z0-9]+)\.js$/);
	return match ? match[1] : "dev";
}
