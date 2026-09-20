/**
 * Choychaqa (`tips`): to'lov oynasidagi tez tanlash tugmalari.
 *
 * KIM HISOBLAYDI
 * ==============
 * Choychaqani server qabul qiladi, yaxlitlaydi va tekshiradi
 * (`billing._apply_tip`: manfiy emas, chekning xizmat haqisiz summasidan
 * oshmaydi); u ERPNext soliq qatori bo'lib yoziladi. Tugmalardagi summalar
 * FAQAT TAKLIF — kassir nimani bosishini ko'rsatish uchun: asos serverdagi
 * chegirmadan keyingi mahsulotlar summasi (`bill.subtotal` = `net_total`).
 * Yakuniy summa `submit_payment(tip=...)` javobida qaytadi.
 *
 * ORTIQCHA SANAMASLIK
 * ===================
 * Chekda choychaqa qatori allaqachon bo'lsa, `bill.payable` uni O'Z ICHIGA
 * OLGAN. Shuning uchun choychaqasiz asos = `payable - bill.tip`, tanlangan
 * choychaqa shunga QO'SHILADI; server ham `tip` ni har doim mavjud qatorni
 * ALMASHTIRUVCHI mutlaq summa deb qabul qiladi.
 */

const { slots, ui, util } = ozturk.cashier;
const { esc, fmtQty, num } = util;

const NONE = "none";
const CUSTOM = "custom";

class TipSection {
	constructor(session) {
		this.session = session;
		this.screen = session.screen;

		this.net = flt(session.bill.subtotal);
		this.existing = flt(session.bill.tip);
		// Choychaqa qatorisiz to'lanadigan summa.
		this.base = session.due - this.existing;
		this.percents = ((this.screen.ctx.feature_settings || {}).tip_percent_options || [])
			.map(flt)
			.filter((percent) => percent > 0);

		this.amount = this.existing;
		this.choice = this.existing > 0 ? CUSTOM : NONE;
		session.setArg("tip", this.amount);

		this.chips = ui.chips({
			options: [
				{ value: NONE, label: __("Choychaqasiz") },
				...this.percents.map((percent) => ({
					value: `p:${percent}`,
					// Ikki qator: foiz va taklif summa (`white-space: pre-line`).
					// Probelli summa qatorga bo'linib ketmasin (qattiq probel).
					label: `${fmtQty(percent)}%\n${this.screen
						.money(this.suggest(percent))
						.replace(/ /g, "\u00a0")}`,
				})),
				{ value: CUSTOM, label: __("Summa") },
			],
			value: this.choice,
			onChange: (value) => this.choose(value),
		});
		// «Choychaqasiz» va «Summa» foizlardan kengroq: matn sig'ishi uchun.
		this.chips.el.querySelector(".rc-chips").style.gridTemplateColumns = [
			"minmax(0, 1.9fr)",
			...this.percents.map(() => "minmax(0, 1fr)"),
			"minmax(0, 1.25fr)",
		].join(" ");

		this.el = document.createElement("div");
		this.el.className = "rc-payment-tip";
		this.el.innerHTML = `<div class="rc-payment-label">
				<span>${esc(__("Choychaqa"))}</span>
				<span class="rc-payment-tip__amount"></span>
			</div>`;
		this.el.appendChild(this.chips.el);
		this.paintAmount();
	}

	/** Foizga mos TAKLIF summa — faqat tugmada ko'rsatish uchun. */
	suggest(percent) {
		return Math.round((this.net * percent) / 100);
	}

	paintAmount() {
		this.el.querySelector(".rc-payment-tip__amount").textContent =
			this.amount > 0 ? `+${this.screen.money(this.amount)}` : "";
	}

	async choose(value) {
		if (value === NONE) return this.apply(0);

		if (value === CUSTOM) {
			const values = await ui.form({
				title: __("Choychaqa summasi"),
				fields: [
					{
						type: "amount",
						name: "amount",
						label: __("Summa"),
						required: true,
						value: this.choice === CUSTOM && this.amount > 0 ? this.amount : undefined,
						hint: __("Eng ko'pi: {0}", [this.screen.money(this.net)]),
					},
				],
				submitLabel: __("Qo'yish"),
			});

			// Bekor qilindi: oldingi tanlov qoladi.
			if (!values) return this.chips.set(this.choice);
			this.choice = CUSTOM;
			return this.apply(values.amount);
		}

		this.choice = value;
		this.apply(this.suggest(num(value.slice(2))));
	}

	/**
	 * Tanlangan choychaqani sessiyaga o'tkazadi: so'rov parametri va ko'rinadigan
	 * jami. To'liq summa kiritilgan to'lov usuli jami bilan birga suriladi
	 * (`session.setDue`); kassir qo'lda boshqa summa yozgan bo'lsa — tegilmaydi.
	 */
	apply(amount) {
		if (amount === 0) this.choice = NONE;

		const { session } = this;

		this.amount = amount;
		session.setArg("tip", amount);
		session.setDue(this.base + amount);
		this.chips.set(this.choice);
		this.paintAmount();
	}
}

export function installTips() {
	// Summa maydonidan OLDIN: choychaqa jami summani o'zgartiradi, kassir esa
	// qabul qilinadigan summani shundan keyin kiritadi.
	return slots.contribute("payment.beforeAmount", {
		id: "payment-tips",
		order: 100,
		render: (session) => new TipSection(session).el,
	});
}
