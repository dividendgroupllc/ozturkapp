# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""URY «desktop POS» (/urypos) talab qiladigan Custom Field'lar.

Bu maydonlar Desktop POS server kodida ishlatiladi
(`ozturkapp/api/desktop_pos.py`), lekin upstream URY app'da YO'Q —
jazira.local bazasida qo'lda yaratilgan edi. Ularsiz POS yuklanmaydi:

    OperationalError: Unknown column 'custom_quick_items' in 'SELECT'

Shuning uchun ta'riflar shu yerda kodga yozilgan va after_migrate'da
avtomatik yaratiladi (idempotent).

    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.ury_custom_fields.create_fields

MUHIM: `Branch` va `URY Menu Item` dagi dinamik narxlash maydonlari ham shu
yerda — ular ilgari `ury` app'ining fixture/patch'ida edi, lekin `ury` toza
upstream holatiga qaytarilgandan keyin bu fayl ularning YAGONA manbai bo'lib
qoldi. O'chirmang.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

URY_CUSTOM_FIELDS = {
    "Branch": [
        {
            "fieldname": "custom_dynamic_pricing_section",
            "label": "Dynamic Pricing",
            "fieldtype": "Section Break",
            "insert_after": "custom_no_taxes",
            "collapsible": 1,
        },
        {
            "fieldname": "custom_dynamic_pricing",
            "label": "Dynamic Pricing Settings (JSON)",
            "fieldtype": "Long Text",
            "insert_after": "custom_dynamic_pricing_section",
            "description": "Bo'sh bo'lsa standart qiymatlar ishlatiladi. `ozturkapp.ozturkapp.api.dynamic_pricing.DEFAULTS` ga qarang.",
        },
        {
            "fieldname": "custom_pricing_version",
            "label": "Pricing Version",
            "fieldtype": "Int",
            "insert_after": "custom_dynamic_pricing",
            "default": "0",
            "description": "Har qo'llangan siklda oshadi. POS shu raqam o'zgarganini ko'rib snapshot tortadi.",
            "read_only": 1,
        },
        {
            "fieldname": "custom_pricing_last_run",
            "label": "Pricing Last Run",
            "fieldtype": "Datetime",
            "insert_after": "custom_pricing_version",
            "read_only": 1,
        },
        {
            "fieldname": "custom_pricing_next_run",
            "label": "Pricing Next Run",
            "fieldtype": "Datetime",
            "insert_after": "custom_pricing_last_run",
            "read_only": 1,
        },
    ],
    "POS Invoice": [
        {
            "fieldname": "custom_ticket_number",
            "label": "Stiker raqami",
            "fieldtype": "Int",
            "insert_after": "custom_active_cashier",
            "description": "Stiker rejimida mijoz oladigan raqam (free-text). Stol rejimida ishlatilmaydi.",
            "in_standard_filter": 1,
        },
        {
            "fieldname": "custom_active_cashier",
            "label": "Faol Kassir",
            "fieldtype": "Data",
            "insert_after": "cashier",
            "description": "Chekni kim amalga oshirdi (POS kassiri ismi)",
            "read_only": 1,
            "in_list_view": 1,
            "in_standard_filter": 1,
            "allow_on_submit": 1,
        },
        {
            "fieldname": "custom_active_cashier_role",
            "label": "Aktiv foydalanuvchi roli",
            "fieldtype": "Data",
            "insert_after": "custom_client_ref",
            "description": "Zakazni urgan foydalanuvchining roli: Kassir yoki Ofitsant",
            "read_only": 1,
            "no_copy": 1,
            "in_standard_filter": 1,
        },
        {
            "fieldname": "custom_cancelled",
            "label": "Bekor qilingan (Draft)",
            "fieldtype": "Check",
            "insert_after": "custom_active_cashier_role",
            "default": "0",
            "description": "Draft (to'lanmagan) invoice bekor qilinganini bildiradi. KPI hisoboti uchun cashier saqlanib qoladi.",
            "read_only": 1,
            "no_copy": 1,
            "print_hide": 1,
            "in_standard_filter": 1,
        },
        {
            "fieldname": "custom_cancel_requested",
            "label": "Bekor So'raldi",
            "fieldtype": "Check",
            "insert_after": "cancel_reason",
            "default": "0",
            "description": "Kassir bekor so'rovi yubordi — manager tasdiqlashi kutilmoqda",
            "read_only": 1,
            "no_copy": 1,
            "print_hide": 1,
            "allow_on_submit": 1,
            "in_list_view": 1,
            "in_standard_filter": 1,
        },
        {
            "fieldname": "custom_cancel_by",
            "label": "Bekor So'ragan Foydalanuvchi",
            "fieldtype": "Data",
            "insert_after": "custom_cancel_requested",
            "depends_on": "eval:doc.custom_cancel_requested",
            "read_only": 1,
            "no_copy": 1,
            "print_hide": 1,
            "allow_on_submit": 1,
        },
        {
            # BEKOR QILINGAN CHEK STOLNI USHLAB TURMASLIGI KERAK
            # ==================================================
            # `custom_cancelled = 1` chek `docstatus = 0` bo'lib qoladi va
            # URY uning `restaurant_table` ini ko'rib turadi:
            #
            #     ury_order.get_order_invoice():
            #         filters    {docstatus: 0, invoice_printed: 0}
            #         or_filters {restaurant_table: <stol>, ...}
            #
            # Bu so'rov `custom_cancelled` ni BILMAYDI, shuning uchun bekor
            # qilingan chek "stoldagi faol buyurtma" bo'lib topiladi va
            # keyingi zakazda «Table-1 is already occupied» xatosi chiqadi
            # (`ury_order.py:840`).
            #
            # Yechim: bekor qilinganda stol bog'lami UZILADI, lekin qaysi
            # stol bo'lgani AUDIT uchun shu yerda saqlanadi.
            "fieldname": "custom_cancelled_table",
            "label": "Bekor qilingan stol",
            "fieldtype": "Data",
            "insert_after": "custom_cancelled",
            "description": (
                "Chek bekor qilinganda qaysi stolga tegishli bo'lgani. "
                "Stol bog'lami uziladi — aks holda u yangi buyurtmani to'sadi."
            ),
            "read_only": 1,
            "no_copy": 1,
            "print_hide": 1,
        },
        {
            "fieldname": "custom_cancel_requested_time",
            "label": "Bekor So'ralgan Vaqt",
            "fieldtype": "Datetime",
            "insert_after": "custom_cancel_by",
            "depends_on": "eval:doc.custom_cancel_requested",
            "read_only": 1,
            "no_copy": 1,
            "print_hide": 1,
            "allow_on_submit": 1,
        },
        {
            # Idempotency kaliti — `overrides/ury_order.sync_order` shu maydon
            # bo'yicha dublikat chekni to'sadi. `unique=1` — poyga (race) holatida
            # ikkinchi INSERT bazada yiqiladi, wrapper uni ushlab mavjudini qaytaradi.
            "fieldname": "custom_client_ref",
            "label": "Client Ref (UUID)",
            "fieldtype": "Data",
            "insert_after": "custom_active_cashier",
            "description": "Idempotency key — Desktop POS UUID. Server bir xil UUID bilan keladigan sync_order so'rovlarini bitta POS Invoice ga ulaydi (duplikat oldini olish).",
            "read_only": 1,
            "no_copy": 1,
            "print_hide": 1,
            "hidden": 1,
            "unique": 1,
        },
    ],
    "POS Profile": [
        # ── Desktop POS bo'limi (layout) ────────────────────────────────────
        # Bu uchtasi qolgan `custom_*` maydonlarning `insert_after` langari —
        # ularsiz maydonlar forma oxiriga tartibsiz tarqab ketadi.
        {
            "fieldname": "custom_desktop_pos_section",
            "label": "URY Desktop POS Sozlamalari",
            "fieldtype": "Section Break",
            "insert_after": "custom_edit_order_type",
            "collapsible": 1,
        },
        {
            "fieldname": "custom_desktop_pos_col1",
            "fieldtype": "Column Break",
            "insert_after": "custom_show_shifts",
        },
        {
            "fieldname": "custom_desktop_pos_col2",
            "fieldtype": "Column Break",
            "insert_after": "custom_order_number_type",
        },
        # ── Mijoz cheki printeri ────────────────────────────────────────────
        # `ozturkapp.ozturkapp.api.desktop_pos.get_printer_config` aynan shu 4 maydonni
        # o'qiydi. `customer_qz_printer_name` bo'sh bo'lsa `print_enabled=False`
        # qaytadi — ya'ni POS mijoz chekini umuman chiqarmaydi.
        {
            "fieldname": "customer_qz_printer_name",
            "label": "Mijoz printer nomi",
            "fieldtype": "Data",
            "insert_after": "qz_print",
            "description": "Mijoz cheki uchun printer device nomi (masalan: XP-365B). Bo'sh bo'lsa chek chop etilmaydi.",
        },
        {
            "fieldname": "customer_qz_printer_driver",
            "label": "Mijoz printer protokoli",
            "fieldtype": "Select",
            "options": "ESC/POS\nTSPL",
            "insert_after": "customer_qz_printer_name",
            "default": "ESC/POS",
            "description": "ESC/POS — oddiy chek printerlari (XP-58 IIH va h.k.). TSPL — stiker/label printerlari (XP-365B va h.k.).",
        },
        {
            "fieldname": "customer_qz_printer_width",
            "label": "Mijoz printer kengligi (mm)",
            "fieldtype": "Int",
            "insert_after": "customer_qz_printer_driver",
            "default": "80",
            "description": "Qog'oz kengligi millimetrlarda (58, 80 va h.k.).",
        },
        {
            "fieldname": "customer_qz_printer_codepage",
            "label": "Mijoz printer kirill kodirovkasi",
            "fieldtype": "Select",
            "options": "CP1251\nCP866",
            "insert_after": "customer_qz_printer_width",
            "default": "CP1251",
            "depends_on": "eval:doc.customer_qz_printer_driver==='ESC/POS'",
            "description": "Kirill harflar xitoy belgilarga aylansa — boshqasini sinab ko'ring. CP1251 — zamonaviy printerlar, CP866 — DOS uslubi (eski printerlar).",
        },
        {
            "fieldname": "custom_company_brand_name",
            "label": "POS Brand Name",
            "fieldtype": "Data",
            "insert_after": "custom_item_columns",
            "description": "Desktop POS sarlavhasida ko'rsatiladigan brend nomi (masalan: JAZIRA, SMART, SHOXIDA). Bo'sh bo'lsa Company nomi ishlatiladi.",
            "allow_on_submit": 1,
        },
        {
            "fieldname": "custom_receipt_footer",
            "label": "Chek pastki matni",
            "fieldtype": "Small Text",
            "insert_after": "custom_company_brand_name",
            "description": "Har bir chekda eng pastda chiqadigan matn",
            "allow_on_submit": 1,
        },
        {
            "fieldname": "custom_order_number_type",
            "label": "Buyurtma raqami turi",
            "fieldtype": "Select",
            "options": "Stiker\nStol",
            "insert_after": "custom_order_type_delivery_saboy",
            "default": "Stiker",
            "description": "Stiker = mijozga beriladigan raqam (free-text). Stol = URY Table doctypedan tanlanadi, occupied avto belgilanadi. Stol rejimida faqat Shu yerda buyurtmasiga stol talab qilinadi.",
            "allow_on_submit": 1,
        },
        {
            "fieldname": "custom_item_columns",
            "label": "Mahsulot ustunlar soni (0=avto, 2-6)",
            "fieldtype": "Int",
            "insert_after": "custom_desktop_pos_col2",
            "default": "0",
            "description": "0 qolsa ekran kengligiga qarab avtomatik hisoblanadi",
            "allow_on_submit": 1,
        },
        {
            "fieldname": "custom_quick_slots_count",
            "label": "Tezkor sotuv slotlari soni",
            "fieldtype": "Int",
            "insert_after": "custom_company_brand_name",
            "default": "3",
            "description": "Desktop POS yuqori qatoridagi tezkor itemlar uchun slot soni (3 yoki 4). Maks 4 ga cheklangan — chunki cart panel kichik bo'lib qoladi.",
            "allow_on_submit": 1,
        },
        {
            "fieldname": "custom_quick_items",
            "label": "Tezkor sotuv tovarlari (JSON)",
            "fieldtype": "Small Text",
            "insert_after": "custom_quick_slots_count",
            "description": "Desktop POS tezkor tugmalari — item kodlari JSON ro'yxati",
        },
        {
            "fieldname": "custom_show_comment",
            "label": "Izoh maydonini ko'rsatish",
            "fieldtype": "Check",
            "insert_after": "custom_desktop_pos_section",
            "default": "1",
            "description": "Buyurtma oynasida 'Izoh' input maydonini ko'rsatish",
            "allow_on_submit": 1,
        },
        {
            "fieldname": "custom_show_customer",
            "label": "Mijoz tanlashni ko'rsatish",
            "fieldtype": "Check",
            "insert_after": "custom_show_ticket",
            "default": "1",
            "allow_on_submit": 1,
        },
        {
            "fieldname": "custom_show_history",
            "label": "Tranzaksiya tarixini ko'rsatish",
            "fieldtype": "Check",
            "insert_after": "custom_show_customer",
            "default": "1",
            "allow_on_submit": 1,
        },
        {
            "fieldname": "custom_show_shifts",
            "label": "Kassa tarixi panelini ko'rsatish",
            "fieldtype": "Check",
            "insert_after": "custom_show_history",
            "default": "1",
            "allow_on_submit": 1,
        },
        {
            "fieldname": "custom_show_ticket",
            "label": "Stiker/Ticket raqamini ko'rsatish",
            "fieldtype": "Check",
            "insert_after": "custom_show_comment",
            "default": "1",
            "allow_on_submit": 1,
        },
        {
            "fieldname": "custom_order_type_dine_in",
            "label": "Buyurtma turi: Shu yerda",
            "fieldtype": "Check",
            "insert_after": "custom_desktop_pos_col1",
            "default": "1",
            "allow_on_submit": 1,
        },
        {
            "fieldname": "custom_order_type_take_away",
            "label": "Buyurtma turi: Saboy",
            "fieldtype": "Check",
            "insert_after": "custom_order_type_dine_in",
            "default": "1",
            "allow_on_submit": 1,
        },
        {
            "fieldname": "custom_order_type_delivery",
            "label": "Buyurtma turi: Dastavka",
            "fieldtype": "Check",
            "insert_after": "custom_order_type_take_away",
            "default": "0",
            "allow_on_submit": 1,
        },
        {
            "fieldname": "custom_order_type_delivery_saboy",
            "label": "Buyurtma turi: Dastavka Saboy",
            "fieldtype": "Check",
            "insert_after": "custom_order_type_delivery",
            "default": "0",
            "allow_on_submit": 1,
        },
    ],
    "POS Profile User": [
        {
            "fieldname": "custom_pin",
            "label": "Desktop POS PIN",
            "fieldtype": "Data",
            "insert_after": "custom_main_cashier",
            "description": "Desktop POS kirish uchun 4 xonali PIN",
        },
    ],
    "URY Menu Item": [
        {
            "fieldname": "custom_pricing_section",
            "label": "Dynamic Pricing",
            "fieldtype": "Section Break",
            "insert_after": "course_icon",
            "collapsible": 1,
        },
        {
            "fieldname": "custom_base_rate",
            "label": "Base Rate",
            "fieldtype": "Currency",
            "insert_after": "custom_pricing_section",
            "description": "Bazaviy narx — koridor shundan hisoblanadi. Engine buni o'zgartirmaydi.",
            "no_copy": 1,
        },
        {
            "fieldname": "custom_cost_rate",
            "label": "Cost Rate",
            "fieldtype": "Currency",
            "insert_after": "custom_pricing_column",
            "description": "Tannarx. 0 = noma'lum, bunda tannarx tekshiruvi qo'llanilmaydi.",
            "read_only": 1,
            "no_copy": 1,
        },
        {
            "fieldname": "custom_cost_source",
            "label": "Cost Source",
            "fieldtype": "Data",
            "insert_after": "custom_cost_rate",
            "read_only": 1,
            "no_copy": 1,
        },
        {
            "fieldname": "custom_cost_updated",
            "label": "Cost Updated",
            "fieldtype": "Datetime",
            "insert_after": "custom_cost_source",
            "read_only": 1,
            "no_copy": 1,
        },
        {
            "fieldname": "custom_demand_score",
            "label": "Demand Score",
            "fieldtype": "Float",
            "insert_after": "custom_cost_updated",
            "description": "log2 shkalasi: +1 = tezlik ikki barobar oshdi, -1 = ikki barobar kamaydi.",
            "read_only": 1,
            "no_copy": 1,
            "precision": "4",
        },
        {
            "fieldname": "custom_last_engine_rate",
            "label": "Last Engine Rate",
            "fieldtype": "Currency",
            "insert_after": "custom_shadow_rate",
            "description": "Engine yozgan oxirgi narx. `rate` bundan farq qilsa — admin qo'lda tahrirlagan.",
            "no_copy": 1,
        },
        {
            "fieldname": "custom_price_locked",
            "label": "Price Locked",
            "fieldtype": "Check",
            "insert_after": "custom_last_engine_rate",
            "default": "0",
            "description": "Belgilansa, engine bu tovar narxiga tegmaydi.",
        },
        {
            "fieldname": "custom_price_updated_at",
            "label": "Price Updated At",
            "fieldtype": "Datetime",
            "insert_after": "custom_pricing_status",
            "description": "Sotuv narxi oxirgi marta o'zgargan vaqt — turish vaqti (dwell) shundan hisoblanadi.",
            "read_only": 1,
            "no_copy": 1,
        },
        {
            "fieldname": "custom_pricing_column",
            "fieldtype": "Column Break",
            "insert_after": "custom_price_locked",
        },
        {
            "fieldname": "custom_pricing_status",
            "label": "Pricing Status",
            "fieldtype": "Select",
            "options": "\nauto\nlocked\nnew\nno_data\nno_base\ncost_violation\nexcluded_low_price\nexcluded",
            "insert_after": "custom_trend",
            "read_only": 1,
            "no_copy": 1,
        },
        {
            "fieldname": "custom_shadow_rate",
            "label": "Shadow Rate",
            "fieldtype": "Float",
            "insert_after": "custom_base_rate",
            "description": "Yaxlitlanmagan ichki narx. Qadam shunga qo'llanadi, sotuv narxi esa uning yaxlitlangan qiymati.",
            "no_copy": 1,
            "precision": "6",
        },
        {
            "fieldname": "custom_shadow_updated_at",
            "label": "Shadow Updated At",
            "fieldtype": "Datetime",
            "insert_after": "custom_price_updated_at",
            "read_only": 1,
            "no_copy": 1,
        },
        {
            "fieldname": "custom_trend",
            "label": "Trend",
            "fieldtype": "Select",
            "options": "\nup\ndown\nflat",
            "insert_after": "custom_demand_score",
            "read_only": 1,
            "no_copy": 1,
        },
    ],
}


def create_fields():
    """URY POS custom fieldlarini yaratadi (idempotent)."""
    create_custom_fields(URY_CUSTOM_FIELDS, ignore_validate=True)
    total = sum(len(v) for v in URY_CUSTOM_FIELDS.values())
    print(f"✅ URY POS custom fieldlari tayyor ({total} ta)")
