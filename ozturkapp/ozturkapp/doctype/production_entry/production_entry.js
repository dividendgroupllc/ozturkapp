// Copyright (c) 2026, Ozturkapp and contributors
// For license information, please see license.txt

frappe.ui.form.on('Production Entry', {
    refresh: function(frm) {
        frm.set_query("item_to_manufacture", function() {
            return {
                filters: {
                    "item_group": "Полуфабрикат"
                }
            };
        });

        frm.set_query("bom_no", function() {
            return {
                filters: {
                    "item": frm.doc.item_to_manufacture,
                    "is_active": 1,
                    "docstatus": 1
                }
            };
        });

        frm.set_query("target_warehouse", function() {
            return {
                filters: {
                    "is_group": 0,
                    "company": frm.doc.company
                }
            };
        });

        if (frm.doc.docstatus === 0) {
            frm.add_custom_button(__('Mavjud miqdorni yangilash'), function() {
                update_all_available_qty(frm);
            });
        }
    },

    item_to_manufacture: function(frm) {
        if (!frm.doc.item_to_manufacture) {
            frm.set_value("bom_no", "");
            frm.clear_table("items");
            frm.refresh_field("items");
            return;
        }
        frappe.call({
            method: "ozturkapp.ozturkapp.doctype.production_entry.production_entry.get_bom_for_item",
            args: { item_code: frm.doc.item_to_manufacture },
            callback: function(r) {
                if (r.message) {
                    frm.set_value("bom_no", r.message);
                } else {
                    frm.set_value("bom_no", "");
                    frappe.msgprint(__("{0} uchun aktiv BOM topilmadi", [frm.doc.item_to_manufacture]));
                }
            }
        });
    },

    bom_no: function(frm) {
        if (frm.doc.bom_no && frm.doc.qty_to_manufacture) {
            fetch_bom_items(frm);
        }
    },

    qty_to_manufacture: function(frm) {
        if (frm.doc.bom_no && flt(frm.doc.qty_to_manufacture) > 0) {
            fetch_bom_items(frm);
        }
    },

    posting_date: function(frm) {
        update_all_available_qty(frm);
    },

    posting_time: function(frm) {
        update_all_available_qty(frm);
    },

    target_warehouse: function(frm) {
        if (!frm.doc.target_warehouse || !frm.doc.items || !frm.doc.items.length) return;
        frm.doc.items.forEach(function(row) {
            frappe.model.set_value(row.doctype, row.name, "source_warehouse", frm.doc.target_warehouse);
        });
        frm.refresh_field("items");
        update_all_available_qty(frm);
    }
});

frappe.ui.form.on('Production Entry Item', {
    item_code: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        if (row.item_code && row.source_warehouse) {
            update_available_qty(frm, row);
        }
    },

    source_warehouse: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        if (row.item_code && row.source_warehouse) {
            update_available_qty(frm, row);
        }
    }
});

function fetch_bom_items(frm) {
    frappe.call({
        method: "ozturkapp.ozturkapp.doctype.production_entry.production_entry.get_bom_items",
        args: {
            bom_no:              frm.doc.bom_no,
            qty_to_manufacture:  frm.doc.qty_to_manufacture,
            posting_date:        frm.doc.posting_date,
            posting_time:        frm.doc.posting_time,
            source_warehouse:    frm.doc.target_warehouse || ""
        },
        callback: function(r) {
            if (!r.message) return;
            frm.clear_table("items");
            r.message.forEach(function(item) {
                let row = frm.add_child("items");
                row.item_code        = item.item_code;
                row.item_name        = item.item_name;
                row.source_warehouse = item.source_warehouse;
                row.required_qty     = item.required_qty;
                row.available_qty    = item.available_qty;
                row.uom              = item.uom;
            });
            frm.refresh_field("items");
        }
    });
}

function update_all_available_qty(frm) {
    if (!frm.doc.items || !frm.doc.items.length) return;
    frm.doc.items.forEach(function(item) {
        if (item.item_code && item.source_warehouse) {
            update_available_qty(frm, item);
        }
    });
}

function update_available_qty(frm, row) {
    frappe.call({
        method: "ozturkapp.ozturkapp.doctype.production_entry.production_entry.get_available_qty_for_item",
        args: {
            item_code:    row.item_code,
            warehouse:    row.source_warehouse,
            posting_date: frm.doc.posting_date,
            posting_time: frm.doc.posting_time
        },
        callback: function(r) {
            if (r.message !== undefined) {
                frappe.model.set_value(row.doctype, row.name, "available_qty", r.message);
            }
        }
    });
}
