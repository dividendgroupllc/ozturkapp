/**
 * Kassa - Client Script v3.6
 * 
 * Company Mode of Payment'dan avtomatik aniqlanadi:
 * 1. Oborot tanlash
 * 2. Mode of Payment tanlash → Company avtomatik (account'dan)
 * 3. Expense filter shu company bo'yicha
 */

frappe.ui.form.on('Kassa', {
    
    // =========================================================================
    // LIFECYCLE
    // =========================================================================
    
    onload(frm) {
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
        // Filial - faqat faol
        frm.set_query('filial', () => ({
            filters: { is_active: 1 }
        }));
        
        // Party Type - barchasi
        frm.set_query('party_type', () => ({}));

        // Kontragent filtri — faqat faol (disabled bo'lmagan) kontragentlar
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
            query: 'ozturkapp.ozturkapp.doctype.kassa.kassa.get_filial_expense_accounts',
            filters: {
                filial: frm.doc.filial || '',
                company: frm.doc.company || ''
            }
        }));

        // Target account - source tanlanmagan bo'lsa bo'sh
        // Source tanlangandan keyin transfer_source_display() da query o'rnatiladi
        frm.set_query('target_account', () => {
            if (!frm.doc.transfer_source_display) {
                // Source tanlanmagan - bo'sh ro'yxat ko'rsat
                return { filters: { name: ['in', []] } };
            }
            // Source tanlangan - company bo'yicha filter (transfer_source_display da set qilingan)
            return {};
        });
    },

    // Filial o'zgarsa - xarajat hisobini tozalash (guruh o'zgaradi)
    filial(frm) {
        frm.set_value('expense_kontragent', '');
    },
    
    // =========================================================================
    // OBOROT O'ZGARSA
    // =========================================================================
    
    oborot(frm) {
        // Barcha fieldlarni tozalash
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
        frm.set_value('company', '');
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
            frm.set_value('company', '');
            return;
        }
        
        frappe.call({
            method: 'ozturkapp.ozturkapp.doctype.kassa.kassa.get_mode_of_payment_info',
            args: { mode_of_payment: frm.doc.source_account },
            callback(r) {
                if (r.message) {
                    if (r.message.account) {
                        frm.set_value('payment_account', r.message.account);
                        frm.set_value('source_balance', r.message.balance);
                        frm.set_value('company', r.message.company);
                    } else {
                        frappe.msgprint({
                            title: __('Hisob topilmadi'),
                            indicator: 'orange',
                            message: __("'{0}' uchun hisob (Account) bog'lanmagan. Mode of Payment sozlamalarini tekshiring.", [frm.doc.source_account])
                        });
                        frm.set_value('source_account', '');
                    }
                }
            }
        });
    },
    
    // =========================================================================
    // TRANSFER SOURCE (Перемещение uchun)
    // =========================================================================
    
    transfer_source_display(frm) {
        // Target account tozalash (har doim)
        frm.set_value('target_account', '');
        frm.set_value('target_balance', 0);
        frm.set_value('payment_account_2', '');
        
        if (!frm.doc.transfer_source_display) {
            frm.set_value('payment_account', '');
            frm.set_value('transfer_source_balance', 0);
            frm.set_value('company', '');
            
            // Target filter - source yo'q bo'lsa, bo'sh ro'yxat
            frm.set_query('target_account', () => ({
                filters: { name: ['in', []] }  // Bo'sh ro'yxat
            }));
            return;
        }
        
        frappe.call({
            method: 'ozturkapp.ozturkapp.doctype.kassa.kassa.get_mode_of_payment_info',
            args: { mode_of_payment: frm.doc.transfer_source_display },
            callback(r) {
                if (r.message) {
                    if (r.message.account) {
                        frm.set_value('payment_account', r.message.account);
                        frm.set_value('transfer_source_balance', r.message.balance);
                        frm.set_value('company', r.message.company);
                        
                        // ✅ TARGET ACCOUNT FILTER - faqat shu company + source exclude
                        frm.set_query('target_account', () => ({
                            query: 'ozturkapp.ozturkapp.doctype.kassa.kassa.get_filtered_mode_of_payments',
                            filters: {
                                company: r.message.company,
                                exclude: frm.doc.transfer_source_display
                            }
                        }));
                        
                    } else {
                        frappe.msgprint({
                            title: __('Hisob topilmadi'),
                            indicator: 'orange',
                            message: __("'{0}' uchun hisob (Account) bog'lanmagan.", [frm.doc.transfer_source_display])
                        });
                        frm.set_value('transfer_source_display', '');
                    }
                }
            }
        });
    },
    
    // =========================================================================
    // TARGET ACCOUNT (Перемещение uchun)
    // =========================================================================
    
    target_account(frm) {
        if (!frm.doc.target_account) {
            frm.set_value('payment_account_2', '');
            frm.set_value('target_balance', 0);
            return;
        }
        
        frappe.call({
            method: 'ozturkapp.ozturkapp.doctype.kassa.kassa.get_mode_of_payment_info',
            args: { mode_of_payment: frm.doc.target_account },
            callback(r) {
                if (r.message) {
                    if (r.message.account) {
                        // MUHIM: Перемещение da company bir xil bo'lishi SHART
                        if (frm.doc.company && r.message.company !== frm.doc.company) {
                            frappe.msgprint({
                                title: __('Kompaniya nomuvofiq'),
                                indicator: 'red',
                                message: __("Перемещение: Manba hisob ({0}) va maqsad hisob ({1}) bir xil kompaniyaga tegishli bo'lishi kerak.<br><br>" +
                                    "Manba: {2}<br>Maqsad: {3}",
                                    [frm.doc.transfer_source_display, frm.doc.target_account, frm.doc.company, r.message.company])
                            });
                            frm.set_value('target_account', '');
                            frm.set_value('payment_account_2', '');
                            frm.set_value('target_balance', 0);
                            return;
                        }
                        
                        frm.set_value('payment_account_2', r.message.account);
                        frm.set_value('target_balance', r.message.balance);
                    } else {
                        frappe.msgprint({
                            title: __('Hisob topilmadi'),
                            indicator: 'orange',
                            message: __("'{0}' uchun hisob (Account) bog'lanmagan.", [frm.doc.target_account])
                        });
                        frm.set_value('target_account', '');
                    }
                }
            }
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
