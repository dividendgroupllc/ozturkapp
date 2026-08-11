from frappe.model.document import Document


class URYPriceRun(Document):
    """Bitta dinamik narxlash sikli.

    `run_key` unique — bir vaqtda bir necha kassa hisoblashni so'rasa ham
    faqat bittasi bajariladi (qolganlari `DuplicateEntryError` oladi).
    Tashqi lock yoki Redis kerak emas.

    Nomlash `autoname` seriyasi orqali: `URY-PRUN-2026-00001`.
    """

    pass
