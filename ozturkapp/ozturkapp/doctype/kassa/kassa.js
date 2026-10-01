/**
 * Kassa — Client Script
 *
 * Bitta kompaniyali kassa (kassa.py docstring):
 * 1. Oborot tanlash
 * 2. Kassa (Mode of Payment) — kompaniya kassadan avtomatik aniqlanadi.
 *    Kassa bir nechta kompaniyada hisobga ega bo'lsa — «Kompaniya» maydonidagi
 *    qator olinadi (guruh kompaniyasi «O'zturk» hisobga olinmaydi).
 * 3. Kontragent / xarajat hisobi — shu kompaniya bo'yicha filtrlanadi
 */

const KASSA_API = 'ozturkapp.ozturkapp.doctype.kassa.kassa.';
const LINK_FIELDS = ['journal_entry', 'payment_entry'];

function mop_query(frm, exclude) {
    // Kompaniya tanlangan bo'lsa — faqat shu kompaniyada hisobi bor kassalar
    return {
        query: KASSA_API + 'get_filtered_mode_of_payments',
        filters: { company: frm.doc.company || '', exclude: exclude || '' }
    };
}

function load_mop(frm, mop, company, on_ok, on_fail) {
    frappe.call({
        method: KASSA_API + 'get_mode_of_payment_info',
        args: { mode_of_payment: mop, company: company || '' },
        callback(r) {
            const info = r.message || {};
            if (info.account) {
                on_ok(info);
                return;
            }
            if (info.ambiguous) {
                frappe.msgprint({
                    title: __('Kompaniyani tanlang'),
                    indicator: 'orange',
                    message: __("'{0}' kassasi bir nechta kompaniyada hisobga ega ({1}). Avval «Kompaniya» maydonini tanlang.",
                        [mop, (info.companies || []).join(', ')])
                });
            } else if (company && (info.companies || []).length) {
                frappe.msgprint({
                    title: __('Kassa hisobi topilmadi'),
                    indicator: 'orange',
                    message: __("'{0}' kassasi '{1}' kompaniyasi uchun hisobga ega emas (u {2} ga tegishli).",
                        [mop, company, info.companies.join(', ')])
                });
            } else {
                frappe.msgprint({
                    title: __('Hisob topilmadi'),
                    indicator: 'orange',
                    message: __("'{0}' uchun hisob (Account) bog'lanmagan. Mode of Payment sozlamalarini tekshiring.", [mop])
                });
            }
            on_fail && on_fail(info);
        }
    });
}

frappe.ui.form.on('Kassa', {

    // =========================================================================
    // LIFECYCLE
    // =========================================================================

    onload(frm) {
        // Amend/Duplicate nusxasida eski hujjatning JE/PE havolalari ko'rinmasin
        // (server ham insert'da tozalaydi).
        if (frm.is_new()) {
            LINK_FIELDS.forEach((f) => { if (frm.doc[f]) frm.doc[f] = null; });
            // Foydalanuvchining standart kompaniyasi guruh («O'zturk») bo'lsa —
            // u kassa yuritmaydi, maydonni bo'sh qoldiramiz
            if (frm.doc.company) {
                frappe.db.get_value('Company', frm.doc.company, 'is_group').then((r) => {
                    if (r && r.message && r.message.is_group) frm.set_value('company', '');
                });
            }
        }
        frm.trigger('setup_filters');
    },

    refresh(frm) {
        frm.trigger('setup_filters');
        frm.trigger('toggle_fields');
    },

    // =========================================================================
    // SETUP FILTERS
    // =========================================================================

    setup_filters(frm) {
        // Guruh kompaniyasi kassa yuritmaydi
        frm.set_query('company', () => ({ filters: { is_group: 0 } }));

        frm.set_query('source_account', () => mop_query(frm));
        frm.set_query('transfer_source_display', () => mop_query(frm));
        // Перемещение faqat bitta kompaniya ichida
        frm.set_query('target_account', () => {
            if (!frm.doc.transfer_source_display || !frm.doc.company) {
                return { filters: { name: ['in', []] } };
            }
            return mop_query(frm, frm.doc.transfer_source_display);
        });

        // Filial - faqat faol
        frm.set_query('filial', () => ({
            filters: { is_active: 1 }
        }));

        // Party Type - barchasi
        frm.set_query('party_type', () => ({}));

        // Kontragent filtri — faqat faol (disabled bo'lmagan) kontragentlar.
        frm.set_query('kontragent', () => {
            const party_type = frm.doc.party_type;
            if (party_type === 'Customer' || party_type === 'Supplier') {
                return { filters: { disabled: 0 } };
            }
            return {};
        });

        // Xarajat kontragenti — kompaniyaning xarajat hisoblari.
        // Filial tanlangan bo'lsa, uning «Xarajat guruhi» ostidagilar bilan
        // filtrlanadi; tanlanmagan bo'lsa barcha xarajat hisoblari chiqadi.
        frm.set_query('expense_kontragent', () => ({
            query: KASSA_API + 'get_filial_expense_accounts',
            filters: {
                filial: frm.doc.filial || '',
                company: frm.doc.company || ''
            }
        }));
    },

    // Filial o'zgarsa - xarajat hisobini tozalash (guruh/kompaniya o'zgaradi)
    filial(frm) {
        frm.set_value('expense_kontragent', '');
    },

    // =========================================================================
    // KOMPANIYA
    // =========================================================================

    company(frm) {
        // Kassa hisobini yangi kompaniya bo'yicha qayta aniqlash
        const mop_field = frm.doc.oborot === 'Перемещение' ? 'transfer_source_display' : 'source_account';
        frm.set_value('target_account', '');
        frm.set_value('target_balance', 0);
        frm.set_value('payment_account_2', '');
        frm.set_value('expense_kontragent', '');
        if (frm.doc[mop_field]) {
            frm.trigger(mop_field);
        } else {
            frm.set_value('payment_account', '');
        }
    },

    // =========================================================================
    // OBOROT O'ZGARSA
    // =========================================================================

    oborot(frm) {
        // Barcha maydonlarni tozalash (kompaniya saqlanadi — u foydalanuvchi tanlovi)
        frm.set_value('source_account', '');
        frm.set_value('source_balance', 0);
        frm.set_value('transfer_source_display', '');
        frm.set_value('transfer_source_balance', 0);
        frm.set_value('target_account', '');
        frm.set_value('target_balance', 0);
        frm.set_value('party_type', '');
        frm.set_value('kontragent', '');
        frm.set_value('expense_kontragent', '');
        frm.set_value('filial', '');
        frm.set_value('payment_account', '');
        frm.set_value('payment_account_2', '');

        frm.trigger('toggle_fields');
    },

    // =========================================================================
    // SOURCE ACCOUNT (Приход/Расход uchun)
    // =========================================================================

    source_account(frm) {
        if (!frm.doc.source_account) {
            frm.set_value('payment_account', '');
            frm.set_value('source_balance', 0);
            return;
        }
        load_mop(frm, frm.doc.source_account, frm.doc.company, (info) => {
            frm.set_value('payment_account', info.account);
            frm.set_value('source_balance', info.balance);
            if (frm.doc.company !== info.company) frm.set_value('company', info.company);
        }, (info) => {
            frm.set_value('payment_account', '');
            frm.set_value('source_balance', 0);
            if (!info.ambiguous) frm.set_value('source_account', '');
        });
    },

    // =========================================================================
    // TRANSFER SOURCE (Перемещение uchun)
    // =========================================================================

    transfer_source_display(frm) {
        frm.set_value('target_account', '');
        frm.set_value('target_balance', 0);
        frm.set_value('payment_account_2', '');

        if (!frm.doc.transfer_source_display) {
            frm.set_value('payment_account', '');
            frm.set_value('transfer_source_balance', 0);
            return;
        }
        load_mop(frm, frm.doc.transfer_source_display, frm.doc.company, (info) => {
            frm.set_value('payment_account', info.account);
            frm.set_value('transfer_source_balance', info.balance);
            if (frm.doc.company !== info.company) frm.set_value('company', info.company);
        }, (info) => {
            frm.set_value('payment_account', '');
            frm.set_value('transfer_source_balance', 0);
            if (!info.ambiguous) frm.set_value('transfer_source_display', '');
        });
    },

    // =========================================================================
    // TARGET ACCOUNT (Перемещение uchun) — faqat manba kompaniyasida
    // =========================================================================

    target_account(frm) {
        if (!frm.doc.target_account) {
            frm.set_value('payment_account_2', '');
            frm.set_value('target_balance', 0);
            return;
        }
        if (!frm.doc.company) {
            frappe.msgprint(__("Avval «Qaysi hisobdan» va «Kompaniya»ni tanlang."));
            frm.set_value('target_account', '');
            return;
        }
        load_mop(frm, frm.doc.target_account, frm.doc.company, (info) => {
            frm.set_value('payment_account_2', info.account);
            frm.set_value('target_balance', info.balance);
        }, () => {
            frm.set_value('target_account', '');
            frm.set_value('payment_account_2', '');
            frm.set_value('target_balance', 0);
        });
    },

    // =========================================================================
    // PARTY TYPE
    // =========================================================================

    party_type(frm) {
        frm.set_value('kontragent', '');
        frm.set_value('expense_kontragent', '');
        frm.set_value('filial', '');

        frm.trigger('toggle_fields');
    },

    // =========================================================================
    // TOGGLE FIELDS
    // =========================================================================

    toggle_fields(frm) {
        const oborot = frm.doc.oborot;
        const party_type = frm.doc.party_type;
        const is_transfer = oborot === 'Перемещение';
        const is_expense = party_type === 'Расходы';

        const standard_types = ['Customer', 'Supplier', 'Employee', 'Shareholder'];
        const is_standard = standard_types.includes(party_type);

        if (!is_transfer && oborot) {
            frm.set_df_property('party_type', 'reqd', 1);
            frm.set_df_property('kontragent', 'reqd', is_standard ? 1 : 0);
            frm.set_df_property('expense_kontragent', 'reqd', is_expense ? 1 : 0);
        } else {
            frm.set_df_property('party_type', 'reqd', 0);
            frm.set_df_property('kontragent', 'reqd', 0);
            frm.set_df_property('expense_kontragent', 'reqd', 0);
        }

        frm.refresh_fields();
    },

    // =========================================================================
    // VALIDATION
    // =========================================================================

    validate(frm) {
        const oborot = frm.doc.oborot;
        const party_type = frm.doc.party_type;

        if (frm.doc.summa <= 0) {
            frappe.throw(__("Summa 0 dan katta bo'lishi kerak"));
        }

        if (oborot === 'Перемещение') {
            if (!frm.doc.transfer_source_display) {
                frappe.throw(__("'Qaysi hisobdan' majburiy"));
            }
            if (!frm.doc.target_account) {
                frappe.throw(__("'Qaysi hisobga' majburiy"));
            }
            if (frm.doc.transfer_source_display === frm.doc.target_account) {
                frappe.throw(__("Manba va maqsad hisob bir xil bo'lishi mumkin emas"));
            }
        } else {
            if (!frm.doc.source_account) {
                frappe.throw(__("'Qaysi hisobdan' majburiy"));
            }
            if (!party_type) {
                frappe.throw(__("Kontragent turi tanlanmagan"));
            }

            const standard_types = ['Customer', 'Supplier', 'Employee', 'Shareholder'];

            if (standard_types.includes(party_type) && !frm.doc.kontragent) {
                frappe.throw(__("Kontragent tanlanmagan"));
            }

            if (party_type === 'Расходы' && !frm.doc.expense_kontragent) {
                frappe.throw(__("Xarajat kontragenti tanlanmagan"));
            }
        }
    }
});
