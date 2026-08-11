// Copyright (c) 2026, Ozturkapp
// License: MIT
// Kunlik Ish Vaqti Hisoboti

frappe.query_reports["Employee Daily Hours"] = {
    filters: [
        {
            fieldname: "company",
            label: __("Filial"),
            fieldtype: "Link",
            options: "Company",
            // Faqat to'liq HR huquqiga ega foydalanuvchilar uchun ko'rinadi
            hidden: !(frappe.user.has_role("System Manager") || frappe.user.has_role("HR Manager")),
            on_change: function() {
                frappe.query_report.set_filter_value("employee", "");
                frappe.query_report.refresh();
            }
        },
        {
            fieldname: "employee",
            label: __("Xodim"),
            fieldtype: "Link",
            options: "Employee",
            // reqd emas - bo'sh qoldirilsa barcha xodimlar ko'rinadi
            get_query: function() {
                let filters = { status: "Active" };
                let company = frappe.query_report.get_filter_value("company");
                if (company) {
                    filters.company = company;
                }
                return { filters: filters };
            }
        },
        {
            fieldname: "date",
            label: __("Sana"),
            fieldtype: "Date",
            reqd: 1,
            default: frappe.datetime.get_today()
        }
    ],

    formatter: function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        
        if (!data) return value;
        
        // Sarlavha qatorlari
        if (data.row_num === "👤" || data.row_num === "📊" || data.row_num === "📋") {
            return `<strong style="font-size: 14px;">${value}</strong>`;
        }
        
        // Ustun sarlavhasi
        if (data.row_num === "#" && column.fieldname === "row_num") {
            return `<strong style="background: #f5f5f5; padding: 4px 8px;">${value}</strong>`;
        }
        
        // Status ranglari
        if (column.fieldname === "log_type") {
            if (data.log_type && data.log_type.includes("Normada")) {
                return `<span style="color: green; font-weight: bold;">${value}</span>`;
            }
            if (data.log_type && (data.log_type.includes("qayd etilmagan") || data.log_type.includes("yo'q"))) {
                return `<span style="color: red; font-weight: bold;">${value}</span>`;
            }
            if (data.time && data.time.includes("Ish vaqti")) {
                const match = value.match(/(\d+):(\d+)/);
                if (match) {
                    const hours = parseInt(match[1]);
                    if (hours >= 8) {
                        return `<span style="color: green; font-weight: bold;">${value}</span>`;
                    } else if (hours >= 4) {
                        return `<span style="color: orange; font-weight: bold;">${value}</span>`;
                    } else if (hours > 0) {
                        return `<span style="color: red;">${value}</span>`;
                    }
                }
            }
            if (data.time && data.time.includes("Daromad")) {
                return `<span style="color: green; font-weight: bold; font-size: 13px;">${value}</span>`;
            }
        }
        
        // Log turlari uchun rang
        if (column.fieldname === "log_type" && data.row_num && !isNaN(data.row_num)) {
            if (value.includes("KELDI")) {
                return `<span style="color: #28a745;">${value}</span>`;
            }
            if (value.includes("KETDI") && !value.includes("tanaffus")) {
                return `<span style="color: #dc3545;">${value}</span>`;
            }
            if (value.includes("CHIQDI") && value.includes("tanaffus")) {
                return `<span style="color: #fd7e14;">${value}</span>`;
            }
            if (value.includes("QAYTDI")) {
                return `<span style="color: #6f42c1;">${value}</span>`;
            }
        }
        
        // Vaqt ustuni
        if (column.fieldname === "time" && data.row_num && !isNaN(data.row_num)) {
            return `<span style="font-family: monospace; font-size: 13px;">${value}</span>`;
        }
        
        // Davomiylik
        if (column.fieldname === "duration" && value && value !== "—") {
            return `<span style="color: #6c757d; font-style: italic;">${value}</span>`;
        }
        
        return value;
    },

    onload: function(report) {
        // Bir marta refresh (faqat birinchi yuklashda)
        if (!report._initial_loaded) {
            report._initial_loaded = true;
            setTimeout(function() {
                frappe.query_report.refresh();
            }, 300);
        }
        
        // Avtomatik yuklash tugmalari
        report.page.add_inner_button(__("Bugun"), function() {
            frappe.query_report.set_filter_value("date", frappe.datetime.get_today());
            frappe.query_report.refresh();
        });
        
        report.page.add_inner_button(__("Kecha"), function() {
            const yesterday = frappe.datetime.add_days(frappe.datetime.get_today(), -1);
            frappe.query_report.set_filter_value("date", yesterday);
            frappe.query_report.refresh();
        });
    }
};