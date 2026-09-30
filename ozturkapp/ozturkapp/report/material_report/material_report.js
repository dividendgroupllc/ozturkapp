// Copyright (c) 2024, Ozturkapp and contributors
// For license information, please see license.txt

frappe.query_reports["Material Report"] = {
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
            fieldname: "warehouse",
            label: __("Ombor"),
            fieldtype: "Link",
            options: "Warehouse",
            get_query: function () {
                const company = frappe.query_report.get_filter_value("company");
                return { filters: company ? { company: company } : {} };
            }
        },
        {
            fieldname: "item_group",
            label: __("Tovar guruhi"),
            fieldtype: "Link",
            options: "Item Group"
        },
        {
            fieldname: "item_code",
            label: __("Tovar"),
            fieldtype: "Link",
            options: "Item"
        }
    ],

    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (data && data.is_total) {
            value = `<b>${value}</b>`;
        } else if (data && column.fieldname === "closing_qty" && data.closing_qty < 0) {
            value = `<span style="color:#b71c1c;">${value}</span>`;
        }
        return value;
    }
};
