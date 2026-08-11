# Copyright (c) 2026, abdulloh and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt


CATEGORY_MAP = {
    "Покупатели": "customer",
    "Поставщики": "supplier",
    "Расходы": "expense",
    "Дивиденды": "dividend",
    "Сотрудники": "employee",
    "Акционеры": "shareholder",
    "Перемещения": "transfer",
}

CATEGORY_LABELS = {
    "customer": "Покупатели",
    "supplier": "Поставщики",
    "expense": "Расходы",
    "dividend": "Дивиденды",
    "employee": "Сотрудники",
    "shareholder": "Акционеры",
    "transfer": "Перемещения",
    "other": "Прочие",
}


def execute(filters=None):
    columns = get_columns()
    data, expense_summaries = get_data(filters)
    summary_html = get_summary_html(data, expense_summaries)
    return columns, data, summary_html


def get_columns():
    return [
        {"fieldname": "posting_date", "label": _("Сана"), "fieldtype": "Date", "width": 100},
        {"fieldname": "account", "label": _("Касса счёт"), "fieldtype": "Link", "options": "Account", "width": 180},
        {"fieldname": "description", "label": _("Категория"), "fieldtype": "Data", "width": 250},
        {"fieldname": "kirim", "label": _("Кирим"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "chiqim", "label": _("Чиқим"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "remarks", "label": _("Изоҳ"), "fieldtype": "Data", "width": 200},
        {"fieldname": "kassa_doc", "label": _("Документ"), "fieldtype": "Link", "options": "Kassa", "width": 160},
    ]


def get_data(filters):
    cash_accounts = get_cash_accounts(filters)
    if not cash_accounts:
        return [], {}

    opening_balance = get_opening_balance(cash_accounts, filters)
    transactions = get_transactions(cash_accounts, filters)

    pe_vouchers = [r.voucher_no for r in transactions if r.voucher_type == "Payment Entry"]
    je_vouchers = [r.voucher_no for r in transactions if r.voucher_type == "Journal Entry"]
    all_vouchers = pe_vouchers + je_vouchers

    pe_info = get_payment_entry_info_batch(pe_vouchers)
    je_info = get_journal_entry_info_batch(je_vouchers)
    je_remarks = get_journal_entry_remarks_batch(je_vouchers)

    # --- YANGI: Kassa hujjati (nom + izoh) batch olish ---
    kassa_map = get_kassa_map_batch(all_vouchers)

    data = []

    # Filterlar
    filter_party_type = filters.get("party_type")
    filter_party = filters.get("party")
    category_filter_val = filters.get("category")
    filter_category = CATEGORY_MAP.get(category_filter_val)
    # Maxsus dividend filtri: "Divident Akmal"/"Divident Elyor" (CATEGORY_MAP da yo'q)
    # -> faqat o'sha aniq dividend hisobi qatorlari ko'rsatiladi.
    dividend_name_filter = category_filter_val if (category_filter_val and not filter_category) else None

    expense_summaries = {}
    balance = opening_balance
    total_kirim = 0
    total_chiqim = 0

    for row in transactions:
        kirim = flt(row.debit_in_account_currency)
        chiqim = flt(row.credit_in_account_currency)

        info = resolve_transaction_info(row, pe_info, je_info, cash_accounts)

        # Category filter
        if filter_category and info["category"] != filter_category:
            balance += kirim - chiqim
            continue

        # Aniq dividend hisobi filtri (Divident Akmal / Divident Elyor)
        if dividend_name_filter:
            if info["category"] != "dividend" or strip_category_prefix(info["description"]) != dividend_name_filter:
                balance += kirim - chiqim
                continue

        # Party filter
        if filter_party_type and info.get("party_type") != filter_party_type:
            balance += kirim - chiqim
            continue
        if filter_party and info.get("party") != filter_party:
            balance += kirim - chiqim
            continue

        balance += kirim - chiqim
        total_kirim += kirim
        total_chiqim += chiqim

        # Xarajatlarni guruhlash
        if info["category"] == "expense":
            desc = info["description"]
            if desc not in expense_summaries:
                expense_summaries[desc] = {"kirim": 0, "chiqim": 0}
            expense_summaries[desc]["kirim"] += kirim
            expense_summaries[desc]["chiqim"] += chiqim

        data.append({
            "posting_date": row.posting_date,
            "account": row.account,
            "direction": "Кирим" if kirim else "Чиқим",
            "description": strip_category_prefix(info["description"]),
            "category": info["category"],
            "summa": kirim if kirim else chiqim,
            # --- YANGI: kassa izohi birinchi, fallback PE/JE ---
            "remarks": get_remarks(row, pe_info, je_remarks, kassa_map),
            # --- YANGI: Документ ustuni Kassa hujjatini ko'rsatadi ---
            "kassa_doc": (kassa_map.get(row.voucher_no) or {}).get("name"),
            "voucher_type": row.voucher_type,
            "voucher_no": row.voucher_no,
            "kirim": kirim or None,
            "chiqim": chiqim or None,
        })

    final_data = list(data)

    # opening_balance va closing balance ni summary HTML uchun saqlash
    if final_data:
        final_data[0]["_opening_balance"] = opening_balance
        final_data[-1]["_closing_balance"] = balance

    return final_data, expense_summaries


def get_cash_accounts(filters):
    conditions = {}
    if filters.get("mode_of_payment"):
        conditions["parent"] = filters["mode_of_payment"]
    # Company bo'yicha filtr (ko'p companyli — har company kassasini ajratish)
    if filters.get("company"):
        conditions["company"] = filters["company"]

    accounts = frappe.get_all(
        "Mode of Payment Account",
        filters=conditions,
        fields=["default_account"],
        pluck="default_account"
    )

    return list(set(a for a in accounts if a))


def get_opening_balance(cash_accounts, filters):
    placeholders = ", ".join(["%s"] * len(cash_accounts))

    result = frappe.db.sql("""
        SELECT IFNULL(SUM(debit_in_account_currency) - SUM(credit_in_account_currency), 0)
        FROM `tabGL Entry`
        WHERE account IN ({placeholders})
          AND posting_date < %s
          AND is_cancelled = 0
    """.format(placeholders=placeholders),
        tuple(cash_accounts) + (filters["from_date"],)
    )

    return flt(result[0][0]) if result else 0


def get_transactions(cash_accounts, filters):
    placeholders = ", ".join(["%s"] * len(cash_accounts))

    return frappe.db.sql("""
        SELECT
            posting_date, voucher_type, voucher_no,
            party_type, party, against,
            debit_in_account_currency, credit_in_account_currency,
            account
        FROM `tabGL Entry`
        WHERE account IN ({placeholders})
          AND posting_date BETWEEN %s AND %s
          AND is_cancelled = 0
        ORDER BY posting_date, creation
    """.format(placeholders=placeholders),
        tuple(cash_accounts) + (filters["from_date"], filters["to_date"]),
        as_dict=True
    )


def get_payment_entry_info_batch(voucher_nos):
    if not voucher_nos:
        return {}

    entries = frappe.db.sql("""
        SELECT name, party_type, party, payment_type, remarks
        FROM `tabPayment Entry`
        WHERE name IN %s
    """, (voucher_nos,), as_dict=True)

    return {e.name: e for e in entries}


def get_journal_entry_info_batch(voucher_nos):
    if not voucher_nos:
        return {}

    entries = frappe.db.sql("""
        SELECT jea.parent, jea.account, jea.party_type, jea.party,
               acc.root_type, acc.account_type, acc.account_name
        FROM `tabJournal Entry Account` jea
        LEFT JOIN `tabAccount` acc ON acc.name = jea.account
        WHERE jea.parent IN %s
    """, (voucher_nos,), as_dict=True)

    result = {}
    for e in entries:
        result.setdefault(e.parent, []).append(e)
    return result


def get_journal_entry_remarks_batch(voucher_nos):
    if not voucher_nos:
        return {}

    entries = frappe.db.sql("""
        SELECT name, user_remark
        FROM `tabJournal Entry`
        WHERE name IN %s
    """, (voucher_nos,), as_dict=True)

    return {e.name: (e.user_remark or "") for e in entries}


def get_kassa_map_batch(voucher_nos):
    """
    Kassa doctype'ni voucher (PE/JE) bo'yicha topish.
    Kassa ikkita havola maydoniga ega: journal_entry, payment_entry.
    Qaytaradi: {voucher_no: {"name": kassa_nomi, "remark": izoh}}
    """
    if not voucher_nos:
        return {}

    voucher_set = set(voucher_nos)
    entries = frappe.db.sql("""
        SELECT name, journal_entry, payment_entry, primechaniya
        FROM `tabKassa`
        WHERE docstatus = 1
          AND (journal_entry IN %(v)s OR payment_entry IN %(v)s)
    """, {"v": tuple(voucher_nos)}, as_dict=True)

    result = {}
    for e in entries:
        info = {"name": e.name, "remark": e.primechaniya or ""}
        for link in (e.journal_entry, e.payment_entry):
            if link and link in voucher_set:
                result[link] = info
    return result


def get_remarks(row, pe_info, je_remarks, kassa_map=None):
    """
    Izoh olish tartibi:
    1. Kassa.primechaniya (voucher_no bog'langan Kassa)
    2. Fallback: Payment Entry.remarks yoki Journal Entry.user_remark
    """
    voucher = row.voucher_no

    # 1. Kassa dan olish (ustuvor)
    if kassa_map and voucher in kassa_map:
        kassa_remark = kassa_map[voucher].get("remark")
        if kassa_remark:
            return kassa_remark

    # 2. Fallback: Payment Entry
    if row.voucher_type == "Payment Entry" and voucher in pe_info:
        return pe_info[voucher].get("remarks") or ""

    # 3. Fallback: Journal Entry
    if row.voucher_type == "Journal Entry" and voucher in je_remarks:
        return je_remarks[voucher] or ""

    return ""


def strip_category_prefix(desc):
    for prefix in ("Расходы: ", "Дивиденды: "):
        if desc.startswith(prefix):
            return desc[len(prefix):]
    return desc


def resolve_transaction_info(row, pe_info, je_info, cash_accounts):
    # 1. GL Entry'da party bor
    if row.party_type and row.party:
        party_name = get_party_name(row.party_type, row.party)
        display_name = party_name or row.party
        return {
            "description": display_name,
            "category": get_category_from_party_type(row.party_type),
            "party_type": row.party_type,
            "party": row.party,
        }

    # 2. Payment Entry
    if row.voucher_type == "Payment Entry" and row.voucher_no in pe_info:
        pe = pe_info[row.voucher_no]
        if pe.payment_type == "Internal Transfer":
            return {"description": "Перемещение", "category": "transfer", "party_type": None, "party": None}
        if pe.party_type and pe.party:
            party_name = get_party_name(pe.party_type, pe.party)
            display_name = party_name or pe.party
            return {
                "description": display_name,
                "category": get_category_from_party_type(pe.party_type),
                "party_type": pe.party_type,
                "party": pe.party,
            }

    # 3. Journal Entry
    if row.voucher_type == "Journal Entry" and row.voucher_no in je_info:
        for acc in je_info[row.voucher_no]:
            if acc.account in cash_accounts:
                continue
            if acc.party_type and acc.party:
                party_name = get_party_name(acc.party_type, acc.party)
                return {
                    "description": party_name or acc.party,
                    "category": get_category_from_party_type(acc.party_type),
                    "party_type": acc.party_type,
                    "party": acc.party,
                }
            if acc.root_type == "Expense":
                return {"description": f"Расходы: {acc.account_name}", "category": "expense", "party_type": None, "party": None}
            if acc.root_type == "Equity":
                return {"description": f"Дивиденды: {acc.account_name}", "category": "dividend", "party_type": None, "party": None}

    # 4. Against field (fallback)
    if row.against:
        against_account = row.against.split(",")[0].strip() if "," in row.against else row.against

        is_cash = frappe.db.get_value("Mode of Payment Account", {"default_account": against_account}, "parent")
        if is_cash:
            direction = "из" if flt(row.debit_in_account_currency) > 0 else "в"
            return {"description": f"Перемещение {direction} {is_cash}", "category": "transfer", "party_type": None, "party": None}

        acc_info = frappe.db.get_value("Account", against_account, ["account_name", "root_type", "account_type"], as_dict=True)
        if acc_info:
            if acc_info.root_type == "Expense":
                return {"description": f"Расходы: {acc_info.account_name}", "category": "expense", "party_type": None, "party": None}
            if acc_info.root_type == "Equity":
                return {"description": f"Дивиденды: {acc_info.account_name}", "category": "dividend", "party_type": None, "party": None}
            if acc_info.account_type == "Receivable":
                return {"description": acc_info.account_name, "category": "customer", "party_type": "Customer", "party": None}
            if acc_info.account_type == "Payable":
                return {"description": acc_info.account_name, "category": "supplier", "party_type": "Supplier", "party": None}
            return {"description": acc_info.account_name, "category": "other", "party_type": None, "party": None}

    return {"description": row.voucher_no or "", "category": "other", "party_type": None, "party": None}


def get_category_from_party_type(party_type):
    return {
        "Customer": "customer",
        "Supplier": "supplier",
        "Employee": "employee",
        "Shareholder": "shareholder",
    }.get(party_type, "other")


def get_party_name(party_type, party):
    field = {"Customer": "customer_name", "Supplier": "supplier_name", "Employee": "employee_name"}.get(party_type)
    if field:
        return frappe.db.get_value(party_type, party, field)
    return party


def get_summary_html(data, expense_summaries=None):
    if not data:
        return ""

    # Opening va closing balancelarni data dan olish
    opening = 0
    closing_balance = 0
    for row in data:
        if "_opening_balance" in row:
            opening = flt(row["_opening_balance"])
        if "_closing_balance" in row:
            closing_balance = flt(row["_closing_balance"])

    customer_kirim = 0
    customer_chiqim = 0
    supplier_kirim = 0
    supplier_chiqim = 0
    expense_kirim = 0
    expense_chiqim = 0
    dividend_kirim = 0
    dividend_chiqim = 0
    transfer_kirim = 0
    transfer_chiqim = 0
    employee_kirim = 0
    employee_chiqim = 0
    other_kirim = 0
    other_chiqim = 0

    for row in data:
        if row.get("is_total"):
            continue

        category = row.get("category") or "other"
        kirim = flt(row.get("kirim"))
        chiqim = flt(row.get("chiqim"))

        if category == "customer":
            customer_kirim += kirim
            customer_chiqim += chiqim
        elif category == "supplier":
            supplier_kirim += kirim
            supplier_chiqim += chiqim
        elif category == "expense":
            expense_kirim += kirim
            expense_chiqim += chiqim
        elif category == "dividend":
            dividend_kirim += kirim
            dividend_chiqim += chiqim
        elif category == "transfer":
            transfer_kirim += kirim
            transfer_chiqim += chiqim
        elif category == "employee":
            employee_kirim += kirim
            employee_chiqim += chiqim
        else:
            other_kirim += kirim
            other_chiqim += chiqim

    closing = opening + (customer_kirim + supplier_kirim + expense_kirim + dividend_kirim + transfer_kirim + employee_kirim + other_kirim) - (customer_chiqim + supplier_chiqim + expense_chiqim + dividend_chiqim + transfer_chiqim + employee_chiqim + other_chiqim)
    if closing_balance:
        closing = closing_balance

    def fmt(val):
        return f"{flt(val):,.2f}"

    def dash_or_val(val):
        return "—" if flt(val) == 0 else f"<span style='color: inherit;'>{fmt(val)}</span>"

    # Расходы subcategory qatorlarini tayyorlash
    expense_sub_rows = ""
    if expense_summaries:
        for desc, totals in expense_summaries.items():
            display_name = desc.replace("Расходы: ", "") if desc.startswith("Расходы: ") else desc
            sub_kirim = fmt(totals["kirim"]) if totals["kirim"] else "—"
            sub_chiqim = fmt(totals["chiqim"]) if totals["chiqim"] else "—"
            expense_sub_rows += f"""
                <tr class="dds-expense-sub" style="display: none; background-color: #fff8e1;">
                    <td style="padding: 8px 10px 8px 30px; border: 1px solid #ddd; font-style: italic;">{display_name}</td>
                    <td style="padding: 8px 10px; border: 1px solid #ddd; text-align: right; color: #388e3c;">{sub_kirim}</td>
                    <td style="padding: 8px 10px; border: 1px solid #ddd; text-align: right; color: #d32f2f;">{sub_chiqim}</td>
                </tr>"""

    expense_arrow = '<span id="dds-expense-arrow" style="margin-right: 5px; font-size: 10px;">&#9654;</span>' if expense_summaries else ""
    expense_cursor = "cursor: pointer;" if expense_summaries else ""
    expense_onclick = """onclick="(function(){
        var rows = document.querySelectorAll('.dds-expense-sub');
        var arrow = document.getElementById('dds-expense-arrow');
        if (!rows.length) return;
        var visible = rows[0].style.display !== 'none';
        for (var i = 0; i < rows.length; i++) { rows[i].style.display = visible ? 'none' : 'table-row'; }
        arrow.innerHTML = visible ? '&#9654;' : '&#9660;';
    })()" """ if expense_summaries else ""

    html = f"""
    <div style="margin-top: 20px; padding: 15px; background-color: #f9f9f9; border-radius: 5px;">
        <table style="width: 100%; border-collapse: collapse; background: white;">
            <thead>
                <tr style="background-color: #f0f0f0;">
                    <th style="padding: 10px; text-align: left; border: 1px solid #ddd; width: 40%;"></th>
                    <th style="padding: 10px; text-align: right; border: 1px solid #ddd; width: 30%; color: #388e3c; font-weight: bold;">Кирим</th>
                    <th style="padding: 10px; text-align: right; border: 1px solid #ddd; width: 30%; color: #d32f2f; font-weight: bold;">Чиқим</th>
                </tr>
            </thead>
            <tbody>
                <tr style="background-color: #e3f2fd;">
                    <td style="padding: 10px; border: 1px solid #ddd; font-weight: bold;">Начальный остаток</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; font-weight: bold;" colspan="2">{fmt(opening)}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;">Покупатели</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #388e3c;">{fmt(customer_kirim) if customer_kirim else '—'}</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #d32f2f;">{fmt(customer_chiqim) if customer_chiqim else '—'}</td>
                </tr>
                <tr style="background-color: #fafafa;">
                    <td style="padding: 10px; border: 1px solid #ddd;">Поставщики</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #388e3c;">{fmt(supplier_kirim) if supplier_kirim else '—'}</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #d32f2f;">{fmt(supplier_chiqim) if supplier_chiqim else '—'}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;">Дивиденды</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #388e3c;">{fmt(dividend_kirim) if dividend_kirim else '—'}</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #d32f2f;">{fmt(dividend_chiqim) if dividend_chiqim else '—'}</td>
                </tr>
                <tr style="background-color: #fafafa;">
                    <td style="padding: 10px; border: 1px solid #ddd;">Сотрудники</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #388e3c;">{fmt(employee_kirim) if employee_kirim else '—'}</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #d32f2f;">{fmt(employee_chiqim) if employee_chiqim else '—'}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;">Перемещения</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #388e3c;">{fmt(transfer_kirim) if transfer_kirim else '—'}</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #d32f2f;">{fmt(transfer_chiqim) if transfer_chiqim else '—'}</td>
                </tr>
                <tr style="background-color: #fafafa;">
                    <td style="padding: 10px; border: 1px solid #ddd;">Прочие</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #388e3c;">{fmt(other_kirim) if other_kirim else '—'}</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #d32f2f;">{fmt(other_chiqim) if other_chiqim else '—'}</td>
                </tr>
                <tr style="{expense_cursor}" {expense_onclick}>
                    <td style="padding: 10px; border: 1px solid #ddd;">{expense_arrow}Расходы</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #388e3c;">{fmt(expense_kirim) if expense_kirim else '—'}</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #d32f2f;">{fmt(expense_chiqim) if expense_chiqim else '—'}</td>
                </tr>
                {expense_sub_rows}
                <tr style="background-color: #e3f2fd; font-weight: bold;">
                    <td style="padding: 12px; border: 1px solid #ddd; font-weight: bold;">Конечный остаток</td>
                    <td style="padding: 12px; border: 1px solid #ddd; text-align: right; font-weight: bold;" colspan="2">{fmt(closing)}</td>
                </tr>
            </tbody>
        </table>
    </div>
    """

    return html
