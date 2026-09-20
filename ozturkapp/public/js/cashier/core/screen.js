/**
 * Kassa oynasi — asosiy ekran sinfi (TZ §26).
 *
 * ═══════════════════════════════════════════════════════════════
 *  BU YERDA BIZNES MANTIQ YO'Q
 * ═══════════════════════════════════════════════════════════════
 *  - Stol holati (AVAILABLE / RESERVED / OCCUPIED) — serverda hisoblanadi.
 *  - Oraliq summa, xizmat haqi, jami — serverdan TAYYOR holda keladi.
 *  - Ruxsatlar serverda tekshiriladi; bu yerdagi `disabled` faqat qulaylik.
 *
 *  Foiz (12%) bu kodda UMUMAN yo'q va bo'lmasligi ham kerak (TZ §8).
 *
 * ═══════════════════════════════════════════════════════════════
 *  YANGILANISH STRATEGIYASI (TZ §13, §25)
 * ═══════════════════════════════════════════════════════════════
 *  Sahifa ochilganda bir marta to'liq yuklanadi. Keyin realtime
 *  signali kelganda faqat kerakli qism qayta so'raladi. Qo'lda
 *  yangilash (F5) talab qilinmaydi.
 *
 *  O'z hodisalarimizdan tashqari URY va Desktop POS ning MAVJUD
 *  hodisalariga ham obuna bo'lamiz — yangisini yozmaymiz.
 *
 * ═══════════════════════════════════════════════════════════════
 *  TUZILISHI
 * ═══════════════════════════════════════════════════════════════
 *  `CashierScreen` — holat va hayot sikli. Har bir mas'uliyat (yuqori
 *  panel, zal, panel, to'lov, ...) alohida modulda sinf sifatida yoziladi
 *  va metodlari shu sinfga QO'SHILADI (`mixin`), ya'ni tashqaridan
 *  hammasi bitta `screen` obyekti bo'lib ko'rinadi. Shu tufayli yangi
 *  funksiya modullari (`features/`) faqat `screen` bilan ishlaydi.
 */

import { mixin } from "./mixin.js";
import { features } from "./features.js";
import { HelperMethods } from "./helpers.js";
import { syncCompact, syncModes, syncOffset } from "./layout.js";
import { RealtimeMethods } from "./realtime.js";
import { ShortcutManager } from "./shortcuts.js";
import { slots } from "./slots.js";
import { trapTab } from "../kit/dialog.js";
import { ui } from "../kit/index.js";
import { elapsedLevel, formatElapsed } from "../util/format.js";
import { ActionsMethods } from "../ui/actions.js";
import { FloorMethods } from "../ui/floor.js";
import { HistoryMethods } from "../ui/history.js";
import { MenuMethods } from "../ui/menu.js";
import { ModalMethods } from "../ui/modal.js";
import { OrdersMethods } from "../ui/orders.js";
import { PanelMethods } from "../ui/panel.js";
import { PaymentMethods } from "../ui/payment.js";
import { ShiftMethods } from "../ui/shift.js";
import { SplitMethods } from "../ui/split.js";
import { TopbarMethods } from "../ui/topbar.js";
import "../ui/shortcuts.js";

/** Soat va o'tgan vaqt shu oraliqda yangilanadi (ms). */
const TICK_INTERVAL = 10 * 1000;

export class CashierScreen {
	constructor(page) {
		this.page = page;
		this.ctx = null;
		this.floor = null;
		this.orders = null;

		this.room = null;
		this.statusFilter = "ALL";
		this.orderFilter = "all";
		this.view = this.readPreference("view") === "orders" ? "orders" : "floor";
		this.selectedTable = null;
		this.layoutEditMode = false;
		this.floorScale = 1;

		this.selectedInvoice = null;
		this.worklistOpen = false; // o'ng panel hozir faol buyurtmalar ro'yxatinimi (keng ekran, tanlov yo'q)
		this.panelOrders = null; // shu ro'yxatning {tabs, list} tugunlari
		this.detail = null; // oxirgi chizilgan panel tafsiloti
		this.infoOpen = null; // `panel.info` «+N yana» ochiqmi

		// Ma'lumot QAYSI PAYTDA yuklangani — o'tgan vaqtni brauzer o'zi
		// ketma-ket oshirib borishi uchun (`tickElapsed`).
		this.floorLoadedAt = 0;
		this.ordersLoadedAt = 0;

		this.state = "loading";
		this.active = false;
		this.installedFeatures = new Map();
		this.shortcuts = new ShortcutManager(this);

		this.subscriptions = [];
		this.timers = [];
		this.refreshTimer = null;
		this.liveTimer = null;
		this.pendingScope = null;
		this.destroyed = false;

		// Sahifada bitta ekran bor; nosozliklarni qidirishda konsoldan ochiladi.
		ozturk.cashier.screen = this;

		this.mount();
		this.boot();
	}

	// ═══════════════════════════════════════════════════════════
	//  Karkas
	// ═══════════════════════════════════════════════════════════

	mount() {
		$(this.page.main).html(frappe.render_template("restaurant_cashier", {}));
		this.$root = $(this.page.main).find(".rc-root");

		const find = (selector) => this.$root.find(selector)[0];
		this.el = {
			rooms: find(".rc-rooms"),
			shift: find(".rc-shift"),
			cashier: find(".rc-cashier"),
			clock: find(".rc-clock"),
			live: find(".rc-live"),
			quick: find(".rc-topquick"),
			menuButton: find(".rc-menu-btn"),
			menu: find(".rc-menu"),
			warnings: find(".rc-warnings"),
			filters: find(".rc-filters"),
			viewTabs: find(".rc-viewtabs"),
			floorToolbar: find(".rc-floor__toolbar"),
			canvas: find(".rc-floor__canvas"),
			floorStage: find(".rc-floor__stage"),
			floorScroll: find(".rc-floor__scroll"),
			floorEmpty: find(".rc-floor__empty"),
			panel: find(".rc-panel"),
			orderTabs: find(".rc-orders__tabs"),
			orderList: find(".rc-orders__list"),
			bootError: find(".rc-boot--error .rc-boot__message"),
			gate: find(".rc-gate__form"),
			overlay: find(".rc-overlay"),
			modalBody: find(".rc-modal__body"),
		};

		this.$root.on("click", ".rc-retry", () => this.boot());
		this.$root.on("click", ".rc-modal__close", () => this.closeModal());
		this.$root.on("click", ".rc-overlay", (e) => {
			if (e.target === this.el.overlay) this.closeModal();
		});
		this.$root.on("click", ".rc-menu-btn", () => this.toggleMenu());
		// Modal oynada Tab tugmasi tashqariga chiqmasin (kichik oynalarniki `kit/dialog.js` da).
		this.$root.on("keydown", ".rc-modal", (e) => trapTab(e.currentTarget, e.originalEvent));
		this.$root.on("click", ".rc-topquick__btn", (e) => this.onQuickClick(e.currentTarget));

		// Zal rejasi va panel ma'lumot bloklari konteyner o'lchamiga qarab qayta
		// sig'diriladi: oyna o'lchami yoki Desk panellari ularni o'zgartirishi mumkin.
		this.resizeObserver = new ResizeObserver(() => {
			this.syncLayout();
			this.fitPanelInfo();
		});
		this.resizeObserver.observe(this.$root[0]);
		this.resizeObserver.observe(this.el.floorScroll);
		this.resizeObserver.observe(this.el.panel);
		this.onWindowResize = () => this.syncLayout();

		syncModes(this.$root[0]);
		this.clearSelection();
		this.timers.push(setInterval(() => this.tick(), TICK_INTERVAL));
	}

	/** Ilova keng ekranda mi (`data-wide`, `core/layout.js`): o'ng panel bo'sh turganda buyurtmalar ro'yxatini ko'rsatadi. */
	get wide() {
		return this.$root[0].dataset.wide === "1";
	}

	/** Kenglik sinfi o'zgardi: buyurtmalar ro'yxati va bo'sh panel yangi joyda qayta chiziladi. */
	onLayoutModeChange() {
		if (!this.selectedTable && !this.selectedInvoice) this.clearSelection();
		else this.renderOrders();
	}

	setState(state) {
		this.state = state;
		this.$root.attr("data-state", state);
		this.syncLayout();
	}

	/**
	 * Ilova o'lchamini Desk sahifasi ichidagi HAQIQIY joyga moslaydi.
	 *
	 * Kassa oddiy Desk sahifasi: navbar va sahifa sarlavhasi doim ko'rinadi,
	 * shuning uchun balandlik `--rc-offset` (ilova ustidagi/ostidagi Desk joyi)
	 * bilan hisoblanadi va sahifa aylanmaydi. Kenglik sinfi (`data-wide`) o'zgarsa
	 * buyurtmalar ro'yxati va o'ng panel qayta chiziladi.
	 */
	syncLayout() {
		const root = this.$root[0];
		syncOffset(root);
		const widthChanged = syncModes(root);
		syncCompact(root);
		this.fitFloor();
		if (widthChanged && this.state === "ready") this.onLayoutModeChange();
	}

	/** Soat va o'tgan vaqt ko'rsatkichlarini yangilaydi. */
	tick() {
		this.tickClock();
		this.tickElapsed();
	}

	/**
	 * O'tgan vaqt: serverdan yuklangan qiymat + yuklangandan beri o'tgan vaqt.
	 * Rang ham vaqt bilan o'zgaradi (`elapsedLevel`).
	 */
	tickElapsed() {
		const now = Date.now();
		this.$root.find("[data-elapsed]").each((_, node) => {
			const minutes =
				cint(node.dataset.elapsed) + Math.floor((now - cint(node.dataset.since)) / 60000);
			node.textContent = formatElapsed(minutes, node.dataset.short === "1");
			node.dataset.level = elapsedLevel(minutes);
		});
	}

	// ═══════════════════════════════════════════════════════════
	//  Yuklash
	// ═══════════════════════════════════════════════════════════

	async boot() {
		this.setState("loading");
		try {
			this.ctx = await this.call("ozturkapp.ozturkapp.api.cashier.get_cashier_context");

			// Standart holat — BARCHA ZALLAR (`room = null`).
			//
			// Kassir odatda butun restoranni bir ekranda ko'rishi kerak;
			// bitta zalga cheklash uning ishini qiyinlashtiradi. Zal
			// tanlansa, tanlov eslab qolinadi va keyingi safar o'sha
			// ochiladi — "barcha zallar" ham to'liq huquqli tanlov.
			const rooms = this.ctx.rooms || [];
			const saved = this.readPreference("room");

			// `null`  -> hech qachon tanlanmagan  -> barcha zallar
			// `""`    -> foydalanuvchi ATAYLAB "barcha zallar" ni tanlagan
			// `"..."` -> aniq zal
			this.room = saved ? saved : null;

			// Saqlangan zal o'chirilgan bo'lsa — barcha zallarga qaytamiz.
			if (this.room && !rooms.some((r) => r.name === this.room)) {
				this.room = null;
			}

			// UI to'plami va funksiya modullari kontekst yuklangach ishga tushadi:
			// ular POS Profile bayroqlariga (`ctx.features`) tayanadi.
			ui.configure({
				root: this.$root[0],
				virtualKeyboard: this.hasVirtualKeyboard(),
			});
			features.sync(this);

			this.renderTopbar();
			this.renderWarnings();
			this.renderRooms();
			this.renderFloorToolbar();
			this.renderOrderTabs();
			this.setView(this.view);
			this.subscribe();

			// ── Smena yopiq bo'lsa kim nima ko'radi ───────────────────
			//
			// KASSIR (kassani ocha oladigan) — bloklovchi ekran. Stol ham,
			// buyurtma ham ko'rsatilmaydi: sotuv smenasiz mumkin emas,
			// ya'ni uning yagona mumkin bo'lgan amali — kassani ochish.
			//
			// ADMINISTRATOR / MENEJER (ocholmaydigan) — bloklanmaydi. U
			// kuzatish uchun kiradi: zal, buyurtmalar va cheklar ko'rinadi.
			// Kassa yopiqligi yuqori paneldagi QIZIL «Kassa yopiq» yozuvida
			// turadi. Sotuv amallarini server baribir rad etadi
			// (`cashier_permissions.assert_shift_open`), shuning uchun
			// bloklovchi ekran unga faqat xalaqit berardi.
			const canOperate = !!(this.ctx.permissions || {}).can_operate_shift;

			if (!(this.ctx.shift || {}).open && canOperate) {
				this.renderShiftGate();
				this.setState("shift");
				return;
			}

			await this.refreshAll();
			this.setState("ready");
		} catch (error) {
			console.error("boot: kassa ochilmadi", error);
			this.el.bootError.textContent = this.errorText(error);
			this.setState("error");
		}
	}

	/** Ekran klaviaturasi: yangi bayroq, eski maydon ham hisobga olinadi. */
	hasVirtualKeyboard() {
		return !!(this.ctx.features || {}).virtual_keyboard || !!this.ctx.enable_virtual_keyboard;
	}

	/** To'liq yangilash — boshlanishida va «⋯» menyusidagi «Yangilash» bosilganda. */
	async refreshAll() {
		await this.refresh({ floor: true, orders: true, panel: true });
	}

	/**
	 * Tanlangan qismlarnigina qayta so'raydi (TZ §6).
	 *
	 * Realtime signali kelganda BUTUN bazani qayta o'qish shart emas:
	 * boshqa zaldagi stol o'zgarsa zal rejasi qayta chizilmaydi, faqat
	 * buyurtmalar ro'yxati yangilanadi.
	 *
	 * Oxirida `refresh` slotidagi funksiya modullari chaqiriladi
	 * (`run(screen, scope)`) — ular o'z ma'lumotini shu yerda yangilaydi.
	 */
	async refresh(scope = {}) {
		const { floor = false, orders = false, panel = false } = scope;
		// URINISH vaqti (muvaffaqiyat emas): tarmoq yo'q paytda yangilash har 250ms
		// qayta boshlanib serverni bosmasligi uchun.
		this.lastRefreshAt = Date.now();
		const jobs = [];
		if (floor) jobs.push(this.loadFloor());
		if (orders) jobs.push(this.loadOrders());
		await Promise.all(jobs);

		// Yangilash o'tdi — oldingi muvaffaqiyatsizlikdan qolgan «eskirgan» belgisi olinadi.
		if (floor || orders) {
			this.stale = false;
			this.updateLiveIndicator();
		}

		// Panelni zal rejasidan KEYIN yangilaymiz — stol yo'qolgan bo'lsa
		// `loadFloor` tanlovni bekor qilib ulguradi.
		if (panel && this.selectedTable) {
			await this.loadTableDetail(this.selectedTable);
		} else if (panel && this.selectedInvoice) {
			// Stolsiz buyurtma tanlangan — uni stol orqali topib bo'lmaydi.
			await this.loadOrderDetail(this.selectedInvoice);
		}

		// Tez tugmalar `when/disabled` orqali holatga bog'liq (smena, tanlov, bayroqlar).
		this.renderQuick();

		for (const item of slots.items("refresh")) {
			try {
				await item.run(this, scope);
			} catch (error) {
				// Bitta funksiya modulining xatosi butun ekranni to'xtatmasin.
				console.error(`refresh: '${item.id}' xato berdi`, error);
			}
		}
	}

	async loadFloor() {
		// Bir necha so'rov ketma-ket ketsa (zal almashtirildi, realtime yangilash)
		// javoblar TARTIBSIZ kelishi mumkin: sekin eski javob yangisini bosib
		// yubormasin — faqat OXIRGI so'rovning javobi qo'llanadi.
		const mine = (this.floorRequest = (this.floorRequest || 0) + 1);
		const floor = await this.call("ozturkapp.ozturkapp.api.table.get_floor_plan", {
			room: this.room,
		});
		if (mine !== this.floorRequest) return;

		this.floor = floor;
		this.floorLoadedAt = Date.now();
		this.renderFilters();
		this.renderFloor();

		// Tanlangan stol ko'rinishdan chiqib ketgan bo'lsa — tanlovni bekor qilamiz.
		if (this.selectedTable) {
			const still = (this.floor.tables || []).some((t) => t.name === this.selectedTable);
			if (!still) this.clearSelection();
		}
	}

	async loadOrders() {
		const mine = (this.ordersRequest = (this.ordersRequest || 0) + 1);
		const orders = await this.call("ozturkapp.ozturkapp.api.order.get_active_orders", {
			room: this.room,
		});
		if (mine !== this.ordersRequest) return;

		this.orders = orders;
		this.ordersLoadedAt = Date.now();
		this.renderOrders();
	}

	// ═══════════════════════════════════════════════════════════
	//  Hayot sikli (Desk sahifa hodisalari)
	// ═══════════════════════════════════════════════════════════

	suspend() {
		this.active = false;
		this.shortcuts.stop();
		this.closeMenu();
		window.removeEventListener("resize", this.onWindowResize);
		this.updateLiveIndicator();
	}

	resume() {
		this.active = true;
		this.shortcuts.start();
		window.addEventListener("resize", this.onWindowResize);
		this.syncLayout();

		// Sahifaga qaytilganda holat eskirgan bo'lishi mumkin — to'liq yangilash.
		if (this.ctx) {
			this.scheduleRefresh({ floor: true, orders: true, panel: true });
		}
	}

	destroy() {
		this.destroyed = true;
		this.suspend();
		this.unsubscribe();
		features.disposeAll(this);
		this.resizeObserver.disconnect();
		this.timers.forEach(clearInterval);
		this.timers = [];
		clearTimeout(this.refreshTimer);
	}
}

mixin(
	CashierScreen,
	HelperMethods,
	RealtimeMethods,
	TopbarMethods,
	MenuMethods,
	FloorMethods,
	PanelMethods,
	ActionsMethods,
	SplitMethods,
	ModalMethods,
	PaymentMethods,
	ShiftMethods,
	HistoryMethods,
	OrdersMethods
);
