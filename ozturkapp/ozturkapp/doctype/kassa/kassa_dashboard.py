from frappe import _


def get_data():
	"""Kassa formasining pastidagi «Connections» bo'limi.

	Kompaniyalararo oqimda bitta Kassa 2-4 ta hujjat (ikki kompaniya kitobida)
	yaratadi — ularning hammasi `custom_kassa` maydoni orqali topiladi
	(after_migrate: `kassa_setup.ensure_kassa_link_fields`).
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
