/**
 * Menejer tasdig'i (PIN) — kassir o'zi qila olmaydigan amallar uchun.
 *
 * OQIM (server tomoni: `utils/manager_approval.py`)
 * =================================================
 *   1. Amal tasdiqsiz yuboriladi;
 *   2. server `ApprovalRequired` tashlaydi (`exc_type` JSON'da);
 *   3. shu yerda menejer ismi tanlanadi va PIN kiritiladi;
 *   4. AYNI amal `approval = {user, pin}` bilan qayta yuboriladi.
 *
 * PIN bu yerda TEKSHIRILMAYDI — faqat yig'ib serverga uzatiladi. U hech
 * qayerda saqlanmaydi va jurnalga yozilmaydi.
 *
 * SERVER TALABI
 * =============
 * `ApprovalRequired` HTTP 403 bilan qaytadi. Frappe 403 da xabar
 * (`_server_messages`) bo'lmasa o'zining «Not permitted» oynasini `silent`
 * bo'lsa ham ochib yuboradi (`request.js`, 403 ishlovchisi). Shuning uchun
 * server uni `frappe.throw(msg, exc=ApprovalRequired)` bilan tashlashi
 * SHART — oddiy `raise ApprovalRequired(msg)` da xabar JSON'ga tushmaydi.
 */

import { ApprovalCancelled, call, errorText, isApprovalRequired } from "../core/api.js";
import { esc } from "../util/format.js";
import { Dialog } from "./dialog.js";
import { toast } from "./controls.js";

const APPROVERS_TTL_MS = 60 * 1000;
const MAX_PIN_LENGTH = 8;
const MIN_PIN_LENGTH = 4;

let cache = null;

/** Menejerlar ro'yxati — bir daqiqa eslab qolinadi (har PIN oynasida qayta so'ralmasin). */
async function loadApprovers() {
	if (cache && Date.now() - cache.at < APPROVERS_TTL_MS) return cache.data;

	const data = await call("ozturkapp.ozturkapp.api.approval.get_approvers");
	cache = { at: Date.now(), data };
	return data;
}

/** PIN klaviaturasi: nuqtalar + raqamlar. Jismoniy klaviatura ham ishlaydi. */
function pinBody(state, hint) {
	const wrap = document.createElement("div");
	wrap.className = "rc-pin";
	wrap.innerHTML = `
		<div class="rc-pin__who">${esc(state.approver.full_name)}</div>
		<div class="rc-pin__dots" aria-live="polite"></div>
		<div class="rc-pin__hint">${esc(hint || "")}</div>
		<div class="rc-numpad rc-numpad--pin">
			${["1", "2", "3", "4", "5", "6", "7", "8", "9", "⌫", "0", "✓"]
				.map(
					(key) =>
						`<button type="button" class="rc-numpad__key${
							key === "⌫" ? " rc-numpad__key--del" : ""
						}${key === "✓" ? " rc-numpad__key--ok" : ""}" data-key="${esc(key)}">${esc(
							key
						)}</button>`
				)
				.join("")}
		</div>`;

	const dots = wrap.querySelector(".rc-pin__dots");
	const paint = () => {
		dots.innerHTML = Array.from({ length: Math.max(MIN_PIN_LENGTH, state.pin.length) })
			.map(
				(_, i) =>
					`<i class="rc-pin__dot${i < state.pin.length ? " rc-pin__dot--on" : ""}"></i>`
			)
			.join("");
	};
	paint();

	wrap.addEventListener("pointerdown", (event) => event.preventDefault());
	wrap.addEventListener("click", (event) => {
		const button = event.target.closest(".rc-numpad__key");
		if (!button) return;

		const key = button.dataset.key;
		if (key === "⌫") state.pin = state.pin.slice(0, -1);
		else if (key === "✓") return state.submit();
		else if (state.pin.length < MAX_PIN_LENGTH) state.pin += key;
		paint();
	});

	state.paint = paint;
	return wrap;
}

/**
 * Menejer tasdig'ini so'raydi.
 *
 * Joriy foydalanuvchining o'zi menejer bo'lsa (`self_approves`) HECH NARSA
 * so'ralmaydi — server unga PIN talab qilmaydi.
 *
 * @param {string} actionLabel  nima tasdiqlanmoqda («Chegirma 25%»)
 * @param {object} [options]
 * @param {string} [options.error]  oldingi urinishdagi xato (masalan «PIN noto'g'ri»)
 * @returns {Promise<{user: string, pin: string}|null>}  `null` — rad etildi
 */
export async function requestApproval(actionLabel, { error = "" } = {}) {
	let info;
	try {
		info = await loadApprovers();
	} catch (e) {
		toast(errorText(e), { indicator: "red", seconds: 7 });
		return null;
	}

	if (info.self_approves) return { user: frappe.session.user, pin: "" };

	const approvers = info.approvers || [];
	if (!approvers.length) {
		toast(__("Menejer PIN-kodi sozlanmagan — tasdiqlash mumkin emas."), {
			indicator: "red",
			seconds: 8,
		});
		return null;
	}

	return new Promise((resolve) => {
		const state = { approver: null, pin: "", paint: null, submit: null };
		let dialog = null;

		const finish = (value) => {
			resolve(value);
			dialog.close(null);
		};

		const showPin = (approver) => {
			state.approver = approver;
			state.pin = "";
			state.submit = () => {
				if (state.pin.length < MIN_PIN_LENGTH) return;
				finish({ user: approver.user, pin: state.pin });
			};

			dialog.bodyEl.innerHTML = "";
			dialog.bodyEl.appendChild(pinBody(state, error));
			dialog.footEl.hidden = approvers.length < 2;
			dialog.focus();
		};

		const showTiles = () => {
			dialog.footEl.hidden = true;
			dialog.bodyEl.innerHTML = `<p class="rc-dialog__note">${esc(
				__("Tasdiqlaydigan menejerni tanlang")
			)}</p>
				<div class="rc-approvers">${approvers
					.map(
						(a) => `<button type="button" class="rc-approver" data-user="${esc(
							a.user
						)}">
							<span class="rc-approver__avatar" aria-hidden="true">${esc(
								(a.full_name || a.user).charAt(0).toUpperCase()
							)}</span>
							<span class="rc-approver__name">${esc(a.full_name || a.user)}</span>
						</button>`
					)
					.join("")}</div>`;
		};

		dialog = new Dialog({
			title: __("Menejer tasdig'i"),
			subtitle: actionLabel,
			size: "sm",
			actions: [
				{
					id: "back",
					label: __("Boshqa menejer"),
					onClick: () => showTiles(),
				},
			],
		}).open();

		dialog.bodyEl.addEventListener("click", (event) => {
			const tile = event.target.closest(".rc-approver");
			if (tile) showPin(approvers.find((a) => a.user === tile.dataset.user));
		});

		// Jismoniy klaviatura: raqamlar, Backspace, Enter.
		dialog.overlay.addEventListener("keydown", (event) => {
			if (!state.approver || dialog.closed) return;
			if (/^\d$/.test(event.key) && state.pin.length < MAX_PIN_LENGTH) state.pin += event.key;
			else if (event.key === "Backspace") state.pin = state.pin.slice(0, -1);
			else if (event.key === "Enter") return state.submit();
			else return;
			state.paint();
		});

		// Oyna tashqaridan yopilsa (× yoki Esc) — rad etish.
		dialog.result.then(() => resolve(null));

		if (approvers.length === 1) showPin(approvers[0]);
		else showTiles();
	});
}

/**
 * `fn(approval)` ni bajaradi va kerak bo'lsa menejer tasdig'ini so'raydi.
 *
 * Avval `fn(null)` — tasdiqsiz. Server `ApprovalRequired` tashlasa PIN oynasi
 * ochiladi va `fn({user, pin})` qayta chaqiriladi. PIN noto'g'ri bo'lsa server
 * yana `ApprovalRequired` beradi — oyna xato bilan qayta ochiladi. Foydalanuvchi
 * bekor qilsa `ApprovalCancelled` tashlanadi (xato sifatida ko'rsatilmaydi).
 *
 * `fn` QAYTA chaqirilishi mumkin — shuning uchun u bir xil argumentlar bilan
 * xavfsiz takrorlanadigan so'rov bo'lishi kerak (server ham shunday: PIN va
 * amal bitta so'rovda keladi).
 *
 * @param {Function} fn  `async (approval|null) => result`
 * @param {object} [options]
 * @param {string} [options.action]  PIN oynasida ko'rsatiladigan amal nomi
 */
export async function withApproval(fn, { action = "" } = {}) {
	let approval = null;
	let hint = "";

	for (;;) {
		try {
			return await fn(approval);
		} catch (error) {
			if (!isApprovalRequired(error)) throw error;

			// Menejerning o'zi (`self_approves`) tasdiqlagan, server esa baribir tasdiq
			// so'rayapti (rol o'zgargan yoki keshdagi ma'lumot eskirgan): qayta yuborish
			// serverni bir soniyada yuzlab so'rov bilan bosardi. To'xtaymiz va serverning
			// matnini ko'rsatamiz; keyingi urinishda ro'yxat qayta o'qiladi.
			if (approval && approval.pin === "") {
				cache = null;
				throw error;
			}

			// Xabar serverdan (`frappe.throw`) keldimi — texnik matn PIN oynasiga chiqmasin.
			const body = error.responseJSON || error;
			const text = body._server_messages ? errorText(error) : "";
			hint = approval ? text || __("PIN noto'g'ri") : "";

			approval = await requestApproval(action, { error: hint });
			if (!approval) throw new ApprovalCancelled();
		}
	}
}
