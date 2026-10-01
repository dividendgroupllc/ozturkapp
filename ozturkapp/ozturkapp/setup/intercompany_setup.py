# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kompaniyalararo (inter-company) sozlama — har migrate'da, idempotent.

NEGA. Branch Stock Transfer va Kassa'ning kompaniyalararo oqimlari har bir
savdo qiluvchi (guruh bo'lmagan) kompaniyani ifodalovchi ICHKI Customer va
Supplier'ni talab qiladi, ular esa boshqa kompaniyalar kitobida ishlatilishi
uchun «Allowed To Transact With» jadvalida o'sha kompaniyalarga ruxsat
olishi kerak. Avval bular qo'lda yaratilgan edi — yangi filial qo'shilganda
unutilsa, birinchi o'tkazmada xato chiqardi. Endi migrate o'zi to'ldiradi:
  * yetishmagan ichki Customer/Supplier yaratiladi;
  * har biriga qolgan barcha savdo kompaniyalari ruxsat sifatida qo'shiladi.
Mavjud yozuvlar o'chirilmaydi va nomi o'zgartirilmaydi.

Shuningdek `Ozturk Settings` uchun boshlang'ich qiymat (Sklad kompaniyasi)
faqat maydon BO'SH bo'lsa yoziladi — admin o'zgartirgan qiymat saqlanadi.
"""

import frappe

# Boshlang'ich (seed) qiymatlar — faqat sozlama bo'sh bo'lsa va shunday
# kompaniya/ombor mavjud bo'lsa yoziladi. Mantiq bu nomlarni BILMAYDI.
DEFAULT_SKLAD_COMPANY = "O'zturk Sklad"
DEFAULT_SKLAD_WAREHOUSE = "Asosiy sklad - OSK"


def _trading_companies():
    """Guruh bo'lmagan kompaniyalar (guruh «O'zturk» savdo qilmaydi)."""
    return frappe.get_all("Company", filters={"is_group": 0}, pluck="name", order_by="name")


def _leaf(doctype, fallback):
    return frappe.db.get_value(doctype, {"is_group": 0}, "name", order_by="name") or fallback


def _free_name(doctype, name):
    """Shu nomdagi (boshqa kompaniyani ifodalovchi) yozuv bo'lsa — farqlaymiz."""
    return name if not frappe.db.exists(doctype, name) else f"{name} (IC)"


def _ensure_party(doctype, company):
    flag = "is_internal_customer" if doctype == "Customer" else "is_internal_supplier"
    name = frappe.db.get_value(doctype, {flag: 1, "represents_company": company}, "name")
    if name:
        return name, False

    doc = frappe.new_doc(doctype)
    if doctype == "Customer":
        doc.customer_name = _free_name("Customer", company)
        doc.customer_group = _leaf("Customer Group", "All Customer Groups")
        doc.territory = _leaf("Territory", "All Territories")
        doc.customer_type = "Company"
    else:
        doc.supplier_name = _free_name("Supplier", company)
        doc.supplier_group = _leaf("Supplier Group", "All Supplier Groups")
        doc.supplier_type = "Company"
    doc.set(flag, 1)
    doc.represents_company = company
    doc.flags.ignore_mandatory = True
    doc.insert(ignore_permissions=True)
    return doc.name, True


def ensure_intercompany_parties():
    """Har bir savdo kompaniyasi uchun ichki Customer/Supplier + o'zaro ruxsatlar."""
    companies = _trading_companies()
    if len(companies) < 2:
        return

    for company in companies:
        others = [c for c in companies if c != company]
        for doctype in ("Customer", "Supplier"):
            name, created = _ensure_party(doctype, company)
            if created:
                print(f"✅ Ichki {doctype} yaratildi: {name} -> {company}")

            allowed = set(frappe.get_all(
                "Allowed To Transact With",
                filters={"parent": name, "parenttype": doctype},
                pluck="company",
            ))
            missing = [c for c in others if c not in allowed]
            if not missing:
                continue
            doc = frappe.get_doc(doctype, name)
            for c in missing:
                doc.append("companies", {"company": c})
            doc.flags.ignore_mandatory = True
            doc.save(ignore_permissions=True)
            print(f"✅ {doctype} {name}: ruxsat qo'shildi -> {', '.join(missing)}")


def ensure_settings_defaults():
    """`Ozturk Settings.sklad_company` bo'sh bo'lsa — boshlang'ich qiymat."""
    if not frappe.db.exists("DocType", "Ozturk Settings"):
        return
    if frappe.db.get_single_value("Ozturk Settings", "sklad_company"):
        return
    if not frappe.db.exists("Company", DEFAULT_SKLAD_COMPANY):
        return

    values = {"sklad_company": DEFAULT_SKLAD_COMPANY}
    if (
        not frappe.db.get_single_value("Ozturk Settings", "sklad_warehouse")
        and frappe.db.get_value("Warehouse", DEFAULT_SKLAD_WAREHOUSE, "company") == DEFAULT_SKLAD_COMPANY
    ):
        values["sklad_warehouse"] = DEFAULT_SKLAD_WAREHOUSE
    for field, value in values.items():
        frappe.db.set_single_value("Ozturk Settings", field, value)
    print(f"✅ Ozturk Settings: Sklad kompaniyasi = {DEFAULT_SKLAD_COMPANY}")


def run():
    ensure_intercompany_parties()
    ensure_settings_defaults()
