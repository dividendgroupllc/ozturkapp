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
                // Xodim tanlangan bo'lsa - tozalash o'zi refresh qiladi
                if (frappe.query_report.get_filter_value("employee")) {
                    frappe.query_report.set_filter_value("employee", "");
                } else {
                    frappe.query_report.refresh();
                }
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
        
        // Barcha xodimlar jadvali - Holat ustuni
        if (column.fieldname === "status" && data.status) {
            const st = String(data.status).toLowerCase();
            if (st.includes("normada")) {
                return `<span style="color: green; font-weight: bold;">${value}</span>`;
            }
            if (st.includes("qayd etilmagan") || st.includes("yo'q")) {
                return `<span style="color: red;">${value}</span>`;
            }
            if (st.includes("ishda")) {
                return `<span style="color: #007bff;">${value}</span>`;
            }
            if (st.includes("dam olish")) {
                return `<span style="color: #6c757d;">${value}</span>`;
            }
        }

        // Status ranglari
        if (column.fieldname === "log_type") {
            const lt = String(data.log_type || "").toLowerCase();
            if (lt.includes("normada")) {
                return `<span style="color: green; font-weight: bold;">${value}</span>`;
            }
            if (lt.includes("qayd etilmagan") || lt.includes("yo'q")) {
                return `<span style="color: red; font-weight: bold;">${value}</span>`;
            }
            if (lt.includes("ishda") || lt.includes("dam olish")) {
                return `<span style="color: #007bff; font-weight: bold;">${value}</span>`;
            }
            if (data.time && data.time.includes("Ish vaqti")) {
                const match = String(value).match(/(\d+):(\d+)/);
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
            value = String(value);
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
        // Filtr qiymati o'zgarsa - o'zi refresh qiladi; o'zgarmasa - qo'lda
        const set_date = function(value) {
            if (frappe.query_report.get_filter_value("date") === value) {
                frappe.query_report.refresh();
            } else {
                frappe.query_report.set_filter_value("date", value);
            }
        };

        // Avtomatik yuklash tugmalari
        report.page.add_inner_button(__("Bugun"), function() {
            set_date(frappe.datetime.get_today());
        });

        report.page.add_inner_button(__("Kecha"), function() {
            set_date(frappe.datetime.add_days(frappe.datetime.get_today(), -1));
        });
    }
};
