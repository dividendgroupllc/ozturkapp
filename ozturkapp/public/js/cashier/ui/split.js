/**
 * Hisobni bo'lish oynasi.
 */

import { ui } from "../kit/index.js";
import { esc, fmtQty, num } from "../util/format.js";

export class SplitMethods {
	/**
	 * Hisobni taqsimlash — kassir mahsulotlarni belgilab, ular uchun
	 * alohida chek chiqaradi va umumiy hisobdan chiqarib oladi.
	 *
	 * Mahsulotni ko'chirish, soliq/xizmat haqini qayta hisoblash — bularni
	 * URY'ning o'zi bajaradi (`ury_order.split_bill`,
	 * `api/billing.py:split_bill` orqali chaqiriladi). Bu yerda faqat
	 * qaysi mahsulotdan nechta ko'chirilishi tanlanadi.
	 *
	 * Tasdiqlangach ikkita amal ketma-ket bajariladi — xuddi `giveBill()`
	 * kabi: yangi chek ochiladi (`invoice_printed = 1`) va darhol chop
	 * etiladi, shunda kassir bitta bosishda mehmonga chek bera oladi.
	 */
	openSplitModal(detail) {
		const bill = detail.bill;
		const items = bill.items || [];

		this.el.modalBody.innerHTML = `
			<div class="rc-split__list">
				${items
					.map(
						(item) => `<div class="rc-split__row" data-name="${esc(
							item.name
						)}" data-max="${flt(item.qty)}" data-rate="${flt(item.rate)}">
							<div>
								<div class="rc-split__name">${esc(item.item_name || item.item_code)}</div>
								<div class="rc-split__meta">${fmtQty(item.qty)} × ${esc(this.money(item.rate))}</div>
							</div>
							<div class="rc-split__stepper">
								<button class="rc-split__step" type="button" data-dir="-1">−</button>
								<span class="rc-split__qty">0</span>
								<button class="rc-split__step" type="button" data-dir="1">+</button>
							</div>
						</div>`
					)
					.join("")}
			</div>
			<div class="rc-split__foot">
				<div class="rc-split__summary">
					<span>${esc(__("Ajratiladigan summa"))}</span>
					<span class="rc-split__amount">${esc(this.money(0))}</span>
				</div>
				<div class="rc-split__error" role="alert"></div>
				<div class="rc-actions">
					<button class="rc-btn rc-btn--primary" data-action="confirm-split" disabled>${esc(
						__("Hisobni bo'lish va chek chiqarish")
					)}</button>
				</div>
			</div>`;

		const $body = $(this.el.modalBody).off();
		const $error = $body.find(".rc-split__error");
		const $amount = $body.find(".rc-split__amount");
		const $confirm = $body.find('[data-action="confirm-split"]');

		const recalc = () => {
			let total = 0;
			let selected = 0;
			$body.find(".rc-split__row").each((_, row) => {
				const $row = $(row);
				const qty = cint($row.find(".rc-split__qty").text());
				if (qty > 0) {
					selected += 1;
					total += qty * num(row.dataset.rate);
				}
				$row.toggleClass("rc-split__row--active", qty > 0);
			});
			$amount.text(this.money(total));
			$confirm.prop("disabled", selected === 0);
		};

		$body.on("click", ".rc-split__step", (e) => {
			const $row = $(e.currentTarget).closest(".rc-split__row");
			const $qty = $row.find(".rc-split__qty");
			const max = cint(flt($row.data("max")));
			const dir = cint(e.currentTarget.dataset.dir);
			const next = Math.min(max, Math.max(0, cint($qty.text()) + dir));
			$qty.text(next);
			recalc();
		});

		// `split_bill` muvaffaqiyatli o'tib, `open_bill` yiqilsa yangi chek ALLAQACHON
		// yaratilgan (mahsulotlar ko'chgan). Qayta bosish `split_bill` ni ikkinchi marta
		// chaqirsa, yana bitta yangi chek ochilib mahsulotlar yana ko'chardi — shuning
		// uchun qayta urinishda faqat yangi chekni berish takrorlanadi.
		let created = null;

		$body.on("click", '[data-action="confirm-split"]', async (e) => {
			const button = e.currentTarget;
			const itemsToMove = [];
			$body.find(".rc-split__row").each((_, row) => {
				const qty = cint($(row).find(".rc-split__qty").text());
				if (qty > 0) itemsToMove.push({ name: row.dataset.name, qty });
			});

			$error.text("");
			try {
				this.busy(button, true);
				this.setModalLocked(true);
				if (!created) {
					created = await this.call("ozturkapp.ozturkapp.api.billing.split_bill", {
						invoice: bill.invoice,
						items_to_move: JSON.stringify(itemsToMove),
					});
				}
				await this.call("ozturkapp.ozturkapp.api.billing.open_bill", {
					invoice: created.new_invoice,
				});
				this.setModalLocked(false);
				this.closeModal();
				this.printReceipt({ bill: { invoice: created.new_invoice } });
				ui.toast(__("Hisob bo'lindi — {0}", [created.new_invoice]), { indicator: "blue" });
				await this.refreshAll();
			} catch (error) {
				this.setModalLocked(false);
				if (created) {
					// Ro'yxat o'zgargan: ekran yangilanadi, qatorlar qayta ishlatilmaydi.
					$body.find(".rc-split__step").prop("disabled", true);
					$confirm.text(__("Yangi chekni berish ({0})", [created.new_invoice]));
					this.refreshAll().catch((refreshError) => this.markStale(refreshError));
				}
				$error.text(this.errorText(error));
			} finally {
				this.busy(button, false);
			}
		});

		this.showModal(__("Hisobni bo'lish"), { size: "md" });
	}
}
