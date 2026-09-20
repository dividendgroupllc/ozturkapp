/**
 * «⋯» menyusi — kunlik ishda kam kerak bo'ladigan amallar shu yerda.
 *
 * Bandlar `topbar.menu` slotidan o'qiladi: asosiy bandlar quyida shu yo'l bilan
 * qo'shilgan, funksiya modullari (chegirma, kassa harakati, ...) esa o'zlariniki
 * qo'shadi. Band shakli:
 *
 *     { id, label, order, kind: "danger", when(screen), checked(screen),
 *       disabled(screen) -> false | true | "sabab matni", onClick(screen) }
 */

import { slots } from "../core/slots.js";
import { esc } from "../util/format.js";

export class MenuMethods {
	toggleMenu() {
		if (this.el.menu.hidden) this.openMenu();
		else this.closeMenu();
	}

	openMenu() {
		const items = slots.visible("topbar.menu", this);

		this.el.menu.innerHTML = `
			<div class="rc-menu__items" role="menu">
				${items
					.map((item) => {
						const disabled = item.disabled ? item.disabled(this) : false;
						const checked = item.checked ? item.checked(this) : null;
						return `<button type="button" role="menuitem"
							class="rc-menu__item ${item.kind === "danger" ? "rc-menu__item--danger" : ""}"
							data-id="${esc(item.id)}" ${disabled ? "disabled" : ""}>
							<span class="rc-menu__label">${esc(item.label)}</span>
							${
								checked === null
									? ""
									: `<span class="rc-menu__state" aria-hidden="true">${esc(
											checked ? __("Yoqiq") : __("O'chiq")
									  )}</span>`
							}
							${typeof disabled === "string" ? `<span class="rc-menu__hint">${esc(disabled)}</span>` : ""}
						</button>`;
					})
					.join("")}
			</div>
			<div class="rc-menu__foot">${esc(__("Versiya"))}: ${esc(ozturk.cashier.BUILD)}</div>`;

		this.el.menu.hidden = false;
		this.el.menuButton.setAttribute("aria-expanded", "true");

		$(this.el.menu)
			.off("click")
			.on("click", ".rc-menu__item", (e) => {
				const item = items.find((i) => i.id === e.currentTarget.dataset.id);
				this.closeMenu();
				if (item) item.onClick(this);
			});

		// Tashqariga bosilsa yopiladi. Menyu `click` da ochiladi, `pointerdown` esa
		// undan OLDIN o'tib bo'lgan — shuning uchun darhol ulash xavfsiz. (Ilgari
		// `setTimeout` ishlatilardi: menyu shu tikkacha yopilsa, ulangan ishlovchi
		// hech qachon yechilmay qolardi.)
		document.removeEventListener("pointerdown", this.menuOutside, true);
		this.menuOutside = (event) => {
			if (!event.target.closest(".rc-menu, .rc-menu-btn")) this.closeMenu();
		};
		document.addEventListener("pointerdown", this.menuOutside, true);
	}

	closeMenu() {
		if (this.el.menu.hidden) return;
		this.el.menu.hidden = true;
		this.el.menuButton.setAttribute("aria-expanded", "false");
		document.removeEventListener("pointerdown", this.menuOutside, true);
	}
}

// ═══════════════════════════════════════════════════════════════
//  Asosiy bandlar
// ═══════════════════════════════════════════════════════════════

slots.contribute("topbar.menu", {
	id: "history",
	order: 10,
	label: __("Tarix"),
	onClick: (screen) => screen.openHistoryModal(),
});

slots.contribute("topbar.menu", {
	id: "refresh",
	order: 20,
	label: __("Yangilash"),
	onClick: (screen) => screen.refreshAll().catch((error) => screen.alertError(error)),
});

// Faqat menejer: kassirning kunlik ishida bu kerak emas.
//
// "Barcha zallar"da o'chirilgan: u yerda stollar zal blokiga qarab QO'SHIMCHA
// vertikal siljish bilan chiziladi (`table_status.apply_layout(stack_rooms=True)`),
// ya'ni ekrandagi piksel saqlanadigan `layout_y` bilan bir xil EMAS.
slots.contribute("topbar.menu", {
	id: "layout-edit",
	order: 30,
	label: __("Joylashuvni tahrirlash"),
	when: (screen) => !!(screen.ctx.permissions || {}).is_supervisor,
	disabled: (screen) => (screen.room ? false : __("Avval aniq zalni tanlang")),
	onClick: (screen) => screen.toggleLayoutEdit(),
});

// Yopish ham faqat biriktirilgan kassirning ishi — u kassadagi naqd pulni
// sanaydi (server ham rad etadi: `assert_shift_operator`). `order: 900` — funksiya
// modullarining bandlari (100–899) doim UNDAN YUQORIDA turadi: qizil «Kassani yopish»
// har doim menyuning eng pastida. Smena YOPIQ bo'lsa
// band chiqmaydi: ochish faqat bloklovchi ekran orqali bo'ladi — ikkita
// ochish yo'li bo'lsa modalni yopib kassasiz ishlashda davom etish mumkin bo'lardi.
slots.contribute("topbar.menu", {
	id: "close-shift",
	order: 900,
	kind: "danger",
	label: __("Kassani yopish"),
	when: (screen) => {
		const shift = screen.ctx.shift || {};
		const canOperate = !!(screen.ctx.permissions || {}).can_operate_shift;
		return !!shift.open && canOperate;
	},
	onClick: (screen) => screen.closeShiftDialog(),
});
