/**
 * Stollar moduli: stolni ko'chirish / birlashtirish / ajratish va bronlar.
 *
 * Alohida yig'ma: yadroga `ozturk.cashier` nomlar fazosi orqali ulanadi,
 * sahifa uni `assets.json` dan o'zi topadi (README §5). Ikki funksiya alohida
 * kalit bilan ro'yxatdan o'tadi:
 *
 *   table_transfer      POS Profile bayrog'i bilan (pul/hisobga ta'sir qiladi)
 *   table_reservations  bayroqsiz — bron ma'lumoti har kassirga kerak
 *
 * `panel.more` / `topbar.menu` tartib raqamlari: 300–399 (stollar).
 */

import { installReservations } from "./reservations.js";
import { installTransfer } from "./table_ops.js";

const { features } = ozturk.cashier;

features.register({ key: "table_transfer", flag: "table_transfer", install: installTransfer });
features.register({ key: "table_reservations", install: installReservations });
