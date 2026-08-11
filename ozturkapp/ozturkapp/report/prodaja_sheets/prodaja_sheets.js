frappe.query_reports["Prodaja Sheets"] = {
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
            "fieldname": "customer",
            "label": __("Клиент"),
            "fieldtype": "Link",
            "options": "Customer"
        },
        {
            "fieldname": "item_group",
            "label": __("Тип"),
            "fieldtype": "Link",
            "options": "Item Group"
        }
    ],

    "formatter": function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (data && data.is_total) {
            value = `<b>${value}</b>`;
            return value;
        }
        // Наценка / Маржа ranglash (musbat yashil, manfiy qizil)
        if ((column.fieldname === "markup" || column.fieldname === "margin") && data) {
            const v = data[column.fieldname];
            if (v != null) {
                const color = v < 0 ? "#b71c1c" : "#1b5e20";
                value = `<span style="color:${color}; font-weight:600;">${value}</span>`;
            }
        }
        return value;
    }
}
