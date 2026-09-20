// E2E senariylari (haqiqiy interfeys + haqiqiy backend, shim tranzaksiyasi ichida).
const CASHIER = "e2e-cashier@example.com";
const MANAGER = "e2e-manager@example.com";
const PIN = "4321";
const WAITER = "e2e-waiter@example.com";


// ── Umumiy yordamchilar ────────────────────────────────────────────────

/** Kassir sifatida buyurtma yaratadi (haqiqiy `create_order` API; UI orqali yaratish `b` senariyida sinaladi). */
async function newOrder(h, { orderType = "Dine In", table = null, items, delivery = null, customer = null }) {
	const args = { order_type: orderType, items: items.map((i) => ({ item: i.item, item_name: i.item, qty: i.qty, comment: i.comment || "" })), client_ref: `e2e-${Math.random().toString(36).slice(2)}` };
	if (table) args.table = table;
	if (delivery) args.delivery = delivery;
	if (customer) args.customer = customer;
	const r = await h.apiAs(CASHIER, "cashier_orders.create_order", args);
	if (r.status !== 200) throw new Error(`newOrder: ${r.status} ${JSON.stringify(r.data).slice(0, 300)}`);
	await h.eval(`ozturk.cashier.screen.refreshAll()`);
	return r.message.invoice;
}

const digits = (text) => String(text).replace(/[^\d]/g, "");

/** Stolni tanlaydi va panel chizilishini kutadi. */
async function selectTable(h, table) {
	await h.click(`.rc-table[data-table="${table}"]`);
	await h.waitFor(`ozturk.cashier.screen.selectedTable === "${table}" && ozturk.cashier.screen.detail`, 8000, `stol ${table} tanlandi`);
	await h.waitIdle(200);
}

async function selectOrder(h, invoice) {
	await h.eval(`ozturk.cashier.screen.selectOrder(${JSON.stringify(invoice)})`);
	await h.waitFor(`ozturk.cashier.screen.selectedInvoice === ${JSON.stringify(invoice)} && ozturk.cashier.screen.detail`, 8000, `chek ${invoice} tanlandi`);
	await h.waitIdle(200);
}

/**
 * Faol buyurtmalar ro'yxatini ochadi. Tor ekranda «Buyurtmalar» almashtirgichi bor; keng ekranda
 * (ilova >= 1200px) ro'yxat bo'sh o'ng panelda turadi — hisob ochiq bo'lsa `‹` unga qaytaradi.
 */
async function openOrdersList(h) {
	const tab = await h.locate({ selector: ".rc-viewtab", text: "Buyurtmalar" });
	if (tab.found) {
		await h.click(".rc-viewtab", "Buyurtmalar");
		return;
	}
	const back = await h.locate({ selector: ".rc-panel__back" });
	if (back.found) await h.click(".rc-panel__back");
	await h.waitFor(`document.querySelector(".rc-panel .rc-orders__list")`, 5000, "o'ng panelda buyurtmalar ro'yxati");
}

const printJobs = (h, type) => h.sql("select name, job_type, status, printer, ref_name, reason from `tabOzturk Print Job` where job_type = %s order by creation", [type]);

/** Menejer PIN oynasi: raqamlar bosiladi va «✓». */
async function enterPin(h, pin, { submit = true } = {}) {
	await h.waitFor(`document.querySelector(".rc-dialog .rc-pin")`, 8000, "PIN oynasi");
	for (const digit of pin) await h.click(".rc-numpad__key", digit, { scope: ".rc-dialog", last: true, exact: true, settle: 60 });
	if (submit) await h.click(".rc-numpad__key", "✓", { scope: ".rc-dialog", last: true, exact: true });
}

/** «Yana ⋯» varag'idan amal tanlaydi. */
async function moreAction(h, label) {
	await h.click('.rc-panel [data-action="panel-more"]');
	await h.waitDialog("Yana amallar");
	await h.clickDialog(".rc-more__item", label);
}

/** Buyurtmani "hisob berilgan" holatga keltiradi (haqiqiy tugma). */
async function giveBill(h) {
	await h.click('.rc-panel [data-action="give-bill"]');
	await h.waitFor(`document.querySelector('.rc-panel [data-action="pay"]') && !document.querySelector('.rc-panel [data-action="pay"]').disabled`, 10000, "«To'lov» yoqildi");
}

const CASH = "Нахт";
const CARD = "Test Karta";

/** Chekni API orqali to'laydi (tayyorlov; to'lov interfeysi `c` senariyida sinaladi). */
async function payViaApi(h, invoice, payments, extra = {}) {
	const bill = await h.apiAs(CASHIER, "billing.get_bill", { invoice }, { http: "GET" });
	if (!bill.message.billed) {
		const r = await h.apiAs(CASHIER, "billing.open_bill", { invoice });
		if (r.status !== 200) throw new Error("open_bill: " + JSON.stringify(r.data).slice(0, 300));
	}
	const r = await h.apiAs(CASHIER, "billing.submit_payment", { invoice, payments, ...extra });
	if (r.status !== 200) throw new Error("submit_payment: " + JSON.stringify(r.data).slice(0, 400));
	return r.message;
}

async function payable(h, invoice) {
	const inv = await h.op("invoice", { name: invoice });
	return Math.round(inv.rounded_total || inv.grand_total);
}

async function openHistory(h) {
	await h.click(".rc-menu-btn");
	await h.click(".rc-menu button, .rc-menu [data-id]", "Tarix");
	await h.waitFor(`document.querySelector(".rc-modal .rc-history__row")`, 8000, "tarix ro'yxati");
}

async function historyDetail(h, invoice) {
	await h.click(`.rc-history__row[data-invoice="${invoice}"]`);
	await h.waitFor(`document.querySelector(".rc-history__detail [data-history-action]")`, 8000, "chek tafsiloti");
}

/** Kassa yopiq ekranidan smena ochish (haqiqiy tugmalar). */
async function openShiftUI(h, amounts) {
	await h.waitFor(`ozturk.cashier.screen.state === "shift"`, 15000, "kassa yopiq ekrani");
	const modes = await h.eval(`[...document.querySelectorAll(".rc-shift-input")].map(i => i.dataset.mode)`);
	for (let i = 0; i < modes.length; i++) {
		const value = amounts[modes[i]];
		if (value) await h.type(".rc-shift-input", String(value), { index: i });
	}
	await h.click('[data-action="open-shift"]');
	await h.waitFor(`ozturk.cashier.screen.state === "ready"`, 15000, "ekran ready");
}

/** Ochiq smena bo'lmasa (masalan `j` yopgan) yangisini ochadi. */
async function ensureShift(h) {
	await h.op("actor", { user: CASHIER });
	const st = await h.state();
	if (st.state === "shift") await openShiftUI(h, { [CASH]: 100000 });
	else if (st.state !== "ready") {
		await h.openCashier();
		if ((await h.state()).state === "shift") await openShiftUI(h, { [CASH]: 100000 });
	}
}

const ALL_FEATURES = ["split_payment", "discount", "table_transfer", "cashier_orders", "customer_attach", "refunds", "cash_drawer", "cash_movements", "tips", "shift_reports"];
const flags = (value) => Object.fromEntries(ALL_FEATURES.map((k) => [k, value]));

/** Bazadan MUSTAQIL hisoblangan naqd: ochilish + naqd savdo - qaytim + kirim - chiqim (ochiq smena, kassir egasi). */
async function independentCash(h) {
	const opening = Number((await h.one("select sum(d.opening_amount) t from `tabPOS Opening Entry Detail` d join `tabPOS Opening Entry` p on p.name = d.parent where p.user = %s and p.status = 'Open'", [CASHIER])).t);
	const sales = Number((await h.one("select coalesce(sum(sip.amount), 0) t from `tabSales Invoice Payment` sip join `tabPOS Invoice` pi on pi.name = sip.parent where pi.docstatus = 1 and pi.owner = %s and sip.mode_of_payment in (%s, %s)", [CASHIER, CASH, "Test Naqd 2"])).t);
	const change = Number((await h.one("select coalesce(sum(change_amount), 0) t from `tabPOS Invoice` where docstatus = 1 and owner = %s", [CASHIER])).t);
	const mv = await h.sql("select kind, amount from `tabOzturk Cash Movement`");
	const moved = mv.reduce((a, m) => a + (m.kind === "In" ? 1 : -1) * Number(m.amount), 0);
	return { opening, sales, change, moved, expected: opening + sales - change + moved };
}

/** JSON dagi barcha son barglari. */
const numbersIn = (value, out = []) => {
	if (Array.isArray(value)) value.forEach((v) => numbersIn(v, out));
	else if (value && typeof value === "object") Object.values(value).forEach((v) => numbersIn(v, out));
	else if (typeof value === "number") out.push(value);
	else if (typeof value === "string" && /^-?\d+(\.\d+)?$/.test(value)) out.push(Number(value));
	return out;
};

const scenarios = {};

scenarios.a = {
	title: "Kassa yopiq ekrani -> smena ochish",
	async run(h) {
		await h.step("bloklovchi ekran", async () => {
			const st = await h.state();
			h.eq(st.state, "shift", "ekran holati = shift (kassa yopiq)");
			h.check("«Kassa yopiq» matni ko'rinadi", (await h.text(".rc-gate")).includes("Kassa yopiq"));
			const inputs = await h.eval(`[...document.querySelectorAll(".rc-shift-input")].map(i => i.dataset.mode)`);
			h.check("ikkala naqd usul kiritish maydoni bor", inputs.length === 2, JSON.stringify(inputs));
			h.check("karta usuli (Bank) kiritish maydonida YO'Q", !inputs.includes("Test Karta"));
		});
		await h.step("smena ochiladi", async () => {
			await h.type(".rc-shift-input", "500000", { index: 0 });
			await h.type(".rc-shift-input", "100000", { index: 1 });
			await h.click('[data-action="open-shift"]');
			await h.waitFor(`ozturk.cashier.screen.state === "ready"`, 15000, "ekran ready");
			const shift = await h.one("select name, user, status, docstatus from `tabPOS Opening Entry` where user = %s order by creation desc limit 1", [CASHIER]);
			h.check("POS Opening Entry yaratildi va ochiq", shift && shift.status === "Open" && shift.docstatus === 1, JSON.stringify(shift));
			const rows = await h.sql("select mode_of_payment, opening_amount from `tabPOS Opening Entry Detail` where parent = %s order by idx", [shift.name]);
			h.eq(rows.map((r) => `${r.mode_of_payment}=${Number(r.opening_amount)}`).sort(), ["Test Naqd 2=100000", "Нахт=500000"].sort(), "ochilish summalari bazada");
		});
	},
};

scenarios.b = {
	title: "Kassir dine-in buyurtma ochadi (menyu, savat, izoh) -> KOT/stol band",
	async run(h) {
		const menuCount = Number((await h.one("select count(*) c from `tabURY Menu Item` where parent = (select active_menu from `tabURY Restaurant` limit 1) and disabled = 0")).c);
		await h.step("stol tanlanadi", async () => {
			await h.click('.rc-table[data-table="Table-1"]');
			await h.waitFor(`ozturk.cashier.screen.selectedTable === "Table-1" && document.querySelector('.rc-panel [data-action="orders-open"]')`, 8000, "panel: Buyurtma ochish");
		});
		await h.step("buyurtma oynasi va menyu", async () => {
			await h.click('.rc-panel [data-action="orders-open"]');
			await h.waitFor(`document.querySelector(".rc-orders-dlg .rc-orders-card")`, 10000, "menyu kartalari");
			const cards = await h.eval(`document.querySelectorAll(".rc-orders-dlg .rc-orders-card").length`);
			h.eq(cards, menuCount, "menyu kartalari soni = URY Menu Item soni");
			const submit = await h.eval(`document.querySelector('.rc-orders-dlg [data-action="submit"]').disabled`);
			h.check("bo'sh savatda yuborish tugmasi o'chiq", submit === true);
		});
		await h.step("taomlar va izohlar", async () => {
			const card = (item) => `.rc-orders-card[data-item="${item}"]`;
			await h.click(card("BARDAK TEA"));
			await h.click(card("BARDAK TEA"));
			await h.click(card("COCA-COLA"));
			await h.click(card("AFGAN BREAD"));
			const lines = await h.eval(`[...document.querySelectorAll(".rc-orders-line")].map(l => l.dataset.item)`);
			h.eq(lines.sort(), ["AFGAN BREAD", "BARDAK TEA", "COCA-COLA"], "savatdagi qatorlar");
			await h.click('.rc-orders-line__main[data-item="BARDAK TEA"]');
			await h.waitDialog("Taom izohi");
			await h.click(".rc-dialog .rc-chip-opt, .rc-dialog button", "Piyozsiz");
			await h.typeField("Boshqa izoh", "Limonli");
			await h.clickDialog('[data-action="submit"], .rc-btn--primary', "Saqlash");
			await h.waitNoDialogTop();
			await h.type(".rc-orders-comment", "Deraza yonida");
			h.check("savat jami ko'rinadi (110 000 = 2x15 000 + 20 000 + 60 000)", (await h.text(".rc-orders-total__value")).replace(/\s/g, "") === "110000", await h.text(".rc-orders-total__value"));
		});
		let invoice;
		await h.step("buyurtma yuboriladi", async () => {
			await h.click('.rc-orders-dlg [data-action="submit"]');
			await h.waitFor(`!document.querySelector(".rc-orders-dlg")`, 15000, "buyurtma oynasi yopilishi");
			await h.waitFor(`ozturk.cashier.screen.selectedInvoice`, 10000, "yangi chek tanlandi");
			invoice = await h.eval(`ozturk.cashier.screen.selectedInvoice`);
		});
		await h.step("DB haqiqati: chek, KOT, stol, chop etish", async () => {
			const inv = await h.op("invoice", { name: invoice });
			h.eq(inv.docstatus, 0, "chek qoralama (to'lanmagan)");
			h.eq(inv.table, "Table-1", "chek stoli");
			h.eq(inv.order_type, "Dine In", "buyurtma turi");
			h.eq(inv.items.map((i) => `${i.item}:${i.qty}@${i.rate}`).sort(), ["AFGAN BREAD:1@60000", "BARDAK TEA:2@15000", "COCA-COLA:1@20000"], "qatorlar (narx Item Price'dan)");
			h.near(inv.net_total, 110000, "net_total");
			const service = inv.taxes.find((t) => /Xizmat|Service/i.test(t.desc || t.account));
			h.check("xizmat haqi qatori bor", !!service, JSON.stringify(inv.taxes));
			h.near(service ? service.amount : 0, 13200, "xizmat haqi = 12% * 110 000");
			h.near(inv.grand_total, 123200, "grand_total");
			const panel = (await h.text(".rc-panel")).replace(/\u00a0/g, " ");
			h.check("xizmat haqi qatorida foiz takrorlanmaydi (12% (12%) emas)", /Xizmat haqi 12%(?!\s*\()/.test(panel) && !/12% \(12%\)/.test(panel), panel.slice(0, 400));
			h.check("panel jami serverdagi rounded_total bilan bir xil", panel.replace(/\s/g, "").includes(String(Math.round(inv.rounded_total || inv.grand_total))), `rounded=${inv.rounded_total}`);
			const table = await h.one("select occupied from `tabURY Table` where name = 'Table-1'");
			h.eq(Number(table.occupied), 1, "URY Table.occupied = 1");
			const tile = await h.eval(`(() => { const t = document.querySelector('.rc-table[data-table="Table-1"]'); return t ? {cls: t.className, text: t.innerText.replace(/\\s+/g, " ")} : null; })()`);
			h.check("stol tugmasi BAND holatida va jami ko'rsatiladi", tile && /OCCUPIED/.test(tile.cls) && tile.text.replace(/\s/g, "").includes("123200"), JSON.stringify(tile));
			const kots = await h.sql("select name, production, docstatus, order_status from `tabURY KOT` where invoice = %s", [invoice]);
			h.eq(kots.map((k) => k.production).sort(), ["Bar", "Oshxona"], "KOT: ichimlik Bar'ga, non Oshxona'ga");
			const kitems = await h.sql("select ki.item, ki.quantity, ki.comments from `tabURY KOT Items` ki join `tabURY KOT` k on k.name = ki.parent where k.invoice = %s", [invoice]);
			h.eq(kitems.find((k) => k.item === "BARDAK TEA").comments, "Piyozsiz, Limonli", "KOT qatori izohi oshxonaga yetdi");
		});
		await h.step("taom qo'shish (Yana -> tahrirlash)", async () => {
			await h.click('.rc-panel [data-action="panel-more"]');
			await h.waitDialog("Yana amallar");
			await h.clickDialog(".rc-more__item", "Taom qo'shish");
			await h.waitFor(`document.querySelector(".rc-orders-dlg .rc-orders-card")`, 10000, "tahrirlash oynasi");
			h.check("mavjud qatorlar oynada ko'rinadi", (await h.text(".rc-orders-existing")).includes("BARDAK TEA"));
			await h.click('.rc-orders-card[data-item="ARUGULA SALAD"]');
			await h.click('.rc-orders-dlg [data-action="submit"]');
			await h.waitFor(`!document.querySelector(".rc-orders-dlg")`, 15000, "oyna yopilishi");
			await h.waitIdle(500);
			const inv = await h.op("invoice", { name: invoice });
			h.eq(inv.items.length, 4, "chekda 4 qator");
			h.near(inv.net_total, 269000, "net_total = 110 000 + 159 000");
			const kots = await h.sql("select production, order_status from `tabURY KOT` where invoice = %s order by creation", [invoice]);
			h.eq(kots.length, 3, "qo'shilgan taom uchun YANGI KOT");
			const panel = (await h.text(".rc-panel")).replace(/\s/g, "");
			h.check("panel yangi jamini ko'rsatadi", panel.includes(String(Math.round(inv.rounded_total || inv.grand_total))), panel.slice(0, 200));
		});
		await h.step("ofitsant 'hisob so'radi' -> qo'ng'iroq belgisi", async () => {
			const r = await h.apiAs(WAITER, "waiter.request_bill", { invoice });
			h.eq(r.status, 200, "waiter.request_bill 200");
			const inv = await h.op("invoice", { name: invoice });
			h.eq(Number(inv.bill_requested), 1, "custom_bill_requested = 1");
			await h.waitFor(`document.querySelector('.rc-table[data-table="Table-1"] .rc-bell')`, 8000, "stol tugmasida qo'ng'iroq (realtime)");
			await h.waitFor(`ozturk.cashier.screen.orders.filter((o) => o.bill_requested).length === 1`, 5000, "buyurtmalar ro'yxatida hisob so'ragan chek");
			h.eq(await h.eval(`document.querySelector(".rc-viewtabs .rc-bell--tab").textContent.replace(/\\D/g, "")`), "1", "qo'ng'iroq soni = 1 («Buyurtmalar» yorlig'ida)");
			h.check("bildirishnoma (toast) ko'rindi", (await h.eval(`document.querySelectorAll(".rc-toast").length`)) > 0);
			await h.waitFor(`document.querySelector(".rc-panel .rc-bell--panel")`, 5000, "panelda qo'ng'iroq");
		});
		h.shared.b = { invoice };
	},
};


scenarios.c = {
	title: "Hisob berish -> chek printer navbatiga -> to'lov usullari (naqd + karta, choychaqa) -> g'aladon -> stol bo'shaydi",
	async run(h) {
		let invoice;
		await h.step("buyurtma tayyorlanadi (API) va tanlanadi", async () => {
			invoice = await newOrder(h, { table: "E2E-T1", items: [{ item: "AFGAN BREAD", qty: 2 }, { item: "BARDAK TEA", qty: 2 }] });
			await selectTable(h, "E2E-T1");
			const btn = await h.eval(`(() => { const b = document.querySelector('.rc-panel [data-action="pay"]'); return b ? b.disabled : null; })()`);
			h.eq(btn, true, "hisob berilmaguncha «To'lov» o'chiq");
		});
		await h.step("hisob berish -> chek printer navbatida", async () => {
			await h.click('.rc-panel [data-action="give-bill"]');
			await h.waitFor(`document.querySelector('.rc-panel [data-action="pay"]') && !document.querySelector('.rc-panel [data-action="pay"]').disabled`, 10000, "«To'lov» yoqildi");
			const inv = await h.op("invoice", { name: invoice });
			h.eq(Number(inv.printed), 1, "invoice_printed = 1");
			const jobs = await printJobs(h, "Bill");
			// Saytda kassir avval chiqargan haqiqiy Bill topshiriqlari bo'lishi mumkin — faqat SHU chekniki sanaladi.
			const mine = jobs.filter((j) => j.ref_name === invoice);
			h.check("Bill topshirig'i navbatda (Pending, chek nomi bilan)", mine.length === 1 && mine[0].status === "Pending", JSON.stringify(jobs));
			h.check("panel «Hisob berilgan»", (await h.text(".rc-panel")).includes("Hisob berilgan"));
		});
		await h.step("to'lov oynasi: usullar ro'yxati + choychaqa + naqd/karta", async () => {
			await h.click('.rc-panel [data-action="pay"]');
			await h.waitFor(`document.querySelector(".rc-modal .rc-pay")`, 8000, "to'lov oynasi");
			h.eq(digits(await h.text(".rc-pay__due-value")), "168000", "to'lanadi = server bill.payable (168 000)");

			// Aralash to'lov tugmasi va «Usul qo'shish» yo'q: POS Profile'dagi HAR BIR usul o'z qatori bilan.
			h.check("«Aralash to'lov» / «Usul qo'shish» tugmalari yo'q", !(await h.eval(`!!document.querySelector(".rc-modal .rc-payment-split, .rc-modal .rc-mode")`)));
			const names = await h.allText(".rc-modal .rc-pay__name");
			h.check("POS Profile'dagi barcha usullar qator bo'lib chiqdi (naqd + karta)", names.includes(CASH) && names.includes(CARD), JSON.stringify(names));
			const inputModes = await h.eval(`[...document.querySelectorAll(".rc-modal .rc-pay__input")].map(i => i.dataset.mode)`);
			h.eq(inputModes, names, "har bir usul nomining o'ngida summa maydoni");
			h.eq(await h.eval(`document.querySelector('.rc-modal [data-action="confirm"]').disabled`), true, "summa kiritilmaguncha tasdiqlash o'chiq");

			await h.click(".rc-modal .rc-chip-opt", "10%");
			await h.waitIdle(400);
			h.eq(digits(await h.text(".rc-pay__due-value")), "183000", "choychaqa 10% (15 000) qo'shilib to'lanadigan 183 000");

			// Karta qatoriga 84 000 yoziladi; yetishmaydi -> tasdiqlash o'chiq.
			await h.type(`.rc-pay__input[data-mode="${CARD}"]`, "84000", { scope: ".rc-modal" });
			await h.waitIdle(200);
			h.eq(await h.eval(`document.querySelector('.rc-modal [data-action="confirm"]').disabled`), true, "yig'indi yetmaganda tasdiqlash o'chiq");
			h.eq(digits(await h.text(".rc-pay__change-value")), "99000", "yetishmaydigan summa 99 000");

			// Naqd nomini bosish — qoldiq (99 000) naqd qatoriga yoziladi.
			await h.click(".rc-modal .rc-pay__name", CASH, { exact: true });
			await h.waitIdle(300);
			const values = await h.eval(`Object.fromEntries([...document.querySelectorAll(".rc-modal .rc-pay__input")].map(i => [i.dataset.mode, i.value.replace(/\\D/g, "")]))`);
			h.eq([values[CASH], values[CARD]], ["99000", "84000"], "naqd qatoriga qoldiq yozildi");
			h.eq(await h.eval(`document.querySelector('.rc-modal [data-action="confirm"]').disabled`), false, "yig'indi to'liq — tasdiqlash yoqildi");
			await h.shot("c_ready");
		});
		await h.step("to'lov tasdiqlanadi -> DB, g'aladon, stol bo'sh", async () => {
			await h.click('.rc-modal [data-action="confirm"]');
			await h.waitFor(`!document.querySelector(".rc-modal .rc-pay") && ozturk.cashier.screen.state === "ready"`, 15000, "to'lov oynasi yopildi");
			await h.waitIdle(600);
			const inv = await h.op("invoice", { name: invoice });
			h.eq(inv.docstatus, 1, "chek to'landi (submitted)");
			h.eq(inv.payments.map((p) => `${p.mode}=${p.amount}`).sort(), ["Test Karta=84000", "Нахт=99000"].sort(), "to'lov qatorlari (naqd + karta)");
			const tip = inv.taxes.find((t) => /Choychaqa|Tip/i.test(t.desc || t.account));
			h.check("choychaqa soliq qatori (Actual) 15 000", !!tip && Math.round(tip.amount) === 15000 && tip.type === "Actual", JSON.stringify(inv.taxes));
			h.near(inv.grand_total, 183000, "grand_total choychaqa bilan");
			h.near(inv.paid_amount, 183000, "paid_amount");
			h.near(inv.change_amount, 0, "qaytim yo'q");
			const table = await h.one("select occupied from `tabURY Table` where name = 'E2E-T1'");
			h.eq(Number(table.occupied), 0, "stol bo'shadi");
			const drawer = await printJobs(h, "Drawer");
			h.check("g'aladon topshirig'i (naqd qismi bor)", drawer.length === 1 && drawer[0].ref_name === invoice, JSON.stringify(drawer));
			const tile = await h.eval(`document.querySelector('.rc-table[data-table="E2E-T1"]').className`);
			h.check("stol tugmasi BO'SH holatga qaytdi", /AVAILABLE/.test(tile), tile);
		});
	},
};


scenarios.d = {
	title: "Chegirma: chegaradan oshsa -> ApprovalRequired -> PIN (noto'g'ri, keyin to'g'ri) -> qayta hisob, chop etish belgisi",
	async run(h) {
		let invoice;
		await h.step("buyurtma + hisob berilgan", async () => {
			invoice = await newOrder(h, { table: "E2E-T2", items: [{ item: "AFGAN BREAD", qty: 2 }, { item: "BARDAK TEA", qty: 2 }] });
			await selectTable(h, "E2E-T2");
			await giveBill(h);
		});
		await h.step("chegara ichidagi 10% — PIN so'ralmaydi", async () => {
			await moreAction(h, "Chegirma");
			await h.waitDialog("Chegirma turi");
			await h.click(".rc-chip-opt", "10%", { scope: ".rc-dialog", last: true });
			await h.click(".rc-chip-opt", "Aksiya", { scope: ".rc-dialog", last: true });
			await h.clickDialog('[data-action="apply"]');
			await h.waitFor(`document.querySelector(".rc-dialog") && document.querySelector(".rc-dialog").innerText.toLowerCase().includes("chek o'zgardi")`, 10000, "qayta chop etish taklifi");
			await h.clickDialog('[data-action="no"]', "Keyinroq");
			await h.waitNoDialog();
			const inv = await h.op("invoice", { name: invoice });
			h.near(inv.discount_percent, 10, "additional_discount_percentage = 10");
			h.near(inv.discount_amount, 15000, "discount_amount = 10% * 150 000");
			h.eq(Number(inv.reprint_needed), 1, "custom_reprint_needed = 1 (hisob allaqachon berilgan)");
			const service = inv.taxes.find((t) => /Xizmat|Service/i.test(t.desc || t.account));
			h.near(service.amount, 16200, "xizmat haqi chegirmadan keyingi summadan: 12% * 135 000");
			h.near(inv.grand_total, 151200, "grand_total = 135 000 + 16 200");
			const panel = (await h.text(".rc-panel")).replace(/\s/g, "");
			h.check("panel serverdagi payable (151 200) ni ko'rsatadi", panel.includes("151200"), panel.slice(0, 300));
			h.check("«Chek o'zgardi» eslatmasi ko'rinadi", (await h.text(".rc-panel")).includes("Chek o'zgardi"));
		});
		await h.step("15% — chegaradan oshdi -> PIN oynasi, noto'g'ri PIN", async () => {
			h.expect("billing.apply_discount", 403, "ApprovalRequired");
			await moreAction(h, "Chegirmani o'zgartirish");
			await h.waitDialog("Chegirma turi");
			await h.click(".rc-chip-opt", "15%", { scope: ".rc-dialog", last: true });
			await h.click(".rc-chip-opt", "Aksiya", { scope: ".rc-dialog", last: true });
			await h.clickDialog('[data-action="apply"]');
			await h.waitFor(`document.querySelector(".rc-dialog .rc-pin")`, 10000, "PIN oynasi ochildi");
			h.check("PIN oynasi menejer nomini ko'rsatadi", (await h.dialogText()).includes("e2e-manager"));
			h.expect("billing.apply_discount", 403, "ApprovalRequired");
			await enterPin(h, "1111");
			await h.waitFor(`document.querySelector(".rc-dialog .rc-pin__hint") && document.querySelector(".rc-dialog .rc-pin__hint").innerText.toLowerCase().includes("noto'g'ri")`, 10000, "noto'g'ri PIN xabari");
			const inv = await h.op("invoice", { name: invoice });
			h.near(inv.discount_percent, 10, "noto'g'ri PIN: chegirma o'zgarmagan (10%)");
		});
		await h.step("to'g'ri PIN — chegirma qo'yiladi, qayta hisoblanadi", async () => {
			await enterPin(h, "4321");
			await h.waitFor(`!document.querySelector(".rc-dialog .rc-pin")`, 10000, "PIN oynasi yopildi");
			await h.waitFor(`document.querySelector(".rc-dialog") && document.querySelector(".rc-dialog").innerText.toLowerCase().includes("chek o'zgardi")`, 10000, "qayta chop etish taklifi");
			await h.clickDialog('[data-action="no"]', "Keyinroq");
			await h.waitNoDialog();
			const inv = await h.op("invoice", { name: invoice });
			h.near(inv.discount_percent, 15, "additional_discount_percentage = 15");
			h.near(inv.discount_amount, 22500, "discount_amount = 15% * 150 000");
			const service = inv.taxes.find((t) => /Xizmat|Service/i.test(t.desc || t.account));
			h.near(service.amount, 15300, "xizmat haqi = 12% * 127 500");
			h.near(inv.grand_total, 142800, "grand_total = 127 500 + 15 300");
			const meta = await h.one("select custom_discount_reason r, custom_discount_approved_by a from `tabPOS Invoice` where name = %s", [invoice]);
			h.check("chegirma sababi va tasdiqlagan menejer chekka yozilgan", meta.r === "Aksiya" && meta.a === MANAGER, JSON.stringify(meta));
			const comment = await h.one("select count(*) c from `tabComment` where reference_doctype = 'POS Invoice' and reference_name = %s and content like %s", [invoice, "%Chegirma%"]);
			h.check("tasdiq izi hujjat tarixiga (Comment) yozilgan", Number(comment.c) >= 1, JSON.stringify(comment));
			const panel = (await h.text(".rc-panel")).replace(/\s/g, "");
			h.check("panel yangi payable (142 800) ni ko'rsatadi", panel.includes("142800"));
		});
		await h.step("chop etish belgisi -> qayta chop etish", async () => {
			h.check("panelda «Chek o'zgardi» belgisi", (await h.text(".rc-panel")).includes("Chek o'zgardi"));
			await h.click(".rc-panel .rc-payment-notice__btn", "Chop etish");
			await h.waitFor(`!ozturk.cashier.screen.detail.bill.reprint_needed`, 10000, "reprint_needed o'chdi");
			const inv = await h.op("invoice", { name: invoice });
			h.eq(Number(inv.reprint_needed || 0), 0, "custom_reprint_needed = 0");
			const jobs = await printJobs(h, "Bill");
			h.check("ikkinchi Bill topshirig'i navbatda", jobs.filter((j) => j.ref_name === invoice).length === 2, JSON.stringify(jobs));
		});
		await h.step("chegirma chegarasi: API chegara qiymatlari", async () => {
			const r10 = await h.apiAs(CASHIER, "billing.apply_discount", { invoice, percent: 10, reason: "Aksiya" });
			h.eq(r10.status, 200, "10% (aynan chegara) — PIN'siz o'tadi");
			h.expect("billing.apply_discount", 403, "ApprovalRequired");
			const r11 = await h.apiAs(CASHIER, "billing.apply_discount", { invoice, percent: 10.01, reason: "Aksiya" });
			h.eq([r11.status, r11.excType], [403, "ApprovalRequired"], "10.01% — PIN so'raydi");
			h.expect("billing.apply_discount", 417, "ValidationError");
			const r0 = await h.apiAs(CASHIER, "billing.apply_discount", { invoice, percent: 0, reason: "Aksiya" });
			h.eq(r0.status, 417, "0% rad etiladi");
			h.expect("billing.apply_discount", 417, "ValidationError");
			const r100 = await h.apiAs(CASHIER, "billing.apply_discount", { invoice, percent: 100, reason: "Aksiya" });
			h.eq(r100.status, 417, "100% rad etiladi");
			h.expect("billing.apply_discount", 417, "ValidationError");
			const rNoReason = await h.apiAs(CASHIER, "billing.apply_discount", { invoice, percent: 5 });
			h.eq(rNoReason.status, 417, "sababsiz chegirma rad etiladi");
			h.expect("billing.apply_discount", 403, "ApprovalRequired");
			const rBadPin = await h.apiAs(CASHIER, "billing.apply_discount", { invoice, percent: 30, reason: "Aksiya", approval: { user: MANAGER, pin: "0000" } });
			h.eq([rBadPin.status, rBadPin.excType], [403, "ApprovalRequired"], "noto'g'ri PIN 403");
			const rCash = await h.apiAs(CASHIER, "billing.remove_discount", { invoice });
			h.eq(rCash.status, 200, "chegirma olib tashlash");
		});
	},
};


scenarios.e = {
	title: "Qaytarish (tarixdan): allow_in_returns ogohlantirishi, menejer PIN, qisman va qolgan qismi",
	async run(h) {
		let invoice;
		await h.step("to'langan chek tayyorlanadi (API) va tarix ochiladi", async () => {
			invoice = await newOrder(h, { table: "E2E-T3", items: [{ item: "AFGAN BREAD", qty: 2 }, { item: "BARDAK TEA", qty: 2 }] });
			const due = await payable(h, invoice);
			await payViaApi(h, invoice, [{ mode_of_payment: CASH, amount: due }]);
			await openHistory(h);
			await historyDetail(h, invoice);
			const actions = await h.allText(".rc-history__detail [data-history-action]");
			h.check("tarix tafsilotida «Qaytarish» tugmasi bor (refunds yoqiq)", actions.some((a) => /Qaytarish/i.test(a)), JSON.stringify(actions));
		});
		await h.step("allow_in_returns=0 usul: ogohlantirish + tugma o'chiq + server rad etadi", async () => {
			await h.click('.rc-history__detail [data-history-action="payment-refund"]');
			await h.waitDialog("Chekni qaytarish");
			h.check("«Allow In Returns» ogohlantirishi ko'rinadi", /Allow In Returns/i.test(await h.dialogText()), (await h.dialogText()).slice(0, 300));
			await h.click(".rc-payment-refund__row [data-dir='1']", null, { scope: ".rc-dialog", last: true });
			h.eq(await h.eval(`[...document.querySelectorAll('.rc-dialog [data-action="refund"]')].pop().disabled`), true, "«Qaytarish» tugmasi o'chiq (usul ruxsat etilmagan)");
			// Server ham (menejer PIN bilan) rad etadi
			const info = await h.apiAs(CASHIER, "billing.get_refundable", { invoice }, { http: "GET" });
			const row = info.message.items[0];
			h.expect("billing.refund_invoice", 417, "ValidationError");
			const r = await h.apiAs(CASHIER, "billing.refund_invoice", { invoice, items: [{ name: row.name, qty: 1 }], reason: "Mijoz e'tirozi", approval: { user: MANAGER, pin: PIN } });
			h.check("server allow_in_returns=0 bo'lsa aniq xato beradi", r.status === 417 && /Allow In Returns|Qaytarishda ruxsat|ruxsat/i.test(JSON.stringify(r.data)), `${r.status} ${JSON.stringify(r.data).slice(0, 300)}`);
			await h.clickDialog('[data-action="cancel"]');
			await h.waitNoDialog();
		});
		await h.step("usul ruxsat etiladi -> qisman qaytarish (1 dona) menejer PIN bilan", async () => {
			await h.op("allow_in_returns", { mode: CASH, allowed: 1 });
			await h.click('.rc-history__detail [data-history-action="payment-refund"]');
			await h.waitDialog("Chekni qaytarish");
			h.check("ogohlantirish endi yo'q", !/Allow In Returns/i.test(await h.dialogText()));
			await h.click(".rc-payment-refund__row[data-name] [data-dir='1']", null, { scope: ".rc-dialog", last: true });
			h.expect("billing.refund_invoice", 403, "ApprovalRequired");
			await h.click(".rc-chip-opt", "Mijoz e'tirozi", { scope: ".rc-dialog", last: true });
			await h.clickDialog('[data-action="refund"]');
			await h.waitFor(`document.querySelector(".rc-dialog .rc-pin")`, 10000, "PIN oynasi");
			await enterPin(h, "4321");
			await h.waitFor(`document.querySelector(".rc-history__row .rc-return-badge")`, 12000, "tarixda qaytarish cheki paydo bo'ldi");
			const returns = await h.sql("select name, grand_total, rounded_total, is_return, return_against, docstatus from `tabPOS Invoice` where return_against = %s", [invoice]);
			h.eq(returns.length, 1, "1 ta qaytarish cheki");
			h.check("qaytarish cheki submit qilingan, manfiy summali", returns[0].docstatus === 1 && Number(returns[0].grand_total) < 0, JSON.stringify(returns));
			const items = await h.sql("select item_code, qty from `tabPOS Invoice Item` where parent = %s", [returns[0].name]);
			h.eq(items.map((i) => `${i.item_code}:${Number(i.qty)}`), ["AFGAN BREAD:-1"], "qaytarilgan qator (1 dona)");
			const posPays = await h.sql("select mode_of_payment, amount from `tabSales Invoice Payment` where parent = %s", [returns[0].name]);
			h.check("qaytarish cheki to'lov qatori Нахт, manfiy (pul naqd qaytadi)", posPays.length === 1 && posPays[0].mode_of_payment === CASH && Number(posPays[0].amount) < 0, JSON.stringify(posPays));
			h.near(Number(returns[0].grand_total), -67200, "qaytarilgan summa = -(60 000 + 12%)");
			const audit = await h.one("select count(*) c from `tabComment` where reference_doctype = 'POS Invoice' and reference_name = %s and content like %s", [invoice, "%Qaytarish%"]);
			h.check("tasdiq izi asl chek tarixida", Number(audit.c) >= 1, JSON.stringify(audit));
		});
		await h.step("qolgan qismini qaytarish (Hammasini tanlash)", async () => {
			await historyDetail(h, invoice);
			await h.click('.rc-history__detail [data-history-action="payment-refund"]');
			await h.waitDialog("Chekni qaytarish");
			await h.click('.rc-dialog [data-part="all"]', null, { last: true });
			await h.click(".rc-chip-opt", "Buyurtma xatosi", { scope: ".rc-dialog", last: true });
			h.expect("billing.refund_invoice", 403, "ApprovalRequired");
			await h.clickDialog('[data-action="refund"]');
			await h.waitFor(`document.querySelector(".rc-dialog .rc-pin")`, 10000, "PIN oynasi");
			await enterPin(h, "4321");
			await h.waitFor(`document.querySelectorAll(".rc-history__row .rc-return-badge").length >= 2`, 12000, "ikkinchi qaytarish cheki");
			const sum = await h.one("select coalesce(sum(grand_total), 0) t, count(*) c from `tabPOS Invoice` where return_against = %s and docstatus = 1", [invoice]);
			h.near(Number(sum.t), -168000, "qaytarishlar yig'indisi = -asl chek jami (168 000)");
			const refundable = await h.apiAs(CASHIER, "billing.get_refundable", { invoice }, { http: "GET" });
			h.check("qaytariladigan qoldiq yo'q", (refundable.message.items || []).every((i) => Number(i.refundable_qty) === 0), JSON.stringify(refundable.message.items));
			await h.click('.rc-modal .rc-modal__close');
		});
	},
};


scenarios.f = {
	title: "Stol ko'chirish + birlashtirish + ajratish (KOT va stol bandligi izchilligi)",
	async run(h) {
		let invoice;
		const tables = () => h.sql("select name, occupied, merged_with from `tabURY Table` where name like %s order by name", ["E2E-T%"]);
		const tableRow = async (name) => (await tables()).find((t) => t.name === name);
		const kotTables = async () => (await h.sql("select distinct restaurant_table t from `tabURY KOT` where invoice = %s", [invoice])).map((r) => r.t);
		await h.step("buyurtma + hisob berilgan", async () => {
			invoice = await newOrder(h, { table: "E2E-T1", items: [{ item: "AFGAN BREAD", qty: 1 }, { item: "COCA-COLA", qty: 2 }] });
			await selectTable(h, "E2E-T1");
			await giveBill(h);
		});
		await h.step("ko'chirish E2E-T1 -> E2E-T3", async () => {
			await moreAction(h, "Stolni ko'chirish");
			await h.waitDialog("Stolni ko'chirish");
			await h.click(".rc-tt__tile", "E2E-T3", { scope: ".rc-dialog", last: true });
			await h.clickDialog('[data-action="submit"]', "E2E-T3 ga ko'chirish");
			await h.waitFor(`[...document.querySelectorAll(".rc-dialog")].some(d => d.innerText.toLowerCase().includes("hisobni qayta chop etish"))`, 10000, "qayta chop etish taklifi (hisob berilgan edi)");
			await h.clickDialog('[data-action="no"]', "Keyinroq");
			await h.waitNoDialog();
			const inv = await h.op("invoice", { name: invoice });
			h.eq(inv.table, "E2E-T3", "chek stoli = E2E-T3");
			h.eq(Number((await tableRow("E2E-T1")).occupied), 0, "eski stol bo'shadi");
			h.eq(Number((await tableRow("E2E-T3")).occupied), 1, "yangi stol band");
			h.eq(await kotTables(), ["E2E-T3"], "barcha KOT lar yangi stolga o'tgan");
			const t1 = await h.eval(`document.querySelector('.rc-table[data-table="E2E-T1"]').className`);
			const t3 = await h.eval(`document.querySelector('.rc-table[data-table="E2E-T3"]').className`);
			h.check("zal rejasida T1 BO'SH, T3 BAND", /AVAILABLE/.test(t1) && /OCCUPIED/.test(t3), `${t1} | ${t3}`);
		});
		await h.step("birlashtirish E2E-T3 + E2E-T1 + E2E-T2", async () => {
			await selectTable(h, "E2E-T3");
			await moreAction(h, "Stollarni birlashtirish");
			await h.waitDialog("Stollarni birlashtirish");
			await h.click(".rc-tt__tile", "E2E-T1", { scope: ".rc-dialog", last: true });
			await h.click(".rc-tt__tile", "E2E-T2", { scope: ".rc-dialog", last: true });
			await h.clickDialog('[data-action="submit"]', "Birlashtirish (2)");
			await h.waitNoDialogAny();
			await h.waitIdle(500);
			const inv = await h.op("invoice", { name: invoice });
			const rows = await tables();
			h.check("chekda birlashgan stollar yozilgan", /E2E-T1/.test(inv.merged || "") && /E2E-T2/.test(inv.merged || ""), inv.merged);
			h.eq(rows.filter((r) => Number(r.occupied) === 1).map((r) => r.name).sort(), ["E2E-T1", "E2E-T2", "E2E-T3"], "uchala stol band");
			h.eq(await kotTables(), ["E2E-T3"], "KOT lar asosiy stolda qoladi");
		});
		await h.step("birlashgan stolda ko'chirish o'chiq", async () => {
			await h.click('.rc-panel [data-action="panel-more"]');
			await h.waitDialog("Yana amallar");
			const item = await h.eval(`(() => { const b = [...document.querySelectorAll('.rc-dialog .rc-more__item[data-id="table-transfer"]')].pop(); return b ? {disabled: b.disabled, text: b.innerText.replace(/\\s+/g, " ")} : null; })()`);
			h.check("«Stolni ko'chirish» o'chirilgan va sababi aytilgan", item && item.disabled && /ajrating/i.test(item.text), JSON.stringify(item));
			await h.clickDialog('[data-action="cancel"], .rc-dialog__x', null, { allowDisabled: true }).catch(() => h.key("Escape"));
			await h.waitNoDialogAny();
			h.expect("table.transfer_table", 417, "ValidationError");
			const r = await h.apiAs(CASHIER, "table.transfer_table", { invoice, to_table: "Table-5" });
			h.check("server ham birlashgan chekni ko'chirishni rad etadi", r.status >= 400, `${r.status} ${JSON.stringify(r.data).slice(0, 200)}`);
		});
		await h.step("ajratish E2E-T2, keyin E2E-T1", async () => {
			await moreAction(h, "Ajratish");
			await h.waitDialog("Stolni ajratish");
			await h.click(".rc-tt__tile", "E2E-T2", { scope: ".rc-dialog", last: true });
			await h.clickDialog('[data-action="submit"]', "E2E-T2 ni ajratish");
			await h.waitNoDialogAny();
			await h.waitIdle(400);
			h.eq(Number((await tableRow("E2E-T2")).occupied), 0, "E2E-T2 bo'shadi");
			h.eq(Number((await tableRow("E2E-T1")).occupied), 1, "E2E-T1 hali birlashgan");
			await moreAction(h, "Ajratish");
			await h.waitDialog("Stolni ajratish");
			await h.click(".rc-tt__tile", "E2E-T1", { scope: ".rc-dialog", last: true });
			await h.clickDialog('[data-action="submit"]', "E2E-T1 ni ajratish");
			await h.waitNoDialogAny();
			await h.waitIdle(400);
			const rows = await tables();
			h.eq(rows.filter((r) => Number(r.occupied) === 1).map((r) => r.name), ["E2E-T3"], "faqat asosiy stol band");
			const inv = await h.op("invoice", { name: invoice });
			h.check("chekda birlashgan stollar qolmagan", !inv.merged, String(inv.merged));
			h.eq(await kotTables(), ["E2E-T3"], "KOT lar izchil");
		});
		await h.step("birlashgan chek to'lanadi -> barcha stollar bo'shaydi", async () => {
			await moreAction(h, "Stollarni birlashtirish");
			await h.waitDialog("Stollarni birlashtirish");
			await h.click(".rc-tt__tile", "E2E-T2", { scope: ".rc-dialog", last: true });
			await h.clickDialog('[data-action="submit"]', "Birlashtirish (1)");
			await h.waitNoDialogAny();
			await h.waitIdle(500);
			h.eq((await tables()).filter((r) => Number(r.occupied) === 1).map((r) => r.name).sort(), ["E2E-T2", "E2E-T3"], "T3 + T2 band");
			const paidResult = await payViaApi(h, invoice, [{ mode_of_payment: CASH, amount: await payable(h, invoice) }]);
			h.check("javobda bo'shagan stollar ro'yxati", JSON.stringify(paidResult).includes("E2E-T"), JSON.stringify(paidResult).slice(0, 300));
			await h.waitIdle(500);
			const rows = await tables();
			h.eq(rows.filter((r) => Number(r.occupied) === 1).length, 0, "to'lovdan keyin hamma stol bo'sh");
			h.check("merged_with tozalangan", rows.every((r) => !r.merged_with), JSON.stringify(rows));
		});
		await h.step("bandlik: band stolga ko'chirish rad etiladi", async () => {
			invoice = await newOrder(h, { table: "E2E-T3", items: [{ item: "AFGAN BREAD", qty: 1 }] });
			const other = await newOrder(h, { table: "E2E-T1", items: [{ item: "AYRAN 0,5L", qty: 1 }] });
			h.expect("table.transfer_table", 417, "ValidationError");
			const r = await h.apiAs(CASHIER, "table.transfer_table", { invoice, to_table: "E2E-T1" });
			h.check("band stolga ko'chirib bo'lmaydi", r.status >= 400, `${r.status}`);
			const inv = await h.op("invoice", { name: invoice });
			h.eq(inv.table, "E2E-T3", "chek joyida");
			h.eq(await kotTables(), ["E2E-T3"], "KOT lar joyida");
		});
	},
};


const openNewOrder = async (h) => {
	await h.click(".rc-topquick__btn", "Buyurtma");
	await h.waitFor(`document.querySelector(".rc-orders-dlg .rc-orders-card")`, 10000, "yangi buyurtma oynasi");
};
const addCards = async (h, items) => {
	for (const [item, times] of items) for (let i = 0; i < times; i++) await h.click(`.rc-orders-card[data-item="${item}"]`, null, { settle: 80 });
};
const submitOrder = async (h) => {
	await h.click('.rc-orders-dlg [data-action="submit"]');
	await h.waitFor(`!document.querySelector(".rc-orders-dlg")`, 15000, "buyurtma yuborildi");
	await h.waitFor(`ozturk.cashier.screen.selectedInvoice`, 10000, "chek tanlandi");
	await h.waitIdle(300);
	return h.eval(`ozturk.cashier.screen.selectedInvoice`);
};

scenarios.g = {
	title: "Olib ketish va yetkazib berish buyurtmasi + mijoz yaratish/biriktirish + to'lov",
	async run(h) {
		let takeaway, delivery;
		await h.step("olib ketish buyurtmasi (interfeys)", async () => {
			await openNewOrder(h);
			await h.click(".rc-orders-type", "Olib ketish");
			h.check("stol tanlash yo'q (olib ketish)", !(await h.eval(`!!document.querySelector('.rc-orders-dlg [data-act="table"]')`)));
			await addCards(h, [["COCA-COLA", 2], ["PEPSI", 1]]);
			takeaway = await submitOrder(h);
			const inv = await h.op("invoice", { name: takeaway });
			h.eq([inv.order_type, inv.table], ["Take Away", null], "Take Away, stolsiz");
			h.near(inv.net_total, 60000, "net_total 3 x 20 000");
			await openOrdersList(h);
			await h.waitFor(`document.querySelector('.rc-order[data-invoice="${takeaway}"]')`, 8000, "buyurtmalar ro'yxatida");
			h.check("«Olib ketish / Yetkazish» yorlig'i bor", (await h.allText(".rc-orders__tabs button")).some((t) => /Olib ketish/i.test(t)), JSON.stringify(await h.allText(".rc-orders__tabs button")));
		});
		await h.step("yetkazib berish: telefon/manzil majburiy", async () => {
			await openNewOrder(h);
			await h.click(".rc-orders-type", "Yetkazib berish");
			await addCards(h, [["LAHMACUN", 2]]);
			await h.click('.rc-orders-dlg [data-action="submit"]', null, { allowDisabled: true, settle: 300 });
			const dlgOpen = await h.eval(`!!document.querySelector(".rc-orders-dlg")`);
			h.check("telefon/manzilsiz yuborilmaydi (oyna ochiq)", dlgOpen);
		});
		await h.step("yangi mijoz yaratiladi va biriktiriladi", async () => {
			await h.type(".rc-orders-phone", "+998901112233");
			await h.type(".rc-orders-address", "Toshkent, Amir Temur 1");
			await h.click('.rc-orders-dlg [data-act="customer"]');
			await h.waitDialog("Mijoz");
			await h.clickDialog('[data-action="new"], [data-action="create"], .rc-btn', "Yangi mijoz");
			await h.waitDialog("Yangi mijoz");
			await h.typeField("Ism", "E2E Mijoz");
			await h.typeField("Telefon", "+998901112233");
			await h.typeField("Manzil", "Toshkent, Amir Temur 1");
			await h.clickDialog('[data-action="submit"]', "Saqlash");
			await h.waitNoDialogTop();
			const cust = await h.one("select name, customer_name, mobile_no from `tabCustomer` where customer_name = %s", ["E2E Mijoz"]);
			h.check("Customer yaratildi", !!cust, JSON.stringify(cust));
			h.check("buyurtma oynasida mijoz ko'rinadi", (await h.text(".rc-orders-setup")).includes("E2E Mijoz"), await h.text(".rc-orders-setup"));
			delivery = await submitOrder(h);
			const inv = await h.op("invoice", { name: delivery });
			h.eq([inv.order_type, inv.customer], ["Delivery", cust.name], "Delivery + biriktirilgan mijoz");
			const d = await h.one("select custom_delivery_phone p, custom_delivery_address a from `tabPOS Invoice` where name = %s", [delivery]);
			h.eq([d.p, d.a], ["+998901112233", "Toshkent, Amir Temur 1"], "yetkazib berish ma'lumoti chekka yozilgan");
			const panel = await h.text(".rc-panel");
			h.check("panelda yetkazib berish telefoni va manzili ko'rinadi", /998901112233/.test(panel.replace(/\s/g, "")) && /Amir Temur/.test(panel), panel.slice(0, 300));
		});
		await h.step("yetkazib berish buyurtmasi to'lanadi (naqd, qaytim bilan)", async () => {
			await giveBill(h);
			await h.click('.rc-panel [data-action="pay"]');
			await h.waitFor(`document.querySelector(".rc-modal .rc-pay")`, 8000, "to'lov oynasi");
			const due = digits(await h.text(".rc-pay__due-value"));
			h.eq(due, String(await payable(h, delivery)), "to'lanadi = server rounded_total");
			await h.type(`.rc-pay__input[data-mode="${CASH}"]`, "300000", { scope: ".rc-modal" });
			await h.waitIdle(200);
			h.check("qaytim ko'rsatilgan", digits(await h.text(".rc-pay__change-value")) === String(300000 - Number(due)), await h.text(".rc-pay__change-value"));
			await h.click('.rc-modal [data-action="confirm"]');
			await h.waitFor(`[...document.querySelectorAll(".rc-dialog")].some(d => d.innerText.toLowerCase().includes("qaytim"))`, 15000, "qaytim oynasi");
			h.check("qaytim oynasi to'g'ri summa ko'rsatadi", digits(await h.dialogText()).includes(String(300000 - Number(due))), await h.dialogText());
			await h.clickDialog('[data-action="ok"]');
			await h.waitNoDialogAny();
			const inv = await h.op("invoice", { name: delivery });
			h.eq(inv.docstatus, 1, "to'landi");
			h.near(inv.change_amount, 300000 - Number(due), "change_amount");
		});
		await h.step("olib ketish buyurtmasiga mijoz biriktirish (panel: Yana -> Mijoz)", async () => {
			await selectOrder(h, takeaway);
			await moreAction(h, "Mijoz");
			await h.waitDialog("Mijoz");
			await h.waitFor(`document.querySelector(".rc-dialog .rc-orders-cust__row")`, 8000, "mijozlar ro'yxati");
			await h.click(".rc-orders-cust__row", "E2E Mijoz", { scope: ".rc-dialog", last: true });
			await h.waitNoDialogAny();
			await h.waitIdle(400);
			const inv = await h.op("invoice", { name: takeaway });
			const cust = await h.one("select name from `tabCustomer` where customer_name = %s", ["E2E Mijoz"]);
			h.eq(inv.customer, cust.name, "mijoz mavjud buyurtmaga biriktirildi");
		});
	},
};


scenarios.h = {
	title: "Bron: yaratish -> ro'yxat -> vaqt formati -> bekor qilish",
	async run(h) {
		const reservations = () => h.sql("select name, `table`, status, from_time, reservation_date, customer_name, pax from `tabURY Table Reservation` where customer_name like %s order by creation", ["E2E%"]);
		await h.step("bronlar oynasi (menyu)", async () => {
			await h.click(".rc-menu-btn");
			await h.click(".rc-menu button, .rc-menu [data-id]", "Bronlar");
			await h.waitDialog("Bronlar");
		});
		await h.step("ertangi kunga 09:15 ga bron", async () => {
			await h.clickDialog('[data-action="new"], .rc-btn', "Yangi bron");
			await h.waitDialog("Yangi bron");
			await h.click(".rc-tt__tile", "E2E-T2", { scope: ".rc-dialog", last: true });
			await h.click(".rc-field--select .rc-chip-opt", null, { scope: ".rc-dialog", last: true, index: 1 });
			await h.click(".rc-field--time .rc-chipset:nth-child(1) .rc-chip-opt", "09", { scope: ".rc-dialog", last: true, exact: true });
			await h.click(".rc-field--time .rc-chipset:nth-child(2) .rc-chip-opt", "15", { scope: ".rc-dialog", last: true, exact: true });
			await h.typeField("Mehmon ismi", "E2E Bron Ertaga");
			await h.typeField("Telefon", "+998907778899");
			await h.clickDialog('[data-action="submit"]', "Bron qilish");
			await h.waitDialogCount(1);
			const rows = await reservations();
			h.eq(rows.length, 1, "1 ta bron yaratildi");
			h.eq(String(rows[0].from_time), "9:15:00", "bazada vaqt (timedelta ko'rinishi: 9:15:00)");
			h.check("ertangi sana", String(rows[0].reservation_date) > new Date().toISOString().slice(0, 10), String(rows[0].reservation_date));
		});
		await h.step("ro'yxatda vaqt 09:15 ko'rinadi (bir xonali soat to'ldiriladi)", async () => {
			await h.click(".rc-tres__days button, .rc-tres__days [data-day]", null, { scope: ".rc-dialog", last: true, index: 1 });
			await h.waitFor(`document.querySelector(".rc-dialog .rc-tres__row")`, 8000, "bron qatori");
			const time = await h.text(".rc-dialog .rc-tres__time strong", { last: true });
			h.eq(time, "09:15", "ro'yxatdagi vaqt formati");
			const rowText = await h.text(".rc-dialog .rc-tres__row", { last: true });
			h.check("mehmon va stol ko'rinadi", /E2E Bron Ertaga/.test(rowText) && /E2E-T2/.test(rowText), rowText);
		});
		await h.step("bugungi bron (keyingi slot) -> stol BRON holatida, ⏰ belgisi", async () => {
			await h.clickDialog('[data-action="new"], .rc-btn', "Yangi bron");
			await h.waitDialog("Yangi bron");
			await h.click(".rc-tt__tile", "E2E-T3", { scope: ".rc-dialog", last: true });
			await h.click(".rc-field--select .rc-chip-opt", "Bugun", { scope: ".rc-dialog", last: true });
			await h.typeField("Mehmon ismi", "E2E Bron Bugun");
			await h.clickDialog('[data-action="submit"]', "Bron qilish");
			await h.waitDialogCount(1);
			await h.waitIdle(800);
			const rows = await reservations();
			const today = rows.find((r) => r.customer_name === "E2E Bron Bugun");
			h.check("bugungi bron yaratildi", !!today, JSON.stringify(rows));
			h.check("bugungi sana", String(today.reservation_date) === (await h.one("select curdate() d")).d, String(today.reservation_date));
			await h.clickDialog('[data-action="close"]', "Yopish");
			await h.waitNoDialogAny();
			await h.waitFor(`/RESERVED/.test(document.querySelector('.rc-table[data-table="E2E-T3"]').className)`, 8000, "E2E-T3 BRON holatida");
			const tile = await h.text('.rc-table[data-table="E2E-T3"]');
			h.check("⏰ belgisi (boshlanishiga <60 daq)", tile.includes("⏰"), tile);
		});
		await h.step("bronni bekor qilish (ro'yxatdan)", async () => {
			await h.click(".rc-menu-btn");
			await h.click(".rc-menu button, .rc-menu [data-id]", "Bronlar");
			await h.waitDialog("Bronlar");
			await h.waitFor(`document.querySelector(".rc-dialog .rc-tres__cancel")`, 8000, "bekor qilish tugmasi");
			await h.click(".rc-tres__cancel", null, { scope: ".rc-dialog", last: true });
			await h.waitDialog("Bronni bekor qilish");
			await h.clickDialog('[data-action="yes"]');
			await h.waitIdle(600);
			const rows = await reservations();
			h.check("bugungi bron Cancelled", rows.some((r) => r.customer_name === "E2E Bron Bugun" && r.status === "Cancelled"), JSON.stringify(rows.map((r) => [r.customer_name, r.status])));
			await h.key("Escape");
			await h.waitNoDialogAny();
			await h.waitFor(`!/RESERVED/.test(document.querySelector('.rc-table[data-table="E2E-T3"]').className)`, 8000, "E2E-T3 yana bo'sh");
		});
	},
};


scenarios.i = {
	title: "G'aladon + kassa harakati (chiqim/kirim, PIN) + X-hisobot (kassir: ko'r, menejer: to'liq)",
	async run(h) {
		let cashInvoice;
		await h.step("kassada naqd savdo (API) — hisobot uchun", async () => {
			const inv = await newOrder(h, { orderType: "Take Away", items: [{ item: "AFGAN BREAD", qty: 1 }] });
			const due = await payable(h, inv);
			await payViaApi(h, inv, [{ mode_of_payment: CASH, amount: due + 2000 }]);
			cashInvoice = inv;
		});
		await h.step("g'aladon: sabab bilan ochish, tez takror rad etiladi", async () => {
			await h.click(".rc-topquick__btn", "G'aladon");
			await h.waitDialog("G'aladonni ochish");
			await h.clickDialog('[data-action="open"]', "Ochish");
			await h.waitFor(`document.querySelector(".rc-dialog .rc-dialog__error") && document.querySelector(".rc-dialog .rc-dialog__error").innerText.trim().length > 0`, 5000, "sabab talab qilinadi");
			await h.click(".rc-chip-opt", "Qaytim", { scope: ".rc-dialog", last: true });
			await h.clickDialog('[data-action="open"]', "Ochish");
			await h.waitNoDialogAny();
			await h.waitIdle(400);
			const jobs = await printJobs(h, "Drawer");
			const manual = jobs.filter((j) => (j.reason || "").toLowerCase().includes("qaytim") || (j.reason || "") === "manual");
			h.check("g'aladon topshirig'i navbatda (sabab: Qaytim)", manual.length >= 1, JSON.stringify(jobs));
			const jobsBefore = jobs.length;
			await h.click(".rc-topquick__btn", "G'aladon");
			await h.waitDialog("G'aladonni ochish");
			await h.click(".rc-chip-opt", "Maydalash", { scope: ".rc-dialog", last: true });
			await h.clickDialog('[data-action="open"]', "Ochish");
			await h.waitIdle(600);
			await h.key("Escape");
			await h.waitNoDialogAny();
			h.eq((await printJobs(h, "Drawer")).length, jobsBefore, "tez takror g'aladon buyrug'i qo'shilmadi");
		});
		await h.step("kassa harakati oynasi", async () => {
			await h.click(".rc-menu-btn");
			await h.click(".rc-menu button, .rc-menu [data-id]", "Kassa harakati");
			await h.waitDialog("Kassa harakati");
		});
		const fillMovement = async (kind, amount, mode) => {
			await h.clickDialog(`[data-action="${kind === "In" ? "in" : "out"}"]`);
			await h.waitDialog(kind === "In" ? "Kassaga kirim" : "Kassadan chiqim");
			await h.type(".rc-input--amount", String(amount), { scope: ".rc-dialog", last: true });
			// Turi va Sabab: har birining birinchi tugmasi; «Naqd usul» — ko'rsatilgan usul
			await h.eval(`(() => { const d = [...document.querySelectorAll(".rc-dialog")].pop(); d.querySelectorAll(".rc-field--select").forEach((f, i) => { const label = f.querySelector(".rc-field__label").textContent.toLowerCase(); f.setAttribute("data-e2e-sel", label.includes("naqd usul") ? "mode" : label.includes("turi") ? "cat" : "reason"); }); })()`);
			await h.click('[data-e2e-sel="cat"] .rc-chip-opt', null, { scope: ".rc-dialog", last: true, index: 0 });
			await h.click('[data-e2e-sel="reason"] .rc-chip-opt', null, { scope: ".rc-dialog", last: true, index: 0 });
			if (mode) await h.click('[data-e2e-sel="mode"] .rc-chip-opt', mode, { scope: ".rc-dialog", last: true });
		};
		await h.step("chiqim 50 000 (chegara ichida) — PIN'siz", async () => {
			await fillMovement("Out", 50000, CASH);
			await h.clickDialog('[data-action="submit"]', "Chiqimni qayd etish");
			await h.waitFor(`document.querySelectorAll(".rc-dialog").length === 1 && document.querySelector(".rc-dialog .rc-shift-mv__table")`, 10000, "harakatlar jadvali yangilandi");
			const rows = await h.sql("select kind, amount, category, reason, approved_by, mode_of_payment from `tabOzturk Cash Movement` order by creation");
			h.eq(rows.length, 1, "1 ta harakat");
			h.check("Out 50 000 Нахт, tasdiqsiz", rows[0].kind === "Out" && Number(rows[0].amount) === 50000 && !rows[0].approved_by, JSON.stringify(rows));
			h.check("jadvalda ko'rinadi", (await h.dialogText()).replace(/\s/g, "").includes("50000"));
		});
		await h.step("chiqim 150 000 (chegaradan oshdi) — PIN kerak", async () => {
			h.expect("cash_movements.create_cash_movement", 403, "ApprovalRequired");
			await fillMovement("Out", 150000, CASH);
			await h.clickDialog('[data-action="submit"]', "Chiqimni qayd etish");
			await h.waitFor(`document.querySelector(".rc-dialog .rc-pin")`, 10000, "PIN oynasi");
			await enterPin(h, "4321");
			await h.waitFor(`!document.querySelector(".rc-dialog .rc-pin")`, 10000, "PIN oynasi yopildi");
			await h.waitFor(`document.querySelectorAll(".rc-dialog").length === 1`, 10000, "forma yopildi");
			const rows = await h.sql("select kind, amount, approved_by from `tabOzturk Cash Movement` order by creation");
			h.eq(rows.length, 2, "2 ta harakat");
			h.check("150 000 chiqim menejer tasdig'i bilan", Number(rows[1].amount) === 150000 && rows[1].approved_by === MANAGER, JSON.stringify(rows));
		});
		await h.step("kirim 30 000 (har doim tasdiq) — ikkinchi naqd usulga", async () => {
			h.expect("cash_movements.create_cash_movement", 403, "ApprovalRequired");
			await fillMovement("In", 30000, "Test Naqd 2");
			await h.clickDialog('[data-action="submit"]', "Kirimni qayd etish");
			await h.waitFor(`document.querySelector(".rc-dialog .rc-pin")`, 10000, "PIN oynasi");
			await enterPin(h, "4321");
			await h.waitFor(`!document.querySelector(".rc-dialog .rc-pin")`, 10000, "PIN oynasi yopildi");
			await h.waitFor(`document.querySelectorAll(".rc-dialog").length === 1`, 10000, "forma yopildi");
			const rows = await h.sql("select kind, amount, approved_by, mode_of_payment from `tabOzturk Cash Movement` order by creation");
			h.check("30 000 kirim tasdiq bilan, Test Naqd 2", rows.length === 3 && rows[2].kind === "In" && Number(rows[2].amount) === 30000 && rows[2].approved_by === MANAGER && rows[2].mode_of_payment === "Test Naqd 2", JSON.stringify(rows));
			await h.key("Escape");
			await h.waitNoDialogAny();
		});
		await h.step("X-hisobot kassir sifatida: ko'r sanoq (kutilgan summa yo'q)", async () => {
			await h.click(".rc-menu-btn");
			await h.click(".rc-menu button, .rc-menu [data-id]", "X-hisobot");
			await h.waitFor(`document.querySelector(".rc-dialog .rc-shift-xr__meta")`, 10000, "hisobot yuklandi");
			const text = await h.dialogText();
			h.check("«Kutilgan» va «Farq» qatorlari yo'q", !/Kutilgan/.test(text) && !/Farq/.test(text), text.slice(0, 500));
			h.check("«faqat menejerga ko'rinadi» izohi", /faqat menejerga/i.test(text));
			h.check("«Sotuv jami» ko'rsatilmaydi", !/Sotuv jami|Sof savdo/.test(text), text.slice(0, 300));
			const cash = await independentCash(h);
			const secret = [cash.expected, cash.sales, cash.sales - cash.change, cash.expected - cash.opening].filter((n) => n > 10000);
			const api = await h.apiAs(CASHIER, "cashier.get_shift_report", { kind: "X" }, { http: "GET" });
			const leaked = numbersIn(api.message).filter((n) => secret.includes(n));
			h.check("API: kutilgan naqd / naqd savdo yig'indisi hech qaysi maydonda yo'q", leaked.length === 0, `sizib chiqqan qiymatlar: ${leaked} (yashirin: ${secret})`);
			const uiText = await h.dialogText();
			h.check("interfeys matnida ham yo'q", secret.every((n) => !uiText.replace(/\s/g, "").includes(String(n))), "");
			const json = JSON.stringify(api.message);
			h.check("API: restricted=true, expected/difference null", api.message.restricted === true && api.message.cash.expected === null && api.message.cash.difference === null, json.slice(0, 400));
			h.check("API javobida umumiy savdo summasi sizmagan", api.message.sales.sales_total == null && api.message.sales.net_total == null, JSON.stringify(api.message.sales));
			await h.click('.rc-dialog [data-action="print"]');
			await h.waitIdle(600);
			const jobs = await printJobs(h, "Shift Report");
			h.check("X-hisobot chop etish topshirig'i navbatda", jobs.length === 1, JSON.stringify(jobs));
			// Qog'ozdagi hisobot ham ko'r bo'lishi shart: ESC/POS baytlaridagi raqamlar
			const payload = await h.one("select payload from `tabOzturk Print Job` where job_type = 'Shift Report' order by creation desc limit 1");
			const slip = Buffer.from(payload.payload, "base64").toString("latin1").replace(/[^\x20-\x7e\n]/g, " ");
			const slipDigits = slip.replace(/[^\d\n]/g, "");
			h.check("chop etilgan X-hisobotda kutilgan naqd / naqd savdo yo'q", secret.every((n) => !slipDigits.includes(String(n))), slip.replace(/\s+/g, " ").slice(0, 400));
			await h.key("Escape");
			await h.waitNoDialogAny();
		});
		await h.step("X-hisobot menejer sifatida: kutilgan naqd = bazadan mustaqil hisob", async () => {
			const api = await h.apiAs(MANAGER, "cashier.get_shift_report", { kind: "X" }, { http: "GET" });
			const rep = api.message;
			h.eq(rep.restricted, false, "menejerga cheklov yo'q");
			// mustaqil hisob: ochilish + naqd savdo - qaytim + kirim - chiqim
			const opening = Number((await h.one("select sum(d.opening_amount) t from `tabPOS Opening Entry Detail` d join `tabPOS Opening Entry` p on p.name = d.parent where p.user = %s and p.status = 'Open'", [CASHIER])).t);
			const sales = Number((await h.one("select coalesce(sum(sip.amount), 0) t from `tabSales Invoice Payment` sip join `tabPOS Invoice` pi on pi.name = sip.parent where pi.docstatus = 1 and pi.owner = %s and sip.mode_of_payment in (%s, %s)", [CASHIER, CASH, "Test Naqd 2"])).t);
			const change = Number((await h.one("select coalesce(sum(change_amount), 0) t from `tabPOS Invoice` where docstatus = 1 and owner = %s", [CASHIER])).t);
			const mv = await h.sql("select kind, amount from `tabOzturk Cash Movement`");
			const moved = mv.reduce((a, m) => a + (m.kind === "In" ? 1 : -1) * Number(m.amount), 0);
			const expected = opening + sales - change + moved;
			h.near(rep.cash.expected, expected, "server kutilgan naqd = mustaqil hisob");
			// interfeys (menejer sifatida)
			await h.op("actor", { user: MANAGER });
			await h.openCashier();
			await h.click(".rc-menu-btn");
			await h.click(".rc-menu button, .rc-menu [data-id]", "X-hisobot");
			await h.waitFor(`document.querySelector(".rc-dialog .rc-shift-xr__meta")`, 10000, "hisobot yuklandi (menejer)");
			const text = (await h.dialogText()).replace(/\s/g, "");
			h.check("menejer «Kutilgan» qatorini ko'radi", /Kutilgan/i.test(await h.dialogText()));
			h.check("ekrandagi kutilgan summa server bilan bir xil", text.includes(String(Math.round(rep.cash.expected))), text.slice(0, 600));
			await h.key("Escape");
			await h.waitNoDialogAny();
			await h.op("actor", { user: CASHIER });
			await h.openCashier();
		});
	},
};


scenarios.j = {
	title: "Kassani yopish: yopilmagan buyurtma to'sig'i, ko'r ikki bosqichli sanoq (60 s), Z-hisobot, GL izchilligi",
	async run(h) {
		let inv1, inv2, inv3, shift;
		await h.step("yangi smena (kun boshi) ochiladi: 200 000", async () => {
			await h.op("reset_shift");
			await h.openCashier();
			await openShiftUI(h, { [CASH]: 200000 });
			shift = (await h.one("select name from `tabPOS Opening Entry` where user = %s and status = 'Open'", [CASHIER])).name;
			h.check("yangi POS Opening Entry", !!shift, shift);
		});
		await h.step("cheklar: choychaqali naqd, chegirmali karta, va bitta to'lanmagan", async () => {
			inv1 = await newOrder(h, { table: "E2E-T1", items: [{ item: "AFGAN BREAD", qty: 2 }, { item: "BARDAK TEA", qty: 2 }] });
			await payViaApi(h, inv1, [{ mode_of_payment: CASH, amount: (await payable(h, inv1)) + 10000 }], { tip: 10000 });
			inv2 = await newOrder(h, { orderType: "Take Away", items: [{ item: "COCA-COLA", qty: 3 }] });
			const d = await h.apiAs(CASHIER, "billing.apply_discount", { invoice: inv2, percent: 10, reason: "Aksiya" });
			h.eq(d.status, 200, "chegirma qo'yildi");
			await payViaApi(h, inv2, [{ mode_of_payment: CARD, amount: await payable(h, inv2) }]);
			inv3 = await newOrder(h, { table: "E2E-T2", items: [{ item: "PEPSI", qty: 1 }] });
			const a = await h.op("invoice", { name: inv1 });
			h.near(a.grand_total, 178000, "inv1 grand_total = 168 000 + choychaqa 10 000");
		});
		await h.step("to'lanmagan buyurtma kassani yopishga to'sqinlik qiladi", async () => {
			await h.click(".rc-menu-btn");
			await h.click(".rc-menu button, .rc-menu [data-id]", "Kassani yopish");
			await h.waitDialog("Yopilmagan buyurtmalar");
			h.check("xabarda 1 ta buyurtma", /1 ta/.test(await h.dialogText()), await h.dialogText());
			await h.clickDialog('[data-action="ok"]');
			await h.waitNoDialogAny();
			// menejer bekor qiladi (kassir oshxona boshlagan buyurtmani o'zi bekor qila olmaydi)
			const r = await h.apiAs(MANAGER, "order.cancel_order", { order: inv3, reason: "E2E sinov" });
			h.eq(r.status, 200, "menejer buyurtmani bekor qildi");
		});
		let closeStartedAt;
		await h.step("1-sanoq (noto'g'ri) -> 60 soniyalik teskari sanoq", async () => {
			await h.click(".rc-menu-btn");
			await h.click(".rc-menu button, .rc-menu [data-id]", "Kassani yopish");
			await h.waitFor(`document.querySelector(".rc-modal .rc-count-input")`, 10000, "sanoq oynasi");
			const text = await h.text(".rc-modal");
			h.check("kassirga kutilgan summa / umumiy savdo ko'rsatilmaydi", !/Kutilgan|Tushum|expected/i.test(text) && !/60\s?480|24\s?480|378\s?000|238\s?000/.test(text), text.slice(0, 400));
			h.check("cheklar soni ko'rsatiladi (2)", /cheklar soni\s*2/i.test(text), text.slice(0, 200));
			const closing = await h.apiAs(CASHIER, "cashier.get_shift_closing_data", {}, { http: "GET" });
			h.check("get_shift_closing_data javobida expected/grand_total yo'q", !("expected_amount" in closing.message) && !("grand_total" in closing.message), JSON.stringify(Object.keys(closing.message)));
			await h.type(".rc-count-input", "1000", { index: 0 });
			await h.type(".rc-count-input", "0", { index: 1 });
			await h.click('.rc-modal [data-action="submit"]');
			await h.waitFor(`document.querySelector(".rc-countdown")`, 8000, "teskari sanoq");
			closeStartedAt = Date.now();
			const disabled = await h.eval(`document.querySelector('.rc-modal [data-action="submit"]').disabled`);
			h.eq(disabled, true, "teskari sanoq tugaguncha «Kassani yopish» o'chiq");
			const stepText = await h.text(".rc-modal .rc-steps__text");
			h.eq(stepText.toLowerCase(), "ikkinchi sanoq", "ikkinchi sanoq bosqichi");
			h.check("birinchi kiritilgan summa 2-bosqichda ko'rsatilmaydi", !/1\s?000/.test((await h.text(".rc-count")).replace(/Cheklar soni\s*\d+/, "")), await h.text(".rc-count"));
		});
		await h.step("60 s kutish -> 2-sanoq mos kelmaydi (boshqa summa) -> yana sanoq", async () => {
			await h.waitFor(`!document.querySelector('.rc-modal [data-action="submit"]').disabled`, 75000, "teskari sanoq tugadi");
			h.check("tugma ~60 soniyadan keyin ochildi", Date.now() - closeStartedAt >= 55000, `${Date.now() - closeStartedAt}ms`);
			await h.type(".rc-count-input", "378000", { index: 0 });
			await h.type(".rc-count-input", "0", { index: 1 });
			await h.click('.rc-modal [data-action="submit"]');
			await h.waitFor(`document.querySelector(".rc-modal .rc-pay__error") && document.querySelector(".rc-modal .rc-pay__error").innerText.toLowerCase().includes("mos kelmadi")`, 8000, "mos kelmadi xabari");
			h.check("sanoq yana boshlandi (qayta sanoq)", /Qayta sanoq|Ikkinchi sanoq/i.test(await h.text(".rc-modal .rc-steps__text")), await h.text(".rc-modal .rc-steps__text"));
			const rows = await h.sql("select count(*) c from `tabPOS Closing Entry` where pos_opening_entry = %s", [shift]);
			h.eq(Number(rows[0].c), 0, "mos kelmasa kassa yopilmadi");
		});
		await h.step("yana 60 s -> to'g'ri sanoq (378 000) -> kassa yopiladi, Z-hisobot", async () => {
			await h.waitFor(`!document.querySelector('.rc-modal [data-action="submit"]').disabled`, 75000, "teskari sanoq tugadi");
			await h.type(".rc-count-input", "378000", { index: 0 });
			await h.type(".rc-count-input", "0", { index: 1 });
			await h.click('.rc-modal [data-action="submit"]');
			await h.waitFor(`ozturk.cashier.screen.state === "shift"`, 30000, "kassa yopildi (kassa yopiq ekrani)");
			const closing = await h.one("select name, docstatus, pos_opening_entry from `tabPOS Closing Entry` where pos_opening_entry = %s and docstatus = 1", [shift]);
			h.check("POS Closing Entry submit qilindi", !!closing, JSON.stringify(closing));
			const opening = await h.one("select status from `tabPOS Opening Entry` where name = %s", [shift]);
			h.eq(opening.status, "Closed", "smena Closed");
			const rec = await h.sql("select mode_of_payment, opening_amount, expected_amount, closing_amount, difference from `tabPOS Closing Entry Detail` where parent = %s order by idx", [closing.name]);
			const cash = rec.find((r) => r.mode_of_payment === CASH);
			h.check("naqd: kutilgan 378 000, sanalgan 378 000, farq 0", cash && Number(cash.expected_amount) === 378000 && Number(cash.closing_amount) === 378000 && Number(cash.difference) === 0, JSON.stringify(rec));
			const card = rec.find((r) => r.mode_of_payment === CARD);
			h.check("karta: kutilgan = chek jami (avtomatik), farq 0", card && Number(card.difference) === 0 && Number(card.expected_amount) > 0, JSON.stringify(card));
			const z = await h.sql("select name, job_type, status, reason, ref_name from `tabOzturk Print Job` where job_type = 'Shift Report' order by creation");
			h.check("Z-hisobot topshirig'i navbatda", z.length >= 1, JSON.stringify(z));
		});
		await h.step("GL izchilligi: konsolidatsiya, choychaqa va xizmat haqi hisoblari", async () => {
			const inv = await h.sql("select name, consolidated_invoice from `tabPOS Invoice` where name in (%s, %s)", [inv1, inv2]);
			h.check("cheklar Sales Invoice ga konsolidatsiya qilingan", inv.every((r) => !!r.consolidated_invoice), JSON.stringify(inv));
			const sis = [...new Set(inv.map((r) => r.consolidated_invoice).filter(Boolean))];
			if (!sis.length) return;
			const ph = sis.map(() => "%s").join(",");
			const totals = await h.one(`select round(sum(debit), 2) d, round(sum(credit), 2) c from \`tabGL Entry\` where voucher_no in (${ph}) and is_cancelled = 0`, sis);
			h.check("GL: debet = kredit", Number(totals.d) === Number(totals.c) && Number(totals.d) > 0, JSON.stringify(totals));
			const byAcc = await h.sql(`select account, round(sum(debit), 2) d, round(sum(credit), 2) c from \`tabGL Entry\` where voucher_no in (${ph}) and is_cancelled = 0 group by account`, sis);
			const tips = byAcc.find((r) => /Tips Payable/i.test(r.account));
			h.check("Choychaqa kreditga 'Tips Payable' hisobida = 10 000", tips && Number(tips.c) === 10000, JSON.stringify(byAcc));
			const service = byAcc.find((r) => /Service Charge/i.test(r.account));
			h.check("Xizmat haqi hisobi = 18 000 + 12% * 54 000", service && Math.round(Number(service.c)) === 24480, JSON.stringify(service));
			// Kassa/bank hisoblariga tushgan pul (COGS va boshqa debetlar hisobga olinmaydi)
			const money = await h.sql(`select round(sum(g.debit), 2) d from \`tabGL Entry\` g join \`tabAccount\` a on a.name = g.account where g.voucher_no in (${ph}) and g.is_cancelled = 0 and a.account_type in ('Cash', 'Bank')`, sis);
			const receipts = Number(money[0].d);
			h.near(receipts, 178000 + (await payable(h, inv2)), "debet (kassa/bank) = to'langan jami (choychaqa bilan)");
		});
		await h.step("kassa yopilgach: server sotuvni rad etadi, hisobot menejerga ochiq", async () => {
			h.expect("cashier_orders.create_order", 417, "ValidationError");
			const r = await h.apiAs(CASHIER, "cashier_orders.create_order", { order_type: "Take Away", items: [{ item: "PEPSI", item_name: "PEPSI", qty: 1 }], client_ref: "e2e-closed" });
			h.check("smenasiz buyurtma rad etiladi", r.status >= 400 && /Kassa yopiq|smena/i.test(JSON.stringify(r.data)), `${r.status} ${JSON.stringify(r.data).slice(0, 200)}`);
			const z = await h.apiAs(MANAGER, "cashier.get_shift_report", { kind: "Z" }, { http: "GET" });
			h.eq(z.status, 200, "Z-hisobot (menejer)");
			h.check("Z: difference = 0", z.message && z.message.cash && Number(z.message.cash.difference) === 0, JSON.stringify(z.message && z.message.cash));
		});
	},
};


scenarios.k = {
	title: "Funksiya bayroqlari O'CHIQ: boshqaruv elementlari yo'qoladi, server to'g'ridan-to'g'ri chaqiruvni rad etadi",
	async run(h) {
		try {
			await scenarios.k.body(h);
		} finally {
			// Yiqilsa ham keyingi senariylar bayroqsiz qolmasin.
			await h.op("features", flags(1));
		}
	},
	async body(h) {
		let invoice, paid;
		await h.step("tayyorlov: smena, buyurtma (bayroqlar yoqiqda)", async () => {
			await ensureShift(h);
			invoice = await newOrder(h, { table: "E2E-T1", items: [{ item: "AFGAN BREAD", qty: 2 }, { item: "BARDAK TEA", qty: 1 }] });
			const p = await newOrder(h, { orderType: "Take Away", items: [{ item: "PEPSI", qty: 1 }] });
			await payViaApi(h, p, [{ mode_of_payment: CASH, amount: await payable(h, p) }]);
			paid = p;
		});
		await h.step("bayroqlar o'chiriladi, sahifa qayta ochiladi", async () => {
			const f = await h.op("features", flags(0));
			h.check("barcha bayroqlar o'chdi", ALL_FEATURES.every((k) => !f[k]), JSON.stringify(f));
			await h.openCashier();
			const topquick = await h.allText(".rc-topquick__btn");
			h.eq(topquick, [], "yuqori panelda «Buyurtma»/«G'aladon» tugmalari yo'q");
			await h.click(".rc-menu-btn");
			const menu = await h.allText(".rc-menu button, .rc-menu [data-id]");
			h.check("menyuda X-hisobot / Kassa harakati yo'q (Bronlar — bayroqsiz eski funksiya, qoladi)", !menu.some((t) => /X-hisobot|Kassa harakati/i.test(t)) && menu.some((t) => /Bronlar/.test(t)), JSON.stringify(menu));
			h.check("menyuda Tarix va Kassani yopish bor (bayroqsiz funksiyalar)", menu.some((t) => /Tarix/.test(t)) && menu.some((t) => /Kassani yopish/.test(t)), JSON.stringify(menu));
			await h.key("Escape");
			await h.click(".rc-menu-btn").catch(() => {});
			await selectTable(h, "E2E-T1");
			await h.click('.rc-panel [data-action="panel-more"]', null, { allowDisabled: true }).catch(() => {});
			const more = await h.eval(`[...document.querySelectorAll(".rc-dialog .rc-more__item")].map(b => b.innerText.replace(/\\s+/g, " ").trim())`);
			h.check("«Yana» varag'ida Chegirma / Ko'chirish / Birlashtirish / Mijoz / Taom qo'shish yo'q", !more.some((t) => /Chegirma|ko'chirish|birlashtirish|Mijoz|Taom qo'shish/i.test(t)), JSON.stringify(more));
			if (more.length) await h.key("Escape");
			await h.waitNoDialogAny().catch(() => {});
			await giveBill(h);
			await h.click('.rc-panel [data-action="pay"]');
			await h.waitFor(`document.querySelector(".rc-modal .rc-pay")`, 8000, "to'lov oynasi");
			// `split_payment` o'chiq: usullar ro'yxati baribir chiqadi, lekin bir vaqtda faqat bitta usulda summa turadi.
			const offNames = await h.allText(".rc-modal .rc-pay__name");
			h.check("usullar ro'yxati bayroqsiz ham chiqadi", offNames.includes(CASH) && offNames.includes(CARD), JSON.stringify(offNames));
			await h.click(".rc-modal .rc-pay__name", CASH, { exact: true });
			await h.click(".rc-modal .rc-pay__name", CARD, { exact: true });
			const offValues = await h.eval(`[...document.querySelectorAll(".rc-modal .rc-pay__input")].map(i => i.value)`);
			h.check("aralash to'lov o'chiq: bir vaqtda faqat bitta usulda summa", offValues.filter(Boolean).length === 1, JSON.stringify(offValues));
			h.check("choychaqa tanlovi yo'q", !/Choychaqa/i.test(await h.text(".rc-modal")), (await h.text(".rc-modal")).slice(0, 300));
			await h.click(".rc-modal .rc-modal__close");
			await openHistory(h);
			await historyDetail(h, paid);
			const actions = await h.allText(".rc-history__detail [data-history-action]");
			h.check("tarixda «Qaytarish» yo'q", !actions.some((a) => /Qaytarish/.test(a)), JSON.stringify(actions));
			await h.click(".rc-modal .rc-modal__close");
		});
		await h.step("server har bir funksiyani PermissionError bilan rad etadi", async () => {
			const denied = async (label, method, args, http = "POST") => {
				h.expect(method, 403, "PermissionError");
				const r = await h.apiAs(CASHIER, method, args, { http });
				h.check(`${label}: rad etildi (403)`, r.status === 403, `${r.status} ${r.excType} ${JSON.stringify(r.data).slice(0, 160)}`);
			};
			await denied("buyurtma ochish", "cashier_orders.create_order", { order_type: "Take Away", items: [{ item: "PEPSI", item_name: "PEPSI", qty: 1 }], client_ref: "e2e-off-1" });
			await denied("chegirma", "billing.apply_discount", { invoice, percent: 5, reason: "Aksiya" });
			await denied("stol ko'chirish", "table.transfer_table", { invoice, to_table: "E2E-T2" });
			await denied("birlashtirish", "table.merge_tables", { invoice, tables: ["E2E-T2"] });
			await denied("qaytarish (menejer PIN bilan ham)", "billing.refund_invoice", { invoice: paid, items: [{ name: "x", qty: 1 }], reason: "x", approval: { user: MANAGER, pin: PIN } });
			await denied("g'aladon", "printing.open_drawer", { reason: "manual" });
			await denied("kassa harakati", "cash_movements.create_cash_movement", { kind: "Out", amount: 1000, category: "Xarajat", reason: "x" });
			await denied("X-hisobot", "cashier.get_shift_report", { kind: "X" }, "GET");
			await denied("mijoz biriktirish", "cashier_orders.set_customer", { invoice, customer: "x" });
			await denied("elementlar qo'shish", "cashier_orders.add_items", { invoice, items: [{ item: "PEPSI", item_name: "PEPSI", qty: 1 }], last_modified_time: "2020-01-01 00:00:00" });
			// aralash to'lov va choychaqa: submit_payment bayroqsiz
			const due = await payable(h, invoice);
			h.expect("billing.submit_payment", 417, "ValidationError");
			const split = await h.apiAs(CASHIER, "billing.submit_payment", { invoice, payments: [{ mode_of_payment: CASH, amount: due - 1000 }, { mode_of_payment: CARD, amount: 1000 }] });
			h.check("aralash to'lov (ko'p qator) rad etiladi", split.status >= 400 && /Aralash/i.test(JSON.stringify(split.data)), `${split.status}`);
			h.expect("billing.submit_payment", 403, "PermissionError");
			const tip = await h.apiAs(CASHIER, "billing.submit_payment", { invoice, payments: [{ mode_of_payment: CASH, amount: due + 5000 }], tip: 5000 });
			h.check("choychaqali to'lov rad etiladi", tip.status >= 400, `${tip.status} ${JSON.stringify(tip.data).slice(0, 200)}`);
			const still = await h.op("invoice", { name: invoice });
			h.eq(still.docstatus, 0, "rad etilgan to'lovlar chekni o'zgartirmadi");
		});
		await h.step("bayroqlar qayta yoqiladi", async () => {
			await h.op("features", flags(1));
			await h.openCashier();
			h.check("«Buyurtma» tugmasi qaytdi", (await h.allText(".rc-topquick__btn")).some((t) => /Buyurtma/.test(t)));
		});
	},
};

scenarios.l = {
	title: "Buyurtmani bekor qilish qoidalari: oshxona boshlamagan / boshlagan (kassir vs menejer)",
	async run(h) {
		let a, b;
		await h.step("oshxona boshlamagan buyurtma — kassir bekor qiladi", async () => {
			await ensureShift(h);
			a = await newOrder(h, { table: "E2E-T1", items: [{ item: "AFGAN BREAD", qty: 1 }] });
			await selectTable(h, "E2E-T1");
			const cancelBtn = await h.eval(`(() => { const b = document.querySelector('.rc-panel [data-action="cancel-order"]'); return b ? {d: b.disabled, t: b.innerText.trim()} : null; })()`);
			h.check("«Bekor qilish» faol", cancelBtn && !cancelBtn.d && /Bekor qilish/i.test(cancelBtn.t) && !/Majburan/i.test(cancelBtn.t), JSON.stringify(cancelBtn));
			await h.click('.rc-panel [data-action="cancel-order"]');
			await h.waitDialog("Buyurtmani bekor qilish");
			await h.click(".rc-chip-opt", null, { scope: ".rc-dialog", last: true, index: 0 });
			await h.clickDialog('[data-action="submit"]', "Bekor qilish");
			await h.waitNoDialogAny();
			await h.waitIdle(500);
			const inv = await h.op("invoice", { name: a });
			h.eq(Number(inv.cancelled), 1, "custom_cancelled = 1");
			h.eq(Number((await h.one("select occupied from `tabURY Table` where name = 'E2E-T1'")).occupied), 0, "stol bo'shadi");
			const kot = await h.sql("select order_status from `tabURY KOT` where invoice = %s and docstatus = 1", [a]);
			h.check("KOT lar bekor qilindi/yopildi", kot.every((k) => /Cancel|Served/i.test(k.order_status)) , JSON.stringify(kot));
			const row = await h.one("select custom_cancel_reason r from `tabPOS Invoice` where name = %s", [a]).catch(() => null);
			h.check("bekor qilish sababi yozilgan", !row || !!row.r, JSON.stringify(row));
		});
		await h.step("oshxona ishni boshladi — oddiy kassir bekor qila olmaydi", async () => {
			b = await newOrder(h, { table: "E2E-T2", items: [{ item: "AFGAN BREAD", qty: 1 }, { item: "PEPSI", qty: 1 }] });
			const item = await h.one("select ki.name n from `tabURY KOT Items` ki join `tabURY KOT` k on k.name = ki.parent where k.invoice = %s and ki.item = 'AFGAN BREAD'", [b]);
			const r = await h.apiAs(MANAGER, "kitchen.update_kot_item_status", { kot_item: item.n, status: "Preparing" });
			h.eq(r.status, 200, "oshpaz (menejer huquqi bilan) taomni «Tayyorlanmoqda» ga o'tkazdi");
			await h.eval(`ozturk.cashier.screen.refreshAll()`);
			await selectTable(h, "E2E-T2");
			await h.waitFor(`document.querySelector('.rc-panel [data-action="cancel-order"]')`, 8000, "bekor qilish tugmasi");
			const btn = await h.eval(`(() => { const b = document.querySelector('.rc-panel [data-action="cancel-order"]'); return {d: b.disabled, title: b.title, t: b.innerText.replace(/\\s+/g, " ").trim(), panel: document.querySelector(".rc-panel").innerText}; })()`);
			h.check("tugma o'chiq", btn.d, JSON.stringify(btn));
			h.check("sababi (menejer kerak) tooltip'da bor", /menejer/i.test(btn.title), btn.title);
			h.check("sababi panelda matn sifatida ko'rinadi (sensorli ekranda tooltip yo'q)", /Oshxona ishni boshlab yuborgan.*faqat menejer bekor qila oladi/i.test(btn.panel), btn.panel.replace(/\s+/g, " ").slice(0, 300));
			h.expect("order.cancel_order", 417, "ValidationError");
			const denied = await h.apiAs(CASHIER, "order.cancel_order", { order: b, reason: "xato" });
			h.check("server ham kassirni rad etadi", denied.status >= 400 && /menejer/i.test(JSON.stringify(denied.data)), `${denied.status} ${JSON.stringify(denied.data).slice(0, 200)}`);
			h.expect("cashier_orders.remove_item", 417, "ValidationError");
			const rm = await h.apiAs(CASHIER, "cashier_orders.remove_item", { invoice: b, item_row: (await h.apiAs(CASHIER, "billing.get_bill", { invoice: b }, { http: "GET" })).message.items[0].name, reason: "x" });
			h.check("boshlangan taomni kassir olib tashlay olmaydi", rm.status >= 400 || (rm.message && rm.message.removed === 0), `${rm.status} ${JSON.stringify(rm.data).slice(0, 200)}`);
			const still = await h.op("invoice", { name: b });
			h.check("chek o'zgarmadi", !Number(still.cancelled) && still.items.length === 2, JSON.stringify(still.items));
		});
		await h.step("menejer «Majburan bekor» — ogohlantirish va sabab", async () => {
			await h.op("actor", { user: MANAGER });
			await h.openCashier();
			await selectTable(h, "E2E-T2");
			const btn = await h.eval(`(() => { const b = document.querySelector('.rc-panel [data-action="cancel-order"]'); return {d: b.disabled, t: b.innerText.replace(/\\s+/g, " ").trim()}; })()`);
			h.check("menejerda «Majburan bekor» faol", !btn.d && /Majburan/i.test(btn.t), JSON.stringify(btn));
			await h.click('.rc-panel [data-action="cancel-order"]');
			await h.waitDialog("Majburan bekor qilish");
			h.check("ogohlantirish: taom pishayotgani aytiladi", /Oshxona ishni boshlab/i.test(await h.dialogText()), (await h.dialogText()).slice(0, 300));
			await h.click(".rc-chip-opt", null, { scope: ".rc-dialog", last: true, index: 0 });
			await h.clickDialog('[data-action="submit"]', "Bekor qilish");
			await h.waitNoDialogAny();
			await h.waitIdle(600);
			const inv = await h.op("invoice", { name: b });
			h.eq(Number(inv.cancelled), 1, "majburan bekor qilindi");
			h.eq(Number((await h.one("select occupied from `tabURY Table` where name = 'E2E-T2'")).occupied), 0, "stol bo'shadi");
			const kot = await h.sql("select type, order_status from `tabURY KOT` where invoice = %s", [b]);
			h.check("oshxonaga bekor qilish KOT'i ketdi", kot.some((k) => /Cancel/i.test(k.type || "") || /Cancel/i.test(k.order_status || "")), JSON.stringify(kot));
			await h.op("actor", { user: CASHIER });
			await h.openCashier();
		});
		await h.step("to'langan buyurtmani bekor qilib bo'lmaydi", async () => {
			const inv = await newOrder(h, { orderType: "Take Away", items: [{ item: "PEPSI", qty: 1 }] });
			await payViaApi(h, inv, [{ mode_of_payment: CASH, amount: await payable(h, inv) }]);
			h.expect("order.cancel_order", 417, "ValidationError");
			const r = await h.apiAs(MANAGER, "order.cancel_order", { order: inv, reason: "x" });
			h.check("to'langan chek bekor qilinmaydi", r.status >= 400, `${r.status} ${JSON.stringify(r.data).slice(0, 200)}`);
		});
	},
};

scenarios.m = {
	title: "Ofitsant API: pul ma'lumoti sizib chiqmasligi (chegirma, xizmat haqi, choychaqa, to'lov, realtime) va kassa amallari yopiqligi",
	async run(h) {
		let invoice;
		const MONEY_KEYS = /discount|tip|service|tax|grand|rounded|payable|paid|change|payment|expected|difference|net_total|opening|counted|approved/i;
		// `paid` (bool: to'langanmi) va `self_service` (bool: o'zi olib ketiladigan taom) — pul emas.
		const NOT_MONEY = new Set(["paid", "self_service"]);
		const scan = (value, path = "", found = []) => {
			if (Array.isArray(value)) value.forEach((v, i) => scan(v, `${path}[${i}]`, found));
			else if (value && typeof value === "object") for (const [k, v] of Object.entries(value)) {
				if (MONEY_KEYS.test(k) && !NOT_MONEY.has(k)) found.push(`${path}.${k}`);
				scan(v, `${path}.${k}`, found);
			}
			return found;
		};
		await h.step("tayyorlov: chegirmali va choychaqali buyurtma", async () => {
			await ensureShift(h);
			invoice = await newOrder(h, { table: "E2E-T1", items: [{ item: "AFGAN BREAD", qty: 2 }, { item: "COCA-COLA", qty: 2 }] });
			const d = await h.apiAs(CASHIER, "billing.apply_discount", { invoice, percent: 10, reason: "Aksiya" });
			h.eq(d.status, 200, "chegirma qo'yildi");
		});
		await h.step("get_order / get_tables / get_bootstrap / get_context: moliyaviy kalitlar yo'q", async () => {
			const calls = [
				["get_order(invoice)", "waiter.get_order", { invoice }, "GET"],
				["get_order(table)", "waiter.get_order", { table: "E2E-T1" }, "GET"],
				["get_tables", "waiter.get_tables", {}, "GET"],
				["get_bootstrap", "waiter.get_bootstrap", {}, "GET"],
				["get_context", "waiter.get_context", {}, "GET"],
				["get_shift_state", "waiter.get_shift_state", {}, "GET"],
			];
			for (const [label, method, args, http] of calls) {
				const r = await h.apiAs(WAITER, method, args, { http });
				h.check(`${label}: 200`, r.status === 200, `${r.status} ${JSON.stringify(r.data).slice(0, 200)}`);
				const leaks = scan(r.message);
				// `items[].rate/amount` — taom narxi ofitsantga ruxsat etilgan; chegirma/xizmat/soliq/to'lov EMAS
				h.check(`${label}: pul kalitlari sizmagan`, leaks.length === 0, leaks.join(", "));
			}
			const order = (await h.apiAs(WAITER, "waiter.get_order", { invoice }, { http: "GET" })).message;
			const items = order.items.reduce((a, i) => a + Number(i.amount), 0);
			h.near(order.items_total, 2 * 60000 + 2 * 20000, "items_total = taomlar summasi (chegirmasiz, xizmat haqisiz)");
			h.near(order.subtotal, order.items_total, "subtotal == items_total (chegirma bilib bo'lmaydi)");
			h.near(items, order.items_total, "qatorlar yig'indisi = items_total");
		});
		await h.step("realtime hodisalari faqat identifikatorlarni tashiydi", async () => {
			const events = await h.op("realtime");
			const cashierEvents = events.filter((e) => /ozturk_/.test(e.event));
			h.check("ozturk_* hodisalari bor", cashierEvents.length > 5, String(cashierEvents.length));
			const leaks = [];
			for (const e of cashierEvents) scan(e.message, e.event, leaks);
			h.check("realtime yuklamalarida pul kalitlari yo'q", leaks.length === 0, leaks.slice(0, 6).join(", "));
			const numbers = cashierEvents.filter((e) => Object.values(e.message || {}).some((v) => typeof v === "number" && v > 1000));
			h.check("realtime yuklamalarida summa-o'xshash raqamlar yo'q", numbers.length === 0, JSON.stringify(numbers.slice(0, 2)));
		});
		await h.step("ofitsant kassa/pul amallarini bajara olmaydi", async () => {
			const forbidden = [
				["billing.get_bill", { invoice }, "GET"],
				["billing.submit_payment", { invoice, payments: [{ mode_of_payment: CASH, amount: 1 }] }, "POST"],
				["billing.apply_discount", { invoice, percent: 5, reason: "x" }, "POST"],
				["billing.refund_invoice", { invoice, items: [], reason: "x" }, "POST"],
				["cashier.get_shift_report", { kind: "X" }, "GET"],
				["cashier.get_cashier_context", {}, "GET"],
				["cashier.close_shift", { counted_cash: JSON.stringify({ [CASH]: 0 }) }, "POST"],
				["cash_movements.get_cash_movements", {}, "GET"],
				["cash_movements.create_cash_movement", { kind: "Out", amount: 1000, category: "Xarajat", reason: "x" }, "POST"],
				["printing.open_drawer", { reason: "manual" }, "POST"],
				["order.get_paid_orders", {}, "GET"],
				["table.transfer_table", { invoice, to_table: "E2E-T2" }, "POST"],
			];
			for (const [method, args, http] of forbidden) {
				h.expect(method, 403, "CashierPermissionError");
				const r = await h.apiAs(WAITER, method, args, { http });
				h.check(`ofitsant ${method}: 403`, r.status === 403, `${r.status} ${r.excType}`);
			}
		});
		await h.step("hisob so'rash faqat bayroq qo'yadi (to'lov emas)", async () => {
			const r = await h.apiAs(WAITER, "waiter.request_bill", { invoice });
			h.eq(r.status, 200, "request_bill");
			const inv = await h.op("invoice", { name: invoice });
			h.eq([inv.docstatus, Number(inv.bill_requested)], [0, 1], "chek to'lanmagan, bayroq qo'yilgan");
		});
	},
};

/** Har bir senariy oldidan: ochiq smena va toza zal (oldingi senariylardan qolgan to'lanmagan buyurtmalarsiz). */
async function prepare(h) {
	await ensureShift(h);
	const open = await h.sql("select name from `tabPOS Invoice` where docstatus = 0 and ifnull(custom_cancelled, 0) = 0");
	for (const row of open) {
		const r = await h.apiAs(MANAGER, "order.cancel_order", { order: row.name, reason: "E2E tozalash" });
		if (r.status !== 200) throw new Error(`prepare: ${row.name} bekor qilinmadi: ${r.status} ${JSON.stringify(r.data).slice(0, 200)}`);
	}
	await h.eval(`ozturk.cashier.screen.refreshAll()`);
	await h.waitIdle(400);
}


scenarios.n = {
	title: "Qaytarish buxgalteriyasi: choychaqali va chegirmali chek (to'liq va qisman)",
	async run(h) {
		let inv, due;
		await h.step("choychaqali (10 000) va chegirmali (10%) chek to'lanadi", async () => {
			inv = await newOrder(h, { orderType: "Take Away", items: [{ item: "AFGAN BREAD", qty: 2 }, { item: "BARDAK TEA", qty: 2 }] });
			h.eq((await h.apiAs(CASHIER, "billing.apply_discount", { invoice: inv, percent: 10, reason: "Aksiya" })).status, 200, "chegirma");
			due = await payable(h, inv);
			await h.op("allow_in_returns", { mode: CASH, allowed: 1 });
			await payViaApi(h, inv, [{ mode_of_payment: CASH, amount: due + 10000 }], { tip: 10000 });
			const doc = await h.op("invoice", { name: inv });
			h.near(doc.grand_total, due + 10000, "grand_total = payable + choychaqa");
		});
		let firstReturn;
		await h.step("qisman qaytarish: choychaqa qaytarilmaydi, chegirma hisobga olinadi", async () => {
			const info = await h.apiAs(CASHIER, "billing.get_refundable", { invoice: inv }, { http: "GET" });
			const afgan = info.message.items.find((i) => i.item_code === "AFGAN BREAD");
			h.expect("billing.refund_invoice", 403, "ApprovalRequired");
			const noPin = await h.apiAs(CASHIER, "billing.refund_invoice", { invoice: inv, items: [{ name: afgan.name, qty: 1 }], reason: "Mijoz e'tirozi" });
			h.eq([noPin.status, noPin.excType], [403, "ApprovalRequired"], "PIN'siz qaytarish rad etiladi");
			const r = await h.apiAs(CASHIER, "billing.refund_invoice", { invoice: inv, items: [{ name: afgan.name, qty: 1 }], reason: "Mijoz e'tirozi", approval: { user: MANAGER, pin: PIN } });
			h.eq(r.status, 200, "qisman qaytarish");
			firstReturn = r.message;
			const ret = await h.op("invoice", { name: firstReturn.invoice });
			// 1 dona AFGAN BREAD: 60 000 - 10% = 54 000, + xizmat haqi 12% = 60 480
			h.near(ret.grand_total, -60480, "qaytarilgan summa = -(60 000 * 0.9 * 1.12)");
			h.check("qaytarish chekida choychaqa qatori yo'q", !ret.taxes.some((t) => /Choychaqa|Tip/i.test(t.desc || t.account) && Math.abs(t.amount) > 0), JSON.stringify(ret.taxes));
			h.eq(r.message.final, false, "qisman: final=false");
		});
		await h.step("qolgan qismi: choychaqa BIRGA qaytariladi, yig'indi = asl chek jami", async () => {
			const info = await h.apiAs(CASHIER, "billing.get_refundable", { invoice: inv }, { http: "GET" });
			h.check("javobda choychaqa ko'rsatilgan", Number(info.message.tip) === 10000, String(info.message.tip));
			const rest = info.message.items.filter((i) => Number(i.refundable_qty) > 0).map((i) => ({ name: i.name, qty: Number(i.refundable_qty) }));
			const r = await h.apiAs(CASHIER, "billing.refund_invoice", { invoice: inv, items: rest, reason: "Buyurtma xatosi", approval: { user: MANAGER, pin: PIN } });
			h.eq(r.status, 200, "qolganini qaytarish");
			h.eq(r.message.final, true, "final=true");
			const sum = await h.one("select coalesce(sum(grand_total), 0) t, coalesce(sum(paid_amount), 0) p from `tabPOS Invoice` where return_against = %s and docstatus = 1", [inv]);
			h.near(Number(sum.t), -(due + 10000), "qaytarishlar yig'indisi = -(chek jami + choychaqa)");
			const pays = await h.one("select coalesce(sum(sip.amount), 0) t from `tabSales Invoice Payment` sip join `tabPOS Invoice` pi on pi.name = sip.parent where pi.return_against = %s and pi.docstatus = 1", [inv]);
			h.near(Number(pays.t), -(due + 10000), "naqd qaytarilgan pul = -(to'langan)");
			const again = await h.apiAs(CASHIER, "billing.get_refundable", { invoice: inv }, { http: "GET" });
			h.check("qaytariladigan qoldiq yo'q", again.message.items.every((i) => Number(i.refundable_qty) === 0) && Number(again.message.refundable || 0) === 0, JSON.stringify(again.message).slice(0, 300));
			h.expect("billing.refund_invoice", 417, "ValidationError");
			const dup = await h.apiAs(CASHIER, "billing.refund_invoice", { invoice: inv, items: rest, reason: "takror", approval: { user: MANAGER, pin: PIN } });
			h.check("takroriy qaytarish rad etiladi", dup.status >= 400, `${dup.status}`);
		});
	},
};


scenarios.o = {
	title: "Klaviatura yorliqlari (F2/F3/F9/Esc), zal tanlash, Desk paneli ko'rinishi, joylashuvni tahrirlash (menejer, sudrash)",
	async run(h) {
		let invoice;
		await h.step("F3 hisob beradi, F2 to'lov oynasini ochadi, Esc yopadi, F9 g'aladon", async () => {
			invoice = await newOrder(h, { table: "E2E-T1", items: [{ item: "AFGAN BREAD", qty: 1 }] });
			await selectTable(h, "E2E-T1");
			await h.key("F3");
			await h.waitFor(`document.querySelector('.rc-panel [data-action="pay"]') && !document.querySelector('.rc-panel [data-action="pay"]').disabled`, 10000, "F3: hisob berildi");
			h.eq(Number((await h.op("invoice", { name: invoice })).printed), 1, "F3 -> invoice_printed");
			await h.key("F2");
			await h.waitFor(`document.querySelector(".rc-modal .rc-pay")`, 8000, "F2: to'lov oynasi");
			await h.key("Escape");
			h.check("birinchi Esc matn maydonidan chiqadi (oyna ochiq qoladi — yozilgan summa yo'qolmasin)", await h.eval(`!document.querySelector(".rc-overlay").hidden`));
			await h.key("Escape");
			await h.waitFor(`document.querySelector(".rc-overlay").hidden`, 5000, "ikkinchi Esc: to'lov oynasi yopildi");
			await h.key("F9");
			await h.waitDialog("G'aladonni ochish");
			await h.escapeAll();
			await h.waitNoDialogAny();
		});
		await h.step("zal tanlash (Ichki zal / Tashqi zal) faqat shu zal stollarini ko'rsatadi", async () => {
			await h.click(".rc-rooms button, .rc-rooms [data-room]", "Tashqi zal");
			await h.waitFor(`ozturk.cashier.screen.room === "Tashqi zal"`, 5000, "zal tanlandi");
			await h.waitIdle(400);
			const tiles = await h.eval(`[...document.querySelectorAll(".rc-table")].map(t => t.dataset.table)`);
			const expected = (await h.sql("select name from `tabURY Table` where restaurant_room = %s order by name", ["Tashqi zal"])).map((r) => r.name);
			h.eq(tiles.sort(), expected.sort(), "zal rejasi: faqat Tashqi zal stollari");
			await h.click(".rc-rooms button, .rc-rooms [data-room]", "Barcha zallar");
			await h.waitFor(`ozturk.cashier.screen.room === null`, 5000, "barcha zallar");
		});
		await h.step("to'liq ekran rejimi yo'q: Desk navbari va sahifa sarlavhasi doim ko'rinadi", async () => {
			await h.click(".rc-menu-btn");
			const items = await h.allText(".rc-menu button, .rc-menu [data-id]");
			await h.key("Escape");
			h.check("«⋯» menyusida to'liq ekran/kiosk bandi yo'q", !items.some((t) => /kiosk|to'liq ekran/i.test(t)), JSON.stringify(items));
			const chrome = await h.eval(`(() => { const vis = (e) => !!e && e.getBoundingClientRect().height > 0 && getComputedStyle(e).display !== "none"; return {navbar: vis(document.querySelector(".navbar")), head: vis(document.querySelector("#page-restaurant-cashier .page-head")), kiosk: document.body.classList.contains("rc-kiosk"), key: localStorage.getItem("ozturk_cashier_kiosk")}; })()`);
			h.eq([chrome.navbar, chrome.head, chrome.kiosk, chrome.key], [true, true, false, null], "navbar + sahifa sarlavhasi ko'rinadi, kiosk izi yo'q");
		});
		await h.step("menejer: joylashuvni tahrirlash — stolni sudrash bazaga yoziladi", async () => {
			await h.op("actor", { user: MANAGER });
			await h.openCashier();
			await h.click(".rc-rooms button, .rc-rooms [data-room]", "Ichki zal");
			await h.waitFor(`ozturk.cashier.screen.room === "Ichki zal"`, 5000, "zal tanlandi");
			await h.click(".rc-menu-btn");
			await h.click(".rc-menu button, .rc-menu [data-id]", "Joylashuvni tahrirlash");
			await h.waitFor(`ozturk.cashier.screen.layoutEditMode === true`, 5000, "tahrirlash rejimi");
			const before = await h.one("select layout_x x, layout_y y, layout_width w, layout_height ht from `tabURY Table` where name = 'E2E-T3'");
			h.shared.layoutBefore = before;
			const pos = await h.locate({ selector: '.rc-table[data-table="E2E-T3"]' });
			const target = { x: pos.x + 60, y: pos.y - 50 };
			await h.cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: pos.x, y: pos.y });
			await h.cdp.send("Input.dispatchMouseEvent", { type: "mousePressed", x: pos.x, y: pos.y, button: "left", clickCount: 1 });
			for (let i = 1; i <= 8; i++) await h.cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: pos.x + (60 * i) / 8, y: pos.y - (50 * i) / 8, button: "left", buttons: 1 });
			await h.cdp.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: target.x, y: target.y, button: "left", clickCount: 1 });
			await h.waitIdle(800);
			const after = await h.one("select layout_x x, layout_y y from `tabURY Table` where name = 'E2E-T3'");
			h.check("layout_x/layout_y o'zgardi (sudrash saqlandi)", Number(after.x) !== Number(before.x) || Number(after.y) !== Number(before.y), `${JSON.stringify(before)} -> ${JSON.stringify(after)}`);
			await h.eval(`ozturk.cashier.screen.toggleLayoutEdit()`);
			await h.op("actor", { user: CASHIER });
			// Hujjatlangan qoida (api/table.py): joylashuv sotuvga tegishli emas — HAR QANDAY kassir o'zgartira oladi.
			// Interfeys esa tugmani faqat menejerga ko'rsatadi (ui/menu.js) — bu ataylab, siyosat savoli hisobotda.
			const byCashier = await h.apiAs(CASHIER, "table.update_table_layout", { table: "E2E-T3", x: 200, y: 300 });
			h.eq(byCashier.status, 200, "server: oddiy kassir ham joylashuvni saqlay oladi (hujjatlangan qoida)");
			// keyingi senariylar uchun stol joyi tiklanadi (aks holda E2E-T1 ustiga tushib qoladi)
			const b = h.shared.layoutBefore;
			await h.apiAs(CASHIER, "table.update_table_layout", { table: "E2E-T3", x: b.x, y: b.y, width: b.w, height: b.ht });
			await h.openCashier();
			const cashierMenu = await (async () => { await h.click(".rc-menu-btn"); const t = await h.allText(".rc-menu button, .rc-menu [data-id]"); await h.key("Escape"); return t; })();
			h.check("kassir menyusida «Joylashuvni tahrirlash» yo'q", !cashierMenu.some((t) => /Joylashuvni tahrirlash/.test(t)), JSON.stringify(cashierMenu));
		});
	},
};


scenarios.p = {
	title: "Ekran o'lchamlari 1024x768 va 1920x1080: sahifa/oyna sig'adi, asosiy tugmalar ko'rinadi va bosiladi",
	async run(h) {
		const fitsRoot = async (label) => {
			const r = await h.eval(`(() => { const root = document.querySelector(".rc-root").getBoundingClientRect(); return {top: root.top, bottom: root.bottom, right: root.right, iw: innerWidth, ih: innerHeight, sw: document.documentElement.scrollWidth}; })()`);
			h.check(`${label}: rc-root ekranga sig'adi (pastki chegara ${Math.round(r.bottom)} <= ${r.ih})`, r.bottom <= r.ih + 1 && r.right <= r.iw + 1 && r.sw <= r.iw + 1, JSON.stringify(r));
			// Desk navbari va sarlavhasi ko'rinib turadi, shunda ham sahifa aylanmaydi.
			const page = await h.eval(`(() => { const s = document.scrollingElement; const nav = document.querySelector(".navbar"); return {scroll: s.scrollHeight, ih: innerHeight, nav: !!nav && nav.getBoundingClientRect().height > 0, top: document.querySelector(".rc-root").getBoundingClientRect().top}; })()`);
			h.check(`${label}: sahifa aylanmaydi va Desk navbari ko'rinadi`, page.scroll <= page.ih + 1 && page.nav && page.top > 40, JSON.stringify(page));
		};
		const dialogFits = async (label, primary) => {
			const r = await h.eval(`(() => { const d = [...document.querySelectorAll(".rc-dialog, .rc-modal")].filter(e => e.getBoundingClientRect().width > 0).pop(); if (!d) return null; const b = d.getBoundingClientRect(); return {top: b.top, bottom: b.bottom, left: b.left, right: b.right, iw: innerWidth, ih: innerHeight}; })()`);
			h.check(`${label}: oyna ekran ichida`, r && r.top >= -1 && r.bottom <= r.ih + 1 && r.left >= -1 && r.right <= r.iw + 1, JSON.stringify(r));
			if (primary) {
				const pos = await h.locate({ selector: primary.selector, text: primary.text || null, scope: primary.scope || null, last: true });
				h.check(`${label}: asosiy tugma ko'rinadi va ustini hech narsa yopmagan`, pos.found && pos.hit && pos.y + pos.h / 2 <= (await h.eval("innerHeight")) + 1, JSON.stringify(pos));
				h.check(`${label}: asosiy tugma sensorli o'lchamda (>=48px)`, pos.found && pos.h >= 47, `h=${pos.h}`);
			}
		};
		try {
			for (const [w, hgt] of [[1024, 768], [1920, 1080]]) {
				const tag = `${w}x${hgt}`;
				await h.step(`${tag}: zal, buyurtma oynasi, to'lov (aralash + choychaqa), PIN`, async () => {
					await h.setViewport(w, hgt);
					await h.openCashier();
					await fitsRoot(`${tag} zal`);
					const invoice = await newOrder(h, { table: "E2E-T2", items: [{ item: "AFGAN BREAD", qty: 2 }, { item: "BARDAK TEA", qty: 2 }, { item: "COCA-COLA", qty: 1 }, { item: "PEPSI", qty: 1 }] });
					await selectTable(h, "E2E-T2");
					await fitsRoot(`${tag} panel`);
					// Yuqori paneldagi «+ Buyurtma» tugmasi tor ekranda faqat belgi (matnsiz) — data-id orqali topamiz.
					await h.click(".rc-topquick__btn", null, { index: 0 });
					await h.waitFor(`document.querySelector(".rc-orders-dlg .rc-orders-card")`, 8000, "menyu");
					await h.click('.rc-orders-card[data-item="PEPSI"]');
					await dialogFits(`${tag} buyurtma oynasi`, { selector: '.rc-orders-dlg [data-action="submit"]', scope: ".rc-orders-dlg" });
					// Esc buyurtma oynasini yopmaydi (savat yo'qolmasin) — «Bekor qilish» + tasdiq.
					await h.key("Escape");
					h.check(`${tag}: Esc savatli buyurtma oynasini yopmadi`, await h.eval(`!!document.querySelector(".rc-orders-dlg")`));
					await h.click('.rc-orders-dlg [data-action="cancel"]');
					await h.waitDialog("yopilsinmi");
					await h.clickDialog('[data-action="yes"]');
					await h.waitNoDialogAny();
					await giveBill(h);
					await h.click('.rc-panel [data-action="pay"]');
					await h.waitFor(`document.querySelector(".rc-modal .rc-pay")`, 8000, "to'lov oynasi");
					await dialogFits(`${tag} to'lov oynasi`, { selector: '.rc-modal [data-action="confirm"]' });
					await h.click(".rc-modal .rc-chip-opt", "10%");
					await h.waitIdle(300);
					await dialogFits(`${tag} to'lov usullari + choychaqa`, { selector: '.rc-modal [data-action="confirm"]' });
					await h.escapeAll();
					await h.waitFor(`document.querySelector(".rc-overlay").hidden`, 5000, "to'lov oynasi yopildi");
					// PIN oynasi
					h.expect("billing.apply_discount", 403, "ApprovalRequired");
					await moreAction(h, "Chegirma");
					await h.waitDialog("Chegirma turi");
					await dialogFits(`${tag} chegirma oynasi`, { selector: '.rc-dialog [data-action="apply"]' });
					await h.click(".rc-chip-opt", "20%", { scope: ".rc-dialog", last: true });
					await h.click(".rc-chip-opt", "Aksiya", { scope: ".rc-dialog", last: true });
					await h.clickDialog('[data-action="apply"]');
					await h.waitFor(`document.querySelector(".rc-dialog .rc-pin")`, 10000, "PIN oynasi");
					await dialogFits(`${tag} PIN oynasi`, { selector: ".rc-numpad__key", text: "✓", scope: ".rc-dialog" });
					await h.escapeAll();
					await h.waitNoDialogAny();
					await h.op("actor", { user: MANAGER });
					const c = await h.apiAs(MANAGER, "order.cancel_order", { order: invoice, reason: "E2E" });
					await h.op("actor", { user: CASHIER });
					h.eq(c.status, 200, "buyurtma tozalandi");
				});
			}
		} finally {
			await h.setViewport(1366, 768);
		}
	},
};


scenarios.q = {
	title: "Optimistik qulf (ofitsant bir vaqtda o'zgartirdi) va takroriy so'rovlar (client_ref, ikki marta to'lov)",
	async run(h) {
		let invoice;
		await h.step("tahrirlash oynasi ochiq turganda ofitsant buyurtmani o'zgartiradi", async () => {
			invoice = await newOrder(h, { table: "E2E-T1", items: [{ item: "AFGAN BREAD", qty: 1 }] });
			await selectTable(h, "E2E-T1");
			await moreAction(h, "Taom qo'shish");
			await h.waitFor(`document.querySelector(".rc-orders-dlg .rc-orders-card")`, 10000, "tahrirlash oynasi");
			const current = (await h.apiAs(WAITER, "waiter.get_order", { invoice }, { http: "GET" })).message;
			const r = await h.apiAs(WAITER, "waiter.submit_order", { table: "E2E-T1", items: [{ item: "AFGAN BREAD", item_name: "AFGAN BREAD", qty: 1, comment: "" }, { item: "COCA-COLA", item_name: "COCA-COLA", qty: 1, comment: "" }], last_modified_time: current.last_modified_time, client_ref: "e2e-waiter-add" });
			h.eq(r.status, 200, "ofitsant taom qo'shdi (server: " + r.status + ")");
		});
		await h.step("kassir eskirgan qulf bilan yuboradi -> xato oynada, savat saqlanadi", async () => {
			await h.click('.rc-orders-card[data-item="ARUGULA SALAD"]');
			h.expect("cashier_orders.add_items", 417, "ValidationError");
			await h.click('.rc-orders-dlg [data-action="submit"]');
			await h.waitFor(`document.querySelector(".rc-orders-dlg .rc-dialog__error") && document.querySelector(".rc-orders-dlg .rc-dialog__error").innerText.trim().length > 0`, 10000, "xato matni oynada");
			const err = await h.text(".rc-orders-dlg .rc-dialog__error");
			h.check("xato matni tushunarli (texnik matn emas)", err.length > 10 && !/Traceback|TypeError|\[object/.test(err), err);
			h.check("xato matni faqat o'zbekcha (URY inglizcha xabari qo'shilmagan)", /Buyurtma o'zgargan/.test(err) && !/This order has been modified/i.test(err), err);
			h.check("oyna yopilmadi, savatdagi taom saqlanib qoldi", (await h.eval(`!!document.querySelector('.rc-orders-dlg .rc-orders-line[data-item="ARUGULA SALAD"]')`)));
			const inv = await h.op("invoice", { name: invoice });
			h.eq(inv.items.map((i) => i.item).sort(), ["AFGAN BREAD", "COCA-COLA"], "chekda kassir taomi YO'Q, ofitsantnikisi bor");
			h.check("mavjud qatorlar ro'yxati yangilandi (COCA-COLA ko'rinadi)", (await h.text(".rc-orders-existing")).includes("COCA-COLA"), await h.text(".rc-orders-existing"));
		});
		await h.step("qayta yuborish (yangilangan qulf bilan) muvaffaqiyatli", async () => {
			await h.click('.rc-orders-dlg [data-action="submit"]');
			await h.waitFor(`!document.querySelector(".rc-orders-dlg")`, 15000, "oyna yopildi");
			await h.waitIdle(500);
			const inv = await h.op("invoice", { name: invoice });
			h.eq(inv.items.map((i) => i.item).sort(), ["AFGAN BREAD", "ARUGULA SALAD", "COCA-COLA"], "uchala taom chekda");
		});
		await h.step("client_ref takrori dublikat chek yaratmaydi; ikki marta to'lov rad etiladi", async () => {
			const args = { order_type: "Take Away", items: [{ item: "PEPSI", item_name: "PEPSI", qty: 1 }], client_ref: "e2e-dup-ref" };
			const a = await h.apiAs(CASHIER, "cashier_orders.create_order", args);
			const b = await h.apiAs(CASHIER, "cashier_orders.create_order", args);
			h.eq([a.status, b.status], [200, 200], "ikkala so'rov 200");
			h.eq(a.message.invoice, b.message.invoice, "bir xil chek qaytdi (dublikat yo'q)");
			const count = await h.one("select count(*) c from `tabPOS Invoice` where custom_client_ref = %s", ["e2e-dup-ref"]).catch(() => null);
			if (count) h.eq(Number(count.c), 1, "bazada bitta chek");
			const kots = await h.sql("select count(*) c from `tabURY KOT` where invoice = %s", [a.message.invoice]);
			h.eq(Number(kots[0].c), 1, "bitta KOT");
			const due = await payable(h, a.message.invoice);
			await payViaApi(h, a.message.invoice, [{ mode_of_payment: CASH, amount: due }]);
			const drawers = (await printJobs(h, "Drawer")).filter((j) => j.ref_name === a.message.invoice).length;
			h.expect("billing.submit_payment", 417, "ValidationError");
			const again = await h.apiAs(CASHIER, "billing.submit_payment", { invoice: a.message.invoice, payments: [{ mode_of_payment: CASH, amount: due }] });
			h.check("ikkinchi to'lov rad etiladi", again.status >= 400, `${again.status} ${JSON.stringify(again.data).slice(0, 200)}`);
			h.eq((await printJobs(h, "Drawer")).filter((j) => j.ref_name === a.message.invoice).length, drawers, "g'aladon ikkinchi marta ochilmadi");
			const pays = await h.sql("select count(*) c from `tabSales Invoice Payment` where parent = %s", [a.message.invoice]);
			h.eq(Number(pays[0].c), 1, "to'lov qatori bitta");
		});
		await h.step("GET orqali yozuvchi endpoint chaqirilsa ham bazaga yozilmaydi (Frappe GET ni rollback qiladi)", async () => {
			const open = await newOrder(h, { orderType: "Take Away", items: [{ item: "PEPSI", qty: 1 }] });
			const viaGet = await h.apiAs(CASHIER, "billing.open_bill", { invoice: open }, { http: "GET" });
			h.check("open_bill GET ni qabul qiladi (methods=['POST'] cheklovi yo'q — kuzatuv)", viaGet.status === 200, `${viaGet.status}`);
			h.eq(Number((await h.op("invoice", { name: open })).printed || 0), 0, "GET so'rovi chekni o'zgartirmadi");
			const jobs = (await printJobs(h, "Bill")).filter((j) => j.ref_name === open);
			h.eq(jobs.length, 0, "GET so'rovi chop etish topshirig'ini qoldirmadi");
		});
	},
};

module.exports = scenarios;
module.exports.prepare = prepare;
module.exports.CASHIER = CASHIER;
module.exports.MANAGER = MANAGER;
module.exports.PIN = PIN;
