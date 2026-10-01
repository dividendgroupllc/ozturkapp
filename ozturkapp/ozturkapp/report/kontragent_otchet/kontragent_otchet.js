// Kontragent Otchet Report

frappe.query_reports["Kontragent Otchet"] = {
    filters: [
        {
            fieldname: "from_date",
            label: __("Boshlanish sanasi"),
            fieldtype: "Date",
            default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
            reqd: 1
        },
        {
            fieldname: "to_date",
            label: __("Tugash sanasi"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1
        },
        {
            fieldname: "company",
            label: __("Kompaniya"),
            fieldtype: "Link",
            options: "Company",
            default: frappe.defaults.get_user_default("Company")
        },
        {
            fieldname: "party_type",
            label: __("Kontragent turi"),
            fieldtype: "Select",
            options: "Customer\nSupplier\nEmployee\nShareholder",
            default: "Customer",
            reqd: 1,
            on_change: function() {
                // on_change avtomatik refresh ni almashtiradi — shuning uchun qo'lda chaqiramiz
                if (frappe.query_report.get_filter_value("party")) {
                    frappe.query_report.set_filter_value("party", "");
                } else {
                    frappe.query_report.refresh();
                }
            }
        },
        {
            fieldname: "party",
            label: __("Kontragent"),
            fieldtype: "Dynamic Link",
            get_options: function() {
                var party_type = frappe.query_report.get_filter_value("party_type");
                if (!party_type) {
                    return null;
                }
                return party_type;
            }
        },
        {
            // Ichki kontragentlar — sklad/filial kompaniyalari (Branch Stock Transfer)
            fieldname: "internal",
            label: __("Ichki (filial/sklad)"),
            fieldtype: "Select",
            options: "\nFaqat tashqi\nFaqat ichki"
        }
    ],
    
    formatter: function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        if (data && data.is_internal && column.fieldname === "category") {
            value = `<span style="color:#6a1b9a;font-weight:600;">${value}</span>`;
        }
        
        // JAMI qatori - birinchi qator
        if (data && data.bold) {
            value = `<span style="font-weight:bold;background:#fffacd;padding:3px 8px;display:inline-block;">${value}</span>`;
        }
        
        return value;
    }
};