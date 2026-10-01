# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa moduli uchun boshlang'ich sozlama.

    bench --site ozturk.local execute ozturkapp.ozturkapp.setup.kassa_setup.run_full_setup
"""

import frappe

# Kassa «Kontragent turi» ro'yxatida chiqadigan qo'shimcha Party Type'lar
PARTY_TYPES = [
    {"party_type": "Расходы", "account_type": "Payable"},
]


def create_party_types():
    """Kassa uchun Party Type'larni yaratadi (idempotent)."""
    for pt in PARTY_TYPES:
        name = pt["party_type"]
        if frappe.db.exists("Party Type", name):
            print(f"⏭️  Party Type allaqachon bor: {name}")
            continue
        doc = frappe.new_doc("Party Type")
        doc.party_type = name
        doc.account_type = pt["account_type"]
        doc.flags.ignore_links = True
        doc.insert(ignore_permissions=True)
        print(f"✅ Party Type yaratildi: {name}")


def create_sample_filials(names=None):
    """Namuna Kassa Filial'lar yaratadi (ixtiyoriy).

    Bitta kompaniyali saytda «filial» = xarajatlarni guruhlash birligi
    (masalan zal, oshxona, ma'muriyat).
    """
    names = names or ["Restoran", "Oshxona", "Administrativ"]
    company = frappe.db.get_value("Company", {}, "name")

    for name in names:
        if frappe.db.exists("Kassa Filial", name):
            print(f"⏭️  Kassa Filial allaqachon bor: {name}")
            continue
        doc = frappe.new_doc("Kassa Filial")
        doc.filial_name = name
        doc.is_active = 1
        doc.company = company
        doc.insert(ignore_permissions=True)
        print(f"✅ Kassa Filial yaratildi: {name}")


def run_full_setup():
    print("=" * 50)
    print("KASSA MODULE SETUP")
    print("=" * 50)

    print("\n1. Party Type'lar...")
    create_party_types()

    print("\n2. Namuna filiallar...")
    create_sample_filials()

    frappe.db.commit()
    print("\n✅ SETUP TAYYOR")


# Kassa yaratadigan hujjatlarga havola maydonlari (Kassa.ACCOUNTING_LINK_FIELDS bilan bir xil)
KASSA_PE_FIELDS = ("payment_entry", "payment_entry_receive", "payment_entry_supplier")


def ensure_kassa_link_fields():
    """Payment Entry va Journal Entry'ga «Kassa» Link maydonini qo'shadi va
    eski hujjatlarni to'ldiradi (idempotent).

    NEGA. Kassa endi bitta emas, bir nechta hujjat yaratadi (kompaniyalararo
    oqimda ikki kompaniya kitobida 2-4 ta PE/JE). `reference_no`/`user_remark`
    oddiy matn — bosib o'tib bo'lmaydi va hisobot (DDS) qaysi kassa ekanini
    ishonchli topa olmaydi. Link maydoni bilan hujjatdan Kassa'ga o'tiladi,
    Kassa'ning «Connections» bo'limida esa barcha yaratilgan hujjatlar
    ko'rinadi. `no_copy` — PE/JE nusxalanganda havola ko'chib qolmasin.
    """
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    field = {
        "fieldname": "custom_kassa",
        "label": "Kassa",
        "fieldtype": "Link",
        "options": "Kassa",
        "read_only": 1,
        "no_copy": 1,
        "print_hide": 1,
        "in_standard_filter": 1,
        "description": "Ushbu hujjatni yaratgan Kassa hujjati",
    }
    for dt, insert_after in (("Payment Entry", "reference_no"),
                             ("Journal Entry", "cheque_no")):
        existed = frappe.db.exists("Custom Field", {"dt": dt, "fieldname": "custom_kassa"})
        create_custom_fields({dt: [dict(field, insert_after=insert_after)]})
        if not existed:
            print(f"✅ {dt}.custom_kassa maydoni yaratildi")

    _backfill_kassa_links()


def _backfill_kassa_links():
    """Eski PE/JE'larga Kassa havolasini yozadi (faqat bo'shlariga).

    Manba — Kassa'ning o'z havola maydonlari. Kassa amend qilinganda eski
    (bekor qilingan) JE kassadan uzilib qoladi — ular uchun zaxira sifatida
    «Kassa: KASSA-... - ...» remark'i o'qiladi.
    """
    kassa_cols = set(frappe.db.get_table_columns("Kassa"))
    pe_fields = [f for f in KASSA_PE_FIELDS if f in kassa_cols]

    for dt, join in (
        ("Payment Entry", "d.name IN ({0})".format(", ".join(f"k.{f}" for f in pe_fields))),
        ("Journal Entry", "d.name = k.journal_entry"),
    ):
        pairs = frappe.db.sql(
            f"""
            SELECT k.name AS kassa, d.name AS doc
            FROM `tabKassa` k
            JOIN `tab{dt}` d ON {join}
            WHERE IFNULL(d.custom_kassa, '') = ''
            """,
            as_dict=True,
        )
        for row in pairs:
            frappe.db.set_value(dt, row.doc, "custom_kassa", row.kassa, update_modified=False)
        if pairs:
            print(f"✅ {len(pairs)} ta eski {dt}'ga Kassa havolasi kiritildi")

    orphans = frappe.db.sql(
        """
        SELECT name, user_remark FROM `tabJournal Entry`
        WHERE IFNULL(custom_kassa, '') = ''
          AND user_remark LIKE %s
        """,
        ("Kassa: KASSA-%",),
        as_dict=True,
    )
    fixed = 0
    for row in orphans:
        kassa_name = row.user_remark[len("Kassa: "):].split(" - ")[0].strip()
        if kassa_name and frappe.db.exists("Kassa", kassa_name):
            frappe.db.set_value("Journal Entry", row.name, "custom_kassa", kassa_name,
                                update_modified=False)
            fixed += 1
    if fixed:
        print(f"✅ {fixed} ta JE'ga remark orqali Kassa havolasi tiklandi")
