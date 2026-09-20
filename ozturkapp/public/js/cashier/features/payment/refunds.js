/**
 * Qaytarish (`refunds`): kassa tarixidagi to'langan chekni to'liq yoki qisman
 * qaytarish.
 *
 * SIYOSAT SERVERDA
 * ================
 * Qaytariladigan summani, usullar bo'yicha taqsimotni va choychaqa qoidasini
 * `utils/refunds.py` hisoblaydi. Qaytarish HAR DOIM menejer PIN-kodini talab
 * qiladi (`ui.withApproval`) va sabab majburiy. Bu yerda faqat tanlangan
 * qatorlar va sabab yig'iladi; qaytariladigan summa TASDIQLASHDAN KEYIN serverdan
 * ko'rsatiladi — oldindan qo'lda hisoblanmaydi.
 *
 * QAYTARISH USULI
 * ===============
 * Pul asl to'lov usullariga qaytadi. Usulda `allow_in_returns` o'chiq bo'lsa
 * server qaytarishni rad etadi — lekin faqat PIN kiritilgandan KEYIN. Bunday
 * usul aniq bo'lsa (`get_refundable().paid`) kassirga oldindan aytiladi va
 * tugma yoqilmaydi: menejer bekorga PIN kiritmasin.
 */

const { slots, ui, util } = ozturk.cashier;
const { esc, fmtQty, num } = util;

const LOOKUP = "ozturkapp.ozturkapp.api.billing.get_refundable";
const REFUND = "ozturkapp.ozturkapp.api.billing.refund_invoice";

/** Tayyor sabablar; «Boshqa…» — erkin matn. */
const REASONS = [__("Mijoz e'tirozi"), __("Buyurtma xatosi"), __("Sifatsiz taom")];

const invoiceOf = (bill) => bill.invoice || bill.order;

/** To'langan, qaytarish bo'lmagan, bekor qilinmagan chek qaytariladi. */
function isRefundable(screen, bill) {
	return !!(
		(screen.ctx.permissions || {}).can_bill &&
		bill.paid &&
		!bill.is_return &&
		!bill.cancelled
	);
}

/** Usulda qaytarishga ruxsat yo'q, lekin qaytariladigan qoldiq bor — server rad etadi. */
const blockedModes = (info) =>
	(info.paid || []).filter((mode) => flt(mode.refundable) > 0 && !mode.allow_in_returns);

function refundDialog(screen, info) {
	const lines = new Map();
	const items = info.items || [];
	const blocked = blockedModes(info);
	let dialog = null;

	const reasonChips = ui.chips({
		options: REASONS,
		columns: 2,
		other: { label: __("Boshqa…"), placeholder: __("Sababni yozing") },
		onChange: () => dialog.setError(""),
	});

	const wrap = document.createElement("div");
	wrap.className = "rc-payment-refund";

	const tools = info.refundable
		? `<div class="rc-payment-refund__tools">
				<span class="rc-payment-label">${esc(__("Qaytariladigan taomlar"))}</span>
				<button type="button" class="rc-btn" data-part="all"></button>
			</div>`
		: `<p class="rc-dialog__note">${esc(__("Bu chekda qaytariladigan taom qolmagan."))}</p>`;

	const rows = items
		.map((item) => {
			const left = flt(item.refundable_qty);
			return `<div class="rc-payment-refund__row${
				left ? "" : " rc-payment-refund__row--done"
			}"
					data-name="${esc(item.name)}" data-max="${left}">
				<div class="rc-payment-refund__what">
					<div class="rc-payment-refund__name">${esc(item.item_name || item.item_code)}</div>
					<div class="rc-payment-refund__meta">${esc(
						__("Sotilgan {0} · qaytarilgan {1}", [
							fmtQty(item.sold_qty),
							fmtQty(item.returned_qty),
						])
					)}</div>
				</div>
				${
					left
						? `<div class="rc-payment-refund__stepper">
								<button type="button" class="rc-btn" data-dir="-1" aria-label="${esc(__("Kamaytirish"))}">−</button>
								<output class="rc-payment-refund__qty">0</output>
								<button type="button" class="rc-btn" data-dir="1" aria-label="${esc(__("Ko'paytirish"))}">+</button>
							</div>`
						: `<span class="rc-payment-refund__gone">${esc(
								__("Qaytarib bo'lingan")
						  )}</span>`
				}
			</div>`;
		})
		.join("");

	const modes = (info.paid || [])
		.map(
			(mode) => `<div class="rc-payment-refund__mode${
				flt(mode.refundable) > 0 && !mode.allow_in_returns
					? " rc-payment-refund__mode--blocked"
					: ""
			}">
				<span>${esc(mode.mode_of_payment)}</span>
				<span>${esc(__("To'langan"))} ${esc(screen.money(mode.paid))} · ${esc(__("qaytadi"))} ${esc(
				screen.money(mode.refundable)
			)}</span>
			</div>`
		)
		.join("");

	// Tartib: avval to'siq (usul ruxsat etilmagan bo'lsa), keyin taomlar, sabab va
	// xulosa; pul qaytadigan usullar ro'yxati ma'lumot uchun — eng oxirida.
	wrap.innerHTML = `
		${
			blocked.length
				? `<p class="rc-payment-refund__warn" role="alert">⚠ ${esc(
						__(
							"«{0}» usuli bilan qaytarish ruxsat etilmagan. POS Profile → To'lov usullari jadvalida shu usul uchun «Qaytarishda ruxsat» (Allow In Returns) belgisini yoqing.",
							[blocked.map((mode) => mode.mode_of_payment).join(", ")]
						)
				  )}</p>`
				: ""
		}
		${tools}
		<div class="rc-payment-refund__rows">${rows}</div>
		<div class="rc-field rc-payment-refund__reason">
			<label class="rc-field__label">${esc(
				__("Qaytarish sababi")
			)}<span class="rc-field__req" aria-hidden="true"> *</span></label>
			<div data-part="reason"></div>
		</div>
		<p class="rc-payment-refund__summary" role="status"></p>
		<div class="rc-payment-label">${esc(__("Pul qaytariladigan usullar"))}</div>
		<div class="rc-payment-refund__modes">${modes}</div>
		${
			flt(info.tip) > 0
				? `<p class="rc-field__hint">${esc(
						__("Choychaqa ({0}) faqat chek butunlay qaytarilganda qaytariladi.", [
							screen.money(info.tip),
						])
				  )}</p>`
				: ""
		}`;
	wrap.querySelector('[data-part="reason"]').appendChild(reasonChips.el);

	const summary = wrap.querySelector(".rc-payment-refund__summary");
	const toggleAll = wrap.querySelector('[data-part="all"]');

	const rowOf = (name) =>
		wrap.querySelector(`.rc-payment-refund__row[data-name="${CSS.escape(name)}"]`);
	const maxOf = (name) => num(rowOf(name).dataset.max);

	const selectable = () => items.filter((item) => flt(item.refundable_qty) > 0);
	const picked = () => [...lines].filter(([, qty]) => qty > 0);
	const allPicked = () =>
		selectable().every((item) => (lines.get(item.name) || 0) === flt(item.refundable_qty));

	function setQty(name, qty) {
		const next = Math.min(Math.max(qty, 0), maxOf(name));
		if (next > 0) lines.set(name, next);
		else lines.delete(name);
		rowOf(name).querySelector(".rc-payment-refund__qty").textContent = fmtQty(next);
		paint();
	}

	function paint() {
		const chosen = picked();
		const pieces = chosen.reduce((sum, [, qty]) => sum + qty, 0);

		summary.textContent = chosen.length
			? __(
					"Tanlandi: {0} ta taom, {1} dona. Qaytariladigan summa tasdiqlangach ko'rsatiladi.",
					[chosen.length, fmtQty(pieces)]
			  )
			: "";

		if (toggleAll) {
			toggleAll.textContent = allPicked() ? __("Tozalash") : __("Hammasini tanlash");
		}

		// So'rov ketayotgan (band) tugma qayta yoqilmaydi: ikki marta yuborilmasin.
		const button = dialog && dialog.footEl.querySelector('[data-action="refund"]');
		if (button && !button.classList.contains("rc-btn--busy")) {
			button.disabled = !chosen.length || blocked.length > 0;
		}
	}

	wrap.addEventListener("click", (event) => {
		const step = event.target.closest("[data-dir]");
		if (step) {
			const row = step.closest(".rc-payment-refund__row");
			const name = row.dataset.name;
			const now = lines.get(name) || 0;
			// Kasr miqdor (masalan 1,5 kg) uchun oxirgi qadam qolgan miqdorga to'g'rilanadi.
			setQty(name, now + cint(step.dataset.dir));
			return;
		}
		if (event.target.closest('[data-part="all"]')) {
			const clear = allPicked();
			selectable().forEach((item) => setQty(item.name, clear ? 0 : flt(item.refundable_qty)));
		}
	});

	async function submit(d) {
		d.setError("");

		const reason = reasonChips.value();
		if (!reason) return d.setError(__("Qaytarish sababini tanlang yoki yozing"));

		const payload = picked().map(([name, qty]) => ({ name, qty }));
		d.setBusy(true);
		try {
			const result = await ui.withApproval(
				(approval) =>
					screen.call(REFUND, {
						invoice: info.invoice,
						items: JSON.stringify(payload),
						reason,
						...(approval ? { approval: JSON.stringify(approval) } : {}),
					}),
				{ action: __("Chekni qaytarish · {0}", [info.invoice]) }
			);

			d.close(result);
			const by = (result.payments || []).map((row) => row.mode_of_payment).join(", ");
			ui.toast(
				`${screen.money(result.refunded)}${by ? ` (${by})` : ""} · ${result.invoice}`,
				{ title: __("Qaytarildi"), seconds: 9 }
			);
			screen.scheduleRefresh({ floor: true, orders: true, panel: true });
			// Ro'yxat qayta yuklanadi: qaytarish cheki (manfiy summa) unda paydo bo'ladi.
			await screen.openHistoryModal();
		} catch (error) {
			// Rad etilgan tasdiq — xato emas: oyna ochiq qoladi.
			if (!error || !error.cancelled) d.setError(screen.errorText(error));
		} finally {
			d.setBusy(false);
			paint();
		}
	}

	dialog = ui.dialog({
		title: __("Chekni qaytarish"),
		subtitle: info.invoice,
		size: ui.virtualKeyboard ? "lg" : "md",
		body: wrap,
		actions: [
			{ id: "cancel", label: __("Yopish"), onClick: (d) => d.close(null) },
			{
				id: "refund",
				label: __("Qaytarish"),
				kind: "danger-solid",
				disabled: true,
				onClick: (d) => submit(d),
			},
		],
	});
	paint();
	return dialog;
}

async function startRefund(screen, bill, button) {
	let info;
	try {
		screen.busy(button, true);
		info = await screen.call(LOOKUP, { invoice: invoiceOf(bill) });
	} catch (error) {
		screen.alertError(error);
		return;
	} finally {
		screen.busy(button, false);
	}
	refundDialog(screen, info);
}

export function installRefunds() {
	return slots.contribute("history.detailActions", {
		id: "payment-refund",
		order: 110,
		kind: "danger",
		label: __("Qaytarish"),
		when: isRefundable,
		onClick: startRefund,
	});
}
