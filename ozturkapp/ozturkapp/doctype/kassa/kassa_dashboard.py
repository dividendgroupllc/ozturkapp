from frappe import _


def get_data():
	"""Kassa formasining pastidagi «Connections» bo'limi.

	Kassa hujjati o'zi Payment Entry / Journal Entry ga link qiladi (reverse emas),
	shuning uchun internal_links orqali joriy hujjatdagi maydon qiymati ko'rsatiladi.
	"""
	return {
		"fieldname": "kassa",
		"internal_links": {
			"Payment Entry": "payment_entry",
			"Journal Entry": "journal_entry",
		},
		"transactions": [
			{
				"label": _("Buxgalteriya hujjatlari"),
				"items": ["Payment Entry", "Journal Entry"],
			},
		],
	}
