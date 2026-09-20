/**
 * Kassa smenasi funksiyalari — alohida yig'ma (`cashier_shift.bundle.js`).
 *
 * Har biri o'z POS Profile bayrog'i bilan yoqiladi (`features.register`),
 * o'chiq bo'lsa ekranda umuman ko'rinmaydi; server ham har amalda
 * bayroqni qayta tekshiradi.
 *
 *   drawer.js     G'aladon         `cash_drawer`      topbar.quick, F9
 *   movements.js  Kassa harakati   `cash_movements`   topbar.menu
 *   report.js     X-hisobot        `shift_reports`    topbar.menu
 *
 * Slot tartibi (`order`) 400-499 oralig'ida: to'lov 100-199, buyurtma 200-299,
 * stollar 300-399. Uslub: `cashier_shift.bundle.css` (`.rc-shift-mv`, `.rc-shift-xr`).
 */

import "./drawer.js";
import "./movements.js";
import "./report.js";
