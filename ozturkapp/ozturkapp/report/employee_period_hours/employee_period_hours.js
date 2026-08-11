// Copyright (c) 2026, Ozturkapp
// License: MIT
// Davriy Ish Vaqti Hisoboti

frappe.query_reports["Employee Period Hours"] = {
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
            fieldname: "from_date",
            label: __("Boshlanish"),
            fieldtype: "Date",
            reqd: 1,
            default: frappe.datetime.month_start()
        },
        {
            fieldname: "to_date",
            label: __("Tugash"),
            fieldtype: "Date",
            reqd: 1,
            default: frappe.datetime.get_today()
        }
    ],

    formatter: function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        
        if (!data) return value;
        
        // JAMI va O'rtacha qatorlari
        if (data.is_total) {
            return `<strong style="font-size: 13px;">${value}</strong>`;
        }
        
        // Dam olish kuni (Shanba/Yakshanba)
        if (data.is_weekend && column.fieldname === "day_name") {
            return `<span style="color: #6c757d;">${value}</span>`;
        }
        
        // Holat ranglari
        if (column.fieldname === "status") {
            if (value.includes("Normada")) {
                return `<span style="color: #28a745;">${value}</span>`;
            }
            if (value.includes("Chiqmagan") || value.includes("Kelmagan")) {
                return `<span style="color: #dc3545;">${value}</span>`;
            }
            if (value.includes("Dam olish")) {
                return `<span style="color: #6c757d;">${value}</span>`;
            }
            if (value.includes("Log yo'q")) {
                return `<span style="color: #adb5bd;">${value}</span>`;
            }
        }
        
        // Ishlagan vaqt ranglari
        if (column.fieldname === "worked" && data.worked_minutes !== undefined) {
            const hours = data.worked_minutes / 60;
            if (hours >= 8) {
                return `<span style="color: #28a745; font-weight: bold;">${value}</span>`;
            } else if (hours >= 6) {
                return `<span style="color: #fd7e14;">${value}</span>`;
            } else if (hours > 0 && hours < 6) {
                return `<span style="color: #dc3545;">${value}</span>`;
            }
        }
        
        // Keldi/Ketdi vaqtlari (monospace)
        if ((column.fieldname === "first_in" || column.fieldname === "last_out") && 
            value !== "—" && !data.is_total) {
            return `<span style="font-family: monospace;">${value}</span>`;
        }
        
        // Tanaffus (sariq)
        if (column.fieldname === "breaks" && value !== "—") {
            return `<span style="color: #fd7e14;">${value}</span>`;
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
        
        // Tez filtrlar
        report.page.add_inner_button(__("Bu oy"), function() {
            frappe.query_report.set_filter_value("from_date", frappe.datetime.month_start());
            frappe.query_report.set_filter_value("to_date", frappe.datetime.get_today());
            frappe.query_report.refresh();
        });
        
        report.page.add_inner_button(__("O'tgan oy"), function() {
            const today = frappe.datetime.get_today();
            const firstDayThisMonth = frappe.datetime.month_start();
            const lastDayPrevMonth = frappe.datetime.add_days(firstDayThisMonth, -1);
            const firstDayPrevMonth = frappe.datetime.add_months(firstDayThisMonth, -1);
            
            frappe.query_report.set_filter_value("from_date", firstDayPrevMonth);
            frappe.query_report.set_filter_value("to_date", lastDayPrevMonth);
            frappe.query_report.refresh();
        });
        
        report.page.add_inner_button(__("Bu hafta"), function() {
            const today = frappe.datetime.get_today();
            const dayOfWeek = new Date(today).getDay();
            const diff = dayOfWeek === 0 ? 6 : dayOfWeek - 1;
            const monday = frappe.datetime.add_days(today, -diff);
            
            frappe.query_report.set_filter_value("from_date", monday);
            frappe.query_report.set_filter_value("to_date", today);
            frappe.query_report.refresh();
        });
    }
};