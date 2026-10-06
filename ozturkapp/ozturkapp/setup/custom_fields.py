# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Ozturkapp ishlashi uchun zarur Custom Field va Property Setter'lar.

Jazira'da bular fixture JSON orqali tashilardi. Bu yerda dasturiy yaratamiz —
yangi saytda ishonchliroq va idempotent (qayta-qayta ishga tushirsa bo'ladi).

    bench --site ozturk.local execute ozturkapp.ozturkapp.setup.custom_fields.run
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

CUSTOM_FIELDS = {
    # Soatbay ish haqi — Employee Daily/Period Hours hisobotlari shunga tayanadi
    "Employee": [
        {
            "fieldname": "hourly_rate",
            "label": "Hourly Rate (Soatlik stavka)",
            "fieldtype": "Currency",
            "options": "salary_currency",
            "insert_after": "designation",
            "translatable": 0,
        }
    ],
    # Kirim/chiqim sababi — ish vaqti hisoblashda TEMP_OUT/RETURN ajratiladi
    "Employee Checkin": [
        {
            "fieldname": "checkin_source",
            "label": "Checkin Source",
            "fieldtype": "Select",
            "options": "\nManual\nImport",
            "default": "Manual",
            "insert_after": "log_type",
            "translatable": 0,
        },
        {
            "fieldname": "checkin_reason",
            "label": "Checkin Reason",
            "fieldtype": "Select",
            "options": "\nIN\nOUT\nTEMP_OUT\nRETURN",
            "insert_after": "checkin_source",
            "translatable": 0,
        },
    ],
    # Production Entry -> Stock Entry bog'lanishi (dashboard link uchun ham)
    "Stock Entry": [
        {
            "fieldname": "custom_production_entry",
            "label": "Production Entry",
            "fieldtype": "Link",
            "options": "Production Entry",
            "insert_after": "stock_entry_type",
            "read_only": 1,
            "no_copy": 1,
            "print_hide": 1,
            "translatable": 0,
        },
        # Buyurtma asosida avtomatik ishlab chiqarilgan yarim tayyor mahsulot
        # qaysi chekka tegishli (utils/auto_manufacture.py)
        {
            "fieldname": "custom_pos_invoice",
            "label": "POS Invoice",
            "fieldtype": "Link",
            "options": "POS Invoice",
            "insert_after": "custom_production_entry",
            "read_only": 1,
            "no_copy": 1,
            "print_hide": 1,
            "search_index": 1,
            "translatable": 0,
        },
    ],
    # Belgilangan yarim tayyor mahsulot buyurtma berilganda o'z BOM'i bo'yicha
    # avtomatik ishlab chiqariladi (utils/auto_manufacture.py)
    "Item": [
        {
            "fieldname": "custom_auto_manufacture",
            "label": "Buyurtmada avto ishlab chiqarish",
            "fieldtype": "Check",
            "default": "0",
            "insert_after": "is_stock_item",
            "description": "Buyurtma berilganda tarkibidagi miqdor default BOM bo'yicha "
            "ishlab chiqariladi, buyurtma bekor qilinsa ishlab chiqarish ham bekor bo'ladi.",
            "translatable": 0,
        }
    ],
    # Kassir ekrani va chekdagi nom. Kassa moduli naqd egalarini ajratish
    # uchun usullar «Нахт Davron», «Kassa Oybek» deb nomlanadi — kassirga
    # esa qisqa «Нахт» ko'rinsin (utils/cashier_billing.pos_label).
    # Kassa yopilganda kassir kiritgan naqd RASXOD (smena davomida g'aladondan
    # chiqarilgan pul). Faqat yozuv — hisob provodkasi yaratilmaydi.
    "POS Closing Entry": [
        {
            "fieldname": "custom_cash_expense",
            "label": "Расход (naqd)",
            "fieldtype": "Currency",
            "insert_after": "total_quantity",
            "read_only": 1,
            "no_copy": 1,
            "translatable": 0,
        }
    ],
    # Qayta tiklangan ESKI sotuv (POS cheklari bekor qilingach kunlik jamlama
    # sifatida kiritilgan, to'lovsiz). Sotuv dashboardi va «Kunlik sotuv» ularni
    # POS cheklariga QO'SHIB ko'rsatadi; POS yopilishidan hosil bo'lgan
    # (konsolidatsiya) Sales Invoice'lar bu belgiga ega emas — ikki marta sanalmaydi.
    "Sales Invoice": [
        {
            "fieldname": "custom_legacy_sale",
            "label": "Eski sotuv (jamlama)",
            "fieldtype": "Check",
            "insert_after": "is_consolidated",
            "default": "0",
            "read_only": 1,
            "no_copy": 1,
            "translatable": 0,
        }
    ],
    "Mode of Payment": [
        {
            "fieldname": "custom_pos_label",
            "label": "POS'dagi nomi",
            "fieldtype": "Data",
            "insert_after": "type",
            "description": "Kassa ekrani va chekda ko'rinadigan nom. Bo'sh bo'lsa — to'lov turi nomi.",
            "translatable": 0,
        }
    ],
}

# (doctype, fieldname, property, value, property_type)
# fieldname None bo'lsa — DocType darajasidagi property
PROPERTY_SETTERS = [
    # Kassa va boshqa Link maydonlarda xodim ISMI asosiy, ID izohda ko'rinadi
    ("Employee", "", "show_title_field_in_link", "1", "Check"),
    # Barcode skanerlash maydonlari restoran oqimida ishlatilmaydi
    ("Sales Invoice", "scan_barcode", "hidden", "1", "Check"),
    ("POS Invoice", "scan_barcode", "hidden", "1", "Check"),
    ("Stock Entry", "scan_barcode", "hidden", "1", "Check"),
    # STIR (tax_id) faktura va chop etishda ko'rinsin
    ("Sales Invoice", "tax_id", "hidden", "0", "Check"),
    ("Sales Invoice", "tax_id", "print_hide", "0", "Check"),
    # Yaxlitlangan jami — ko'rsatiladi
    ("Sales Invoice", "disable_rounded_total", "default", "0", "Text"),
    ("Sales Invoice", "rounded_total", "hidden", "0", "Check"),
    ("Sales Invoice", "rounded_total", "print_hide", "0", "Check"),
    ("Sales Invoice", "base_rounded_total", "hidden", "0", "Check"),
    ("Sales Invoice", "base_rounded_total", "print_hide", "1", "Check"),
    # Summa so'z bilan
    ("Sales Invoice", "in_words", "hidden", "0", "Check"),
    ("Sales Invoice", "in_words", "print_hide", "0", "Check"),
    # Qo'shimcha chegirma hisobi yashiriladi
    ("Sales Invoice", "additional_discount_account", "hidden", "1", "Check"),
    ("Sales Invoice", "additional_discount_account", "mandatory_depends_on", "", "Code"),
    # Sales Invoice — soliq bo'limlari yashiriladi (Jazira bilan bir xil)
    ("Sales Invoice", "section_break_40", "hidden", "1", "Check"),
    ("Sales Invoice", "taxes_section", "hidden", "1", "Check"),
    # Purchase Invoice — keraksiz bo'limlar yashiriladi (Jazira bilan bir xil)
    ("Purchase Invoice", "accounting_details_section", "hidden", "1", "Check"),
    ("Purchase Invoice", "accounting_dimensions_section", "hidden", "1", "Check"),
    ("Purchase Invoice", "advances_section", "hidden", "1", "Check"),
    ("Purchase Invoice", "apply_tds", "hidden", "1", "Check"),
    ("Purchase Invoice", "column_break2", "hidden", "1", "Check"),
    ("Purchase Invoice", "company_billing_address_section", "hidden", "1", "Check"),
    ("Purchase Invoice", "company_shipping_address_section", "hidden", "1", "Check"),
    ("Purchase Invoice", "currency_and_price_list", "hidden", "1", "Check"),
    ("Purchase Invoice", "due_date", "hidden", "1", "Check"),
    ("Purchase Invoice", "is_subcontracted", "hidden", "1", "Check"),
    ("Purchase Invoice", "payment_schedule_section", "hidden", "1", "Check"),
    ("Purchase Invoice", "payments_section", "hidden", "1", "Check"),
    ("Purchase Invoice", "pricing_rule_details", "hidden", "1", "Check"),
    ("Purchase Invoice", "raw_materials_supplied", "hidden", "1", "Check"),
    ("Purchase Invoice", "rejected_warehouse", "hidden", "1", "Check"),
    ("Purchase Invoice", "scan_barcode", "hidden", "1", "Check"),
    ("Purchase Invoice", "sec_tax_breakup", "hidden", "1", "Check"),
    ("Purchase Invoice", "section_addresses", "hidden", "1", "Check"),
    ("Purchase Invoice", "section_break_44", "hidden", "1", "Check"),
    ("Purchase Invoice", "section_break_49", "hidden", "1", "Check"),
    ("Purchase Invoice", "section_break_51", "hidden", "1", "Check"),
    ("Purchase Invoice", "status_section", "hidden", "1", "Check"),
    ("Purchase Invoice", "subscription_section", "hidden", "1", "Check"),
    ("Purchase Invoice", "supplier_invoice_details", "hidden", "1", "Check"),
    ("Purchase Invoice", "tax_withheld_vouchers_section", "hidden", "1", "Check"),
    ("Purchase Invoice", "taxes_section", "hidden", "1", "Check"),
    ("Purchase Invoice", "terms_section_break", "hidden", "1", "Check"),
    ("Purchase Invoice", "totals", "hidden", "1", "Check"),
    ("Purchase Invoice", "write_off", "hidden", "1", "Check"),
]


def create_fields():
    """Custom Field'larni yaratadi (idempotent)."""
    create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
    print(f"✅ Custom Field'lar tayyor ({sum(len(v) for v in CUSTOM_FIELDS.values())} ta)")


def create_property_setters():
    """Property Setter'larni o'rnatadi (idempotent)."""
    for doctype, fieldname, prop, value, prop_type in PROPERTY_SETTERS:
        if not frappe.db.exists("DocType", doctype):
            print(f"⏭️  DocType yo'q, o'tkazildi: {doctype}")
            continue
        make_property_setter(
            doctype, fieldname, prop, value, prop_type, for_doctype=not fieldname
        )
    print(f"✅ Property Setter'lar tayyor ({len(PROPERTY_SETTERS)} ta)")


#: Summa formati: 11 224 341,00 (probel — minglik, vergul — o'nlik).
NUMBER_FORMAT = "# ###,##"


def set_number_format():
    """Barcha hujjat va hisobotlarda summa formatini bir xil qiladi.

    Currency.number_format System Settings'dan USTUN turadi, shuning uchun
    UZS'ning o'zida ham o'rnatiladi.
    """
    frappe.db.set_single_value("System Settings", "number_format", NUMBER_FORMAT)
    # fmt_money formatni System Settings'dan emas, default'lar jadvalidan
    # o'qiydi (System Settings.on_update shuni yozadi).
    frappe.db.set_default("number_format", NUMBER_FORMAT)
    if frappe.db.exists("Currency", "UZS"):
        frappe.db.set_value("Currency", "UZS", "number_format", NUMBER_FORMAT)
    print(f"✅ Summa formati: {NUMBER_FORMAT}")


def run():
    create_fields()
    create_property_setters()
    set_number_format()
