/**
 * Kassa oynasi — Desk sahifasi kirish nuqtasi.
 *
 * Sahifaning butun kodi va uslubi `public/` da turadi va Frappe yig'masiga
 * (`bench build --app ozturkapp`) yig'iladi:
 *
 *   public/js/cashier/cashier.bundle.js     — yadro (Screen, UI to'plami)
 *   public/css/cashier.bundle.scss          — uslublar
 *   public/js/cashier/features/cashier_*.bundle.js|css
 *                                           — qo'shimcha funksiya modullari
 *
 * Bu fayl faqat ularni yuklaydi va Desk hayot sikli hodisalarini `Screen` ga
 * uzatadi. Funksiya modullari bu faylni TAHRIRLAMAYDI: `cashier_` bilan
 * boshlanadigan yig'malar `assets.json` dan o'zi topiladi.
 *
 * NEGA YIG'MA
 * ===========
 * Fayl nomida hash bor (`cashier.bundle.ABCD1234.js`), shuning uchun
 * brauzer keshi har deployda o'zi yangilanadi — «Ctrl+Shift+R» kerak emas.
 * `deploy/deploy.sh` `bench build` ni allaqachon ishga tushiradi.
 */

frappe.provide("ozturk.cashier");

/**
 * Yig'ma shuncha vaqtda (ms) yuklanmasa yuklanmadi deb hisoblanadi.
 *
 * `frappe.require` FAQAT `resolve` qiladi, `reject` yo'q (`assets.js`): fayl so'rovi
 * yiqilsa (tarmoq, deploydan keyin eskirgan hash -> 404) va'da hech qachon hal bo'lmaydi,
 * Desk esa `frappe.dom.freeze()` ni yechmaydi — kassir xira BO'SH sahifa oldida qolardi.
 */
const LOAD_TIMEOUT_MS = 20 * 1000;

/**
 * Yig'ma yuklanmagan sahifa BO'SH qolmasin: sabab va «Qayta yuklash» tugmasi.
 * Matn HTML'ga faqat `__()` dan (statik) olinadi — serverdan kelgan qiymat yo'q.
 */
function showLoadError(page, message) {
	$(page.main).html(
		`<div class="text-center text-muted" style="padding:64px 24px">
			<p style="font-size:16px;margin-bottom:20px">${message}</p>
			<button class="btn btn-primary btn-lg rc-reload" type="button" style="min-height:48px">${__(
				"Qayta yuklash"
			)}</button>
		</div>`
	);
	$(page.main)
		.find(".rc-reload")
		.on("click", () => window.location.reload());
}

frappe.pages["restaurant-cashier"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Restoran kassasi"),
		single_column: true,
	});

	// Desk v15 `on_page_hide` ni CHAQIRMAYDI: sahifa almashganda faqat sahifa
	// elementiga jQuery "hide" hodisasi yuboriladi (`views/container.js`). Kassa
	// yorliqlari (F2/F3/Esc) va fon vazifalari (davriy so'rov, ovoz) shu hodisa bilan
	// to'xtaydi — aks holda boshqa Desk sahifasida ham ishlab turardi.
	$(wrapper).on("hide", () => {
		wrapper.shown = false;
		wrapper.cashier && wrapper.cashier.suspend();
	});

	// Yadro AVVAL, funksiya modullari undan KEYIN (ular `ozturk.cashier` ga tayanadi):
	// `frappe.require` ro'yxat tartibida bajaradi.
	const featureBundles = Object.keys(frappe.boot.assets_json || {})
		.filter((name) => /^cashier_[a-z0-9_]+\.bundle\.(js|css)$/.test(name))
		.sort();

	const loaded = frappe.require(["cashier.bundle.css", "cashier.bundle.js", ...featureBundles]);
	const timeout = new Promise((_, reject) =>
		setTimeout(() => reject(new Error("bundle load timeout")), LOAD_TIMEOUT_MS)
	);

	Promise.race([loaded, timeout]).then(() => {
		// Yig'ma yig'ilmagan (`bench build` unutilgan) bo'lsa `Screen` yo'q —
		// bo'sh sahifa o'rniga sababni aytamiz.
		if (!ozturk.cashier.Screen) {
			showLoadError(
				page,
				__(
					"Kassa skripti topilmadi. Administrator «bench build --app ozturkapp» ni ishga tushirishi kerak."
				)
			);
			return;
		}

		wrapper.cashier = new ozturk.cashier.Screen(page);

		// Yig'ma sahifa ko'rsatilgandan KEYIN yuklanishi mumkin — `on_page_show`
		// o'sha paytda Screen'ni topolmagan edi. Sahifa hali ko'rinib tursa
		// hayot siklini shu yerda boshlaymiz.
		if (wrapper.shown) wrapper.cashier.resume();
	}).catch((error) => {
		// Tarmoq uzilishi yoki yig'ma fayli topilmasa `frappe.require` rad etiladi —
		// ilgari sahifa jim BO'SH qolardi.
		console.error("restaurant-cashier: yig'ma yuklanmadi", error);
		frappe.dom.unfreeze();
		showLoadError(
			page,
			__("Kassa yuklanmadi — internet aloqasini tekshiring va sahifani qayta yuklang.")
		);
	});
};

frappe.pages["restaurant-cashier"].on_page_show = function (wrapper) {
	wrapper.shown = true;
	wrapper.cashier && wrapper.cashier.resume();
};
