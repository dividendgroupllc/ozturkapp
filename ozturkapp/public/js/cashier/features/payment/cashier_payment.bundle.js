/**
 * To'lov funksiyalari: choychaqa, chegirma, qaytarish.
 *
 * Har biri o'z kaliti va bayrog'i bilan ro'yxatdan o'tadi — POS Profile'da
 * alohida yoqiladi/o'chiriladi (`ctx.features`). Bayroqsiz bittasi (eskirgan
 * chek eslatmasi) o'tgan amalning izini ko'rsatadi, shuning uchun funksiya
 * o'chirilgandan keyin ham kerak. Qaytarish cheklarining «QAYTARISH» belgisi
 * va asl chekka havolasi yadroda (`ui/history.js`).
 *
 * BU YERDA BIZNES MANTIQ YO'Q: summa, chegirma, qoldiq, qaytariladigan pul va
 * ruxsatlarni server hisoblaydi va tekshiradi. Frontend faqat kiritishni yig'adi
 * va serverning javobi/xatosini ko'rsatadi. Yadroga faqat `ozturk.cashier`
 * orqali ulanadi (README §3); yadro fayllari tahrirlanmaydi.
 */

import { installDiscount } from "./discount.js";
import { installRefunds } from "./refunds.js";
import { installReprintBanner } from "./reprint.js";
import { installTips } from "./tips.js";

const { features } = ozturk.cashier;

features.register({ key: "payment-tips", flag: "tips", install: installTips });
features.register({ key: "payment-discount", flag: "discount", install: installDiscount });
features.register({ key: "payment-refunds", flag: "refunds", install: installRefunds });
features.register({ key: "payment-reprint-banner", install: installReprintBanner });
