/**
 * To'lov oynasi (TZ §22) — ikki ustunli, 768px balandlikka sig'adi.
 *
 *   chap:  to'lanadigan summa, USULLAR RO'YXATI (chapda usul nomi, o'ngda summa),
 *          tez summalar, holat (yetishmaydi / to'liq / qaytim)
 *   o'ng:  raqam paneli (ekran klaviaturasi yoqiq bo'lsa)
 *   past:  «To'lovni tasdiqlash» — har doim ko'rinadi
 *
 * USULLAR RO'YXATI
 * ================
 * POS Profile'dagi HAR BIR to'lov usuli o'z qatori bilan chiqadi. Kassir chek
 * qaysi usul bilan to'langan bo'lsa, o'sha qatorga summani kiritib «To'lovni
 * tasdiqlash» ni bosadi. Usul nomini bosish — qoldiq summani shu usulga yozadi.
 *
 * Bir nechta usulga summa kiritish (naqd + karta) POS Profile'dagi
 * `split_payment` bayrog'i bilan yoqiladi (server ham shuni majburlaydi,
 * `billing._validate_payments`). O'chiq bo'lsa, bir qatorga yozilganda
 * qolganlari bo'shatiladi.
 *
 * FUNKSIYA MODULLARI UCHUN
 * ========================
 * Choychaqa kabi qo'shimchalar oynani o'zgartirmaydi: ular
 * `payment.beforeAmount`, `payment.afterAmount`, `payment.footer` slotlariga
 * bo'lim qo'shadi va `PaymentSession` orqali summa/so'rov parametrlariga
 * ta'sir qiladi (qarang: README).
 */

import { slots } from "../core/slots.js";
import { ui } from "../kit/index.js";
import { bindAmountInput, esc, groupAmount, parseAmount, quickAmounts } from "../util/format.js";
import { dueOf } from "./panel.js";

/** Summalar tiyin aniqligida butun songa o'tkaziladi: `0.1 + 0.2` xatosi bo'lmasin. */
const cents = (value) => Math.round(flt(value) * 100);

/**
 * Ochiq to'lov oynasining holati. Funksiya modullari shu obyekt bilan
 * ishlaydi — DOM bilan emas.
 */
class PaymentSession {
	constructor(screen, detail, methods) {
		this.screen = screen;
		this.detail = detail;
		this.bill = detail.bill;
		this.methods = methods;

		this.due = dueOf(detail.bill);
		// Bir nechta usulga summa yozish — POS Profile bayrog'i bilan (server ham tekshiradi).
		this.multi = !!(screen.ctx.features || {}).split_payment;
		// Naqd usullar serverdan (`Mode of Payment.type == "Cash"`): nom bo'yicha taxmin qilinmaydi.
		this.cashModes = new Set(
			screen.ctx.cash_modes ||
				methods
					.filter((method) => (method.type || "").toLowerCase() === "cash")
					.map((method) => method.mode_of_payment)
		);
		// Oxirgi tegilgan usul: tez summalar shu qatorga yoziladi.
		this.active = (methods.find((m) => m.default) || methods[0]).mode_of_payment;
		this.args = {};
		this.listeners = [];
		this.$body = null;
		this.numpad = null; // ekran raqam paneli (klaviatura yoqilmagan bo'lsa — null)
	}

	/** Har bir usulning kiritish maydoni (`data-mode` — usul nomi). */
	inputs() {
		return this.$body.find(".rc-pay__input").toArray();
	}

	/** Summa kiritilgan usullar: `[{mode_of_payment, amount}]`. */
	entries() {
		return this.inputs()
			.map((input) => ({
				mode_of_payment: input.dataset.mode,
				amount: parseAmount(input.value),
			}))
			.filter((entry) => entry.amount > 0);
	}

	/** Qabul qilingan jami summa (barcha usullar yig'indisi). */
	get amount() {
		return this.entries().reduce((sum, entry) => sum + entry.amount, 0);
	}

	/** O'zgarishga obuna: summa yoki to'lanadigan summa o'zgarganda. */
	onChange(listener) {
		this.listeners.push(listener);
	}

	changed() {
		this.recalc();
		this.listeners.forEach((listener) => listener(this));
	}

	/**
	 * To'lanadigan summani o'zgartiradi (masalan choychaqa qo'shildi).
	 * Bu FAQAT ko'rsatish: haqiqiy summani server hisoblaydi va tekshiradi.
	 *
	 * To'liq summa kiritilgan usul yangi summaga ergashadi; kassir qo'lda
	 * boshqa summa yozgan bo'lsa — tegilmaydi.
	 */
	setDue(value) {
		const previous = cents(this.due);
		this.due = flt(value);

		if (previous > 0) {
			this.inputs().forEach((input) => {
				if (cents(parseAmount(input.value)) === previous) {
					input.value = groupAmount(this.due);
				}
			});
		}

		this.$body.find(".rc-pay__due-value").text(this.screen.money(this.due));
		this.renderQuick();
		this.changed();
	}

	/** `submit_payment` ga qo'shimcha parametr (masalan `tip`); `undefined` — olib tashlash. */
	setArg(name, value) {
		if (value === undefined) delete this.args[name];
		else this.args[name] = value;
	}

	/** `submit_payment` ga ketadigan qatorlar: faqat summa kiritilgan usullar. */
	payments() {
		return this.entries();
	}

	/**
	 * Bitta usulga summa yozadi. Bir nechta usul yoqilmagan bo'lsa qolgan
	 * usullar bo'shatiladi: chek faqat bitta usul bilan to'lanadi.
	 */
	setAmount(mode, value) {
		this.inputs().forEach((input) => {
			if (input.dataset.mode === mode) input.value = value > 0 ? groupAmount(value) : "";
			else if (!this.multi) input.value = "";
		});
		this.active = mode;
		this.changed();
	}

	/** Kassir maydonga yozdi (yoki raqam paneli yozdi). */
	typed(input) {
		this.active = input.dataset.mode;
		if (!this.multi && parseAmount(input.value) > 0) {
			this.inputs().forEach((other) => {
				if (other !== input) other.value = "";
			});
		}
		this.changed();
	}

	/** Boshqa usullar to'lamagan qoldiq — shu usulga sig'adigan summa. */
	restFor(mode) {
		const others = this.multi
			? this.entries()
					.filter((entry) => entry.mode_of_payment !== mode)
					.reduce((sum, entry) => sum + cents(entry.amount), 0)
			: 0;
		return Math.max(cents(this.due) - others, 0) / 100;
	}

	setError(text) {
		this.$body.find(".rc-pay__error").text(text || "");
	}

	/**
	 * Ekran raqam paneliga yangi kiritish maydonini ulaydi (dinamik qatorlar,
	 * tahrirlagich): ikkinchi panel qo'yish shart emas. Panel YO'Q bo'lsa
	 * (ekran klaviaturasi o'chiq) hech narsa qilmaydi — maydon brauzerning
	 * o'z klaviaturasi bilan ishlaydi.
	 *
	 * @returns {boolean}  panel bor edimi
	 */
	bindNumpad(input) {
		if (!this.numpad) return false;
		this.numpad.bind(input);
		return true;
	}

	/**
	 * «To'lovni tasdiqlash» ni yoqadi/o'chiradi. Holat `screen.busy(false)` dan
	 * OMON QOLADI: so'rov xato bilan tugasa yadro tugmani qayta yoqmaydi — shart
	 * bajarilmagan bo'lsa u o'chiq turaveradi. So'rov ketayotgan (band) tugmaga
	 * tegilmaydi.
	 */
	setConfirmEnabled(enabled) {
		const button = this.$body.find('[data-action="confirm"]')[0];
		if (!button) return;

		if (enabled) delete button.dataset.locked;
		else button.dataset.locked = "1";
		if (!button.classList.contains("rc-btn--busy")) button.disabled = !enabled;
	}

	renderQuick() {
		this.$body.find(".rc-pay__quick").html(
			quickAmounts(this.due)
				.map(
					(value) =>
						`<button class="rc-quick" type="button" data-amount="${value}">${esc(
							this.screen.money(value)
						)}</button>`
				)
				.join("")
		);
	}

	/**
	 * Server qoidasining ishora shakli (`billing._validate_payments`):
	 *   yetishmasa            — davom etib bo'lmaydi;
	 *   ortiqcha, faqat naqd  — qaytim;
	 *   ortiqcha, naqd emas   — mumkin emas.
	 * Bu kassirga ISHORA; qat'iy tekshiruv serverda (TZ §17).
	 */
	plan() {
		const due = cents(this.due);
		let total = 0;
		let cash = 0;
		this.entries().forEach((entry) => {
			const value = cents(entry.amount);
			total += value;
			if (this.cashModes.has(entry.mode_of_payment)) cash += value;
		});

		const rest = due - total;
		const nonCash = total - cash;
		const money = (value) => this.screen.money(value / 100);

		if (rest > 0) return { ok: false, label: __("Yetishmaydi"), value: money(rest) };
		if (rest === 0) return { ok: true, label: __("To'liq ✓"), value: "" };

		if (nonCash > due) {
			return {
				ok: false,
				value: "",
				label: __(
					"Naqd bo'lmagan usullar summasi to'lanadigan summadan oshdi — faqat naqd ortiqcha qabul qilinadi"
				),
			};
		}
		if (cash && nonCash >= due) {
			return {
				ok: false,
				value: "",
				label: __(
					"Naqd bo'lmagan usullar summani to'liq qoplaydi — naqd summasini olib tashlang"
				),
			};
		}
		return { ok: true, label: __("Qaytim"), value: money(-rest) };
	}

	/** Holat qatori (yetishmaydi / to'liq / qaytim) va «To'lovni tasdiqlash» holati. */
	recalc() {
		const plan = this.plan();
		const $status = this.$body.find(".rc-pay__change");

		$status.toggleClass("rc-pay__change--short", !plan.ok);
		$status.find(".rc-pay__change-label").text(plan.label);
		$status.find(".rc-pay__change-value").text(plan.value);
		this.setConfirmEnabled(plan.ok);
	}
}

export class PaymentMethods {
	openPaymentModal(detail) {
		// So'rov ketayotgan (qulflangan) oyna ustidan ikkinchi to'lov oynasi ochilmaydi.
		if (this.modalLocked) return;

		const bill = detail.bill;
		const methods = this.ctx.payment_methods || [];
		if (!methods.length) {
			ui.alert({
				title: __("To'lov"),
				message: __("POS Profile'da to'lov usuli sozlanmagan"),
			});
			return;
		}

		const session = new PaymentSession(this, detail, methods);

		this.el.modalBody.innerHTML = `
			<div class="rc-pay">
				<div class="rc-pay__due">
					<div class="rc-pay__due-label">${esc(__("To'lanadi"))}</div>
					<div class="rc-pay__due-value">${esc(this.money(session.due))}</div>
					<div class="rc-pay__breakdown">
						${esc(detail.table || bill.invoice)} ·
						${esc(__("Oraliq"))} ${esc(this.money(bill.subtotal))}
						${
							bill.service_charge
								? ` + ${esc(bill.service_charge.description)} ${esc(
										this.money(bill.service_charge.amount)
								  )}`
								: ""
						}
					</div>
				</div>
				<div class="rc-pay__cols">
					<div class="rc-pay__main">
							<div class="rc-pay__slot" data-slot="beforeAmount"></div>

							<div class="rc-pay__label">
								<span>${esc(__("To'lov usullari"))}</span>
								<span class="rc-pay__hint">${esc(__("nomini bosing — qoldiq summa"))}</span>
							</div>
							<div class="rc-pay__rows">
								${methods
									.map(
										(m) => `<div class="rc-pay__row">
											<button class="rc-pay__name" type="button"
												data-mode="${esc(m.mode_of_payment)}">${esc(m.mode_of_payment)}</button>
											<input class="rc-pay__input" type="text" autocomplete="off"
												data-mode="${esc(m.mode_of_payment)}"
												inputmode="${ui.virtualKeyboard ? "none" : "numeric"}"
												placeholder="0"
												aria-label="${esc(m.mode_of_payment)}">
										</div>`
									)
									.join("")}
							</div>
							<div class="rc-pay__quick"></div>

							<div class="rc-pay__slot" data-slot="afterAmount"></div>

							<div class="rc-pay__change" role="status">
								<span class="rc-pay__change-label"></span>
								<span class="rc-pay__change-value"></span>
							</div>
						</div>
					<div class="rc-pay__side rc-numpad-slot"></div>
				</div>

				<div class="rc-pay__foot">
					<div class="rc-pay__error" role="alert"></div>
					<div class="rc-pay__slot rc-pay__slot--footer" data-slot="footer"></div>
					<button class="rc-btn rc-btn--pay" data-action="confirm">${esc(__("To'lovni tasdiqlash"))}</button>
				</div>
			</div>`;

		// XATO EDI: `modalBody` — DOIMIY element, `innerHTML` uni almashtirsa
		// ham unga OSILGAN delegatsiyalangan ishlovchilar QOLADI. Oyna ikki
		// bosqichli bo'lgani uchun ular to'planib, bitta bosishda IKKALASI
		// ham ishlab ketardi (2-bosqich tugmasi sanoqni qaytadan boshlardi).
		// Shuning uchun har safar eski ishlovchilar yechiladi.
		const $body = $(this.el.modalBody).off();
		const $inputs = $body.find(".rc-pay__input");
		session.$body = $body;

		// Tez tanlash tugmalari — kassirning yozishini kamaytiradi.
		session.renderQuick();

		// Usul nomini bosish: qoldiq summa shu usulga yoziladi.
		$body.on("click", ".rc-pay__name", (e) => {
			const mode = e.currentTarget.dataset.mode;
			session.setAmount(mode, session.restFor(mode));
			$inputs.filter((_, input) => input.dataset.mode === mode).trigger("focus");
		});
		$body.on("click", ".rc-quick", (e) => {
			session.setAmount(session.active, flt(e.currentTarget.dataset.amount));
		});
		bindAmountInput($inputs, (input) => session.typed(input));
		$inputs.on("focus", (e) => (session.active = e.currentTarget.dataset.mode));
		session.numpad = this.mountNumpad($body, $inputs);

		this.mountPaymentSections($body, session);

		$body.on("click", '[data-action="confirm"]', async (e) => {
			const button = e.currentTarget;

			session.setError("");
			try {
				this.busy(button, true);
				// So'rov ketayotganda oyna YOPILMAYDI (×, Esc, orqa fon): yopilsa xato
				// ko'rinmay qolardi, kassir esa oynani qayta ochib ikkinchi marta
				// to'lashi mumkin edi.
				this.setModalLocked(true);
				// To'lov menejer tasdig'ini HECH QACHON talab qilmaydi (`submit_payment`
				// `approval` qabul qilmaydi): chegirma/qaytarish o'z oqimida so'raydi.
				const result = await this.call("ozturkapp.ozturkapp.api.billing.submit_payment", {
					invoice: bill.invoice,
					payments: JSON.stringify(session.payments()),
					...session.args,
				});
				this.setModalLocked(false);
				this.closeModal();
				ui.toast(__("To'lov qabul qilindi — {0}", [result.invoice]));
				if (result.change_amount) {
					ui.alert({
						title: __("Qaytim"),
						message: this.money(result.change_amount),
						big: true,
					});
				}
				await this.refreshAll();
			} catch (error) {
				// Xato bo'lsa oyna YOPILMAYDI va stol band qoladi (TZ §23).
				this.setModalLocked(false);
				session.setError(this.errorText(error));
			} finally {
				this.busy(button, false);
			}
		});

		session.recalc();
		this.showModal(__("To'lov"), { size: ui.virtualKeyboard ? "lg" : "md" });
		setTimeout(
			() => $inputs.filter((_, input) => input.dataset.mode === session.active).trigger("focus"),
			50
		);
	}

	/**
	 * Funksiya modullarining bo'limlarini chizadi (qarang `payment.*` slotlari).
	 *
	 * Bitta modulning `render` xatosi butun to'lov oynasini ochilmay qoldirmasligi
	 * kerak: to'lov qo'shimcha funksiya tufayli to'xtab qolmaydi — xatoli bo'lim
	 * tushib qoladi va sabab konsolga yoziladi.
	 */
	mountPaymentSections($body, session) {
		const places = {
			"payment.beforeAmount": "beforeAmount",
			"payment.afterAmount": "afterAmount",
			"payment.footer": "footer",
		};

		Object.entries(places).forEach(([slot, place]) => {
			const host = $body.find(`[data-slot="${place}"]`)[0];
			slots.visible(slot, session).forEach((item) => {
				let content;
				try {
					content = item.render(session);
				} catch (error) {
					console.error(`${slot}: '${item.id}' bo'limi chizilmadi`, error);
					return;
				}

				if (typeof content === "string") host.insertAdjacentHTML("beforeend", content);
				else if (content) host.appendChild(content);
			});
		});
	}
}
