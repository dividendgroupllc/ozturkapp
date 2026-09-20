/**
 * Zal rejasi (TZ §4, §20): stollar, tuvalni sig'dirish, joylashuvni tahrirlash.
 */

import { slots } from "../core/slots.js";
import { elapsedHtml, esc, fromTable, hhmm } from "../util/format.js";

/**
 * Tuval masshtabi chegaralari. Stol o'zining tabiiy o'lchamiga yaqin turadi: kichik
 * zalni faqat biroz kattalashtiramiz (katta ekranda 120px stol 180px «semiz» kartaga
 * aylanmasin), zich zalni esa 0.7 dan ortiq kichraytirmaymiz — undan kichikda matn
 * o'qilmaydi; bunda zal o'zi aylantiriladi.
 */
const FLOOR_MIN_SCALE = 0.7;
const FLOOR_MAX_SCALE = 1.15;

/** `.rc-floor__scroll` ning CSS `padding` i (px) — shu qiymat mavjud joydan ayriladi. */
const FLOOR_PADDING = 16;

/** Sudrash paytida zalni qayta chizishni kechiktirishning eng uzun muddati (ms). */
const DRAG_DEFER_MAX = 15 * 1000;

/** Shu masshtabdan kichikda stol kartasidan ikkinchi darajali qator olib tashlanadi. */
const FLOOR_DENSE_SCALE = 0.85;

/**
 * «Barcha zallar»: zal bloklarining oralig'i (tuval birligida) va sarlavhadan birinchi
 * qator stolgacha bo'shliq. Blok pastida stol chetidagi tabletka (belgilar) uchun joy qoladi.
 */
const BLOCK_GAP_X = 40;
const BLOCK_GAP_Y = 40;
const BLOCK_TOP_GAP = 10;
const BLOCK_BOTTOM_PAD = 12;

/** Zallar soni shundan oshsa barcha bo'linishlarni sanab chiqmaymiz (2^n) — oddiy qatorlash. */
const MAX_EXHAUSTIVE_BLOCKS = 12;

let scrollbarWidthCache = null;

/** Brauzerning aylantirish yo'lagi kengligi (overlay bo'lsa 0). Bir marta o'lchanadi. */
function scrollbarWidth() {
	if (scrollbarWidthCache === null) {
		const probe = document.createElement("div");
		probe.style.cssText =
			"position:absolute;top:-9999px;width:100px;height:100px;overflow:scroll;visibility:hidden";
		document.body.appendChild(probe);
		scrollbarWidthCache = probe.offsetWidth - probe.clientWidth;
		probe.remove();
	}
	return scrollbarWidthCache;
}

const num = (value) => Number(value) || 0;
const bandStyle = (band) =>
	`left:${band.x}px;top:${band.y}px;width:${band.width}px;height:${band.height}px`;
const clampScale = (value) => Math.max(FLOOR_MIN_SCALE, Math.min(FLOOR_MAX_SCALE, value));

/**
 * Zal bloklarini qatorlarga joylaydi: bloklar tartibi saqlanadi, faqat qator uzilishlari
 * tanlanadi (yonma-yon ustunlar). Har bir bo'linish uchun mavjud joyga sig'dirish
 * masshtabi hisoblanadi; eng KATTA masshtab yutadi, lekin eng kichik masshtabda ham
 * eniga sig'maydigan (gorizontal aylantirish talab qiladigan) bo'linish faqat boshqa
 * yo'l qolmaganda tanlanadi. Masshtab teng bo'lsa (hammasi `FLOOR_MAX_SCALE` da) —
 * qatorlari KAM (ya'ni ko'proq yonma-yon), keyin past bo'lgani.
 *
 * @param {{width:number,height:number}[]} blocks tabiiy o'lchamlar
 * @returns {{rows:number[][], scale:number, width:number, height:number}}
 */
export function packBlocks(blocks, availableWidth, availableHeight) {
	const n = blocks.length;
	const masks = n > MAX_EXHAUSTIVE_BLOCKS ? [null] : Array.from({ length: 1 << (n - 1) }, (_, i) => i);

	const rowsFor = (mask) => {
		const rows = [[]];
		blocks.forEach((_, i) => {
			rows[rows.length - 1].push(i);
			// `mask` bit i — i-blokdan KEYIN yangi qator boshlanadi.
			const breakAfter =
				mask === null
					? i < n - 1 &&
					  rowWidth(rows[rows.length - 1].concat(i + 1)) > availableWidth
					: i < n - 1 && (mask >> i) & 1;
			if (breakAfter) rows.push([]);
		});
		return rows;
	};
	const rowWidth = (row) =>
		row.reduce((sum, i) => sum + blocks[i].width, 0) + BLOCK_GAP_X * (row.length - 1);

	let best = null;
	for (const mask of masks) {
		const rows = rowsFor(mask);
		const width = Math.max(...rows.map(rowWidth));
		const height =
			rows.reduce((sum, row) => sum + Math.max(...row.map((i) => blocks[i].height)), 0) +
			BLOCK_GAP_Y * (rows.length - 1);
		const widthScale = availableWidth / width;
		const scale = clampScale(Math.min(widthScale, availableHeight / height));
		// Eng kichik masshtabda ham eniga sig'maydigan bo'linish (gorizontal aylantirish) —
		// faqat boshqa yo'li yo'q bo'lsagina; shunda ham eng kam oshib ketgani.
		const overflow = widthScale < FLOOR_MIN_SCALE;
		const better =
			!best ||
			(best.overflow && !overflow) ||
			(best.overflow && overflow && widthScale > best.widthScale + 1e-6) ||
			(best.overflow === overflow &&
				(scale > best.scale + 1e-6 ||
					(Math.abs(scale - best.scale) <= 1e-6 &&
						(rows.length < best.rows.length ||
							(rows.length === best.rows.length && height < best.height)))));
		if (better) best = { rows, scale, width, height, overflow, widthScale };
	}
	return best;
}

export class FloorMethods {
	/**
	 * Joylashuvni tahrirlash — stollarni bosib-sudrab ko'chirish rejimi.
	 *
	 * "Barcha zallar" ko'rinishida o'chirilgan turadi: u yerda stollar
	 * zal blokiga qarab QO'SHIMCHA vertikal siljish bilan chiziladi
	 * (`table_status.apply_layout(stack_rooms=True)`), ya'ni ekrandagi
	 * piksel — saqlanadigan `layout_y` bilan bir xil EMAS. Aniq bitta zal
	 * tanlanganda esa bu siljish yo'q — sudrab qo'yilgan joy TO'G'RIDAN-
	 * TO'G'RI serverga yuboriladi.
	 *
	 * Rejim «⋯» menyusidan yoqiladi; yoqilganda zal ustida qisqa panel
	 * chiqadi va shu yerdan tugatiladi.
	 */
	toggleLayoutEdit() {
		if (!this.room) return;

		this.layoutEditMode = !this.layoutEditMode;
		if (this.layoutEditMode) {
			this.clearSelection();
			this.setView("floor");
		}
		this.renderFloorToolbar();
		this.renderFloor();
	}

	renderFloorToolbar() {
		if (!this.el.floorToolbar) return;

		this.el.floorToolbar.hidden = !this.layoutEditMode;
		this.el.floorToolbar.innerHTML = this.layoutEditMode
			? `<span class="rc-floor__mode">✥ ${esc(__("Tahrirlash rejimi"))}</span>
				<button type="button" class="rc-btn rc-btn--primary" data-action="toggle-layout-edit">${esc(
					__("Tahrirlashni tugatish")
				)}</button>`
			: "";

		$(this.el.floorToolbar)
			.off("click")
			.on("click", '[data-action="toggle-layout-edit"]', () => this.toggleLayoutEdit());
	}

	renderFloor() {
		// Sudrash ketayotganda zal qayta chizilsa (realtime yangilash) sudralayotgan stol
		// DOM'dan olib tashlanadi, `pointerup` unga yetmaydi va surish jimgina yo'qoladi.
		// Chizish sudrash tugaguncha kechiktiriladi (osilib qolmasligi uchun eng ko'pi 15 soniya).
		if (this.draggingTable && Date.now() - this.dragStartedAt < DRAG_DEFER_MAX) {
			this.floorDirty = true;
			return;
		}
		const tables = (this.floor || {}).tables || [];
		this.el.floorEmpty.hidden = tables.length > 0;

		if (!tables.length) {
			this.el.canvas.innerHTML = "";
			return;
		}

		this.el.canvas.classList.toggle("rc-floor__canvas--edit", this.layoutEditMode);

		// "Barcha zallar" ko'rinishida har bir zal alohida blok (`room_bands`); ular
		// mavjud joyga qarab yonma-yon qatorlarga joylanadi (`arrangeFloor`). Aniq bitta
		// zalda stollar saqlangan koordinatada turadi (sudrash matematikasi shunga tayanadi).
		const arrangement = this.arrangeFloor();

		this.el.canvas.innerHTML =
			arrangement.bands.map((band) => this.roomBandHtml(band)).join("") +
			tables.map((t) => this.tableHtml(t, arrangement.tables.get(t.name))).join("");
		this.positionFloor(arrangement);

		$(this.el.canvas)
			.off("click")
			.on("click", ".rc-table", (e) => {
				if (this.layoutEditMode) return; // shu rejimda bosish ko'chirish uchun
				this.selectTable(e.currentTarget.dataset.table);
			});

		$(this.el.canvas)
			.off("pointerdown")
			.on("pointerdown", ".rc-table", (e) => this.startTableDrag(e));
	}

	/** Zal bloki: sarlavha va uning ostidagi chiziq blokning TO'LIQ eniga cho'ziladi. */
	roomBandHtml(band) {
		return `<div class="rc-band" data-room="${esc(band.room)}" style="${bandStyle(band)}">
			<div class="rc-band__header" style="height:${band.header_height}px">
				<span class="rc-band__label">${esc(band.room)}
					<span class="rc-band__count">${cint(band.count)}</span>
				</span>
			</div>
		</div>`;
	}

	tableHtml(table, position) {
		const layout = table.layout || {};
		const x = position ? position.x : layout.x;
		const y = position ? position.y : layout.y;
		const order = table.order;
		const reservation = table.reservation;
		const dim = this.statusFilter !== "ALL" && table.status !== this.statusFilter;

		// Holat matni + belgi — rangga tayanmaslik uchun (TZ §20).
		const marks = { AVAILABLE: "●", RESERVED: "◆", OCCUPIED: "■" };
		const labels = {
			AVAILABLE: __("Bo'sh"),
			RESERVED: __("Bron"),
			OCCUPIED: __("Band"),
		};

		// Uchinchi qator: summa (band) / bron vaqti + mehmon / o'rinlar soni. O'rinlar soni
		// yo'q stolda qator bo'sh qoladi (CSS joyini saqlaydi) — kartalar bir tekis turadi.
		let detail = "";
		let who = "";
		if (order) {
			detail = `<span class="rc-table__amount">${esc(this.money(order.amount))}</span>`;
			const waiter = order.waiter ? order.waiter.split("@")[0] : "";
			who = waiter ? `<span class="rc-table__who">${esc(waiter)}</span>` : "";
		} else if (reservation) {
			detail = `<span class="rc-table__meta">${esc(hhmm(reservation.from_time))} · ${esc(
				reservation.customer_name || __("Bron")
			)}</span>`;
		} else {
			const seats = cint(table.no_of_seats);
			detail = `<span class="rc-table__meta">${seats ? `${seats} ${esc(__("o'rin"))}` : ""}</span>`;
		}

		// Yuqori chet: o'tgan vaqt. Server yangi maydonni bermasa (`elapsed_minutes`)
		// tabletka chizilmaydi.
		const age = order ? elapsedHtml(fromTable(table, "elapsed_minutes"), this.floorLoadedAt, true) : "";

		// Pastki chet: «hisob so'radi» qo'ng'irog'i va belgilar.
		const billRequested = !!fromTable(table, "bill_requested");
		const flags = [];
		if (billRequested) {
			flags.push(
				`<span class="rc-bell" title="${esc(
					__("Hisob so'radi")
				)}"><span aria-hidden="true">🔔</span></span>`
			);
		}

		const badges = [];
		if (table.is_merged) badges.push("⛓");
		if (cint(table.open_order_count) > 1) badges.push(`×${cint(table.open_order_count)}`);
		if (order && order.billed) badges.push("🧾");
		slots.visible("floor.tileBadges", table, this).forEach((item) => {
			const badge = item.badge(table, this);
			if (badge) badges.push(typeof badge === "string" ? badge : badge.text);
		});
		if (badges.length)
			flags.push(`<span class="rc-table__badge">${esc(badges.join(" "))}</span>`);

		return `<button type="button"
			class="rc-table rc-table--${esc(table.status)} rc-table--${esc(table.table_shape || "Square")} ${
			dim ? "rc-table--dim" : ""
		} ${billRequested ? "rc-table--bill" : ""}"
			data-table="${esc(table.name)}"
			aria-pressed="${this.selectedTable === table.name}"
			aria-label="${esc(table.name)} — ${esc(labels[table.status])}${
			billRequested ? ", " + esc(__("hisob so'radi")) : ""
		}"
			style="left:${x}px;top:${y}px;width:${layout.width}px;height:${layout.height}px">
			${age ? `<span class="rc-table__age">${age}</span>` : ""}
			<span class="rc-table__name">${esc(table.name)}</span>
			<span class="rc-table__status"><span aria-hidden="true">${marks[table.status]}</span>${esc(
			labels[table.status]
		)}</span>
			${detail}
			${who}
			${flags.length ? `<span class="rc-table__flags">${flags.join("")}</span>` : ""}
		</button>`;
	}

	/**
	 * Joriy mavjud joyga mos zal joylashuvini hisoblaydi (DOM ga TEGMAYDI).
	 *
	 * Server javobi (`floor.tables[].layout`, `room_bands`, `extent`) O'ZGARTIRILMAYDI —
	 * `touchesVisibleTables`, sudrash va tahrirlash shunga tayanadi. Bu yerda faqat
	 * ko'rsatish uchun qo'shimcha joylashuv chiqariladi:
	 *
	 *  - bitta zal: stollar saqlangan koordinatada (`mode: "plain"`);
	 *  - barcha zallar: har bir zal chap-yuqoriga tekislangan blok (sarlavha + stollar);
	 *    bloklar `packBlocks` bilan yonma-yon qatorlarga joylanadi, qatordagi bloklar
	 *    bo'sh joyni teng bo'lishadi — sarlavha chizig'i butun zal eniga yetadi.
	 *
	 * @returns {{mode:string, scale:number, width:number, height:number,
	 *            bands:object[], tables:Map<string,{x:number,y:number}>}|null}
	 */
	arrangeFloor() {
		const floor = this.floor || {};
		const tables = floor.tables || [];
		const extent = floor.extent || {};
		const box = this.el.floorScroll;

		// Mavjud joy (tuval birligi emas, ekran pikseli). Aylantirish yo'lagi joyi HAR DOIM
		// ayriladi: yo'lak paydo bo'lib/yo'qolib ketsa ham hisob o'zgarmaydi (RO tebranmaydi).
		let availableWidth = box.offsetWidth - FLOOR_PADDING * 2 - scrollbarWidth();
		let availableHeight = box.offsetHeight - FLOOR_PADDING * 2;
		if (availableWidth <= 0 || availableHeight <= 0) {
			// Zal hozir ko'rinmaydi (tor ekranda «Buyurtmalar» ochiq): eng ko'p ishlatiladigan o'lcham.
			availableWidth = 900;
			availableHeight = 560;
		}

		// Har bir zal: tabiiy blok (sarlavha balandligi + stollar), chap-yuqoriga tekislangan.
		const blocks = ((!this.room && floor.room_bands) || [])
			.map((band) => {
				const members = tables.filter((t) => (t.restaurant_room || "") === band.room);
				if (!members.length) return null;

				const header = band.header_height || 0;
				const relY = (t) => num(t.layout.y) - num(band.y);
				const minX = Math.min(...members.map((t) => num(t.layout.x)));
				const minY = Math.min(...members.map(relY));
				const positions = new Map();
				let width = 0;
				let height = header;
				members.forEach((t) => {
					const x = num(t.layout.x) - minX;
					const y = relY(t) - minY + header + BLOCK_TOP_GAP;
					positions.set(t.name, { x, y });
					width = Math.max(width, x + num(t.layout.width));
					height = Math.max(height, y + num(t.layout.height) + BLOCK_BOTTOM_PAD);
				});
				return { band, positions, width: width || 120, height };
			})
			.filter(Boolean);

		if (!blocks.length) {
			const fit =
				extent.width && extent.height
					? Math.min(availableWidth / extent.width, availableHeight / extent.height)
					: 1;
			return {
				mode: "plain",
				scale: clampScale(fit),
				width: extent.width || 0,
				height: extent.height || 0,
				bands: [],
				tables: new Map(),
			};
		}

		const packed = packBlocks(blocks, availableWidth, availableHeight);
		const canvasWidth = Math.max(packed.width, Math.floor(availableWidth / packed.scale));

		const arranged = { bands: [], tables: new Map() };
		let y = 0;
		packed.rows.forEach((row) => {
			const rowNatural =
				row.reduce((sum, i) => sum + blocks[i].width, 0) + BLOCK_GAP_X * (row.length - 1);
			const extra = Math.max(0, canvasWidth - rowNatural) / row.length;
			let x = 0;
			row.forEach((i) => {
				const block = blocks[i];
				const width = block.width + extra;
				arranged.bands.push({
					room: block.band.room,
					count: block.band.count,
					header_height: block.band.header_height || 0,
					x,
					y,
					width,
					height: block.height,
				});
				block.positions.forEach((pos, name) =>
					arranged.tables.set(name, { x: pos.x + x, y: pos.y + y })
				);
				x += width + BLOCK_GAP_X;
			});
			y += Math.max(...row.map((i) => blocks[i].height)) + BLOCK_GAP_Y;
		});

		return {
			mode: "bands",
			scale: packed.scale,
			width: canvasWidth,
			height: y - BLOCK_GAP_Y,
			bands: arranged.bands,
			tables: arranged.tables,
		};
	}

	/**
	 * Hisoblangan joylashuvni tuvalga qo'llaydi: masshtab, stol/blok o'rni, sahna o'lchami.
	 * Faqat o'zgargan qiymatlar yoziladi (qayta chizish yo'q).
	 */
	positionFloor(arrangement) {
		const { scale, width, height } = arrangement;

		this.el.canvas.style.width = `${width}px`;
		this.el.canvas.style.height = `${height}px`;
		this.el.canvas.style.transform = `scale(${scale})`;
		this.el.canvas.style.setProperty("--rc-scale", scale);
		this.el.canvas.classList.toggle("rc-floor__canvas--dense", scale < FLOOR_DENSE_SCALE);
		this.el.floorStage.style.width = `${Math.ceil(width * scale)}px`;
		this.el.floorStage.style.height = `${Math.ceil(height * scale)}px`;
		this.floorScale = scale;

		if (arrangement.mode !== "bands") return;

		// Qator bo'linishi o'zgarganda bloklar tartibi ham o'zgaradi — zal nomi bo'yicha topamiz.
		const bandByRoom = new Map(arrangement.bands.map((band) => [band.room, band]));
		this.el.canvas.querySelectorAll(".rc-band").forEach((node) => {
			const band = bandByRoom.get(node.dataset.room);
			if (band) node.setAttribute("style", bandStyle(band));
		});
		this.el.canvas.querySelectorAll(".rc-table").forEach((node) => {
			const position = arrangement.tables.get(node.dataset.table);
			if (!position) return;
			node.style.left = `${position.x}px`;
			node.style.top = `${position.y}px`;
		});
	}

	/**
	 * Zal rejasini mavjud joyga sig'dirish — ENI va BALANDLIGI bo'yicha.
	 *
	 * Masshtab `FLOOR_MIN_SCALE`..`FLOOR_MAX_SCALE` oralig'ida: kichik zal tabiiy
	 * o'lchamiga yaqin, chap-yuqorida turadi; zich zal kichrayadi, 0.7 dan kichikka
	 * tushmaydi (o'shanda zal aylantiriladi). "Barcha zallar"da bloklar joyga qarab
	 * qayta joylanadi (`arrangeFloor`). Konteyner o'lchami o'zgarganda `ResizeObserver`
	 * qayta chaqiradi.
	 */
	fitFloor() {
		if (!((this.floor || {}).tables || []).length || !this.el.floorScroll.offsetWidth) return;
		this.positionFloor(this.arrangeFloor());
	}

	/**
	 * Stolni bosib-sudrab ko'chirish — faqat `layoutEditMode`da ishlaydi.
	 *
	 * Sichqoncha/barmoq siljishi tuval `scale(...)` bilan kichraytirilgan
	 * bo'lishi mumkin (`fitFloor`), shuning uchun piksel farqi ekrandagi
	 * (ekrandan mustaqil) koordinataga aylantirilishi uchun shu koeffitsientga
	 * bo'linadi — aks holda katta zallarda stol sichqonchadan orqada qolib
	 * ketardi.
	 */
	startTableDrag(e) {
		if (!this.layoutEditMode) return;
		e.preventDefault();

		const el = e.currentTarget;
		const table = el.dataset.table;
		const scale = this.floorScale || 1;

		const startX = e.clientX;
		const startY = e.clientY;
		const startLeft = parseFloat(el.style.left) || 0;
		const startTop = parseFloat(el.style.top) || 0;
		let moved = false;

		el.setPointerCapture(e.pointerId);
		el.classList.add("rc-table--dragging");
		this.draggingTable = true;
		this.dragStartedAt = Date.now();

		const onMove = (ev) => {
			const dx = (ev.clientX - startX) / scale;
			const dy = (ev.clientY - startY) / scale;
			if (Math.abs(dx) > 1 || Math.abs(dy) > 1) moved = true;

			el.style.left = `${Math.max(0, startLeft + dx)}px`;
			el.style.top = `${Math.max(0, startTop + dy)}px`;
		};

		const onUp = async () => {
			el.removeEventListener("pointermove", onMove);
			el.removeEventListener("pointerup", onUp);
			el.removeEventListener("pointercancel", onUp);
			el.classList.remove("rc-table--dragging");
			this.draggingTable = false;

			// Sudrash paytida kelgan yangilanish endi chiziladi (xato bo'lsa ham).
			const redrawLater = () => {
				if (!this.floorDirty) return;
				this.floorDirty = false;
				this.renderFloor();
			};

			if (!moved) return redrawLater(); // oddiy bosish — joy o'zgarmadi

			const x = parseFloat(el.style.left) || 0;
			const y = parseFloat(el.style.top) || 0;
			const width = parseFloat(el.style.width) || 0;
			const height = parseFloat(el.style.height) || 0;

			try {
				await this.call("ozturkapp.ozturkapp.api.table.update_table_layout", {
					table,
					x,
					y,
					width,
					height,
				});

				// Keshdagi holatni ham yangilaymiz — realtime signal
				// kelguncha zal rejasi eskirgan ko'rinmasin.
				const cached = (this.floor.tables || []).find((t) => t.name === table);
				if (cached) {
					cached.layout_x = x;
					cached.layout_y = y;
					cached.layout = { ...(cached.layout || {}), x, y, auto: false };
				}
			} catch (error) {
				this.alertError(error);
				this.floorDirty = false;
				this.renderFloor(); // xatoda eski joyga qaytaramiz
				return;
			}
			redrawLater();
		};

		el.addEventListener("pointermove", onMove);
		el.addEventListener("pointerup", onUp);
		el.addEventListener("pointercancel", onUp);
	}
}
