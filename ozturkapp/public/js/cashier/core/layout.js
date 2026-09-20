/**
 * Kassa sahifasining o'lchami: Desk sahifasi ichida QOLGAN joy va kenglik sinfi.
 *
 * Kassa har doim oddiy Desk sahifasi kabi chiziladi (yuqori panel va sahifa
 * sarlavhasi ko'rinib turadi; to'liq ekran rejimi ATAYLAB olib tashlangan).
 * Sahifa HECH QACHON aylanmasligi uchun ilova balandligi ekran o'lchamidan
 * emas, `.rc-root` ning ekrandagi HAQIQIY o'rnidan hisoblanadi: yuqoridagi
 * Desk panellari qancha joy olsa, qolganini ilova oladi.
 */

/**
 * Kenglik sinflari (`.rc-root[data-wide]`). Ilovaning O'Z kengligi o'lchanadi,
 * oyna kengligi emas: Desk sahifasi torroq konteynerda turishi mumkin.
 *
 * `wide` — zal rejasi bilan yonma-yon faol buyurtmalar ro'yxatiga joy yetadi
 * (o'ng panel bo'sh turganda ro'yxatni ko'rsatadi, «Stollar | Buyurtmalar»
 * almashtirgichi kerak emas).
 */
export const WIDE_MIN_WIDTH = 1200;

/** Ilova balandligi shu qiymatdan past bo'lsa «ixcham» zichlik (`data-short`). */
export const SHORT_MAX_HEIGHT = 640;

/** Ilova ostida qoldiriladigan bo'shliq (px): ramka Desk sahifasi chetiga yopishib qolmasin. */
const BOTTOM_GAP = 12;

const MAX_COMPACT = 5;

/**
 * `--rc-offset` (ilova tepasidan va ostidan Desk egallagan joy) ni o'rnatadi.
 *
 * Ikki bosqich: avval faqat yuqori chegara bo'yicha, so'ng hujjat hali ham
 * aylanadigan bo'lsa (Desk sahifa ostida qo'shimcha bo'shliq/padding qoldirgan)
 * o'sha ortiqcha qo'shiladi. Shunda sahifa aylanmaydi, ilova esa ekranning
 * qolgan qismini to'liq egallaydi.
 */
export function syncOffset(root) {
	if (!root) return;
	const top = Math.max(0, Math.round(root.getBoundingClientRect().top)) + BOTTOM_GAP;
	root.style.setProperty("--rc-offset", `${top}px`);

	const scroller = document.scrollingElement || document.documentElement;
	const overflow = Math.ceil(scroller.scrollHeight - window.innerHeight);
	if (overflow > 0) root.style.setProperty("--rc-offset", `${top + overflow}px`);
}

/**
 * Yuqori panel sig'masa kamroq muhim qismlarni ketma-ket yig'adi (`data-compact` 0..5,
 * qoidalari `layout.css` da): 1 kassir nomi, 2 filtr yozuvi, 3 tez tugma yozuvi, 4 zallar soni
 * va «Kassa ochiq» yozuvi, 5 zallar aylantiriladi. Panel kengligi ekran o'lchamidan emas, HAQIQIY tarkibdan o'lchanadi —
 * shuning uchun ko'p zalli yoki ko'p tez tugmali kassada ham hech narsa ustma-ust tushmaydi.
 */
export function syncCompact(root) {
	const bar = root && root.querySelector(".rc-topbar");
	if (!bar) return;
	let stage = 0;
	root.dataset.compact = "0";
	while (bar.clientWidth > 0 && bar.scrollWidth > bar.clientWidth + 1 && stage < MAX_COMPACT) {
		stage += 1;
		root.dataset.compact = String(stage);
	}
}

/** `data-wide` va `data-short` ni ilovaning haqiqiy o'lchamiga qarab qo'yadi. Kenglik sinfi o'zgardimi — qaytaradi. */
export function syncModes(root) {
	if (!root) return false;
	const { width, height } = root.getBoundingClientRect();
	const wide = width >= WIDE_MIN_WIDTH;
	const short = height > 0 && height < SHORT_MAX_HEIGHT;

	const changed = root.dataset.wide !== (wide ? "1" : "0");
	root.dataset.wide = wide ? "1" : "0";
	root.dataset.short = short ? "1" : "0";
	return changed;
}
