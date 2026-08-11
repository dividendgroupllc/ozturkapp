from frappe.model.document import Document


class URYPriceChangeLog(Document):
    """Narx o'zgarishi audit yozuvi va sparkline uchun tarix manbai.

    Ikki xil yozuv bor:

    * `change`   — narx haqiqatan o'zgarganda. Savolga javob beradi:
                   "nega bugun osh 25 500 bo'lib qoldi?"
    * `snapshot` — kuniga bir marta har tovar uchun. POS'dagi mini grafik
                   (sparkline) shundan quriladi.

    Hajm nazorati muhim: har siklda har tovar yozilsa 465 × 24 = kuniga
    11 000 qator bo'lardi. Shuning uchun faqat shu ikki holat yoziladi va
    `prune_price_logs` eskilarini tozalab turadi.
    """

    pass
