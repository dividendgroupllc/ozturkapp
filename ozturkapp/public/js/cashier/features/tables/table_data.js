/**
 * Stol modullari uchun umumiy yordamchilar: server chaqiruvlari, vaqt va sana.
 *
 * Bu yerda biznes mantiq yo'q: stol holati, bron qoidalari va ruxsatlar
 * serverda. Bu fayl faqat serverdan kelgan qiymatlarni ko'rsatishga tayyorlaydi.
 */

const { hhmm } = ozturk.cashier.util;

/** Serverdagi funksiyalar — yozilishi to'liq: testlar shu qatorlarni tekshiradi. */
export const METHODS = {
	floorPlan: "ozturkapp.ozturkapp.api.table.get_floor_plan",
	transfer: "ozturkapp.ozturkapp.api.table.transfer_table",
	merge: "ozturkapp.ozturkapp.api.table.merge_tables",
	unmerge: "ozturkapp.ozturkapp.api.table.unmerge_table",
	reservations: "ozturkapp.ozturkapp.api.table.get_reservations",
	reserve: "ozturkapp.ozturkapp.api.table.reserve_table",
	cancelReservation: "ozturkapp.ozturkapp.api.table.cancel_reservation",
	printBill: "ozturkapp.ozturkapp.api.printing.print_bill",
};

/**
 * Filialdagi BARCHA stollarning yangi holati.
 *
 * `screen.floor` ishlatilmaydi: kassir bitta zalni tanlagan bo'lsa u faqat
 * shu zalning stollarini saqlaydi, ko'chirish esa boshqa zalga ham bo'lishi
 * mumkin. Yangi so'rov stollarning eng so'nggi holatini ham beradi — ikki
 * kassir bir stolni tanlab qolish ehtimoli kamayadi.
 *
 * «Olib ketish» stollari (`is_take_away`) — haqiqiy joy emas, ularga buyurtma
 * ko'chirilmaydi va bron qilinmaydi.
 */
export async function loadTables(screen) {
	const floor = await screen.call(METHODS.floorPlan, {});
	return (floor.tables || []).filter((table) => !cint(table.is_take_away));
}

/** Zallar tartibi (kassa konteksti bo'yicha). */
export function roomNames(screen) {
	return ((screen.ctx || {}).rooms || []).map((room) => room.name);
}

/**
 * Bron shu chekning mehmoniga tegishlimi.
 *
 * Server (`order_transfer._reservation_is_for`) xuddi shunday hal qiladi: bron
 * mijozi chekdagi mijoz bilan bir xil, yoki ism harf registrisiz mos. Bu yerda
 * FAQAT qulaylik: mos kelmaydigan stol bosilmasin. Oxirgi so'z serverniki.
 */
export function reservationIsFor(reservation, bill) {
	if (reservation.customer && reservation.customer === bill.customer) return true;

	const booked = String(reservation.customer_name || "")
		.trim()
		.toLowerCase();
	const seated = String(bill.customer_name || "")
		.trim()
		.toLowerCase();
	return !!booked && booked === seated;
}

// ═══════════════════════════════════════════════════════════════
//  Vaqt va sana
// ═══════════════════════════════════════════════════════════════

/**
 * `YYYY-MM-DD SS:DD[:SS]` matnini millisekundga aylantiradi.
 *
 * Sana va vaqt HAR IKKALASI serverdan keladi va bir xil tarzda o'qiladi, shu
 * sababli brauzer vaqt mintaqasi ayirmada yo'qoladi.
 */
function serverMs(text) {
	const match = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?/.exec(
		String(text || "")
	);
	if (!match) return NaN;
	const [, year, month, day, hour, minute, second] = match;
	return new Date(+year, +month - 1, +day, +hour, +minute, +(second || 0)).getTime();
}

/**
 * Bron boshlanishigacha necha daqiqa qoldi (o'tib ketgan bo'lsa manfiy).
 *
 * Zal rejasi yuklangan paytdagi server soati (`generated_at`) + shundan beri
 * o'tgan vaqt hisoblanadi: zal uzoq vaqt qayta yuklanmasa ham hisob to'g'ri.
 * Server soati yoki bron vaqti yo'q bo'lsa — `null`.
 */
export function minutesUntil(screen, reservation) {
	const generated = serverMs((screen.floor || {}).generated_at);
	const start = serverMs(`${reservation.date} ${hhmm(reservation.from_time)}`);
	if (Number.isNaN(generated) || Number.isNaN(start)) return null;

	const now = generated + (Date.now() - screen.floorLoadedAt);
	return (start - now) / 60000;
}

const WEEKDAYS_SHORT = [
	__("Yak"),
	__("Dush"),
	__("Sesh"),
	__("Chor"),
	__("Pay"),
	__("Jum"),
	__("Shan"),
];
const WEEKDAYS_LONG = [
	__("Yakshanba"),
	__("Dushanba"),
	__("Seshanba"),
	__("Chorshanba"),
	__("Payshanba"),
	__("Juma"),
	__("Shanba"),
];

/** `2026-09-20` -> `20.09` */
function dayMonth(iso) {
	return `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;
}

function weekdayOf(iso) {
	return new Date(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)).getDay();
}

/**
 * Kun tugmalari: Bugun / Ertaga / Indinga, keyin `Dush 22.09` ko'rinishida.
 * Sana kiritish maydoni sensorli ekranda noqulay, bron esa odatda bir haftalik.
 */
export function dayOptions(count = 7) {
	const today = frappe.datetime.get_today();
	const named = [__("Bugun"), __("Ertaga"), __("Indinga")];

	return Array.from({ length: count }, (_, offset) => {
		const value = offset ? frappe.datetime.add_days(today, offset) : today;
		const label =
			offset < named.length
				? named[offset]
				: `${WEEKDAYS_SHORT[weekdayOf(value)]} ${dayMonth(value)}`;
		return { value, label };
	});
}

/** `Shanba, 20.09.2026` — ro'yxat sarlavhasi uchun. */
export function longDay(iso) {
	return `${WEEKDAYS_LONG[weekdayOf(iso)]}, ${dayMonth(iso)}.${iso.slice(0, 4)}`;
}
