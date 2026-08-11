// Employee DocType uchun Manager-friendly forma
// Fayl: ozturkapp/public/js/employee.js

frappe.ui.form.on('Employee', {
    refresh: function(frm) {
        simplify_for_manager(frm);
    },
    
    onload: function(frm) {
        simplify_for_manager(frm);
    }
});

function simplify_for_manager(frm) {
    // To'liq huquqli foydalanuvchilarga (Administrator / System Manager /
    // HR Manager) standart to'liq forma ko'rsatiladi — soddalashtirish faqat
    // filial menejerlari uchun.
    if (frappe.session.user === "Administrator" ||
        frappe.user.has_role("System Manager") ||
        frappe.user.has_role("HR Manager")) {
        return;
    }
    
    // ═══════════════════════════════════════════════════════════════
    // BOSHQA TABLARNI YASHIRISH
    // ═══════════════════════════════════════════════════════════════
    setTimeout(function() {
        frm.page.wrapper.find('.form-tabs .nav-link').each(function() {
            const tab_text = $(this).text().trim();
            if (tab_text && tab_text !== 'Overview') {
                $(this).parent().hide();
            }
        });
    }, 100);
    
    // ═══════════════════════════════════════════════════════════════
    // OVERVIEW'DA KO'RSATILADIGAN FIELDLAR
    // ═══════════════════════════════════════════════════════════════
    const visible_fields = [
        'naming_series', 
        'first_name', 
        'middle_name', 
        'last_name',
        'employee_name',
        'gender', 
        'date_of_birth', 
        'date_of_joining', 
        'status',
        'company', 
        'designation',
        // Boshqa tab'lardagi fieldlar
        'attendance_device_id', 
        'default_shift',
        'hourly_rate',
        'cell_number', 
        'personal_email'
    ];
    
    // ═══════════════════════════════════════════════════════════════
    // BARCHA FIELDLARNI YASHIRISH (keraklilaridan tashqari)
    // ═══════════════════════════════════════════════════════════════
    frm.meta.fields.forEach(function(field) {
        const fn = field.fieldname;
        if (!fn) return;
        
        if (field.fieldtype === 'Tab Break' || 
            field.fieldtype === 'Section Break' || 
            field.fieldtype === 'Column Break') {
            return;
        }
        
        if (visible_fields.includes(fn)) {
            frm.set_df_property(fn, 'hidden', 0);
        } else {
            frm.set_df_property(fn, 'hidden', 1);
        }
    });
    
    // ═══════════════════════════════════════════════════════════════
    // MUHIM FIELDLARNI COMPANY DETAILS SECTION'GA KO'CHIRISH
    // ═══════════════════════════════════════════════════════════════
    setTimeout(function() {
        const company_section = frm.fields_dict['company']?.wrapper?.closest('.form-section');
        
        if (company_section) {
            // Attendance Device ID
            move_field_to_section(frm, 'attendance_device_id', company_section);
            // Default Shift  
            move_field_to_section(frm, 'default_shift', company_section);
            // Hourly Rate
            move_field_to_section(frm, 'hourly_rate', company_section);
            // Phone
            move_field_to_section(frm, 'cell_number', company_section);
            // Email
            move_field_to_section(frm, 'personal_email', company_section);
        }
    }, 200);
}

function move_field_to_section(frm, fieldname, target_section) {
    const field = frm.fields_dict[fieldname];
    if (field && field.wrapper) {
        // Field'ni ko'rsatish
        $(field.wrapper).show();
        frm.set_df_property(fieldname, 'hidden', 0);
        
        // Target section'ga ko'chirish
        const form_column = $(target_section).find('.form-column').last();
        if (form_column.length) {
            $(field.wrapper).appendTo(form_column);
        }
    }
}