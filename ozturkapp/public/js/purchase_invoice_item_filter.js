// Purchase Invoice — prixod (kirim) formasi.
//
// 1. Item ro'yxati: xomashyo, yarim tayyor va ichimlik guruhlari. Ular sotib
//    olinadi (prixod qilinadi); taomlar esa Production Entry orqali
//    ishlab chiqariladi va bu yerda tanlanmaydi.
//    Guruh saytda mavjud bo'lmasa filtr qo'yilmaydi (qarang item_group_filter.js).
//
// 2. Bo'sh yangi faktura prixod rejimida ochiladi: "Update Stock" yoqilgan.
//    Usiz faktura omborga kirim yozmaydi — demak tannarx ham hosil bo'lmaydi.
//    Buyurtma/qabul hujjatidan yaratilgan (qatorlari tayyor) faktura va
//    qaytarish (debit note) o'zgarmaydi. Kerak bo'lsa galochka qo'lda
//    olib tashlanadi.
//    Ombor: har bir qatorga tovarning standart ombori (Item Default) tushadi,
//    u bo'lmasa Stock Settings'dagi standart ombor.
const PURCHASE_ITEM_GROUPS = ['Сырьё', 'Полуфабрикат', 'Напитки'];

frappe.ui.form.on('Purchase Invoice', {
    refresh(frm) {
        ozturkapp.item_filter.apply(frm, PURCHASE_ITEM_GROUPS);
    },

    onload(frm) {
        const is_blank_invoice =
            frm.is_new() &&
            !frm.doc.is_return &&
            !frm.doc.return_against &&
            !(frm.doc.items || []).some((row) => row.item_code);

        if (is_blank_invoice && !frm.doc.update_stock) {
            frm.set_value('update_stock', 1);
        }
    }
});
