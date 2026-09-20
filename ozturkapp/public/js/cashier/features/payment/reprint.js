/**
 * «Chek o'zgardi» — chop etilgan chek eskirgani haqida eslatma.
 *
 * Chegirma hisob mijozga berilgandan KEYIN qo'yilsa (yoki olib tashlansa),
 * qog'ozdagi chek eskiradi. Server buni `bill.reprint_needed` bilan bildiradi
 * va chek qayta chop etilgach (`printing.print_bill`) o'zi o'chiradi. Eslatma
 * chegirma funksiyasi o'chirilgan bo'lsa ham ishlashi kerak (belgi bazada
 * qolgan bo'lishi mumkin), shuning uchun u o'z bayrog'isiz o'rnatiladi.
 */

const { slots, ui, util } = ozturk.cashier;
const { esc } = util;

/**
 * Chekni qayta chop etadi. Natijani (navbatga qo'yildi / printer yo'q) yadroning
 * `printReceipt` metodi o'zi ko'rsatadi; bu yerda faqat paneldagi eslatma
 * yangilanadi — server belgini chop topshirig'i navbatga tushganda o'chiradi.
 */
export async function reprintBill(screen, invoice, button) {
	screen.busy(button, true);
	try {
		await screen.printReceipt({ bill: { invoice } });
	} finally {
		screen.busy(button, false);
	}
	await screen.refresh({ panel: true });
}

/** Server chekni eskirgan deb belgilagan bo'lsa qayta chop etishni taklif qiladi. */
export async function offerReprint(screen, bill) {
	if (!bill || !bill.reprint_needed) return;

	const yes = await ui.confirm({
		title: __("Chek o'zgardi"),
		message: __("Chek o'zgardi — qayta chop etilsinmi?"),
		confirmLabel: __("Chop etish"),
		cancelLabel: __("Keyinroq"),
	});
	if (yes) await reprintBill(screen, bill.invoice);
}

export function installReprintBanner() {
	return slots.contribute("panel.info", {
		id: "payment-reprint",
		order: 100,
		when: (screen, detail) =>
			!!(detail.bill && detail.bill.reprint_needed && !detail.bill.paid),
		render: (screen, detail) => {
			const el = document.createElement("div");
			el.className = "rc-payment-notice rc-payment-notice--warn";
			el.innerHTML = `<span class="rc-payment-notice__text">⚠ ${esc(
				__("Chek o'zgardi — mijozdagi chek eskirgan")
			)}</span>
				<button type="button" class="rc-btn rc-btn--primary rc-payment-notice__btn">${esc(
					__("Chop etish")
				)}</button>`;

			el.querySelector("button").addEventListener("click", (event) =>
				reprintBill(screen, detail.bill.invoice, event.currentTarget)
			);
			return el;
		},
	});
}
