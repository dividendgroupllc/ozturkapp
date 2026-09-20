/**
 * Kassa smenasi: ochish (bloklovchi ekran) va yopish (ikki bosqichli ko'r sanoq).
 */

import { ui } from "../kit/index.js";
import { esc, parseAmount, bindAmountInput } from "../util/format.js";

export class ShiftMethods {
	/**
	 * Kassani ochish — BLOKLOVCHI ekran (modal emas).
	 *
	 * Kassir smena boshida kassadagi NAQD PULNI sanab kiritadi. Bu summa
	 * smena oxiridagi solishtiruvning boshlang'ich nuqtasi:
	 *
	 *     kutilayotgan naqd = ochilish summasi + naqd sotuvlar
	 *
	 * Modal ATAYLAB ishlatilmadi — modalni yopib ishlashda davom etish
	 * mumkin bo'lardi. Bu qadam o'tkazib yuborilmasligi kerak, chunki
	 * smenasiz sotuv umuman mumkin emas: server tomonda
	 * `cashier_permissions.assert_shift_open()` hisob ochish, to'lov va
	 * ofitsant buyurtmasini rad etadi.
	 */
	renderShiftGate() {
		// FAQAT NAQD. Bank/karta bo'yicha boshlang'ich qoldiq yo'q — u pul
		// kassada emas, bankda turadi va kassir uni sanay olmaydi. Server
		// ham shuni majburlaydi (`_opening_balance_details`).
		// DIQQAT: bu ekran FAQAT kassani ocha oladigan foydalanuvchiga
		// chiziladi — qaror `boot()` da qabul qilinadi. Ocholmaydigan
		// foydalanuvchi bu yergacha yetib kelmaydi, u to'g'ridan-to'g'ri
		// kuzatuv rejimida ishlaydi.
		const modes = this.ctx.cash_modes || [];
		const $form = $(this.el.gate).off();

		if (!modes.length) {
			$form.html(
				`<div class="rc-pay__error">${esc(
					__("POS Profile'da naqd to'lov usuli sozlanmagan — kassani ochib bo'lmaydi.")
				)}</div>`
			);
			return;
		}

		$form.html(`
			<div class="rc-gate__cols">
				<div class="rc-gate__main">
					<table class="rc-shift-table">
					<thead>
						<tr>
							<th>${esc(__("Naqd pul"))}</th>
							<th class="rc-num">${esc(__("Kassadagi summa"))}</th>
						</tr>
					</thead>
					<tbody>
						${modes
							.map(
								(mode) => `<tr>
									<td>${esc(mode)}</td>
									<td class="rc-num">
										<input class="rc-shift-input" type="text" inputmode="${ui.virtualKeyboard ? "none" : "numeric"}"
											value="" placeholder="0" data-mode="${esc(mode)}"
											aria-label="${esc(mode)}">
									</td>
								</tr>`
							)
							.join("")}
					</tbody>
				</table>
				</div>
				<div class="rc-numpad-slot"></div>
			</div>
			<div class="rc-pay__error" role="alert"></div>
			<div class="rc-actions">
				<button class="rc-btn rc-btn--pay" data-action="open-shift">${esc(__("Kassani ochish"))}</button>
			</div>`);

		const $error = $form.find(".rc-pay__error");
		const $shiftInputs = $form.find(".rc-shift-input");
		bindAmountInput($shiftInputs);
		this.mountNumpad($form, $shiftInputs);

		$form.on("click", '[data-action="open-shift"]', async (e) => {
			const rows = $form
				.find(".rc-shift-input")
				.map((_, input) => ({
					mode_of_payment: input.dataset.mode,
					opening_amount: parseAmount(input.value),
				}))
				.get();

			$error.text("");
			try {
				this.busy(e.currentTarget, true);
				await this.call("ozturkapp.ozturkapp.api.cashier.open_shift", {
					balance_details: JSON.stringify(rows),
				});
				ui.toast(__("Kassa ochildi"));
				await this.boot();
			} catch (error) {
				$error.text(this.errorText(error));
				this.busy(e.currentTarget, false);
			}
		});
	}

	// ═══════════════════════════════════════════════════════════
	//  Kassa smenasi — ochish / yopish
	// ═══════════════════════════════════════════════════════════

	/**
	 * Kassani yopish — KETMA-KET IKKI MOS SANOQ (ko'r sanoq).
	 *
	 *     1-kiritish: naqd pulni sanab kiriting  ->  [Davom etish]
	 *                        |
	 *                60 soniya teskari sanoq (pulni QAYTA sanang)
	 *                        |
	 *     2-kiritish: yana bir marta kiriting    ->  [Kassani yopish]
	 *
	 * Har bir kiritish O'ZIDAN OLDINGISI bilan solishtiriladi — KETMA-KET
	 * ikkitasi mos kelsa kassa yopiladi.
	 *
	 * Mos kelmasa sanoq BOSHIDAN BOSHLANMAYDI. Ilgari hammasi bekor
	 * qilinardi va hozirgina kiritilgan TO'G'RI raqam ham yo'qolardi —
	 * kassir uni yana ikki marta kiritishga majbur bo'lardi:
	 *
	 *     200 000 -> 180 000 (bekor) -> 180 000 -> 180 000   = 4 kiritish
	 *
	 * Endi oxirgi kiritish yangi taqqoslash asosi bo'lib qoladi:
	 *
	 *     200 000 -> 180 000 -> 180 000 (mos — yopiladi)     = 3 kiritish
	 *
	 * Har kiritishdan oldingi 60 soniyalik sanoq esa SAQLANADI: usiz
	 * kassir sanamasdan bir xil raqamni ketma-ket yozib yuborardi va
	 * ko'r sanoq nazorat vazifasini bajarmay qolardi.
	 *
	 * Birinchi kiritilgan summa 2-bosqichda KO'RSATILMAYDI, aks holda
	 * kassir uni ko'chirib yozib qo'yardi va nazorat ma'nosini yo'qotardi.
	 *
	 * Kassirga umumiy savdo va kutilayotgan summa HECH QACHON
	 * ko'rsatilmaydi (ko'r sanoq) — server ularni yubormaydi ham.
	 */
	async closeShiftDialog() {
		let data;
		try {
			data = await this.call("ozturkapp.ozturkapp.api.cashier.get_shift_closing_data");
		} catch (error) {
			this.alertError(error);
			return;
		}

		if (cint(data.open_orders) > 0) {
			ui.alert({
				title: __("Yopilmagan buyurtmalar"),
				message: __("{0} ta to'lanmagan buyurtma bor — kassa yopilmaydi.", [
					cint(data.open_orders),
				]),
			});
			return;
		}

		this.showModal(__("Kassani yopish"), { size: ui.virtualKeyboard ? "lg" : "md" });
		this.renderCountStep(data, null);
	}

	/**
	 * Sanoq bosqichini chizadi.
	 *
	 * @param {object} data   server bergan yopish ma'lumoti
	 * @param {object|null} first  OLDINGI kiritishda olingan summalar —
	 *        yangi kiritish AYNAN shu bilan solishtiriladi (`null` bo'lsa
	 *        — eng birinchi kiritish, solishtiradigan narsa yo'q)
	 */
	renderCountStep(data, first) {
		const second = first !== null;

		// Bosqich holati YOPILMA (closure) da emas, INSTANSDA saqlanadi.
		// Sabab: `modalBody` doimiy element bo'lgani uchun eski ishlovchi
		// qandaydir yo'l bilan qolib ketsa, u o'zining ESKI `first`/`second`
		// qiymatlarini ko'rar va noto'g'ri tarmoqqa kirardi. Endi ishlovchi
		// bosqichni BOSILGAN TUGMADAN, birinchi sanoqni esa instansdan
		// o'qiydi — ya'ni qaysi ishlovchi ishga tushishidan qat'i nazar
		// natija bir xil bo'ladi.
		this.countFirst = first;

		// Nechanchi kiritish ekani — faqat sarlavha uchun. Mos kelmaganda
		// sanoq boshidan boshlanmagani sababli bu 2 dan oshishi mumkin, va
		// kassir nechanchi urinishda turganini ko'rib turadi.
		this.countAttempt = second ? cint(this.countAttempt) + 1 : 1;

		const modes = data.cash_modes || [];

		// MAYDON HAR DOIM BO'SH — `0` faqat PLACEHOLDER sifatida ko'rinadi.
		//
		// Ilgari savdosiz smenada maydonga tayyor `0` yozib qo'yilardi.
		// Kassir uning ustiga summa yozganda nol oldinda qolib ketardi va
		// kiritishga xalal berardi. Endi maydon bo'sh: kassir nima yozsa,
		// faqat o'shani ko'radi.
		//
		// Savdo BO'LGAN smenada bo'sh maydon ko'r sanoq uchun ham shart —
		// tayyor raqam kassirni sanamasdan tasdiqlashga undardi.
		//
		// SAVDO BO'LMAGAN SMENA: sanaydigan chek yo'q, shuning uchun bo'sh
		// maydon 0 deb qabul qilinadi (pastdagi `allowEmpty`) — aks holda
		// bo'sh smenani yopib bo'lmay qolardi.
		const allowEmpty = !cint(data.total_invoices);

		const stepText =
			this.countAttempt > 2
				? __("Qayta sanoq ({0})", [this.countAttempt])
				: second
				? __("Ikkinchi sanoq")
				: __("Birinchi sanoq");

		this.el.modalBody.innerHTML = `
			<div class="rc-count">
			<div class="rc-count__cols">
			<div class="rc-count__main">
			<div class="rc-count__head">
				<div class="rc-count__label">${esc(__("Cheklar soni"))}</div>
				<div class="rc-count__value">${cint(data.total_invoices)}</div>
			</div>

			<div class="rc-steps">
				<span class="rc-step ${second ? "rc-step--done" : "rc-step--active"}">1</span>
				<span class="rc-steps__line"></span>
				<span class="rc-step ${second ? "rc-step--active" : ""}">2</span>
				<span class="rc-steps__text">${esc(stepText)}</span>
			</div>

			<div class="rc-pay__label">${esc(
				second
					? __("Pulni qayta sanang va summani YANA kiriting")
					: __("Qo'lingizdagi naqd pulni sanang")
			)}</div>

			${modes
				.map(
					(mode) => `<div class="rc-count__row">
						<span>${esc(mode)}</span>
						<input class="rc-pay__input rc-count-input" type="text" inputmode="${
							ui.virtualKeyboard ? "none" : "numeric"
						}"
							value="" placeholder="0" data-mode="${esc(mode)}"
							aria-label="${esc(mode)}">
					</div>`
				)
				.join("")}

			${
				second
					? `<div class="rc-countdown">
						<div class="rc-countdown__ring"><span class="rc-countdown__num">--</span></div>
						<div class="rc-countdown__text">${esc(
							__("Pulni qayta sanang. Shu vaqt tugagach tasdiqlash ochiladi.")
						)}</div>
					</div>`
					: ""
			}

			<div class="rc-pay__error" role="alert"></div>
			</div>
			<div class="rc-numpad-slot"></div>
			</div>
			<div class="rc-actions">
				<button class="rc-btn ${
					second ? "rc-btn--danger" : "rc-btn--primary"
				}" data-action="submit" data-step="${second ? 2 : 1}" ${
			second ? "disabled" : ""
		}>${esc(second ? __("Kassani yopish") : __("Davom etish"))}</button>
			</div>
			</div>`;

		// XATO EDI: `modalBody` — DOIMIY element, `innerHTML` uni almashtirsa
		// ham unga OSILGAN delegatsiyalangan ishlovchilar QOLADI. Oyna ikki
		// bosqichli bo'lgani uchun ular to'planib, bitta bosishda IKKALASI
		// ham ishlab ketardi (2-bosqich tugmasi sanoqni qaytadan boshlardi).
		// Shuning uchun har safar eski ishlovchilar yechiladi.
		const $body = $(this.el.modalBody).off();
		const $error = $body.find(".rc-pay__error");
		const $submit = $body.find('[data-action="submit"]');

		// ── 2-bosqichda oyna QULFLANADI va sanoq ketadi ──────────────
		clearInterval(this.countdownTimer);
		this.countdownTimer = null;

		if (second) {
			this.setModalLocked(true);
			let left = cint(data.countdown_seconds) || 60;
			const $num = $body.find(".rc-countdown__num");

			const tick = () => {
				$num.text(left);
				if (left > 0) {
					left -= 1;
					return;
				}
				clearInterval(this.countdownTimer);
				this.countdownTimer = null;
				this.setModalLocked(false);
				$body.find(".rc-countdown").addClass("rc-countdown--done");
				$body.find(".rc-countdown__text").text(__("Endi tasdiqlashingiz mumkin."));
				$submit.prop("disabled", false);
			};

			tick();
			this.countdownTimer = setInterval(tick, 1000);
			this.timers.push(this.countdownTimer);
		} else {
			this.setModalLocked(false);
		}

		bindAmountInput($body.find(".rc-count-input"));
		this.mountNumpad($body, $body.find(".rc-count-input"));
		setTimeout(() => $body.find(".rc-count-input").first().trigger("focus"), 60);

		$body.on("click", '[data-action="submit"]', async (e) => {
			const counted = {};
			let missing = false;

			$body.find(".rc-count-input").each((_, input) => {
				if (String(input.value).trim() === "" && !allowEmpty) missing = true;
				counted[input.dataset.mode] = parseAmount(input.value);
			});

			if (missing) {
				// 0 — TO'LIQ HAQLI javob (masalan hamma to'lov karta bilan
				// bo'lgan). Xato matni buni aytib turishi kerak, aks holda
				// kassir 0 ni "qabul qilinmaydi" deb o'ylaydi.
				$error.text(
					__("Sanalgan naqd pul summasini kiriting. Kassa bo'sh bo'lsa — 0 yozing.")
				);
				return;
			}

			// Bosqichni BOSILGAN TUGMADAN o'qiymiz — yopilmadagi eskirgan
			// qiymatga tayanmaymiz.
			const isSecond = e.currentTarget.dataset.step === "2";
			const firstCount = this.countFirst;

			// ── 1-bosqich: eslab qolamiz va sanoqni boshlaymiz ───────
			if (!isSecond) {
				this.renderCountStep(data, counted);
				return;
			}

			// ── 2-bosqich: ikkala sanoq mos kelishi SHART ────────────
			if (!firstCount) {
				// Holat yo'qolgan — boshidan boshlaymiz (bu yerga tushmasligi kerak).
				this.renderCountStep(data, null);
				return;
			}

			const mismatch = Object.keys(counted).filter(
				(mode) => Math.abs(counted[mode] - flt(firstCount[mode])) > 0.004
			);

			if (mismatch.length) {
				ui.toast(__("Sanoqlar mos kelmadi — yana bir marta sanang"), {
					indicator: "orange",
					seconds: 7,
				});
				// SANOQ BOSHIDAN BOSHLANMAYDI.
				//
				// Ilgari bu yerda sanoq bo'sh asos bilan qaytadan chizilardi
				// va hozirgina kiritilgan TO'G'RI raqam ham bekor bo'lardi:
				// kassir uni yana IKKI marta kiritishga majbur edi. Endi
				// oxirgi kiritish yangi taqqoslash asosi bo'ladi — keyingi
				// kiritish shu bilan solishtiriladi, ya'ni ketma-ket ikkita
				// mos raqam yetarli.
				//
				// Ko'r sanoq buzilmaydi: maydonlar baribir BO'SH chiziladi va
				// keyingi kiritishdan oldin 60 soniyalik sanoq yana ketadi.
				this.renderCountStep(data, counted);
				$(this.el.modalBody)
					.find(".rc-pay__error")
					.text(
						__(
							"Oldingi sanoq bilan mos kelmadi. Pulni qayta sanang va summani kiriting."
						)
					);
				return;
			}

			$error.text("");
			try {
				this.busy(e.currentTarget, true);
				// So'rov ketayotganda oyna yopilmasin: xato ko'rinmay qolardi.
				this.setModalLocked(true);
				const result = await this.call("ozturkapp.ozturkapp.api.cashier.close_shift", {
					counted_cash: JSON.stringify(counted),
				});
				this.setModalLocked(false);
				this.closeModal();
				ui.toast(__("Kassa yopildi"));
				// Smena yopildi; Z-hisobotning chop etilishi esa alohida natija —
				// qog'oz chiqmasa kassir buni bilishi kerak.
				if ((this.ctx.features || {}).shift_reports && result) {
					if (result.z_report_queued === true) {
						ui.toast(__("Z-hisobot chop etilmoqda"));
					} else if (result.z_report_queued === false) {
						ui.toast(__("Z-hisobot printerga yuborilmadi — menejerga xabar bering"), {
							indicator: "orange",
							seconds: 10,
						});
					}
				}
				await this.boot();
			} catch (error) {
				this.setModalLocked(false);
				$error.text(this.errorText(error));
				this.busy(e.currentTarget, false);
			}
		});
	}
}
