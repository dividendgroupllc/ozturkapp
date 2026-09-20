/**
 * Sof yordamchi funksiyalar: ekranlash, summa formati, kiritish maydoni,
 * o'tgan vaqt ko'rsatkichi.
 */

/**
 * Summani PROBEL bilan guruhlaydi: `1080800` -> `1 080 800`.
 *
 * NEGA `format_currency()` EMAS
 * =============================
 * U saytning raqam formatiga tayanadi (`#,###.##`) va shu saytda
 * `лв 1,080,800.00` chiqaradi. Uchta muammo bor:
 *
 *   1. vergul — ingliz yozuvi; o'zbek/rus yozuvida u O'NLIK
 *      ajratgich sifatida o'qiladi, ya'ni chalg'itadi;
 *   2. `,00` — summalar butun so'mda, nol tiyin faqat ekranni
 *      to'ldiradi;
 *   3. `лв` — bolgar levi belgisi, UZS uchun noto'g'ri (Currency
 *      yozuvida shunday sozlangan).
 *
 * Saytning global sozlamasini o'zgartirish butun ERPNext'ga ta'sir
 * qiladi, shuning uchun qoida O'ZIMIZDA:
 * `utils/money.py: format_amount()` va ofitsant ilovasidagi
 * `Fmt.money()` — uchalasi bir xil natija berishi shart.
 */
export function money(value) {
	const number = num(value);
	const abs = Math.abs(number);

	const whole = Math.trunc(abs);
	let fraction = Math.round((abs - whole) * 100);

	// Yaxlitlash butun songa o'tkazgan bo'lishi mumkin: 1.999 -> 2,00.
	const carried = fraction >= 100;
	const grouped = String(carried ? whole + 1 : whole).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
	if (carried) fraction = 0;

	// Tiyin FAQAT nolga teng bo'lmasa ko'rsatiladi.
	const text = fraction ? `${grouped},${String(fraction).padStart(2, "0")}` : grouped;

	// Yaxlitlangach nol bo'lib qolgan qiymatga («-0,004») minus qo'yilmaydi.
	return number < 0 && (whole || fraction || carried) ? `-${text}` : text;
}

/**
 * Sonni saytning raqam formatiga BOG'LIQ BO'LMAGAN holda o'qiydi.
 *
 * NEGA `flt()` EMAS
 * =================
 * `flt("1234.5")` matnni saytning raqam formati (`System Settings.number_format`)
 * bilan o'qiydi. `#.###,##` (nuqta — guruh, vergul — o'nlik) formatida nuqta
 * GURUH ajratgichi hisoblanib `12345` chiqadi — kassa summasi 10 baravar
 * o'zgarardi. Bu yerda matn allaqachon `1234.5` (nuqta — o'nlik) ko'rinishida.
 * Son (`number`) esa o'zgarishsiz qaytadi.
 */
export function num(value) {
	if (typeof value === "number") return Number.isFinite(value) ? value : 0;
	const parsed = parseFloat(String(value == null ? "" : value));
	return Number.isFinite(parsed) ? parsed : 0;
}

// ═══════════════════════════════════════════════════════════════
//  O'tgan vaqt — kassir uzoq kutgan stolni bir qarashda ko'rsin
// ═══════════════════════════════════════════════════════════════

/**
 * Diqqat chegaralari (daqiqa). Bu FAQAT ko'rinish: chegaradan oshgan stol
 * sarg'ayadi/qizaradi, boshqa hech narsa o'zgarmaydi — biznes qoidasi emas.
 */
const ELAPSED_WARN_MINUTES = 60;
const ELAPSED_ALERT_MINUTES = 90;

export function elapsedLevel(minutes) {
	if (minutes > ELAPSED_ALERT_MINUTES) return "alert";
	if (minutes > ELAPSED_WARN_MINUTES) return "warn";
	return "ok";
}

/**
 * `47 daq` / `1 soat 35 daq`. `short` — stol kartasi uchun ixcham shakl:
 * `47 daq` / `1:35` (aylana stolda uzun matn sig'maydi).
 */
export function formatElapsed(minutes, short = false) {
	const total = Math.max(0, cint(minutes));
	if (total < 60) return `${total} ${__("daq")}`;
	if (short) return `${Math.floor(total / 60)}:${pad2(total % 60)}`;
	return `${Math.floor(total / 60)} ${__("soat")} ${total % 60} ${__("daq")}`;
}

/**
 * O'tgan vaqt belgisi. Serverdan `elapsed_minutes` YUKLANGAN paytda keladi;
 * keyingi daqiqalarni brauzer o'zi qo'shadi (`tickElapsed`). Server soati
 * bilan brauzer soati/vaqt mintaqasi farq qilishi mumkin, shuning uchun
 * `opened_at` matni emas, aynan yuklangan paytdan beri o'tgan vaqt
 * qo'shiladi.
 *
 * `minutes` yo'q (eski server) bo'lsa belgi chizilmaydi.
 */
export function elapsedHtml(minutes, loadedAt, short = false) {
	if (minutes === null || minutes === undefined || minutes === "") return "";
	const value = cint(minutes);
	return `<span class="rc-elapsed" data-elapsed="${value}" data-since="${cint(
		loadedAt
	)}" data-short="${short ? 1 : 0}" data-level="${elapsedLevel(value)}">${esc(
		formatElapsed(value, short)
	)}</span>`;
}

/**
 * Serverdagi `order_type` (`Dine In` / `Take Away` / `Delivery`, URY qiymatlari) ni
 * kassirga o'zbekcha ko'rsatadi. Stolsiz buyurtmada u sarlavha o'rnida chiqadi —
 * inglizcha yozuv ekranda tasodifiy ko'rinardi. Noma'lum qiymat o'zgarishsiz qaytadi.
 */
export function orderTypeLabel(type) {
	const labels = {
		"Dine In": __("Zalda"),
		"Take Away": __("Olib ketish"),
		Delivery: __("Yetkazib berish"),
	};
	return labels[type] || type || "";
}

/** Bayroq yoki maydonni stol yozuvidan ham, uning ichidagi `order` dan ham o'qiydi. */
export function fromTable(table, key) {
	if (table[key] !== undefined && table[key] !== null) return table[key];
	return table.order ? table.order[key] : undefined;
}

/**
 * `SS:DD`. Server vaqtni har xil shaklda qaytaradi: `9:00:00` (soat nol bilan
 * to'ldirilmagan), `09:00:00`, `9:00`, `2026-09-20 9:05:00.123` yoki
 * `2026-09-20T09:05:00`. `slice(0, 5)` birinchisida `9:00:` beradi — shuning
 * uchun vaqt HAR DOIM shu funksiya orqali ko'rsatiladi. Vaqt yo'q yoki tanib
 * bo'lmasa — bo'sh satr.
 */
export function hhmm(value) {
	const match = /(?:^|[ T])(\d{1,2}):(\d{2})/.exec(String(value == null ? "" : value));
	return match ? `${match[1].padStart(2, "0")}:${match[2]}` : "";
}

/** Bron vaqtini tanlash uchun ro'yxatlar — yozishdan ko'ra tanlash tezroq. */
export const HOUR_OPTIONS = Array.from({ length: 24 }, (_, i) => pad2(i));
export const MINUTE_OPTIONS = ["00", "15", "30", "45"];

export function pad2(value) {
	return String(value).padStart(2, "0");
}

/** Hozirgi vaqtni keyingi 15 daqiqalik qadamga yaxlitlaydi. */
export function defaultTimeSlot() {
	const parts = hhmm(frappe.datetime.now_time()).split(":");
	let hour = cint(parts[0]);
	let minute = Math.ceil(cint(parts[1]) / 15) * 15;

	if (minute > 45) {
		minute = 0;
		hour = (hour + 1) % 24;
	}

	return { hour: pad2(hour), minute: pad2(minute) };
}

/** HTML'ga qo'yishdan oldin ekranlash — serverdan kelgan matnga ishonmaymiz. */
export function esc(value) {
	if (value === null || value === undefined) return "";
	return String(value).replace(
		/[&<>"']/g,
		(ch) =>
			({
				"&": "&amp;",
				"<": "&lt;",
				">": "&gt;",
				'"': "&quot;",
				"'": "&#39;",
			}[ch])
	);
}

/**
 * Summa kiritish maydonida eng ko'pi bilan shuncha raqam (999 999 999 999).
 * Chegarasiz maydonga 20+ raqam yopishtirilsa son `2^53` dan oshib aniqligini
 * yo'qotardi va serverga noto'g'ri summa ketardi.
 */
export const MAX_WHOLE_DIGITS = 12;

/**
 * Qiymatni matnga aylantiradi. Son `1e21` yoki `1e-7` ko'rinishida («ilmiy»)
 * chiqmasin — `groupAmount` undan raqamlarni terib olib `121` yasab qo'yardi.
 */
function amountText(value) {
	if (typeof value !== "number") return String(value == null ? "" : value);
	if (!Number.isFinite(value)) return "";

	const limit = 10 ** MAX_WHOLE_DIGITS - 1;
	const clamped = Math.max(-limit, Math.min(limit, value));
	return clamped.toFixed(2).replace(/\.?0+$/, "");
}

/**
 * Kiritilayotgan summani PROBEL bilan guruhlaydi: `1080800` -> `1 080 800`.
 *
 * NEGA KIRITISH MAYDONIDA HAM
 * ===========================
 * Kassir eng ko'p xatoni AYNAN yozayotganda qiladi: nolni bittasini
 * ortiq yoki kam bosgani bir qarashda ko'rinmaydi. `1080800` va
 * `10808000` bir xilga o'xshaydi, `1 080 800` va `10 808 000` esa yo'q.
 *
 * Shu sababli maydon `type="number"` EMAS — brauzer unda probelga yo'l
 * qo'ymaydi. `inputmode="numeric"` telefon/planshetda raqamli
 * klaviaturani baribir ochadi.
 */
export function groupAmount(text) {
	const raw = amountText(text)
		.replace(/\s/g, "")
		.replace(",", ".");
	const negative = raw.startsWith("-");

	const digits = raw.replace(/[^\d.]/g, "");
	const dot = digits.indexOf(".");
	// BOSHIDAGI NOLLAR OLIB TASHLANADI.
	//
	// Maydon ba'zan tayyor `0` bilan chiziladi (bo'sh smena, kassa ochish).
	// Kassir uning USTIGA yozganda `0` joyida qolib `05 000` bo'lardi:
	// summa to'g'ri hisoblansa ham ekranda xato ko'rinardi va kassirni
	// chalg'itardi. `0` faqat o'zi yolg'iz qolganda saqlanadi (`0`, `0,50`).
	const whole = (dot === -1 ? digits : digits.slice(0, dot))
		.replace(/^0+(?=\d)/, "")
		.slice(0, MAX_WHOLE_DIGITS);
	const fraction =
		dot === -1
			? null
			: digits
					.slice(dot + 1)
					.replace(/\./g, "")
					.slice(0, 2);

	const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
	// Yozish jarayonida `1234,` holati ham bo'ladi — vergul saqlanadi.
	const text2 = fraction === null ? grouped : `${grouped},${fraction}`;

	return (negative ? "-" : "") + text2;
}

/**
 * Guruhlangan matndan sonni qaytaradi: `1 234,50` -> `1234.5`.
 *
 * Matn AVVAL `groupAmount` dan o'tadi: maydonda ko'rinadigan summa va serverga
 * ketadigan summa bitta qoidadan chiqadi (uzunlik chegarasi, tiyin, belgilar).
 */
export function parseAmount(text) {
	return num(groupAmount(text).replace(/\s/g, "").replace(",", "."));
}

/**
 * Maydonni guruhlab turadigan qiladi va KURSORNI joyida saqlaydi.
 *
 * Kursorni tiklamasak, probel qo'shilgan zahoti u satr oxiriga sakraydi
 * va kassir raqamni o'rtasidan to'g'irlay olmaydi.
 *
 * `onChange(input)` yozilgan maydonni oladi: bir nechta maydon bitta ishlovchiga
 * ulanganda (to'lov usullari) qaysi biri o'zgarganini bilish uchun.
 */
export function bindAmountInput($input, onChange) {
	// Maydonga tegilganda ichidagi summa TANLANADI: keyingi raqam uni almashtiradi.
	// Aks holda to'lov maydoniga (tayyor `551 040` turgan) tegib `600000` yozilsa,
	// raqamlar mavjud summaning OXIRIGA qo'shilib `551 040 600 000` bo'lardi.
	$input.on("focus", (event) => {
		const el = event.currentTarget;
		setTimeout(() => {
			if (document.activeElement === el) el.select();
		}, 0);
	});

	$input.on("input", (event) => {
		const el = event.currentTarget;
		const caret = el.selectionStart || 0;
		const typedBefore = (el.value.slice(0, caret).match(/[\d.,]/g) || []).length;

		// Kiritilgan summa manfiy bo'lmaydi: «−» yozilsa tashlab yuboriladi.
		el.value = groupAmount(el.value).replace(/^-/, "");

		let seen = 0;
		let pos = 0;
		while (pos < el.value.length && seen < typedBefore) {
			if (/[\d.,]/.test(el.value[pos])) seen += 1;
			pos += 1;
		}
		el.setSelectionRange(pos, pos);

		if (onChange) onChange(el);
	});
}

export function fmtQty(value) {
	// `0.1 + 0.2` kabi sonli shovqin («0.30000000000000004») ko'rsatilmasin.
	return String(Math.round(num(value) * 1000) / 1000);
}

/**
 * Soliq/xizmat haqi qatori nomi.
 *
 * Server tavsifiga foizni o'zi yozadi («Xizmat haqi 12%»): unga yana `(12%)` qo'shilsa
 * kassir «Xizmat haqi 12% (12%)» ko'rib, ikki marta hisoblangan deb o'ylardi.
 */
export function taxLabel(tax) {
	const text = String(tax.description || "");
	if (!tax.rate) return text;

	const rate = `${fmtQty(tax.rate)}%`;
	return text.includes(rate) ? text : `${text} (${rate})`;
}

/** To'lov uchun tez tanlash summalari (faqat qulaylik, hisob emas). */
export function quickAmounts(due) {
	const steps = [1000, 5000, 10000, 50000, 100000];
	const values = new Set([Math.ceil(due)]);
	steps.forEach((step) => {
		const rounded = Math.ceil(due / step) * step;
		if (rounded > due) values.add(rounded);
	});
	return Array.from(values).slice(0, 4);
}
