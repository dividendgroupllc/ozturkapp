from frappe import _


def get_data():
	"""Kassa formasining pastidagi «Connections» bo'limi.

	PE/JE `custom_kassa` maydoni orqali topiladi (after_migrate:
	`kassa_setup.ensure_kassa_link_fields`) — PE/JE tomonida ham Kassa'ga
	bosiladigan havola bor, eski hujjatlar migrate'da to'ldiriladi.
	"""
	return {
		"fieldname": "custom_kassa",
		"transactions": [
			{
				"label": _("Buxgalteriya hujjatlari"),
				"items": ["Payment Entry", "Journal Entry"],
			},
		],
	}
