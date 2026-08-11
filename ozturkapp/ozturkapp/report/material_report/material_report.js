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
            fieldname: "warehouse",
            label: __("Ombor"),
            fieldtype: "Link",
            options: "Warehouse"
        },
        {
            fieldname: "item_code",
            label: __("Tovar"),
            fieldtype: "Link",
            options: "Item"
        }
    ]
};
