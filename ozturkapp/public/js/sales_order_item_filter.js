// Sales Order — item kiritishda yarim tayyor va xom-ashyo guruhlari.
// Guruhlar saytda mavjud bo'lmasa filtr qo'yilmaydi (qarang item_group_filter.js).
frappe.ui.form.on('Sales Order', {
    refresh(frm) {
        ozturkapp.item_filter.apply(frm, ['Полуфабрикат', 'Сырьё']);
    }
});
