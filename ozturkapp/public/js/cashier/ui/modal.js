/**
 * Umumiy modal oyna — to'lov, kassa tarixi, hisobni bo'lish va smena yopish
 * shu bitta oynada ochiladi.
 *
 * Kichik shakl/tasdiq oynalari (`ui.form`, `ui.confirm`, PIN) boshqa —
 * ular shu oyna USTIDA ochiladi va bir-biriga xalaqit bermaydi.
 */

import { ui } from "../kit/index.js";

const SIZES = ["md", "lg", "xl"];

export class ModalMethods {
	/** Oynani yopib bo'lmaydigan qilish (sanoq davomida). */
	setModalLocked(locked) {
		this.modalLocked = locked;
		this.$root.find(".rc-modal__close").toggle(!locked);
	}

	/**
	 * @param {string} title
	 * @param {object} [options]
	 * @param {boolean} [options.locked]      yopib bo'lmaydi (sanoq)
	 * @param {"md"|"lg"|"xl"} [options.size] eni: 460 / 780 / 980 px
	 */
	showModal(title, { locked = false, size = "md" } = {}) {
		this.setModalLocked(locked);
		this.$root.find(".rc-modal__title").text(title);
		this.$root
			.find(".rc-modal")
			.attr("aria-label", title)
			.removeClass(SIZES.map((s) => `rc-modal--${s}`).join(" "))
			.addClass(`rc-modal--${size}`);

		// Oyna bir ekrandan ikkinchisiga almashsa (to'lov -> qaytim) ochgan element yo'qolmasin.
		if (this.el.overlay.hidden) this.modalOpener = document.activeElement;
		this.el.overlay.hidden = false;
	}

	/**
	 * Raqam panelini oyna ichidagi `.rc-numpad-slot` ga qo'yadi va `$inputs`
	 * maydonlariga bog'laydi. Ekran klaviaturasi o'chiq bo'lsa hech narsa
	 * chizilmaydi — maydonlar brauzerning o'z klaviaturasi bilan ishlaydi.
	 *
	 * @returns {HTMLElement|null}  panel (`bind(input)` bilan) yoki `null`
	 */
	mountNumpad($root, $inputs) {
		if (!ui.virtualKeyboard) return null;

		const slot = $root.find(".rc-numpad-slot")[0];
		if (!slot) return null;

		const pad = ui.keyboard.numpad({ inputs: $inputs.get() });
		slot.appendChild(pad);
		return pad;
	}

	closeModal() {
		// Qulflangan oyna (kassa yopish sanog'i) yopilmaydi.
		if (this.modalLocked) return;

		clearInterval(this.countdownTimer);
		this.countdownTimer = null;
		this.el.overlay.hidden = true;
		this.$root.find(".rc-modal__close").show();
		$(this.el.modalBody).off().empty();

		// Fokus oynani ochgan elementga qaytadi (aks holda `body` ga tushib qoladi).
		const opener = this.modalOpener;
		this.modalOpener = null;
		if (opener && opener !== document.body && opener.isConnected && opener.focus) {
			opener.focus({ preventScroll: true });
		}
	}
}
