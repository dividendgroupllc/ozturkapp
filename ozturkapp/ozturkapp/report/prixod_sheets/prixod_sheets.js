frappe.query_reports["Prixod Sheets"] = {
    "filters": [
        {
            "fieldname": "from_date",
            "label": __("Сана дан"),
            "fieldtype": "Date",
            "default": frappe.datetime.add_months(frappe.datetime.get_today(), -1),
            "reqd": 1
        },
        {
            "fieldname": "to_date",
            "label": __("Сана гача"),
            "fieldtype": "Date",
            "default": frappe.datetime.get_today(),
            "reqd": 1
        },
        {
            "fieldname": "company",
            "label": __("Компания"),
            "fieldtype": "Link",
            "options": "Company",
            "default": frappe.defaults.get_user_default("Company")
        },
        {
            "fieldname": "supplier",
            "label": __("Етказиб берувчи"),
            "fieldtype": "Link",
            "options": "Supplier"
        }
    ],

    "formatter": function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        // "ЖАМИ" qatorini qalin qilib ko'rsatish
        if (data && data.item_name === "ЖАМИ") {
            value = `<b>${value}</b>`;
        }
        return value;
    }
}
