/**
 * Server bilan aloqa: so'rov va xato matni.
 *
 * Bu funksiyalar `screen` ga bog'liq EMAS — UI to'plami (`kit/`) ham
 * shulardan foydalanadi.
 */

/** Menejer tasdig'i so'rovi foydalanuvchi tomonidan rad etilganda. */
export class ApprovalCancelled extends Error {
	constructor() {
		super("ApprovalCancelled");
		this.name = "ApprovalCancelled";
		this.cancelled = true;
	}
}

export function call(method, args) {
	// `silent: true` — ERPNext'ning O'Z msgprint oynasi CHIQMAYDI
	// (`frappe/public/js/frappe/request.js:459`). Xatoni sahifaning
	// o'zi joyida, tushunarli qilib ko'rsatadi.
	return frappe
		.call({ method, args: args || {}, freeze: false, silent: true })
		.then((r) => r.message);
}

/**
 * Menejer PIN'i Frappe xato hisobotiga tushmasin.
 *
 * Server 500 bersa Frappe «Server Error» oynasini ochadi: «Copy error to clipboard»
 * (yoki sozlangan bo'lsa e-pochta hisoboti) so'rov argumentlarini MATNGA yozadi, PIN
 * ham shunda — menejerning paroli yordam so'ralganda chatga tushib ketardi.
 * `cleanup_request_opts` faqat `password`/`passphrase` kalitlarini yashiradi; PIN esa
 * `approval` kaliti ichida (JSON), shuning uchun o'sha funksiya kengaytiriladi.
 * Faqat hisobot uchun ishlatiladigan nusxaga tegadi — so'rovning o'ziga emas.
 */
export function maskApprovalInErrorReports() {
	const request = frappe.request;
	if (!request || request.ozturkMasksApproval || typeof request.cleanup_request_opts !== "function") {
		return;
	}

	const original = request.cleanup_request_opts;
	request.cleanup_request_opts = function (opts) {
		const cleaned = original.call(this, opts);
		if (cleaned && cleaned.args && cleaned.args.approval) cleaned.args.approval = "*****";
		return cleaned;
	};
	request.ozturkMasksApproval = true;
}

/** Server tashlagan xato turi (`exc_type`), masalan `ApprovalRequired`. */
function exceptionType(error) {
	const body = error && (error.responseJSON || error);
	return (body && body.exc_type) || "";
}

/**
 * Server menejer tasdig'ini so'radimi?
 *
 * Frappe xatoni JSON'da `exc_type` bilan qaytaradi (`utils/response.py:
 * report_error`), `frappe.call` esa uni **jqXHR** bilan rad etadi — shuning
 * uchun `error.responseJSON.exc_type` o'qiladi.
 */
export function isApprovalRequired(error) {
	return exceptionType(error) === "ApprovalRequired";
}

/**
 * Serverdan kelgan HTML bo'lishi mumkin bo'lgan matnni oddiy matnga aylantiradi.
 *
 * `$("<div>").html(matn)` ISHLATILMAYDI: jQuery `<img src=x onerror=...>` ni
 * haqiqiy element qilib yaratadi va brauzer rasmni yuklab, `onerror` ni ISHGA
 * TUSHIRADI — hujjat DOM'ga ulanmagan bo'lsa ham. Xabarda mijoz/taom nomi
 * bo'lishi mumkin (`frappe.throw(_("...{0}").format(nom))`) — kassir yaratgan
 * mijoz nomi menejerning sessiyasida skript bo'lib ishlab ketardi. `DOMParser`
 * hujjati faol emas: skript ishlamaydi, rasm yuklanmaydi.
 */
function plainText(html) {
	const doc = new DOMParser().parseFromString(String(html), "text/html");
	doc.querySelectorAll("script, style").forEach((node) => node.remove());
	return (doc.body.textContent || "").trim();
}

/**
 * Xabarsiz (`_server_messages` yo'q) xatoda kassirga tushunarli matn: tarmoq
 * uzilishi, sessiya tugashi, server nosozligi. Texnik matn («TypeError», «error»,
 * «OK», Python stack qatori) kassirga hech narsa demaydi. Bu holatlar bo'lmasa —
 * `null` (chaqiruvchi xabar maydonlariga qaraydi).
 */
function transportText(status) {
	if (status === 0) return __("Server bilan aloqa yo'q — internetni tekshiring");
	if (status === 401 || status === 403) {
		return __("Sessiya tugagan yoki ruxsat yo'q — sahifani qayta yuklang (F5)");
	}
	if (status === 502 || status === 503 || status === 504) {
		return __("Server javob bermadi — bir necha soniyadan keyin qayta urinib ko'ring");
	}
	if (status >= 500) {
		return __(
			"Serverda xatolik yuz berdi — qayta urinib ko'ring, takrorlansa menejerga xabar bering"
		);
	}
	// So'rov rad etildi, lekin status 200: javob JSON emas — odatda sessiya tugab,
	// kirish sahifasi qaytgan.
	if (status === 200) {
		return __(
			"Server kutilmagan javob berdi — sessiya tugagan bo'lishi mumkin, sahifani qayta yuklang (F5)"
		);
	}
	return null;
}

/**
 * Serverdan kelgan xatoni O'QILADIGAN matnga aylantiradi.
 *
 * `frappe.call` promise'ni **jqXHR** bilan rad etadi, ya'ni xabar
 * `error.responseJSON._server_messages` ichida turadi — to'g'ridan-to'g'ri
 * `error._server_messages` da EMAS. Buni hisobga olmasak `String(error)`
 * ishlab ketadi va ekranda `[object Object]` chiqadi (ko'rilgan xato).
 */
export function errorText(error) {
	if (!error) return __("Noma'lum xato");
	if (typeof error === "string") return error;
	if (error.cancelled) return "";

	const body = error.responseJSON || error;

	// 1) `frappe.throw()` xabarlari — eng tushunarlisi shu.
	const raw = body._server_messages;
	if (raw) {
		try {
			const list = typeof raw === "string" ? JSON.parse(raw) : raw;
			const messages = (Array.isArray(list) ? list : [list])
				.map((m) => {
					try {
						const parsed = typeof m === "string" ? JSON.parse(m) : m;
						return parsed && parsed.message ? parsed.message : m;
					} catch (e) {
						return m;
					}
				})
				.filter((m) => typeof m === "string" && m.trim())
				.map(plainText)
				.filter(Boolean);
			if (messages.length) return messages.join("\n");
		} catch (e) {
			/* keyingi manbaga o'tamiz */
		}
	}

	// 2) Tarmoq / sessiya / server nosozligi. JavaScript xatosida (`Error`) `status` yo'q.
	const transport = typeof error.status === "number" ? transportText(error.status) : null;
	if (transport) return transport;

	// 3) Oddiy matnli maydonlar (`exc_type` va `statusText` texnik — ko'rsatilmaydi).
	for (const key of ["message", "exception"]) {
		const value = body[key];
		if (typeof value === "string" && value.trim()) {
			const text = plainText(value.replace(/^[\w.]+(Error|Exception):\s*/, ""));
			if (text) return text;
		}
	}

	return __("Noma'lum xato");
}
