/**
 * Sensorli oyna va forma yasovchi.
 *
 * Desk'ning `frappe.prompt/confirm/msgprint` oynalari sichqoncha uchun
 * yozilgan: maydonlar kichik, matn kiritish uchun jismoniy klaviatura kerak,
 * kassa uslubi (`--rc-*`, 48px nishonlar) ustiga esa Desk uslubi tushib ko'rinishi buziladi. Bu yerda
 * hammasi katta tugmali, ekran klaviaturasi bilan ishlaydi va kassa
 * oynasining o'z uslubida.
 *
 * Oynalar STEK bo'lib turadi: to'lov oynasi ustida menejer PIN oynasi
 * ochilishi mumkin. `Esc` eng ustkisini yopadi (`closeTop`).
 */

import { errorText } from "../core/api.js";
import { defaultTimeSlot, esc, HOUR_OPTIONS, hhmm, MINUTE_OPTIONS, pad2 } from "../util/format.js";
import { amountInput, chips } from "./controls.js";
import { kit } from "./context.js";
import { TextKeyboard } from "./keyboard.js";

const stack = [];

/** Kichik oynalardan (forma, tasdiq, PIN) biri ochiqmi? */
export const hasDialog = () => stack.length > 0;

/**
 * Eng ustdagi oyna bor-yo'qligini qaytaradi; yopilishi mumkin bo'lsa yopadi.
 * So'rov ketayotgan (band) oyna Esc bilan yopilmaydi: natija (ayniqsa xato)
 * ko'rinmay qolardi va kassir amal bajarilgan-bajarilmaganini bilmasdi.
 */
export function closeTop() {
	const top = stack[stack.length - 1];
	if (!top) return false;
	if (top.dismissible && !top.busy) top.close(null);
	return true;
}

const FOCUSABLE =
	'button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), ' +
	'textarea:not([disabled]), a[href], summary, [tabindex]:not([tabindex="-1"])';

/**
 * Tab tugmasi oyna ichida aylanadi: fokus oyna ostidagi (ko'rinmas) elementlarga
 * o'tib ketmasin — Enter/Space bilan orqadagi «To'lov» tugmasi bosilib qolardi.
 */
export function trapTab(container, event) {
	if (event.key !== "Tab" || event.defaultPrevented) return;

	const items = [...container.querySelectorAll(FOCUSABLE)].filter(
		(el) => el.getClientRects().length > 0
	);
	if (!items.length) {
		event.preventDefault();
		return;
	}

	const active = document.activeElement;
	const inside = container.contains(active);
	if (event.shiftKey && (!inside || active === items[0])) {
		items[items.length - 1].focus();
		event.preventDefault();
	} else if (!event.shiftKey && (!inside || active === items[items.length - 1])) {
		items[0].focus();
		event.preventDefault();
	}
}

// ═══════════════════════════════════════════════════════════════
//  Forma maydonlari
// ═══════════════════════════════════════════════════════════════

function fieldShell(def) {
	const el = document.createElement("div");
	el.className = `rc-field rc-field--${def.type || "text"}`;
	el.innerHTML = `${
		def.label
			? `<label class="rc-field__label">${esc(def.label)}${
					def.required ? '<span class="rc-field__req" aria-hidden="true"> *</span>' : ""
			  }</label>`
			: ""
	}<div class="rc-field__control"></div>
	${def.hint ? `<p class="rc-field__hint">${esc(def.hint)}</p>` : ""}
	<div class="rc-field__error" role="alert"></div>`;
	return el;
}

/**
 * Bitta maydon.
 *
 * Har bir maydon `{name, el, get(), validate(), focus()}` qaytaradi:
 * `validate()` xato matnini (yoki bo'sh satr) beradi.
 */
function buildField(def) {
	const type = def.type || "text";
	const el = fieldShell(def);
	const control = el.querySelector(".rc-field__control");
	const required = __("Majburiy maydon");

	const result = { name: def.name, el, focus: null };
	const check = (empty, value) => {
		if (def.required && empty) return required;
		return (def.validate && !empty && def.validate(value)) || "";
	};

	if (type === "note") {
		control.innerHTML = `<p class="rc-field__note">${esc(def.text)}</p>`;
		return { ...result, name: null, get: () => undefined, validate: () => "" };
	}

	if (type === "text" || type === "tel" || type === "textarea") {
		const input = document.createElement(type === "textarea" ? "textarea" : "input");
		input.className = "rc-input";
		if (type === "textarea") input.rows = def.rows || 2;
		else input.type = "text";
		input.autocomplete = "off";
		input.value = def.value || "";
		input.placeholder = def.placeholder || "";
		input.dataset.vk = type === "tel" ? "tel" : "text";
		input.setAttribute(
			"inputmode",
			kit.virtualKeyboard ? "none" : type === "tel" ? "tel" : "text"
		);
		control.appendChild(input);

		return {
			...result,
			get: () => input.value.trim(),
			validate: () => check(!input.value.trim(), input.value.trim()),
			focus: () => input.focus(),
		};
	}

	if (type === "amount") {
		const amount = amountInput({ value: def.value, label: def.label });
		control.appendChild(amount.el);
		return {
			...result,
			get: () => amount.value(),
			validate: () => check(!amount.value(), amount.value()),
			focus: amount.focus,
		};
	}

	if (type === "time") {
		const slot = def.value
			? { hour: hhmm(def.value).split(":")[0], minute: hhmm(def.value).split(":")[1] }
			: defaultTimeSlot();
		const hours = chips({ options: HOUR_OPTIONS, value: slot.hour, columns: 8 });
		const minutes = chips({ options: MINUTE_OPTIONS, value: pad2(slot.minute), columns: 4 });
		control.append(hours.el, minutes.el);
		return {
			...result,
			get: () => `${hours.value()}:${minutes.value()}`,
			validate: () => "",
		};
	}

	// select / chips — ikkalasi ham tanlov tugmalari.
	const picker = chips({
		options: def.options || [],
		value: def.value === undefined ? null : def.value,
		multiple: type === "chips" && !!def.multiple,
		columns: def.columns || 0,
		other: def.other || null,
	});
	control.appendChild(picker.el);
	return {
		...result,
		get: () => picker.value(),
		validate: () => {
			const value = picker.value();
			return check(Array.isArray(value) ? !value.length : !value, value);
		},
		focus: null,
	};
}

// ═══════════════════════════════════════════════════════════════
//  Oyna
// ═══════════════════════════════════════════════════════════════

/**
 * @typedef {object} DialogOptions
 * @property {string} title
 * @property {string} [subtitle]
 * @property {"sm"|"md"|"lg"|"xl"} [size="md"]
 * @property {string} [note]              oddiy matn (ekranlanadi)
 * @property {boolean} [big]              matn katta harflarda (masalan qaytim summasi)
 * @property {string|Node|Function} [body]  tayyor tarkib; funksiya bo'lsa `body(dialog)`
 * @property {object[]} [fields]          forma maydonlari (`buildField`)
 * @property {object[]} [actions]         `{id, label, kind, onClick(dialog), closes}`
 * @property {boolean} [dismissible=true] Esc / orqa fon / × bilan yopiladimi
 */
export class Dialog {
	constructor(options) {
		this.options = options;
		this.dismissible = options.dismissible !== false;
		this.closed = false;
		this.busy = false;
		this.opener = null;
		this.fields = [];
		this.kb = null;

		this.result = new Promise((resolve) => (this.resolve = resolve));
		this.build();
	}

	build() {
		const { title, subtitle, note, body, fields, actions = [] } = this.options;

		// Matn klaviaturasi ochiladigan oyna kamida 760px kenglikda: tor oynada
		// 10 tugmali qator 48px dan kichik tugmalarga bo'linib ketardi.
		const typed = (fields || []).some(
			(f) => ["text", "textarea", "tel"].includes(f.type || "text") || f.other
		);
		const size =
			kit.virtualKeyboard && typed && ["sm", "md"].includes(this.options.size || "md")
				? "lg"
				: this.options.size || "md";

		this.overlay = document.createElement("div");
		this.overlay.className = "rc-overlay rc-overlay--dialog";
		this.overlay.innerHTML = `
			<div class="rc-dialog rc-dialog--${esc(size)}" role="dialog" aria-modal="true"
				aria-label="${esc(title)}">
				<div class="rc-dialog__head">
					<div class="rc-dialog__heading">
						<h2 class="rc-dialog__title">${esc(title)}</h2>
						${subtitle ? `<p class="rc-dialog__subtitle">${esc(subtitle)}</p>` : ""}
					</div>
					${
						this.dismissible
							? `<button type="button" class="rc-dialog__x"
								aria-label="${esc(__("Yopish"))}">×</button>`
							: ""
					}
				</div>
				<div class="rc-dialog__body"></div>
				<div class="rc-dialog__error" role="alert"></div>
				<div class="rc-dialog__kb" hidden></div>
				<div class="rc-dialog__foot"></div>
			</div>`;

		this.dialogEl = this.overlay.querySelector(".rc-dialog");
		this.bodyEl = this.overlay.querySelector(".rc-dialog__body");
		this.errorEl = this.overlay.querySelector(".rc-dialog__error");
		this.footEl = this.overlay.querySelector(".rc-dialog__foot");

		if (note) {
			const p = document.createElement("p");
			p.className = `rc-dialog__note${this.options.big ? " rc-dialog__note--big" : ""}`;
			p.textContent = note;
			this.bodyEl.appendChild(p);
		}

		if (body) {
			const content = typeof body === "function" ? body(this) : body;
			if (typeof content === "string") this.bodyEl.insertAdjacentHTML("beforeend", content);
			else if (content) this.bodyEl.appendChild(content);
		}

		(fields || []).forEach((def) => {
			const field = buildField(def);
			this.fields.push(field);
			this.bodyEl.appendChild(field.el);

			// Xato kassir maydonni tuzatgan zahoti yo'qoladi: to'g'rilangan maydonda
			// «Majburiy maydon» qolib ketsa, kassir yana xato deb o'ylaydi. Yozish
			// (`input`), tanlov (`change`) va tugma-tanlov (chip) bosilganda tozalanadi;
			// maydonga shunchaki tegish (fokus) xatoni olib tashlamaydi.
			field.el.addEventListener("input", () => this.clearFieldError(field));
			field.el.addEventListener("change", () => this.clearFieldError(field));
			field.el.addEventListener("click", (event) => {
				if (event.target.closest(".rc-chip-opt")) this.clearFieldError(field);
			});
		});

		this.footEl.innerHTML = actions
			.map(
				(action) => `<button type="button" class="rc-btn rc-btn--${esc(
					action.kind || "default"
				)}"
					data-action="${esc(action.id)}" ${action.disabled ? "disabled" : ""}>${esc(action.label)}</button>`
			)
			.join("");
		this.footEl.hidden = !actions.length;

		this.bind(actions);
	}

	bind(actions) {
		const x = this.overlay.querySelector(".rc-dialog__x");
		if (x) x.addEventListener("click", () => !this.busy && this.close(null));

		this.overlay.addEventListener("click", (event) => {
			if (event.target === this.overlay && this.dismissible && !this.busy) this.close(null);
		});
		this.overlay.addEventListener("keydown", (event) => trapTab(this.dialogEl, event));

		this.footEl.addEventListener("click", async (event) => {
			const button = event.target.closest("[data-action]");
			if (!button || button.disabled) return;

			const action = actions.find((a) => a.id === button.dataset.action);
			if (!action || !action.onClick) return;
			await action.onClick(this, button);
		});

		if (kit.virtualKeyboard) this.bindKeyboard();
	}

	/** Matn maydoniga fokus tushganda klaviatura oynaning pastida ochiladi. */
	bindKeyboard() {
		const host = this.overlay.querySelector(".rc-dialog__kb");
		this.kb = new TextKeyboard(host, { onEnter: (input) => this.enter(input) });

		this.overlay.addEventListener("focusin", (event) => {
			if (!event.target.dataset || !event.target.dataset.vk) return;
			this.kb.bind(event.target);
			event.target.scrollIntoView({ block: "nearest" });
		});
		this.overlay.addEventListener("focusout", () => {
			// Fokus boshqa matn maydoniga o'tayotgan bo'lishi mumkin — bir tik kutamiz.
			setTimeout(() => {
				const active = document.activeElement;
				if (
					!(
						active &&
						active.dataset &&
						active.dataset.vk &&
						this.overlay.contains(active)
					)
				) {
					this.kb.unbind();
				}
			}, 60);
		});
	}

	/** «↵»: keyingi maydonga o'tadi, oxirgisida — yuboradi. */
	enter(input) {
		const inputs = [...this.overlay.querySelectorAll("[data-vk]")].filter((el) => !el.hidden);
		const next = inputs[inputs.indexOf(input) + 1];
		if (next) next.focus();
		else input.blur();
	}

	open() {
		// Yopilganda fokus shu elementga qaytadi (aks holda `body` ga tushib, klaviatura
		// bilan ishlayotgan kassir joyini yo'qotardi).
		this.opener = document.activeElement;
		kit.host.appendChild(this.overlay);
		stack.push(this);

		const first = this.fields.find((f) => f.focus);
		const primary = this.footEl.querySelector(
			".rc-btn--primary, .rc-btn--pay, .rc-btn--danger-solid"
		);
		// Ekran klaviaturasi yoqiq bo'lsa birinchi maydonga fokus klaviaturani ham ochadi.
		if (first) first.focus();
		else if (primary) primary.focus();
		else this.focus();
		return this;
	}

	/** Fokusni oynaning o'ziga qaytaradi (jismoniy klaviatura shu yerdan eshitiladi). */
	focus() {
		this.dialogEl.tabIndex = -1;
		this.dialogEl.focus({ preventScroll: true });
	}

	values() {
		const out = {};
		this.fields.forEach((field) => {
			if (field.name) out[field.name] = field.get();
		});
		return out;
	}

	clearFieldError(field) {
		field.el.classList.remove("rc-field--invalid");
		field.el.querySelector(".rc-field__error").textContent = "";
	}

	/** Majburiy maydonlarni tekshiradi; birinchi xatoli maydonga o'tadi. */
	validate() {
		let firstBad = null;
		this.fields.forEach((field) => {
			const message = field.validate();
			field.el.classList.toggle("rc-field--invalid", !!message);
			field.el.querySelector(".rc-field__error").textContent = message;
			if (message && !firstBad) firstBad = field;
		});
		if (firstBad) firstBad.el.scrollIntoView({ block: "nearest" });
		return !firstBad;
	}

	setError(text) {
		this.errorEl.textContent = text || "";
	}

	setBusy(state) {
		this.busy = !!state;
		this.footEl.querySelectorAll("button").forEach((button) => {
			button.disabled = !!state;
			button.classList.toggle("rc-btn--busy", !!state);
		});
	}

	close(result = null) {
		if (this.closed) return;
		this.closed = true;

		const at = stack.indexOf(this);
		if (at !== -1) stack.splice(at, 1);
		this.overlay.remove();

		const { opener } = this;
		if (opener && opener !== document.body && opener.isConnected && opener.focus) {
			opener.focus({ preventScroll: true });
		}
		this.resolve(result);
	}
}

// ═══════════════════════════════════════════════════════════════
//  Tayyor shakllar
// ═══════════════════════════════════════════════════════════════

/** Erkin oyna. `dialog.result` — yopilganda hal bo'ladigan promise. */
export function dialog(options) {
	return new Dialog(options).open();
}

/**
 * Forma oynasi.
 *
 * `onSubmit(values, dialog)` berilsa — yuborish tugmasi shuni kutadi: xato
 * tashlasa oyna YOPILMAYDI va xato matni oynada ko'rinadi (masalan server
 * sababni rad etdi), muvaffaqiyatli bo'lsa oyna yopiladi.
 *
 * @returns {Promise<object|null>}  kiritilgan qiymatlar; bekor qilinsa `null`
 */
export function form({
	title,
	subtitle,
	note,
	fields,
	submitLabel,
	cancelLabel,
	kind = "primary",
	size,
	onSubmit,
}) {
	const instance = new Dialog({
		title,
		subtitle,
		note,
		size,
		fields,
		actions: [
			{ id: "cancel", label: cancelLabel || __("Yopish"), onClick: (d) => d.close(null) },
			{
				id: "submit",
				label: submitLabel || __("Tasdiqlash"),
				kind,
				onClick: async (d) => {
					d.setError("");
					if (!d.validate()) return;

					const values = d.values();
					if (!onSubmit) return d.close(values);

					d.setBusy(true);
					try {
						await onSubmit(values, d);
						d.close(values);
					} catch (error) {
						d.setError(errorText(error));
					} finally {
						d.setBusy(false);
					}
				},
			},
		],
	});
	return instance.open().result;
}

/** Ha/Yo'q savoli. @returns {Promise<boolean>} */
export function confirm({
	title,
	message,
	confirmLabel,
	cancelLabel,
	kind = "primary",
	size = "sm",
}) {
	return new Dialog({
		title,
		note: message,
		size,
		actions: [
			{ id: "no", label: cancelLabel || __("Yo'q"), onClick: (d) => d.close(false) },
			{ id: "yes", label: confirmLabel || __("Ha"), kind, onClick: (d) => d.close(true) },
		],
	})
		.open()
		.result.then(Boolean);
}

/** Bitta «OK» tugmali xabar (masalan qaytim summasi). @returns {Promise<void>} */
export function alert({ title, message, okLabel, big = false, size = "sm" }) {
	return new Dialog({
		title,
		note: message,
		big,
		size,
		actions: [
			{
				id: "ok",
				label: okLabel || __("OK"),
				kind: "primary",
				onClick: (d) => d.close(true),
			},
		],
	})
		.open()
		.result.then(() => undefined);
}
