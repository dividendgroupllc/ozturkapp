app_name = "ozturkapp"
app_title = "Ozturkapp"
app_publisher = "srohatov1@gmail.com"
app_description = "O\'zTurk Resturant"
app_email = "srohatov1@gmail.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# =============================================================================
# SETUP (migrate'dan keyin)
# =============================================================================
# Har bir sozlama alohida try/except bilan chaqiriladi (bittasi xato bersa ham
# qolganlari ishlaydi) — qarang setup/after_migrate.py
after_migrate = [
	"ozturkapp.ozturkapp.setup.after_migrate.run",
]

# =============================================================================
# DOCUMENT EVENTS
# =============================================================================
doc_events = {
	"POS Invoice": {
		# ── Kassa oynasi uchun realtime signallari (TZ §13) ──────────────
		# URY stol bandligini `frappe.db.set_value` bilan yozadi — u hech
		# qanday hujjat hodisasini ishga tushirmaydi. Shuning uchun ASOSIY
		# signal POS Invoice'ning o'zidan olinadi: ofitsant buyurtma
		# yaratganda `sync_order()` -> `invoice.save()` shu hook'ni uyg'otadi.
		"after_insert": "ozturkapp.ozturkapp.utils.cashier_realtime.on_pos_invoice_change",
		"on_update": "ozturkapp.ozturkapp.utils.cashier_realtime.on_pos_invoice_change",
		"on_cancel": "ozturkapp.ozturkapp.utils.cashier_realtime.on_pos_invoice_change",
		# To'lovdan keyin stol holatini YAKUNIY hal qiladi. `ury` dan keyin
		# ishlaydi va uning shartsiz bo'shatishini to'g'rilaydi (TZ §23).
		"on_submit": "ozturkapp.ozturkapp.overrides.pos_invoice.on_submit",
	},
	"URY Table": {
		# Desk orqali qo'lda tahrirlash (layout, o'rindiqlar soni, ...).
		"on_update": "ozturkapp.ozturkapp.utils.cashier_realtime.on_table_change",
	},
	"URY KOT": {
		# Oshxona ekrani uchun realtime (TZ §12). URY'ning o'z
		# `kotDisplayRealtime()` metodi faqat Mosaic kanaliga yuboradi —
		# KOT yaratish/bekor qilish mantig'iga TEGILMAYDI.
		"on_submit": "ozturkapp.ozturkapp.utils.kitchen_realtime.on_kot_submit",
		"on_cancel": "ozturkapp.ozturkapp.utils.kitchen_realtime.on_kot_cancel",
	},
	# ── Kassa smenasi ──────────────────────────────────────────────
	# Smenani UCH xil mijoz ochadi/yopadi: kassa sahifasi, Desktop POS va
	# ERPNext Desk. Signal manbaning O'ZIGA osiladi — shunda qaysi yo'l
	# bilan o'zgarishidan qat'i nazar ofitsant ilovasi xabardor bo'ladi.
	"POS Opening Entry": {
		# Smena EGASI POS Profile'ga biriktirilgan kassir bo'lishi shart.
		# ERPNext Z-hisobotni `where owner = <opening.user>` bilan yig'adi,
		# ya'ni noto'g'ri egali smena cheklarni hisobotdan tushirib
		# qoldiradi. Batafsil: overrides/pos_opening_entry.py
		"validate": "ozturkapp.ozturkapp.overrides.pos_opening_entry.validate",
		"on_submit": "ozturkapp.ozturkapp.utils.cashier_realtime.on_pos_opening_change",
		"on_cancel": "ozturkapp.ozturkapp.utils.cashier_realtime.on_pos_opening_change",
	},
	"POS Closing Entry": {
		"on_submit": "ozturkapp.ozturkapp.utils.cashier_realtime.on_pos_closing_change",
		"on_cancel": "ozturkapp.ozturkapp.utils.cashier_realtime.on_pos_closing_change",
	},
	# ── Menyu ──────────────────────────────────────────────────────
	# Ofitsant ilovasi menyuni xotirada saqlaydi — o'zgarganda xabar
	# bo'lmasa eski narxni ko'rsatib turaveradi (utils/menu_realtime.py).
	"URY Menu": {
		"on_update": "ozturkapp.ozturkapp.utils.menu_realtime.on_menu_change",
	},
	"BOM": {
		# Taom tannarxi (`Item.valuation_rate`) retseptdan olinadi — POS
		# smenani yopganda stok provodkasi shu maydondan narx oladi.
		# Batafsil: utils/bom_valuation.py
		"on_submit": "ozturkapp.ozturkapp.utils.bom_valuation.on_bom_change",
		"on_update_after_submit": "ozturkapp.ozturkapp.utils.bom_valuation.on_bom_change",
		"on_cancel": "ozturkapp.ozturkapp.utils.bom_valuation.on_bom_cancel",
	},
}

# =============================================================================
# SCHEDULER — dinamik narxlash va tannarx
# =============================================================================
scheduler_events = {
	# Xomashyo narxi o'zgarsa BOM tannarxi eskirib qoladi — hujjat hodisasi
	# ishga tushmaydi, chunki `BOM.update_cost()` `db_update()` bilan yozadi.
	# Shuning uchun kuniga bir marta majburan qayta hisoblanadi.
	"daily": [
		"ozturkapp.ozturkapp.utils.bom_valuation.sync_all",
	],
	"cron": {
		# Har 5 daqiqada tekshiriladi, lekin qaysi filial haqiqatan
		# hisoblanishi `interval_minutes` va sikl kaliti bilan belgilanadi —
		# bu yerdagi qadam faqat aniqlik chegarasi.
		"*/5 * * * *": [
			"ozturkapp.ozturkapp.api.dynamic_pricing.run_due_branches"
		],
		"0 3 * * *": [
			"ozturkapp.ozturkapp.api.dynamic_pricing.prune_price_logs"
		],
	}
}

# =============================================================================
# WHITELISTED METHOD OVERRIDES
# =============================================================================
# ┌───────────────────────────────────────────────────────────────────────────┐
# │ Desktop POS server API'si `ozturkapp/api/desktop_pos.py` da turadi, lekin │
# │ POS mijozi uni `ury.ury_pos.api.<metod>` yo'li bilan chaqiradi.          │
# │                                                                          │
# │ Bu ishlaydi, chunki `frappe/handler.py:67` da override satri             │
# │ `get_attr` (75-qator) dan OLDIN qo'llanadi — ya'ni upstream `ury` da     │
# │ MAVJUD BO'LMAGAN nomni ham yo'naltirish mumkin.                          │
# │                                                                          │
# │ Sabab: `ury` upstream repo (ury-erp/ury), unga push qila olmaymiz.       │
# │ Kod shu app'da tursa — `ury` toza qoladi, yangilanishlari muammosiz      │
# │ tortiladi, POS mijozini qayta yig'ish esa shart emas.                    │
# │                                                                          │
# │ ⚠️  Yangi metod qo'shsangiz — quyidagi ro'yxatga ham qo'shing.            │
# │ ⚠️  Deploy'dan keyin `bench clear-cache` MAJBURIY (hook'lar keshda).      │
# └───────────────────────────────────────────────────────────────────────────┘
DESKTOP_POS_METHODS = [
	# Kassa smenasi
	"checkPosOpening",
	"createPosOpening",
	"getPosClosingData",
	"createPosClosing",
	# Kutilayotgan buyurtmalar
	"getPendingOrders",
	"getPendingOrderCounts",
	"getPendingOrderDetail",
	"cancelPendingOrder",
	# Stol / xona
	"getTables",
	"getRoomsForBranch",
	"freeTable",
	"cleanupOrphanTables",
	# Sozlamalar
	"get_pos_cashiers",
	"get_printer_config",
	"save_pos_quick_items",
	# Menyu tartibi
	"saveMenuCourseOrder",
	"saveMenuItemOrder",
	# Profil — upstream `getPosProfile` ustiga qo'shimcha maydonlar qo'shadi
	"getPosProfile",
	# Dinamik narxlash
	"getPricingVersion",
	"getPricingSnapshot",
	"getPricingSettings",
	"getPriceHistory",
	"getPricingAlerts",
	"verifyCartPrices",
	"recalcPricing",
	"savePricingSettings",
	"setBasePrices",
	"setItemPriceLock",
	"revertPricingToBase",
	"seedPricingDemo",
	"simulatePricing",
]

override_whitelisted_methods = {
	f"ury.ury_pos.api.{_m}": f"ozturkapp.ozturkapp.api.desktop_pos.{_m}"
	for _m in DESKTOP_POS_METHODS
}

# Upstream `sync_order` Desktop POS yuboradigan `ticket_number`,
# `active_cashier`, `active_cashier_role`, `client_ref` kwarg'larini jimgina
# tashlab yuboradi (funksiya signaturasida yo'q). O'ram ularni POS Invoice'ga
# yozadi va `client_ref` bo'yicha dublikat chekni to'sadi.
# Qarang: overrides/ury_order.py
override_whitelisted_methods[
	"ury.ury.doctype.ury_order.ury_order.sync_order"
] = "ozturkapp.ozturkapp.overrides.ury_order.sync_order"

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "ozturkapp",
# 		"logo": "/assets/ozturkapp/logo.png",
# 		"title": "Ozturkapp",
# 		"route": "/ozturkapp",
# 		"has_permission": "ozturkapp.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/ozturkapp/css/ozturkapp.css"
app_include_js = [
	# «Profit and Loss Statement» hisobotiga «Ozturk PDF» tugmasini qo'shadi
	"/assets/ozturkapp/js/pl_pdf_button.js",
	# doctype_js fayllari ishlatadigan Item Group filtri yordamchisi
	"/assets/ozturkapp/js/item_group_filter.js",
]

# include js, css files in header of web template
# web_include_css = "/assets/ozturkapp/css/ozturkapp.css"
# web_include_js = "/assets/ozturkapp/js/ozturkapp.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "ozturkapp/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
	# Menejerlar uchun soddalashtirilgan Employee formasi
	"Employee": "public/js/employee.js",
	# Xarid/sotuv hujjatlarida item ro'yxatini filtrlash
	"Purchase Invoice": "public/js/purchase_invoice_item_filter.js",
	"Purchase Order": "public/js/purchase_order_item_filter.js",
	"Sales Order": "public/js/sales_order_item_filter.js",
}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "ozturkapp/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "ozturkapp.utils.jinja_methods",
# 	"filters": "ozturkapp.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "ozturkapp.install.before_install"
# after_install = "ozturkapp.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "ozturkapp.uninstall.before_uninstall"
# after_uninstall = "ozturkapp.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "ozturkapp.utils.before_app_install"
# after_app_install = "ozturkapp.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "ozturkapp.utils.before_app_uninstall"
# after_app_uninstall = "ozturkapp.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "ozturkapp.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"ozturkapp.tasks.all"
# 	],
# 	"daily": [
# 		"ozturkapp.tasks.daily"
# 	],
# 	"hourly": [
# 		"ozturkapp.tasks.hourly"
# 	],
# 	"weekly": [
# 		"ozturkapp.tasks.weekly"
# 	],
# 	"monthly": [
# 		"ozturkapp.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "ozturkapp.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "ozturkapp.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "ozturkapp.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["ozturkapp.utils.before_request"]
# after_request = ["ozturkapp.utils.after_request"]

# Job Events
# ----------
# before_job = ["ozturkapp.utils.before_job"]
# after_job = ["ozturkapp.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"ozturkapp.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

