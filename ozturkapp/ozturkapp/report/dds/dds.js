frappe.query_reports["DDS"] = {
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
            "default": frappe.defaults.get_user_default("Company"),
            "on_change": function() {
                frappe.query_report.set_filter_value('mode_of_payment', '');
                frappe.query_report.refresh();
            }
        },
        {
            "fieldname": "mode_of_payment",
            "label": __("Способ оплаты"),
            "fieldtype": "Link",
            "options": "Mode of Payment",
            "get_query": function() {
                const company = frappe.query_report.get_filter_value('company');
                if (company) {
                    return {
                        query: "ozturkapp.ozturkapp.doctype.kassa.kassa.get_filtered_mode_of_payments",
                        filters: { company: company }
                    };
                }
            }
        },
        {
            "fieldname": "party_type",
            "label": __("Контрагент тури"),
            "fieldtype": "Select",
            "options": "\nCustomer\nSupplier\nEmployee\nShareholder",
            "on_change": function() {
                frappe.query_report.set_filter_value('party', '');
            }
        },
        {
            "fieldname": "party",
            "label": __("Контрагент"),
            "fieldtype": "Dynamic Link",
            "options": "party_type",
            "get_options": function() {
                return frappe.query_report.get_filter_value('party_type');
            }
        },
        {
            "fieldname": "category",
            "label": __("Категория"),
            "fieldtype": "Select",
            "options": "\nПокупатели\nПоставщики\nРасходы\nДивиденды\nСотрудники\nАкционеры\nПеремещения"
        }
    ],

    "formatter": function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        if (column.fieldtype == "Currency" && value) {
            value = value.replace(/\$/g, '');
        }

        if (column.fieldname == "kirim" && data && data.kirim) {
            value = `<span style="color: #1b5e20; font-weight: 600;">${value}</span>`;
        }
        if (column.fieldname == "chiqim" && data && data.chiqim) {
            value = `<span style="color: #b71c1c; font-weight: 600;">${value}</span>`;
        }

        return value;
    }
}
