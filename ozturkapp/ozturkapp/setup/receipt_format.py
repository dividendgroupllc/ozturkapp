# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Mijoz cheki — o'zbekcha chek formati.

NEGA ALOHIDA FORMAT
===================
ERPNext'ning standart «POS Invoice» formatiga TEGILMAYDI (u boshqa
saytlarda ham ishlatiladi va yangilanishda qayta yoziladi). O'rniga
ozturkapp o'z formatini yaratadi va uni `POS Profile.print_format` ga
biriktiradi.

STANDART FORMATDAN FARQI
========================
    miqdor va narx    bitta ustunda («2 @ 35,000»)  ->  ALOHIDA ustunlarda
    pastki qatorlar   4-5 ta (Total Excl. Tax, ...)  ->  ATIGI 3 ta
    til               inglizcha                      ->  o'zbekcha
    izoh              «Thank you, please visit again» -> «Tashrifingiz uchun rahmat»

Ishga tushirish::

    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.receipt_format.setup
"""

import frappe

#: Chek formati nomi.
FORMAT_NAME = "Ozturk Chek"

#: 80mm chek printeri uchun kenglik.
RECEIPT_WIDTH = "3.15in"


RECEIPT_HTML = """
<style>
	/*
	 * BRAUZER SARLAVHA/KOLONTITULASINI O'CHIRISH
	 * ==========================================
	 * Chop etishda brauzer sahifa chetlariga O'ZI qo'shadi:
	 *     yuqorida  — sana va sahifa nomi
	 *     pastda    — URL va sahifa raqami (1/1)
	 *
	 * Bular chekning qismi EMAS. `@page { margin: 0 }` ularni olib
	 * tashlaydi (Chrome/Edge shu qoidaga amal qiladi). Chek chetlari
	 * o'rniga `.print-format` ning o'z `padding` i ishlatiladi.
	 */
	@page { size: auto; margin: 0mm; }
	@media print {
		html, body { margin: 0 !important; padding: 0 !important; }
		.print-format { padding: 4mm !important; }
	}

	.print-format, .print-format table, .print-format tr,
	.print-format td, .print-format th, .print-format div, .print-format p {
		font-family: Tahoma, "DejaVu Sans", sans-serif;
		line-height: 140%;
		vertical-align: middle;
	}
	@media screen {
		.print-format { width: WIDTH_PLACEHOLDER; padding: 0.18in; min-height: 6in; }
	}
	.oz-head { text-align: center; margin-bottom: 10px; }
	.oz-brand { font-size: 15px; font-weight: bold; letter-spacing: .5px; }
	.oz-sub { font-size: 11px; }

	.oz-meta { font-size: 11px; width: 100%; margin-bottom: 6px; }
	.oz-meta td { padding: 1px 0; }
	.oz-meta td:last-child { text-align: right; font-weight: bold; }

	.oz-rule { border: 0; border-top: 1px dashed #000; margin: 6px 0; }

	.oz-items { width: 100%; font-size: 11px; border-collapse: collapse; }
	.oz-items th {
		border-bottom: 1px solid #000; padding: 3px 0;
		font-size: 10px; text-transform: uppercase; letter-spacing: .3px;
	}
	.oz-items td { padding: 3px 0; vertical-align: top; }
	.oz-items .num { text-align: right; white-space: nowrap; }
	.oz-items .name { word-break: break-word; }
	.oz-note { font-size: 10px; font-style: italic; padding-left: 6px; }

	/* Pastki uchta qator — chekning eng muhim qismi. */
	.oz-totals { width: 100%; font-size: 12px; margin-top: 8px; border-collapse: collapse; }
	.oz-totals td { padding: 3px 0; }
	.oz-totals td:last-child { text-align: right; white-space: nowrap; }
	.oz-totals .oz-grand td {
		border-top: 2px solid #000; padding-top: 6px;
		font-size: 15px; font-weight: bold;
	}

	.oz-foot { text-align: center; font-size: 11px; margin-top: 12px; }
</style>

{% if letter_head %}{{ letter_head }}{% endif %}

<div class="oz-head">
	<div class="oz-brand">{{ doc.company }}</div>
	<div class="oz-sub">Chek</div>
</div>

<table class="oz-meta">
	<tr><td>Chek raqami</td><td>{{ doc.name }}</td></tr>
	<tr><td>Sana</td><td>{{ doc.get_formatted("posting_date") }} {{ doc.get_formatted("posting_time") }}</td></tr>
	{% if doc.restaurant_table %}
	<tr><td>Stol</td><td>{{ doc.restaurant_table }}</td></tr>
	{% endif %}
	{% if doc.waiter %}
	<tr><td>Ofitsant</td><td>{{ frappe.db.get_value("User", doc.waiter, "full_name") or doc.waiter }}</td></tr>
	{% endif %}
	{#- "Mijoz" qatori ATAYLAB yo'q: POS'da u har doim standart texnik
	    mijoz bo'ladi ("... klient") va chekda ma'no bermaydi. -#}
</table>

<table class="oz-items">
	<thead>
		<tr>
			<th style="width:44%; text-align:left">Nomi</th>
			<th style="width:12%; text-align:right">Soni</th>
			<th style="width:22%; text-align:right">Narxi</th>
			<th style="width:22%; text-align:right">Summa</th>
		</tr>
	</thead>
	<tbody>
		{%- for item in doc.items %}
		<tr>
			<td class="name">{{ item.item_name or item.item_code }}</td>
			<td class="num">{{ item.qty | int }}</td>
			<td class="num">{{ format_amount(item.rate) }}</td>
			<td class="num">{{ format_amount(item.amount) }}</td>
		</tr>
		{%- if item.comment %}
		<tr><td class="oz-note" colspan="4">{{ item.comment }}</td></tr>
		{%- endif %}
		{%- endfor %}
	</tbody>
</table>

<hr class="oz-rule">

<!-- ATIGI UCHTA QATOR: jami, xizmat haqi, umumiy jami -->
<table class="oz-totals">
	<tr>
		<td>Jami</td>
		<td>{{ format_amount(doc.net_total) }}</td>
	</tr>
	{%- for row in doc.taxes %}
	<tr>
		<td>{{ row.description }}</td>
		<td>{{ format_amount(row.tax_amount) }}</td>
	</tr>
	{%- endfor %}
	<tr class="oz-grand">
		<td>Umumiy summa</td>
		<td>{{ format_amount(doc.rounded_total or doc.grand_total) }}</td>
	</tr>
</table>

{%- if doc.payments %}
<hr class="oz-rule">
<table class="oz-totals">
	{%- for payment in doc.payments %}
	{%- if payment.amount %}
	<tr><td>{{ payment.mode_of_payment }}</td><td>{{ format_amount(payment.amount) }}</td></tr>
	{%- endif %}
	{%- endfor %}
	{%- if doc.change_amount %}
	<tr><td>Qaytim</td><td>{{ format_amount(doc.change_amount) }}</td></tr>
	{%- endif %}
</table>
{%- endif %}

<div class="oz-foot">Tashrifingiz uchun rahmat</div>

<script>
	/*
	 * Sahifa sarlavhasini almashtiramiz.
	 *
	 * Frappe `<title>` ga hujjat sarlavhasini qo'yadi — POS Invoice uchun
	 * bu `customer_name`, ya'ni «... klient» degan texnik mijoz nomi.
	 * Brauzer chop etishda shu sarlavhani sahifa tepasiga yozadi.
	 *
	 * `@page { margin: 0 }` uni odatda yashiradi, lekin foydalanuvchi
	 * brauzerda «Headers and footers» ni yoqib qo'ysa ham mijoz nomi
	 * emas, chek raqami ko'rinsin.
	 */
	document.title = "{{ doc.name }}";
</script>
""".replace("WIDTH_PLACEHOLDER", RECEIPT_WIDTH)


def setup():
    """Chek formatini yaratadi/yangilaydi va POS Profile'ga biriktiradi."""
    create_format()
    attach_to_pos_profiles()
    frappe.db.commit()


def create_format():
    if frappe.db.exists("Print Format", FORMAT_NAME):
        doc = frappe.get_doc("Print Format", FORMAT_NAME)
        doc.html = RECEIPT_HTML
        doc.save(ignore_permissions=True)
        print(f"✅ Chek formati yangilandi: {FORMAT_NAME}")
        return FORMAT_NAME

    frappe.get_doc(
        {
            "doctype": "Print Format",
            "name": FORMAT_NAME,
            "doc_type": "POS Invoice",
            "module": "Ozturkapp",
            "print_format_type": "Jinja",
            "custom_format": 1,
            "disabled": 0,
            "standard": "No",
            "html": RECEIPT_HTML,
        }
    ).insert(ignore_permissions=True)

    print(f"✅ Chek formati yaratildi: {FORMAT_NAME}")
    return FORMAT_NAME


def attach_to_pos_profiles():
    """Barcha POS Profile'larga biriktiradi (allaqachon boshqasi bo'lsa tegmaydi)."""
    attached = 0
    for name in frappe.get_all("POS Profile", pluck="name"):
        current = frappe.db.get_value("POS Profile", name, "print_format")
        if current and current != FORMAT_NAME:
            print(f"⏭️  '{name}' da boshqa format tanlangan: {current} — tegilmadi")
            continue
        if current == FORMAT_NAME:
            continue
        frappe.db.set_value("POS Profile", name, "print_format", FORMAT_NAME)
        attached += 1

    print(f"✅ POS Profile'ga biriktirildi ({attached} ta)")
    return attached
