/**
 * Mijoz: qidirish/yangi mijoz oynasi, chekka biriktirish va panel ma'lumot bloki.
 *
 * Mijoz tanlash buyurtma oynasida ham (`order_dialog.js`), tayyor chekda ham
 * (`panel.more` -> «Mijoz») ishlatiladi — shuning uchun tanlash oynasi alohida.
 */

import { orderApi } from "./api.js";
import { esc, ui } from "./shared.js";

/** Yozish tugagach shuncha kutib qidiriladi — har harfga so'rov ketmasin. */
const SEARCH_DELAY_MS = 300;
const RESULT_LIMIT = 20;

// ═══════════════════════════════════════════════════════════════
//  Yangi mijoz
// ═══════════════════════════════════════════════════════════════

/**
 * Yangi mijoz formasi: ism majburiy, telefon va manzil ixtiyoriy.
 * Server xato bersa (masalan telefon noto'g'ri) oyna ochiq qoladi.
 *
 * @returns {Promise<object|null>}  yaratilgan mijoz `{name, customer_name, mobile_number, address}`
 */
async function createCustomer(screen, prefill) {
	let created = null;

	const values = await ui.form({
		title: __("Yangi mijoz"),
		fields: [
			{
				type: "text",
				name: "customer_name",
				label: __("Ism"),
				required: true,
				value: prefill,
			},
			{ type: "tel", name: "mobile_number", label: __("Telefon"), placeholder: "+998" },
			{ type: "text", name: "address", label: __("Manzil") },
		],
		submitLabel: __("Saqlash"),
		onSubmit: async (input) => {
			created = await orderApi.createCustomer(screen, {
				customerName: input.customer_name,
				mobileNumber: input.mobile_number,
				address: input.address,
			});
		},
	});

	return values ? created : null;
}

// ═══════════════════════════════════════════════════════════════
//  Mijoz tanlash
// ═══════════════════════════════════════════════════════════════

/**
 * Mijozni ism yoki telefon bo'yicha qidirib tanlaydi. Qidiruv bo'sh bo'lsa
 * eng oxirgi mijozlar ko'rsatiladi. Ro'yxatda yo'q mijoz «Yangi mijoz» bilan
 * yaratiladi va darhol tanlanadi.
 *
 * @returns {Promise<object|null>}  `{name, customer_name, mobile_number}`; yopilsa `null`
 */
export function pickCustomer(screen) {
	const body = document.createElement("div");
	body.className = "rc-orders-cust";
	body.innerHTML = `
		<input type="text" class="rc-input rc-orders-cust__search" data-vk="text" autocomplete="off"
			placeholder="${esc(__("Ism yoki telefon bo'yicha qidirish"))}"
			inputmode="${ui.virtualKeyboard ? "none" : "text"}">
		<div class="rc-orders-cust__list" role="listbox"></div>`;

	const search = body.querySelector(".rc-orders-cust__search");
	const list = body.querySelector(".rc-orders-cust__list");

	let found = [];
	let timer = null;
	let sequence = 0;

	const paint = (html) => (list.innerHTML = html);
	const note = (text) =>
		paint(
			`<div class="rc-empty rc-empty--inline"><p class="rc-empty__hint">${esc(
				text
			)}</p></div>`
		);

	const load = async () => {
		const mine = (sequence += 1);
		note(__("Qidirilmoqda…"));
		try {
			const rows = await orderApi.searchCustomers(screen, {
				query: search.value.trim(),
				limit: RESULT_LIMIT,
			});
			// Kassir ulgurmay yangi so'z yozgan bo'lsa, eski javob ko'rsatilmaydi.
			if (mine !== sequence) return;

			dialog.setError("");
			found = rows || [];
			if (!found.length) {
				note(__("Mijoz topilmadi — «Yangi mijoz» tugmasi bilan qo'shing."));
				return;
			}
			paint(
				found
					.map(
						(
							row,
							index
						) => `<button type="button" class="rc-orders-cust__row" role="option"
							data-index="${index}">
							<strong>${esc(row.customer_name || row.name)}</strong>
							<span>${esc(row.mobile_number || "")}</span>
						</button>`
					)
					.join("")
			);
		} catch (error) {
			if (mine !== sequence) return;
			paint("");
			dialog.setError(screen.errorText(error));
		}
	};

	const dialog = ui.dialog({
		title: __("Mijoz"),
		size: "lg",
		body,
		actions: [
			{ id: "close", label: __("Yopish"), onClick: (d) => d.close(null) },
			{
				id: "new",
				label: __("Yangi mijoz"),
				kind: "primary",
				onClick: async (d) => {
					const created = await createCustomer(screen, search.value.trim());
					if (created) d.close(created);
				},
			},
		],
	});

	search.addEventListener("input", () => {
		clearTimeout(timer);
		timer = setTimeout(load, SEARCH_DELAY_MS);
	});
	list.addEventListener("click", (event) => {
		const row = event.target.closest("[data-index]");
		if (row) dialog.close(found[cint(row.dataset.index)]);
	});

	load();
	search.focus();
	dialog.result.then(() => clearTimeout(timer));
	return dialog.result;
}

// ═══════════════════════════════════════════════════════════════
//  Tayyor chekka biriktirish
// ═══════════════════════════════════════════════════════════════

/**
 * Ochiq chekka mijoz biriktiradi (`panel.more` -> «Mijoz»).
 *
 * Hisob allaqachon chiqarilgan bo'lsa ham mumkin, lekin chekda mijoz
 * ko'rinadi — server chekni «qayta chop etish kerak» deb belgilaydi.
 */
export async function attachCustomer(screen, detail) {
	const bill = detail.bill;
	const customer = await pickCustomer(screen);
	if (!customer || customer.name === bill.customer) return;

	try {
		const updated = await orderApi.setCustomer(screen, {
			invoice: bill.invoice,
			customer: customer.name,
		});
		ui.toast(__("Mijoz biriktirildi: {0}", [customer.customer_name || customer.name]), {
			seconds: 3,
		});
		if (updated.reprint_needed) {
			ui.toast(__("Hisob allaqachon chiqarilgan — chekni qayta chop eting."), {
				indicator: "orange",
				seconds: 8,
			});
		}
		await screen.refresh({ floor: true, orders: true, panel: true });
	} catch (error) {
		screen.alertError(error);
	}
}

// ═══════════════════════════════════════════════════════════════
//  Panel ma'lumot bloki (`panel.info`)
// ═══════════════════════════════════════════════════════════════

/**
 * Chek panelidagi 1-2 qatorli blok: yetkazib berish telefoni/manzili va
 * standartdan boshqa mijoz. Standart mijoz (POS Profile'dagi) ko'rsatilmaydi —
 * u har chekda bor va faqat joy egallardi.
 *
 * @returns {HTMLElement|null}
 */
export function renderInfo(screen, detail) {
	const bill = detail.bill || {};
	const delivery = bill.delivery;
	const named = bill.customer && bill.customer !== screen.ctx.default_customer;
	if (!delivery && !named) return null;

	// Matn `textContent` bilan qo'yiladi (HTML emas), shuning uchun serverdan kelgan
	// qiymat ekranlanmaydi; satrlar `join` bilan yig'iladi.
	const who = named ? ["👤", bill.customer_name || bill.customer].join(" ") : "";
	const rows = [];
	if (delivery) {
		const phone = delivery.phone ? ["📞", delivery.phone].join(" ") : "";
		rows.push({ text: [phone, who].filter(Boolean).join(" · ") });
		if (delivery.address)
			rows.push({ text: ["📍", delivery.address].join(" "), address: true });
	} else {
		rows.push({ text: [who, bill.mobile_number].filter(Boolean).join(" · ") });
	}

	const block = document.createElement("div");
	block.className = "rc-orders-info";
	rows.filter((entry) => entry.text).forEach((entry) => {
		const row = document.createElement("div");
		row.className = "rc-orders-info__row";
		if (entry.address) row.classList.add("rc-orders-info__row--address");
		row.textContent = entry.text;
		row.title = entry.text;
		block.appendChild(row);
	});
	return block;
}
