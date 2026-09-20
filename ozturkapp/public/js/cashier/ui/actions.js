/**
 * Panel amallari: bron, hisob berish, bekor qilish, stolni bo'shatish, chek chop etish.
 *
 * Kiritish kerak bo'lgan har bir amal (bron, bekor qilish sababi, ...) sensorli
 * forma (`ui.form`) bilan so'raydi — Desk'ning sichqoncha uchun yozilgan
 * oynalari emas.
 */

import { ui } from "../kit/index.js";

/** Buyurtmani bekor qilish uchun tayyor sabablar; «Boshqa…» — erkin matn. */
const CANCEL_REASONS = [__("Mijoz fikridan qaytdi"), __("Ofitsant xatosi"), __("Uzoq kutdi")];

export class ActionsMethods {
	/** Bo'sh stolni bron qilish. Stol BAND BO'LMAYDI — faqat RESERVED. */
	reserveTable(detail) {
		// Vaqt matn sifatida yozilmaydi: soat va daqiqa tugmalardan tanlanadi —
		// sensorli ekranda tezroq va xatosizroq.
		return ui.form({
			title: __("{0} — bron qilish", [detail.table]),
			fields: [
				{ type: "text", name: "customer_name", label: __("Mehmon ismi"), required: true },
				{ type: "tel", name: "phone", label: __("Telefon") },
				{ type: "time", name: "time", label: __("Bron vaqti") },
				{ type: "textarea", name: "notes", label: __("Izoh") },
			],
			submitLabel: __("Bron qilish"),
			onSubmit: async (values) => {
				await this.call("ozturkapp.ozturkapp.api.table.reserve_table", {
					table: detail.table,
					customer_name: values.customer_name,
					phone: values.phone,
					from_time: `${values.time}:00`,
					notes: values.notes,
				});
				ui.toast(__("{0} bron qilindi", [detail.table]), { indicator: "orange" });
				await this.refreshAll();
			},
		});
	}

	/** Bronni yechish — stol yana bo'sh bo'ladi. */
	async cancelReservation(detail) {
		const reservation = (detail.reservation || {}).name;

		const yes = await ui.confirm({
			title: __("Bronni bekor qilish"),
			message: __("{0} stolidagi bron bekor qilinsinmi?", [detail.table]),
			confirmLabel: __("Ha, bekor qilish"),
			cancelLabel: __("Yo'q"),
			kind: "danger-solid",
		});
		if (!yes) return;

		try {
			await this.call("ozturkapp.ozturkapp.api.table.cancel_reservation", {
				table: detail.table,
				reservation,
			});
			ui.toast(__("Bron bekor qilindi"));
			await this.refreshAll();
		} catch (error) {
			this.alertError(error);
		}
	}

	/**
	 * Hisobni mijozga berish.
	 *
	 * Bitta amalda ikkita ish bajariladi:
	 *   1. `open_bill` — chek "berilgan" deb belgilanadi (`invoice_printed=1`).
	 *      URY buni TALAB QILADI: `ury/hooks/ury_pos_invoice.py:validate_invoice_print`
	 *      stolga bog'langan chekni bu bayroqsiz submit qilishga yo'l qo'ymaydi.
	 *   2. chek chop etiladi va mijozga beriladi.
	 *
	 * Ya'ni "hisobni berish" — mijozga chekni taqdim etish amali.
	 */
	async giveBill(detail, button) {
		try {
			this.busy(button, true);
			await this.call("ozturkapp.ozturkapp.api.billing.open_bill", {
				invoice: detail.bill.invoice,
			});
			this.printReceipt(detail);
			ui.toast(__("Hisob berildi"), { indicator: "blue" });
			await this.refreshAll();
		} catch (error) {
			this.alertError(error);
		} finally {
			this.busy(button, false);
		}
	}

	/**
	 * Buyurtmani bekor qilish — ofitsant xato zakaz olib qo'yganda.
	 *
	 * QOIDA (serverda majburlanadi, `utils/order_cancel.py`)
	 * ======================================================
	 *     Oshxona hali BOSHLAMAGAN  ->  har qanday kassir
	 *     Oshxona BOSHLAB YUBORGAN  ->  faqat menejer
	 *
	 * "Boshlangan" = kamida bitta taom `URY KOT Items.custom_kitchen_status`
	 * da «Kutilmoqda» dan chiqib ketgan. `URY KOT.start_time_prep` ga
	 * QARALMAYDI — u KOT yaratilganda to'ladigan maydon.
	 *
	 * Chek O'CHIRILMAYDI: `custom_cancelled = 1` bo'lib bazada qoladi,
	 * sabab esa hisobotga tushadi. Shuning uchun sabab majburiy — tayyor
	 * sabablardan biri yoki «Boshqa…» (erkin matn, ekran klaviaturasi bilan).
	 */
	cancelOrder(detail) {
		const bill = detail.bill || {};
		const cancellation = bill.cancellation || {};

		return ui.form({
			title: cancellation.requires_supervisor
				? __("Majburan bekor qilish")
				: __("Buyurtmani bekor qilish"),
			subtitle: detail.table || bill.invoice,
			// Menejer majburan bekor qilayotgan bo'lsa — taom allaqachon
			// pishayotganini AYTIB turamiz, u chiqindiga ketadi.
			note: cancellation.warning ? `⚠ ${cancellation.warning}` : "",
			fields: [
				{
					type: "select",
					name: "reason",
					label: __("Bekor qilish sababi"),
					required: true,
					options: CANCEL_REASONS,
					columns: 1,
					other: { label: __("Boshqa…"), placeholder: __("Sababni yozing") },
				},
			],
			submitLabel: __("Bekor qilish"),
			kind: "danger-solid",
			onSubmit: async (values) => {
				const result = await this.call("ozturkapp.ozturkapp.api.order.cancel_order", {
					order: bill.invoice,
					reason: values.reason,
				});

				ui.toast(__("{0} bekor qilindi", [bill.invoice]), { indicator: "orange" });

				// Oshxonaga chipta ketgan bo'lsa — kassir buni bilishi
				// kerak: oshpaz ekranida ham chipta yopildi.
				if (result && cint(result.cancelled_items)) {
					ui.toast(
						__("Oshxonaga xabar berildi ({0} taom)", [cint(result.cancelled_items)]),
						{
							indicator: "blue",
						}
					);
				}

				this.clearSelection();
				await this.refreshAll();
			},
		});
	}

	/**
	 * Stolni majburan bo'shatish — FAQAT buzilgan holat uchun.
	 *
	 * Oddiy ish jarayonida bu KERAK EMAS: stol to'lov qilinganda avtomatik
	 * bo'shaydi. Bu metod faqat `STALE_OCCUPIED_FLAG` holatida (stol band,
	 * lekin buyurtma yo'q) menejerga ko'rsatiladi — busiz bunday stolni
	 * hech qachon tozalab bo'lmaydi.
	 */
	releaseTable(detail) {
		return ui.form({
			title: __("Stolni bo'shatish"),
			subtitle: detail.table,
			fields: [
				{
					type: "textarea",
					name: "reason",
					label: __("Bo'shatish sababi"),
					required: true,
				},
			],
			submitLabel: __("Bo'shatish"),
			kind: "danger-solid",
			onSubmit: async (values) => {
				await this.call("ozturkapp.ozturkapp.api.table.release_table", {
					table: detail.table,
					reason: values.reason,
				});
				ui.toast(__("{0} bo'shatildi", [detail.table]));
				await this.refreshAll();
			},
		});
	}

	/**
	 * Mijoz uchun chekni chop etish.
	 *
	 * Frappe'ning STANDART chop etish ko'rinishi ochiladi va brauzerning
	 * chop etish oynasi darhol chiqadi (`trigger_print=1`). Shu tufayli
	 * hech qanday printer sozlamasi TALAB QILINMAYDI — kassir istalgan
	 * ulangan printerga (jumladan chek printeriga) chiqara oladi.
	 *
	 * URY'ning QZ/network printer oqimiga TEGILMAYDI — u oshxona cheki
	 * (KOT) uchun va o'z holicha ishlashda davom etadi.
	 */
	async printReceipt(detail) {
		const invoice = (detail.bill || {}).invoice;
		if (!invoice) return;

		// Chek TARMOQ PRINTERIGA chiqadi — server navbati va monoblokdagi
		// agent orqali, xuddi oshxonadagi "Tayyor" tugmasi kabi. Kassir
		// tugmani bosadi, chek printerdan chiqadi; brauzer oynasi O'ZI
		// OCHILMAYDI.
		let queued = null;
		let failure = null;

		try {
			// `this.call()` ATAYLAB ishlatilmaydi: u faqat `r.message` ni
			// qaytaradi va serverning `r.exc` xatosini yutib yuboradi. Bu
			// yerda nosozlik SABABI kerak, aks holda u yana yashirin qoladi.
			const response = await frappe.call({
				method: "ozturkapp.ozturkapp.api.printing.print_bill",
				args: { invoice },
				freeze: false,
				silent: true,
			});
			const result = (response && response.message) || null;

			if (response && response.exc) {
				failure = this.errorText(response);
			} else if (result && result.queued) {
				queued = result;
			} else if (result && result.reason === "no_printer") {
				failure = __("Filialga kassa printeri biriktirilmagan (Ozturk Printer).");
			} else {
				failure = __("Server chekni navbatga qo'ymadi (bo'sh javob).");
			}
		} catch (error) {
			failure = this.errorText(error);
		}

		if (queued) {
			ui.toast(
				queued.agent_online
					? __("Chek printerga yuborildi")
					: __("Chek navbatga qo'yildi — print-agent hozir oflayn"),
				{ indicator: queued.agent_online ? "green" : "orange" }
			);
			return;
		}

		// Bu yerga tushdik — chek printerga BORMADI. Ilgari kod shu joyda
		// jimgina brauzer oynasini ochardi: kassir nosozlikni sezmasdi,
		// Error Log bo'sh qolardi va sabab hech qayerda ko'rinmasdi.
		// Endi sabab ekranda; brauzer yo'li faqat kassir O'ZI tanlasa.
		const openBrowser = await ui.confirm({
			title: __("Chek printerga chiqmadi"),
			message: failure || __("Noma'lum xato"),
			confirmLabel: __("Brauzer orqali chop etish"),
			cancelLabel: __("Yopish"),
			kind: "primary",
		});
		if (openBrowser) this.openBrowserPrint(invoice);
	}

	/**
	 * Zaxira yo'l — brauzerning chop etish oynasi.
	 *
	 * FAQAT kassir o'zi tanlaganda chaqiriladi (`printReceipt` xato
	 * dialogidagi tugma). Avtomatik ochilmaydi.
	 */
	openBrowserPrint(invoice) {
		const params = new URLSearchParams({
			doctype: "POS Invoice",
			name: invoice,
			format: this.ctx.print_format || "POS Invoice",
			no_letterhead: "0",
			_lang: frappe.boot.lang || "uz",
			trigger_print: "1",
		});

		window.open(`/printview?${params.toString()}`, "_blank", "noopener");
	}
}
