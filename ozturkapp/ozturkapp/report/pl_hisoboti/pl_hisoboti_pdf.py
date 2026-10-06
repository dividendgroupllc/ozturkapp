# Copyright (c) 2026, Ozturkapp
# License: MIT

"""PL Hisoboti — PDF eksport (umumiy report/fin_pdf.py dvigateli)."""

import frappe

from ozturkapp.ozturkapp.report import fin_pdf
from ozturkapp.ozturkapp.report.pl_hisoboti.pl_hisoboti import execute


@frappe.whitelist()
def generate_pdf(filters):
	fin_pdf.send(filters, execute, "PL Hisoboti", "Фойда — зарар ҳисоботи", "PL_Hisoboti")
