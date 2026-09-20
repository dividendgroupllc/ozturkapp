/**
 * Realtime hodisalari (TZ §13): obuna, bildirishnoma, qayta so'rovni birlashtirish.
 */

import { slots } from "./slots.js";
import { ui } from "../kit/index.js";

/** Realtime signaldan keyin qayta so'rovni birlashtirish oynasi (ms). */
const REFRESH_DEBOUNCE = 250;

/**
 * Signallar tinimsiz kelsa ham yangilanish shundan kech qolmaydi (ms). Faqat
 * «oxirgi signaldan 250ms keyin» qoidasi bo'lsa, har 200ms da hodisa kelib
 * turgan band soatda ekran umuman yangilanmay qolardi.
 */
const REFRESH_MAX_WAIT = 2000;

/**
 * Realtime ulanishi uzilganda ekran shuncha vaqtda (ms) bir marta o'zi yangilanadi.
 * Aks holda signal kelmaydi va zal rejasi kassir qo'lda «Yangilash» bosmaguncha
 * eskirgan holda qolardi (faqat kichik qizil nuqta ogohlantiradi).
 */
const OFFLINE_POLL = 20 * 1000;

/** URY va Desktop POS ning mavjud hodisalari — qayta ishlatamiz. */
const LEGACY_EVENTS = [
	"reload_ro",
	"pos_invoice_updated",
	"table_freed",
	"pending_order_cancelled",
];

export class RealtimeMethods {
	// ═══════════════════════════════════════════════════════════
	//  Realtime (TZ §13)
	// ═══════════════════════════════════════════════════════════

	subscribe() {
		// `boot()` bir necha marta chaqirilishi mumkin (smena ochilgach, qayta
		// urinishda) — eski obunalar yechilmasa har hodisa bir necha marta
		// ishlab, ovoz ham ikki marta chalinardi.
		//
		// FAQAT yadro obunalari yechiladi: funksiya modullari `install()` da
		// `screen.listen()` bilan o'z obunasini oladi, `install` esa bir marta
		// chaqiriladi — hammasini yechsak ular jimgina yo'qolib qolardi.
		this.unsubscribeCore();
		this.subscribingCore = true;

		const events = this.ctx.events || {};

		// Xabar faqat identifikator tashiydi — ma'lumot API orqali QAYTA
		// so'raladi, chunki serverda ruxsat tekshiriladi (TZ §17).

		// Stol holati o'zgardi -> zal rejasi + buyurtmalar.
		this.listen(events.floor, (data) => {
			if (!this.isOurBranch(data)) return;
			if (!this.touchesVisibleTables(data)) return; // boshqa zal — e'tibor bermaymiz
			this.scheduleRefresh({
				floor: true,
				orders: true,
				panel: this.touchesSelection(data),
			});
		});

		// Buyurtma o'zgardi -> ro'yxat; zal rejasi faqat ko'rinadigan stol
		// bo'lsa; panel faqat AYNAN tanlangan stol bo'lsa.
		this.listen(events.order, (data) => {
			if (!this.isOurBranch(data)) return;
			this.scheduleRefresh({
				floor: this.touchesVisibleTables(data),
				orders: true,
				panel: this.touchesSelection(data),
			});
		});

		// Oshxona taom holatini o'zgartirdi -> chek panelidagi "🍳 ..."
		// ko'rsatkichi va buyurtmalar ro'yxatidagi teg.
		//
		// NEGA ALOHIDA KANAL KERAK
		// ========================
		// Oshpaz holatni `URY KOT Items` da o'zgartiradi, POS Invoice'ga
		// TEGMAYDI. Ya'ni `on_pos_invoice_change` ishga tushmaydi va
		// `ozturk_cashier_order` CHIQMAYDI. Bu kanalsiz kassa oshxona
		// holatini qo'lda yangilanmaguncha eski holicha ko'rsatardi.
		this.listen(events.kitchen_item, (data) => {
			if (!this.isOurBranch(data)) return;
			this.scheduleRefresh({
				// Zal rejasida oshxona holati ko'rsatilmaydi — uni bezovta qilmaymiz.
				floor: false,
				orders: true,
				panel: this.touchesSelection(data),
			});
		});

		// Ofitsant hisob so'radi -> KASSIRGA ko'rinadigan xabar.
		this.listen(events.notify, (data) => this.showNotification(data, "cashier"));

		// URY va Desktop POS ning eski hodisalari qamrovni bildirmaydi —
		// ular uchun to'liq yangilash qilamiz.
		LEGACY_EVENTS.forEach((event) => {
			this.listen(event, () =>
				this.scheduleRefresh({ floor: true, orders: true, panel: true })
			);
		});

		// Funksiya modullarining hodisalari (`realtime` slot): `event` — nom yoki
		// `(screen) => nom`, `handler(data, screen)`.
		slots.items("realtime").forEach((item) => {
			const event = typeof item.event === "function" ? item.event(this) : item.event;
			this.listen(event, (data) => item.handler(data, this));
		});

		this.subscribingCore = false;
		this.updateLiveIndicator();
		clearInterval(this.liveTimer);
		this.liveTimer = setInterval(() => this.updateLiveIndicator(), 5000);
	}

	/** Barcha obunalar (`destroy`). */
	unsubscribe() {
		this.subscriptions.forEach(([event, handler]) => frappe.realtime.off(event, handler));
		this.subscriptions = [];
		clearInterval(this.liveTimer);
	}

	/** Faqat `subscribe()` o'zi qo'shgan obunalar — modullarniki qoladi. */
	unsubscribeCore() {
		this.subscriptions = this.subscriptions.filter(([event, handler, core]) => {
			if (core) frappe.realtime.off(event, handler);
			return !core;
		});
	}

	/**
	 * KO'RINADIGAN bildirishnoma — banner + ovoz.
	 *
	 * NEGA JIM YANGILASH YETMAYDI
	 * ===========================
	 * Qolgan realtime hodisalari ekranni jim yangilaydi. Xodim aynan
	 * o'sha joyga qarab turmasa o'zgarishni SEZMAYDI — hisob so'rovi ham,
	 * yangi buyurtma ham e'tibordan chetda qolardi.
	 *
	 * Ovoz `try` ichida: brauzer foydalanuvchi sahifaga tegmaguncha
	 * audio'ni bloklaydi, va bu holda banner baribir ko'rinishi kerak.
	 */
	showNotification(data, audience) {
		if (!this.isOurBranch(data)) return;
		if (!data || data.audience !== audience) return;

		ui.toast(data.body || "", {
			title: data.title || "",
			indicator: data.kind === "BILL_REQUESTED" ? "orange" : "green",
			seconds: 15,
		});
		// Ovoz faqat sahifa ko'rinib turganda: boshqa Desk sahifasida xabar ko'rinmaydi,
		// sababsiz signal esa chalg'itadi (README §4: fon vazifalari `active` da).
		if (this.active) this.beep();

		// «Hisob so'radi» belgisi (🔔) stol va buyurtma qatorida turadi —
		// signal kelishi bilan ko'rinishi uchun ro'yxatlar yangilanadi.
		if (data.kind === "BILL_REQUESTED") {
			this.scheduleRefresh({ floor: true, orders: true, panel: this.touchesSelection(data) });
		}
	}

	beep() {
		try {
			const Ctx = window.AudioContext || window.webkitAudioContext;
			if (!Ctx) return;
			this.audio = this.audio || new Ctx();
			const osc = this.audio.createOscillator();
			const gain = this.audio.createGain();
			osc.connect(gain);
			gain.connect(this.audio.destination);
			osc.frequency.value = 880;
			gain.gain.setValueAtTime(0.15, this.audio.currentTime);
			gain.gain.exponentialRampToValueAtTime(0.001, this.audio.currentTime + 0.35);
			osc.start();
			osc.stop(this.audio.currentTime + 0.35);
		} catch (e) {
			// Ovoz ishlamasa ham banner ko'rinadi — to'xtatmaymiz.
		}
	}

	listen(event, handler) {
		if (!event) return;
		frappe.realtime.on(event, handler);
		this.subscriptions.push([event, handler, !!this.subscribingCore]);
	}

	isOurBranch(data) {
		return !(data && data.branch && data.branch !== this.ctx.branch);
	}

	/** Hodisa hozir EKRANDA turgan stollarga tegishlimi? */
	touchesVisibleTables(data) {
		const tables = (data && data.tables) || (data && data.table ? [data.table] : []);
		if (!tables.length) return true; // qamrov noma'lum — ehtiyot uchun yangilaymiz

		const visible = new Set((this.floor?.tables || []).map((t) => t.name));
		if (!visible.size) return true;

		return tables.some((name) => visible.has(name));
	}

	/** Hodisa AYNAN tanlangan stol yoki uning chekiga tegishlimi? */
	touchesSelection(data) {
		if (!data) return false;

		// Stolsiz buyurtmada (olib ketish, Desktop POS) `selectedTable`
		// bo'sh bo'ladi — tanlov faqat chek nomida yashaydi.
		if (!this.selectedTable && !this.selectedInvoice) return false;

		if (data.invoice && data.invoice === this.selectedInvoice) return true;

		if (this.selectedTable) {
			if (data.table === this.selectedTable) return true;
			if (Array.isArray(data.tables) && data.tables.includes(this.selectedTable)) {
				return true;
			}
		}

		// Qamrov ko'rsatilmagan bo'lsa — panelni ham yangilaymiz.
		return !data.table && !data.invoice && !(data.tables || []).length;
	}

	scheduleRefresh(scope = {}) {
		// Ketma-ket kelgan signallarni BITTA so'rovga birlashtiramiz va
		// qamrovlarini yig'amiz (biri panelni so'rasa — panel yangilanadi).
		// Qamrov kalitlari `floor`, `orders`, `panel` va funksiya modullari
		// qo'shgan istalgan kalit (`refresh` slotiga uzatiladi).
		this.pendingScope = { ...(this.pendingScope || {}) };
		Object.entries(scope).forEach(([key, value]) => {
			if (value) this.pendingScope[key] = true;
		});

		// Birinchi signaldan beri REFRESH_MAX_WAIT o'tgan bo'lsa kutish to'xtaydi.
		const now = Date.now();
		if (!this.pendingSince) this.pendingSince = now;
		const wait = Math.max(0, Math.min(REFRESH_DEBOUNCE, this.pendingSince + REFRESH_MAX_WAIT - now));

		clearTimeout(this.refreshTimer);
		this.refreshTimer = setTimeout(() => {
			if (this.destroyed) return;
			const pending = this.pendingScope;
			this.pendingScope = null;
			this.pendingSince = 0;
			// Xato bu yerda yutilmasa «unhandled rejection» bo'lib jim qolardi va ekran
			// eskirganini kassir bilmasdi.
			this.refresh(pending).catch((error) => this.markStale(error));
		}, wait);
	}

	/** Yangilash yiqildi: ekran eskirgan — jonli nuqta qizarib, sababini aytadi. */
	markStale(error) {
		console.error("refresh: ekran yangilanmadi", error);
		this.stale = true;
		this.updateLiveIndicator();
	}

	updateLiveIndicator() {
		const connected = !!(frappe.realtime.socket && frappe.realtime.socket.connected);
		this.el.live.classList.toggle("rc-live--down", !connected || !!this.stale);

		if (
			!connected &&
			this.active &&
			this.state === "ready" &&
			Date.now() - (this.lastRefreshAt || 0) > OFFLINE_POLL
		) {
			this.scheduleRefresh({ floor: true, orders: true, panel: true });
		}
		this.el.live.title = this.stale
			? __("Ma'lumot yangilanmadi — internetni tekshiring")
			: connected
			? __("Jonli")
			: __("Ulanish yo'q");
	}
}
