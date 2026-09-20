/**
 * Stol tanlash setkasi: zal tugmalari + stol kartalari.
 *
 * Ko'chirish, birlashtirish, ajratish va yangi bron shu bitta komponentdan
 * foydalanadi. Karta holatni FAQAT rang bilan emas, belgi va matn bilan ham
 * ko'rsatadi (● bo'sh, ◆ bron, ■ band).
 */

const { ui, util } = ozturk.cashier;
const { esc } = util;

const collator = new Intl.Collator(undefined, { numeric: true });

/**
 * @typedef {object} PickerEntry
 * @property {string}  name      stol nomi
 * @property {string}  room      zal
 * @property {"free"|"reserved"|"busy"|"merged"|"primary"} tone  karta ko'rinishi
 * @property {string}  mark      holat belgisi
 * @property {string}  text      kartadagi ikkinchi qator
 * @property {boolean} [disabled]
 * @property {string}  [reason]  o'chirilgan kartaning sababi (tooltip)
 * @property {number}  [seats]
 */

/**
 * @param {object}   options
 * @param {PickerEntry[]} options.entries
 * @param {string[]} [options.rooms]     zallar tartibi
 * @param {string}   [options.room]      boshlang'ich zal (bo'sh — barcha zallar)
 * @param {boolean}  [options.multiple]  bir nechta stol tanlash
 * @param {string}   [options.emptyText]
 * @param {Function} [options.onChange]  `onChange(value)`
 * @returns {{el: HTMLElement, value: Function, entry: Function}}
 */
export function tablePicker({
	entries,
	rooms = [],
	room = "",
	multiple = false,
	emptyText = "",
	onChange = null,
}) {
	const rank = (name) => {
		const at = rooms.indexOf(name);
		return at === -1 ? rooms.length : at;
	};
	const sorted = [...entries].sort(
		(a, b) => rank(a.room) - rank(b.room) || collator.compare(a.name, b.name)
	);
	const roomList = [...new Set(sorted.map((entry) => entry.room))];

	let filter = roomList.includes(room) ? room : "";
	const selected = new Set();

	const el = document.createElement("div");
	el.className = "rc-tt";

	if (roomList.length > 1) {
		const count = (name) => sorted.filter((entry) => entry.room === name).length;
		const chipset = ui.chips({
			options: [
				{ value: "", label: `${__("Barcha zallar")} (${sorted.length})` },
				...roomList.map((name) => ({ value: name, label: `${name} (${count(name)})` })),
			],
			value: filter,
			columns: Math.min(roomList.length + 1, 4),
			onChange: (value) => {
				filter = value;
				paint();
			},
		});
		const bar = document.createElement("div");
		bar.className = "rc-tt__rooms";
		bar.appendChild(chipset.el);
		el.appendChild(bar);
	}

	const grid = document.createElement("div");
	grid.className = "rc-tt__grid";
	el.appendChild(grid);

	function tileHtml(entry) {
		return `<button type="button" class="rc-tt__tile rc-tt__tile--${esc(entry.tone)}"
			data-table="${esc(entry.name)}" aria-pressed="${selected.has(entry.name)}"
			${entry.disabled ? "disabled" : ""}${entry.reason ? ` title="${esc(entry.reason)}"` : ""}>
			<span class="rc-tt__name">${esc(entry.name)}</span>
			<span class="rc-tt__meta"><span aria-hidden="true">${esc(entry.mark)}</span> ${esc(
			entry.text
		)}</span>
		</button>`;
	}

	function paint() {
		const visible = sorted.filter((entry) => !filter || entry.room === filter);
		if (!visible.length) {
			grid.innerHTML = `<p class="rc-tt__empty">${esc(emptyText)}</p>`;
			return;
		}

		// Barcha zallar ko'rinishida stollar zal bo'yicha guruhlanadi.
		const grouped = !filter && roomList.length > 1;
		let previous = null;
		grid.innerHTML = visible
			.map((entry) => {
				const head =
					grouped && entry.room !== previous
						? `<div class="rc-tt__group">${esc(entry.room)}</div>`
						: "";
				previous = entry.room;
				return head + tileHtml(entry);
			})
			.join("");
	}

	function value() {
		if (multiple) return sorted.filter((entry) => selected.has(entry.name)).map((e) => e.name);
		const [name] = [...selected];
		return name || "";
	}

	grid.addEventListener("click", (event) => {
		const tile = event.target.closest(".rc-tt__tile");
		if (!tile || tile.disabled) return;

		const name = tile.dataset.table;
		if (!multiple) selected.clear();
		if (multiple && selected.has(name)) selected.delete(name);
		else selected.add(name);

		grid.querySelectorAll(".rc-tt__tile").forEach((node) =>
			node.setAttribute("aria-pressed", String(selected.has(node.dataset.table)))
		);
		if (onChange) onChange(value());
	});

	paint();
	return { el, value, entry: (name) => entries.find((entry) => entry.name === name) || null };
}
