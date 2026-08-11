// Purchase Invoice — item kiritishda faqat xom-ashyo guruhidagi itemlar.
// Guruh saytda mavjud bo'lmasa filtr qo'yilmaydi (qarang item_group_filter.js).
frappe.ui.form.on('Purchase Invoice', {
    refresh(frm) {
        ozturkapp.item_filter.apply(frm, ['Сырьё']);
    }
});
