/**
 * Chegirma (`discount`): «Yana ⋯» varag'idagi «Chegirma» amali va paneldagi
 * qo'yilgan chegirma xulosasi.
 *
 * SIYOSAT SERVERDA
 * ================
 * Chegirma summasini ERPNext hisoblaydi (`utils/discounts.py`), kassir
 * chegarasidan oshganini server aniqlaydi va menejer PIN-kodini so'raydi
 * (`ApprovalRequired`). Bu yerda faqat kiritishni yig'amiz: foiz YOKI summa
 * (ikkalasi birga emas), sabab (majburiy) — va serverning javobini ko'rsatamiz.
 */

import { offerReprint } from "./reprint.js";

const { slots, ui, util } = ozturk.cashier;
const { esc, fmtQty } = util;

const APPLY = "ozturkapp.ozturkapp.api.billing.apply_discount";
const REMOVE = "ozturkapp.ozturkapp.api.billing.remove_discount";

/** Tez foizlar: kassir eng ko'p shulardan birini qo'yadi. */
const QUICK_PERCENTS = [5, 10, 15, 20];

/** Tayyor sabablar; «Boshqa…» — erkin matn. Tanlangan matn serverga sabab bo'lib boradi. */
const REASONS = [__("Doimiy mijoz"), __("Xodim"), __("Shikoyat"), __("Aksiya")];

const hasDiscount = (bill) => flt(bill.discount_percent) > 0 || flt(bill.discount) > 0;

/** Chegirma faqat hali to'lanmagan, bekor qilinmagan, taomli qoralama chekka qo'yiladi. */
function isDiscountable(screen, detail) {
	const bill = detail.bill;
	return !!(
		bill &&
		(screen.ctx.permissions || {}).can_bill &&
		!bill.paid &&
		!bill.cancelled &&
		!bill.is_return &&
		cint(bill.item_count) > 0
	);
}

/** Kassir chegarasi haqida ko'rsatma matni (`max_cashier_discount_percent`). */
function limitHint(screen) {
	const limit = flt((screen.ctx.feature_settings || {}).max_cashier_discount_percent);
	return limit > 0
		? __("Kassir chegarasi — {0}%. Undan oshsa menejer PIN-kodi so'raladi.", [fmtQty(limit)])
		: __("Har qanday chegirma uchun menejer PIN-kodi so'raladi.");
}

function openDiscountDialog(screen, detail) {
	const bill = detail.bill;
	let mode = "percent";

	const modeChips = ui.chips({
		options: [
			{ value: "percent", label: __("Foiz") },
			{ value: "amount", label: __("Summa") },
		],
		value: mode,
		columns: 2,
		onChange: (value) => setMode(value),
	});

	const quickChips = ui.chips({
		options: QUICK_PERCENTS.map((percent) => ({ value: percent, label: `${percent}%` })),
		columns: QUICK_PERCENTS.length,
		onChange: (percent) => amount.set(percent),
	});

	const amount = ui.amountInput({
		label: __("Chegirma miqdori"),
		onChange: (value) => quickChips.set(QUICK_PERCENTS.includes(value) ? value : null),
	});

	const reasonChips = ui.chips({
		options: REASONS,
		columns: 3,
		other: { label: __("Boshqa…"), placeholder: __("Sababni yozing") },
		onChange: () => dialog.setError(""),
	});

	const wrap = document.createElement("div");
	wrap.className = "rc-payment-discount";
	wrap.innerHTML = `
		<div class="rc-field">
			<label class="rc-field__label">${esc(__("Chegirma turi"))}</label>
			<div data-part="mode"></div>
		</div>
		<div class="rc-field" data-part="quick-field">
			<label class="rc-field__label">${esc(__("Tez tanlash"))}</label>
			<div data-part="quick"></div>
		</div>
		<div class="rc-field">
			<label class="rc-field__label" data-part="amount-label"></label>
			<div data-part="amount"></div>
			<p class="rc-field__hint">${esc(limitHint(screen))}</p>
		</div>
		<div class="rc-field">
			<label class="rc-field__label">${esc(
				__("Sabab")
			)}<span class="rc-field__req" aria-hidden="true"> *</span></label>
			<div data-part="reason"></div>
		</div>`;

	const part = (name) => wrap.querySelector(`[data-part="${name}"]`);
	part("mode").appendChild(modeChips.el);
	part("quick").appendChild(quickChips.el);
	part("amount").appendChild(amount.el);
	part("reason").appendChild(reasonChips.el);

	function setMode(next) {
		mode = next || mode;
		part("quick-field").hidden = mode !== "percent";
		part("amount-label").textContent =
			mode === "percent"
				? __("Foiz (%)")
				: __("Summa ({0})", [bill.currency || screen.ctx.currency || ""]);
		quickChips.set(null);
		amount.set(null);
	}
	setMode("percent");

	const dialog = ui.dialog({
		title: __("Chegirma"),
		subtitle: `${detail.table || bill.invoice} · ${__("Taomlar summasi")}: ${screen.money(
			bill.total
		)}`,
		size: ui.virtualKeyboard ? "lg" : "md",
		body: wrap,
		actions: [
			{ id: "cancel", label: __("Yopish"), onClick: (d) => d.close(null) },
			{
				id: "apply",
				label: __("Chegirma qo'yish"),
				kind: "primary",
				onClick: (d) => submit(d),
			},
		],
	});

	async function submit(d) {
		d.setError("");

		const value = amount.value();
		const reason = reasonChips.value();
		if (!(value > 0)) return d.setError(__("Chegirma miqdorini kiriting"));
		if (!reason) return d.setError(__("Chegirma sababini tanlang yoki yozing"));

		d.setBusy(true);
		try {
			const result = await ui.withApproval(
				(approval) =>
					screen.call(APPLY, {
						invoice: bill.invoice,
						reason,
						...(mode === "percent" ? { percent: value } : { amount: value }),
						...(approval ? { approval: JSON.stringify(approval) } : {}),
					}),
				{
					action: __("Chegirma {0}", [
						mode === "percent" ? `${fmtQty(value)}%` : screen.money(value),
					]),
				}
			);

			d.close(result);
			ui.toast(
				__("Chegirma qo'yildi: −{0} ({1}%)", [
					screen.money(result.discount),
					fmtQty(result.discount_percent),
				])
			);
			await screen.refresh({ floor: true, orders: true, panel: true });
			await offerReprint(screen, result);
		} catch (error) {
			// Rad etilgan tasdiq — xato emas: oyna ochiq qoladi, kassir qiymatni o'zgartira oladi.
			if (!error || !error.cancelled) d.setError(screen.errorText(error));
		} finally {
			d.setBusy(false);
		}
	}

	return dialog;
}

async function removeDiscount(screen, invoice, button) {
	const yes = await ui.confirm({
		title: __("Chegirmani bekor qilish"),
		message: __("Chegirma olib tashlansinmi? Chek summasi asl holiga qaytadi."),
		confirmLabel: __("Ha, olib tashlash"),
		cancelLabel: __("Yo'q"),
		kind: "danger-solid",
	});
	if (!yes) return;

	try {
		screen.busy(button, true);
		const result = await screen.call(REMOVE, { invoice });
		ui.toast(__("Chegirma bekor qilindi"), { indicator: "orange" });
		await screen.refresh({ floor: true, orders: true, panel: true });
		await offerReprint(screen, result);
	} catch (error) {
		screen.alertError(error);
	} finally {
		screen.busy(button, false);
	}
}

/** Paneldagi xulosa: nechchi foiz, sabab, kim tasdiqlagan + «Bekor qilish». */
function summary(screen, detail) {
	const bill = detail.bill;
	const facts = [
		bill.discount_reason ? `${__("Sabab")}: ${bill.discount_reason}` : "",
		bill.discount_approved_by_name
			? `${__("Tasdiqladi")}: ${bill.discount_approved_by_name}`
			: "",
	].filter(Boolean);

	const el = document.createElement("div");
	el.className = "rc-payment-notice";
	el.innerHTML = `<div class="rc-payment-notice__text">
			<strong>${esc(__("Chegirma"))} ${esc(fmtQty(bill.discount_percent))}% · −${esc(
		screen.money(bill.discount)
	)}</strong>
			${facts.length ? `<span>${facts.map(esc).join(" · ")}</span>` : ""}
		</div>
		<button type="button" class="rc-btn rc-payment-notice__btn">${esc(__("Bekor qilish"))}</button>`;

	el.querySelector("button").addEventListener("click", (event) =>
		removeDiscount(screen, bill.invoice, event.currentTarget)
	);
	return el;
}

export function installDiscount() {
	return [
		slots.contribute("panel.more", {
			id: "payment-discount",
			order: 110,
			label: (screen, detail) =>
				hasDiscount(detail.bill) ? __("Chegirmani o'zgartirish") : __("Chegirma"),
			when: isDiscountable,
			onClick: (screen, detail) => openDiscountDialog(screen, detail),
		}),
		slots.contribute("panel.info", {
			id: "payment-discount-info",
			order: 110,
			when: (screen, detail) => isDiscountable(screen, detail) && hasDiscount(detail.bill),
			render: summary,
		}),
	];
}
