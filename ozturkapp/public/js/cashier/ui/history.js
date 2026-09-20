/**
 * Kassa tarixi — to'langan cheklar (FAQAT O'QISH).
 *
 * Qator va tafsilot amallari `history.rowActions` / `history.detailActions`
 * slotlaridan chiziladi (asosiy «Chop etish» pastda shu yo'l bilan qo'shilgan;
 * funksiya modullari — masalan qaytarish — o'zlariniki qo'shadi).
 */

import { slots } from "../core/slots.js";
import { esc, fmtQty, hhmm, orderTypeLabel, taxLabel } from "../util/format.js";

/**
 * Qaytarish chekining qizil belgisi. Qaytarish cheki tarixda manfiy summa bilan
 * oddiy cheklar orasida turadi — belgisiz uni sotuvdan ajratish qiyin. Belgi
 * FAQAT rangdan iborat emas: matn ham bor (TZ §20).
 */
const returnBadge = () => `<span class="rc-return-badge">${esc(__("QAYTARISH"))}</span>`;

export class HistoryMethods {
	// ═══════════════════════════════════════════════════════════
	//  Kassa tarixi — to'langan cheklar (FAQAT O'QISH)
	// ═══════════════════════════════════════════════════════════

	/**
	 * Kassa tarixi — tanlangan SMENADA (kassa ochilgandan yopilgunicha) to'langan
	 * cheklar ro'yxati. Kassir FAQAT hozirgi ochiq smenani ko'radi (tanlagich
	 * chizilmaydi — server oldingi smenalarni baribir bermaydi); menejerga
	 * yopiq smenalar ham ro'yxatdan tanlanadi, standart — hozirgi smena.
	 *
	 * Bu oyna HECH NARSA YARATMAYDI yoki O'ZGARTIRMAYDI — faqat
	 * `order.get_paid_orders()` orqali mavjud cheklarni o'qib ko'rsatadi
	 * va kerak bo'lsa qayta chop etishga imkon beradi.
	 */
	async openHistoryModal() {
		this.el.modalBody.innerHTML = `
			<div class="rc-history__filters">
				<div class="rc-history__shiftnote"></div>
				<select class="rc-history__select rc-history__select--shift" data-field="shift" aria-label="${esc(
					__("Smena")
				)}"></select>
				<select class="rc-history__select" data-field="table" aria-label="${esc(__("Stol"))}">
					<option value="">${esc(__("Barcha stollar"))}</option>
				</select>
				<select class="rc-history__select" data-field="waiter" aria-label="${esc(__("Ofitsiant"))}">
					<option value="">${esc(__("Barcha ofitsiantlar"))}</option>
				</select>
			</div>
			<div class="rc-history__summary"></div>
			<div class="rc-history__list"></div>
			<div class="rc-history__detail"></div>`;

		const $body = $(this.el.modalBody).off();
		const $list = $body.find(".rc-history__list");
		const $summary = $body.find(".rc-history__summary");
		const $filters = $body.find(".rc-history__filters");
		// `hidden` atributi ISHLATILMAYDI: Desk CSS'ida `[hidden]{display:none
		// !important}` bor — bu jQuery'ning `.show()`sini yutib yuboradi va
		// tafsilot bo'limi to'ldirilgan holda ham ko'rinmay qolardi.
		const $detail = $body.find(".rc-history__detail").hide();

		// Smena yoki filtr tez almashtirilsa eski (sekin) javob yangisining ustiga yozilmasin.
		let loadRequest = 0;
		let detailRequest = 0;

		const load = async () => {
			const mine = (loadRequest += 1);
			$list.html(`<div class="rc-hint">${esc(__("Yuklanmoqda…"))}</div>`);
			$summary.empty();

			const shift = $body.find('[data-field="shift"]').val();
			const table = $body.find('[data-field="table"]').val();
			const waiter = $body.find('[data-field="waiter"]').val();

			try {
				const rows = await this.call("ozturkapp.ozturkapp.api.order.get_paid_orders", {
					shift,
					table: table || undefined,
					waiter: waiter || undefined,
				});
				if (mine !== loadRequest) return;
				this.renderHistoryList(rows, $list, $summary);
			} catch (error) {
				if (mine !== loadRequest) return;
				$list.html(`<div class="rc-hint">${esc(this.errorText(error))}</div>`);
			}
		};

		const showList = () => {
			detailRequest += 1;
			$detail.hide().empty();
			$filters.show();
			$summary.show();
			$list.show();
		};

		const showDetail = async (invoice) => {
			const mine = (detailRequest += 1);
			$filters.hide();
			$summary.hide();
			$list.hide();
			$detail.html(`<div class="rc-hint">${esc(__("Yuklanmoqda…"))}</div>`).show();

			try {
				const bill = await this.call(
					"ozturkapp.ozturkapp.api.order.get_order_bill_preview",
					{ order: invoice }
				);
				if (mine !== detailRequest) return;
				this.renderHistoryDetail(bill, $detail);
			} catch (error) {
				if (mine !== detailRequest) return;
				$detail.html(
					`<button class="rc-btn" type="button" data-action="history-back">← ${esc(
						__("Orqaga")
					)}</button><div class="rc-hint">${esc(this.errorText(error))}</div>`
				);
			}
		};

		$body.on("change", ".rc-history__select", load);

		// Qator ichidagi tugma qatorning o'zini (tafsilotni) ochib yubormasin.
		$body.on("click", "[data-history-action]", (e) => {
			e.stopPropagation();
			const button = e.currentTarget;
			const { slot, historyAction, invoice } = button.dataset;

			const item = slots.items(slot).find((i) => i.id === historyAction);
			const subject =
				slot === "history.rowActions" ? this.historyRows.get(invoice) : this.historyBill;
			if (item && subject) item.onClick(this, subject, button);
		});

		$body.on("click", ".rc-history__row", (e) => {
			showDetail(e.currentTarget.dataset.invoice);
		});

		$body.on("click", '[data-action="history-back"]', showList);

		// Qaytarish chekidan asl chekning tafsilotiga o'tish («Orqaga» ro'yxatga qaytaradi).
		$body.on("click", '[data-action="history-open"]', (e) => {
			showDetail(e.currentTarget.dataset.invoice);
		});

		this.showModal(__("Kassa tarixi"), { size: "xl" });

		let options;
		try {
			options = await this.call("ozturkapp.ozturkapp.api.order.get_paid_order_filter_options");
		} catch (error) {
			// Smenalar ro'yxatisiz qaysi oyna ko'rsatilishini bilib bo'lmaydi.
			$list.html(`<div class="rc-hint">${esc(this.errorText(error))}</div>`);
			return;
		}

		const shifts = options.shifts || [];
		const $shift = $body.find('[data-field="shift"]');
		const $shiftNote = $body.find(".rc-history__shiftnote");
		if (!shifts.length) {
			$shift.hide();
			$shiftNote.hide();
			$list.html(`<div class="rc-hint">${esc(__("Ochiq kassa smenasi yo'q."))}</div>`);
			return;
		}

		// Standart — hozirgi ochiq smena; u bo'lmasa (kassa yopiq) — oxirgi smena.
		const current = (this.ctx.shift || {}).name;
		const preferred =
			shifts.find((s) => s.name === current) || shifts.find((s) => s.open) || shifts[0];
		$shift.html(
			shifts
				.map(
					(s) =>
						`<option value="${esc(s.name)}"${s.name === preferred.name ? " selected" : ""}>${esc(
							this.shiftLabel(s)
						)}</option>`
				)
				.join("")
		);
		// Tanlanadigan narsa yo'q (kassir): tanlagich o'rniga hozirgi smena yozuvi.
		if (shifts.length === 1) {
			$shift.hide();
			$shiftNote.text(this.shiftLabel(preferred));
		} else {
			$shiftNote.hide();
		}

		const $table = $body.find('[data-field="table"]');
		(options.tables || []).forEach((t) =>
			$table.append(`<option value="${esc(t)}">${esc(t)}</option>`)
		);
		const $waiter = $body.find('[data-field="waiter"]');
		(options.waiters || []).forEach((w) =>
			$waiter.append(`<option value="${esc(w.value)}">${esc(w.label)}</option>`)
		);

		load();
	}

	/**
	 * Smena tanlagichidagi yozuv: `Ochiq · 2026-08-25 01:40 → hozir · Kassa`.
	 * Sana ham ko'rsatiladi — smena yarim tundan o'tib yoki bir necha kunga
	 * cho'zilishi mumkin, faqat soat noaniq bo'lardi.
	 */
	shiftLabel(shift) {
		const stamp = (value) => `${String(value).slice(0, 10)} ${hhmm(value)}`;
		const range = `${stamp(shift.opened_at)} → ${shift.closed_at ? stamp(shift.closed_at) : __("hozir")}`;
		return [shift.open ? __("Ochiq") : "", range, shift.user_name || shift.user]
			.filter(Boolean)
			.join(" · ");
	}

	renderHistoryList(rows, $list, $summary) {
		if (!rows.length) {
			$list.html(
				`<div class="rc-hint">${esc(__("Bu smenada to'langan chek topilmadi."))}</div>`
			);
			return;
		}

		this.historyRows = new Map(rows.map((r) => [r.invoice, r]));
		const total = rows.reduce((sum, r) => sum + flt(r.amount), 0);
		$summary.html(
			`<span>${cint(rows.length)} ${esc(__("ta chek"))}</span><span>${esc(
				this.money(total)
			)}</span>`
		);

		$list.html(
			rows
				.map((r) => {
					const modes = (r.payments || []).map((p) => esc(p.mode_of_payment)).join(", ");
					return `<div class="rc-history__row ${
						r.is_return ? "rc-history__row--return" : ""
					}" data-invoice="${esc(r.invoice)}">
						<div class="rc-history__main">
							<div class="rc-history__invoice">${esc(r.invoice)}${r.is_return ? ` ${returnBadge()}` : ""}</div>
							<div class="rc-history__meta">${esc(r.date)} ${esc(hhmm(r.time))} · ${esc(
						r.table || orderTypeLabel(r.order_type)
					)} · ${esc(r.customer_name || "")}</div>
							<div class="rc-history__meta">${esc(__("Kassir"))}: ${esc(r.cashier_name || "—")}${
						modes ? " · " + modes : ""
					}</div>
						</div>
						<div class="rc-history__side">
							<div class="rc-history__amount">${esc(this.money(r.amount))}</div>
							${this.historyActionsHtml("history.rowActions", r, r.invoice)}
						</div>
					</div>`;
				})
				.join("")
		);
	}

	/** Kassa tarixidagi bitta chekning to'liq tarkibi — FAQAT O'QISH. */
	renderHistoryDetail(bill, $detail) {
		this.historyBill = bill;
		const items = (bill.items || [])
			.map(
				(item) => `<div class="rc-item">
					<div>
						<div class="rc-item__name">${esc(item.item_name || item.item_code)}</div>
						<div class="rc-item__qty">${fmtQty(item.qty)} × ${esc(this.money(item.rate))}</div>
					</div>
					<div class="rc-item__amount">${esc(this.money(item.amount))}</div>
				</div>`
			)
			.join("");

		const taxes = (bill.taxes || [])
			.map(
				(tax) => `<div class="rc-total ${tax.is_service_charge ? "rc-total--service" : ""}">
					<span>${esc(taxLabel(tax))}</span>
					<span>${esc(this.money(tax.amount))}</span>
				</div>`
			)
			.join("");

		const invoice = bill.invoice || bill.order;

		const payments = bill.payments || [];
		const paymentTypes = payments.length
			? payments.map((p) => p.mode_of_payment).join(", ")
			: "—";

		const paymentDetails = payments.length
			? `<div class="rc-payment-details">
					<div class="rc-payment-details__title">${esc(__("To'lov tafsilotlari"))}</div>
					${payments
						.map(
							(p) => `<div class="rc-total rc-total--payment">
								<span>${esc(p.mode_of_payment)}</span>
								<span>${esc(this.money(p.amount))}</span>
							</div>`
						)
						.join("")}
				</div>`
			: "";

		// Qaytarish cheki: sarlavhada belgi va asl chekka bosiladigan havola. Asl chek
		// nomi serverdan (`return_against`); yo'q bo'lsa havola chizilmaydi.
		$detail.html(`
			<div class="rc-history__bar">
				<button class="rc-btn" type="button" data-action="history-back">← ${esc(__("Orqaga"))}</button>
				${bill.is_return ? returnBadge() : ""}
			</div>
			${
				bill.is_return && bill.return_against
					? `<button class="rc-history__origin" type="button" data-action="history-open"
							data-invoice="${esc(bill.return_against)}">${esc(__("Asl chek"))}: ${esc(
							bill.return_against
					  )} →</button>`
					: ""
			}
			<div class="rc-facts" style="margin-top:12px">
				<div class="rc-fact"><div class="rc-fact__label">${esc(
					__("Buyurtma")
				)}</div><div class="rc-fact__value">${esc(invoice)}</div></div>
				<div class="rc-fact"><div class="rc-fact__label">${esc(
					__("Ofitsant")
				)}</div><div class="rc-fact__value">${esc(bill.waiter_name || "—")}</div></div>
				<div class="rc-fact"><div class="rc-fact__label">${esc(
					__("To'lov turi")
				)}</div><div class="rc-fact__value">${esc(paymentTypes)}</div></div>
				<div class="rc-fact"><div class="rc-fact__label">${esc(
					__("Kassir")
				)}</div><div class="rc-fact__value">${esc(bill.cashier_name || "—")}</div></div>
			</div>
			<div class="rc-items" style="margin-top:10px">${items}</div>
			<div class="rc-totals">
				<div class="rc-total"><span>${esc(__("Umumiy"))}</span><span>${esc(
			this.money(bill.subtotal)
		)}</span></div>
				${taxes}
			</div>
			${paymentDetails}
			<div class="rc-totals">
				<div class="rc-total rc-total--grand"><span>${esc(__("Jami"))}</span><span>${esc(
			this.money(bill.rounded_total)
		)}</span></div>
				<div class="rc-total rc-total--change"><span>${esc(__("Qaytim"))}</span><span>${esc(
			this.money(bill.change_amount)
		)}</span></div>
			</div>
			<div class="rc-actions rc-actions--row">
				${this.historyActionsHtml("history.detailActions", bill, invoice)}
			</div>`);
	}

	/** Slotdagi amal tugmalari (`when(screen, subject)` bo'yicha saralangan). */
	historyActionsHtml(slot, subject, invoice) {
		return slots
			.visible(slot, this, subject)
			.map(
				(item) => `<button class="rc-btn rc-btn--${esc(
					item.kind || "default"
				)}" type="button"
					data-history-action="${esc(item.id)}" data-slot="${esc(slot)}"
					data-invoice="${esc(invoice)}">${esc(item.label)}</button>`
			)
			.join("");
	}
}

slots.contribute("history.rowActions", {
	id: "reprint",
	order: 10,
	label: __("Chop etish"),
	onClick: (screen, row) => screen.printReceipt({ bill: { invoice: row.invoice } }),
});

slots.contribute("history.detailActions", {
	id: "reprint",
	order: 10,
	kind: "primary",
	label: __("Chop etish"),
	onClick: (screen, bill) =>
		screen.printReceipt({ bill: { invoice: bill.invoice || bill.order } }),
});
