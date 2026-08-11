# Copyright (c) 2026, Ozturkapp and contributors
# For license information, please see license.txt

"""
Expense Analysis Report
=======================
P&L'dagi barcha xarajatlarni Payment Entry va Journal Entry orqali
tahlil qilish uchun report. Joriy davr va oldingi davr solishtirish,
oshgan/tushgan xarajatlarni aniqlash, har bir tranzaksiyani drill-down qilish.

Manba: GL Entry (account.root_type = 'Expense', debit > 0)
       voucher_type IN ('Payment Entry', 'Journal Entry')
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, add_days, date_diff, formatdate, today, add_months, nowdate


# Qaysi voucher type'lar hisoblanadi
ALLOWED_VOUCHER_TYPES = ("Payment Entry", "Journal Entry")

# P&L account turlari (Income va Expense)
PNL_ROOT_TYPES = ("Income", "Expense")

# Anomaliya chegarasi (foizda)
ANOMALY_THRESHOLD = 20.0

# Xarajat kategoriyalari (DDS uslubida moslashtirilgan)
CATEGORY_FILTERS = {
    "Поставщики": {"party_type": "Supplier"},
    "Сотрудники": {"party_type": "Employee"},
    "Покупатели": {"party_type": "Customer"},
    "Прочие": {"no_party": True},
}


def execute(filters=None):
    filters = filters or {}
    filters = apply_default_filters(filters)

    columns = get_columns()
    data = get_data(filters)
    summary_html = get_summary_html(filters)

    return columns, data, summary_html


# ============================================================
# DEFAULT FILTERLAR
# ============================================================

def apply_default_filters(filters):
    """
    Agar filterlar bo'sh bo'lsa default qiymatlar o'rnatadi:
    - to_date: bugun
    - from_date: bir oy oldin
    """
    if not filters.get("to_date"):
        filters["to_date"] = today()

    if not filters.get("from_date"):
        filters["from_date"] = add_months(filters["to_date"], -1)

    # Sanalar noto'g'ri tartibda bo'lsa - almashtirish
    if getdate(filters["from_date"]) > getdate(filters["to_date"]):
        filters["from_date"], filters["to_date"] = filters["to_date"], filters["from_date"]

    return filters


def get_prior_period(filters):
    """Joriy davrning davomiyligiga teng oldingi davrni hisoblaydi."""
    from_date = getdate(filters["from_date"])
    to_date = getdate(filters["to_date"])
    period_days = date_diff(to_date, from_date) + 1

    prior_to = add_days(from_date, -1)
    prior_from = add_days(prior_to, -(period_days - 1))

    return prior_from, prior_to


# ============================================================
# USTUNLAR
# ============================================================

def get_columns():
    return [
        {
            "fieldname": "posting_date",
            "label": _("Сана"),
            "fieldtype": "Date",
            "width": 100,
        },
        {
            "fieldname": "account",
            "label": _("Харажат счёти"),
            "fieldtype": "Link",
            "options": "Account",
            "width": 240,
        },
        {
            "fieldname": "amount",
            "label": _("Сумма"),
            "fieldtype": "Currency",
            "width": 130,
        },
        {
            "fieldname": "remarks",
            "label": _("Изоҳ"),
            "fieldtype": "Data",
            "width": 280,
        },
        {
            "fieldname": "voucher_type",
            "label": _("Ҳужжат тури"),
            "fieldtype": "Data",
            "width": 130,
        },
        {
            "fieldname": "voucher_no",
            "label": _("Ҳужжат"),
            "fieldtype": "Dynamic Link",
            "options": "voucher_type",
            "width": 170,
        },
    ]


# ============================================================
# FLAT LIST (asosiy data)
# ============================================================

def get_data(filters):
    """Har bir tranzaksiya alohida qator. Flat list.

    Income account: amount = credit - debit (musbat = daromad)
    Expense account: amount = debit - credit (musbat = xarajat)
    Manfiy son = teskari tranzaksiya (refund, qaytarilish).
    """
    rows = get_gl_entries(filters, filters["from_date"], filters["to_date"])
    if not rows:
        return []

    pe_remarks = get_payment_entry_remarks([r.voucher_no for r in rows if r.voucher_type == "Payment Entry"])
    je_remarks = get_journal_entry_remarks([r.voucher_no for r in rows if r.voucher_type == "Journal Entry"])
    kassa_remarks = get_kassa_remarks([r.voucher_no for r in rows])
    party_names = get_party_names_batch(rows)

    data = []
    for r in rows:
        remarks = ""
        if r.voucher_type == "Payment Entry":
            remarks = pe_remarks.get(r.voucher_no, "") or (r.remarks or "")
        elif r.voucher_type == "Journal Entry":
            remarks = je_remarks.get(r.voucher_no, "") or (r.remarks or "")

        # Kassa orqali yaratilgan PE/JE'da odatda faqat shablon matn bo'ladi
        # ("Kassa: KASSA-2026-00123 - Расход") — foydalanuvchi yozgan haqiqiy
        # izoh Kassa hujjatining o'zida (primechaniya) turadi. Bor bo'lsa, shuni
        # ustuvor qilib ko'rsatamiz.
        if r.voucher_no in kassa_remarks:
            remarks = kassa_remarks[r.voucher_no]

        party_display = ""
        if r.party_type and r.party:
            party_display = party_names.get((r.party_type, r.party)) or r.party

        # Account turi bo'yicha natural summa
        amount = calculate_natural_amount(r.debit, r.credit, r.root_type)

        data.append({
            "posting_date": r.posting_date,
            "account": r.account,
            "root_type": r.root_type,
            "party_type": r.party_type or "",
            "party_name": party_display,
            "amount": amount,
            "remarks": clean_remarks(remarks),
            "voucher_type": r.voucher_type,
            "voucher_no": r.voucher_no,
        })

    return data


def clean_remarks(text):
    if not text:
        return ""
    text = text.replace("\n", " ").replace("\r", " ").strip()
    return text[:500]


# ============================================================
# GL ENTRY OLISH
# ============================================================

def get_gl_entries(filters, from_date, to_date):
    """
    P&L (Income va Expense) account'lariga tegishli GL Entry qatorlarini oladi.
    Faqat Payment Entry va Journal Entry.

    Income account: natural balance = credit - debit (musbat = daromad)
    Expense account: natural balance = debit - credit (musbat = xarajat)
    """
    conditions = ["gle.is_cancelled = 0"]
    params = {
        "from_date": from_date,
        "to_date": to_date,
        "voucher_types": ALLOWED_VOUCHER_TYPES,
        "root_types": PNL_ROOT_TYPES,
    }

    if filters.get("expense_account"):
        conditions.append("gle.account = %(expense_account)s")
        params["expense_account"] = filters["expense_account"]

    if filters.get("party_type"):
        conditions.append("gle.party_type = %(party_type)s")
        params["party_type"] = filters["party_type"]

    if filters.get("party"):
        conditions.append("gle.party = %(party)s")
        params["party"] = filters["party"]

    if filters.get("cost_center"):
        conditions.append("gle.cost_center = %(cost_center)s")
        params["cost_center"] = filters["cost_center"]

    # root_type filter (Income / Expense / All)
    root_type_filter = filters.get("root_type")
    if root_type_filter and root_type_filter in PNL_ROOT_TYPES:
        conditions.append("acc.root_type = %(specific_root_type)s")
        params["specific_root_type"] = root_type_filter

    # Kategoriya filter (DDS uslubi)
    category = filters.get("category")
    if category and category in CATEGORY_FILTERS:
        cat_cfg = CATEGORY_FILTERS[category]
        if cat_cfg.get("no_party"):
            conditions.append("(gle.party_type IS NULL OR gle.party_type = '')")
        elif cat_cfg.get("party_type"):
            conditions.append("gle.party_type = %(category_party_type)s")
            params["category_party_type"] = cat_cfg["party_type"]

    where_clause = " AND ".join(conditions)

    return frappe.db.sql(f"""
        SELECT
            gle.posting_date,
            gle.account,
            gle.party_type,
            gle.party,
            gle.debit,
            gle.credit,
            gle.voucher_type,
            gle.voucher_no,
            gle.remarks,
            gle.cost_center,
            acc.account_name,
            acc.root_type
        FROM `tabGL Entry` gle
        INNER JOIN `tabAccount` acc ON acc.name = gle.account
        WHERE {where_clause}
          AND gle.posting_date BETWEEN %(from_date)s AND %(to_date)s
          AND gle.voucher_type IN %(voucher_types)s
          AND acc.root_type IN %(root_types)s
          AND (gle.debit > 0 OR gle.credit > 0)
        ORDER BY acc.root_type, gle.posting_date, gle.account, gle.creation
    """, params, as_dict=True)


def calculate_natural_amount(debit, credit, root_type):
    """
    Account turi bo'yicha musbat natural summa.
    Income: credit - debit (daromad credit balance)
    Expense: debit - credit (xarajat debit balance)
    """
    debit = flt(debit)
    credit = flt(credit)
    if root_type == "Income":
        return credit - debit
    return debit - credit


# ============================================================
# IZOH BATCH FETCH
# ============================================================

def get_payment_entry_remarks(voucher_nos):
    if not voucher_nos:
        return {}
    voucher_nos = list(set(voucher_nos))
    entries = frappe.db.sql("""
        SELECT name, remarks
        FROM `tabPayment Entry`
        WHERE name IN %s
    """, (voucher_nos,), as_dict=True)
    return {e.name: (e.remarks or "") for e in entries}


def get_journal_entry_remarks(voucher_nos):
    if not voucher_nos:
        return {}
    voucher_nos = list(set(voucher_nos))
    entries = frappe.db.sql("""
        SELECT name, user_remark
        FROM `tabJournal Entry`
        WHERE name IN %s
    """, (voucher_nos,), as_dict=True)
    return {e.name: (e.user_remark or "") for e in entries}


def get_kassa_remarks(voucher_nos):
    """Kassa hujjatining o'z izohini (primechaniya) shu Kassa yaratgan
    PE/JE voucher_no'siga bog'lab qaytaradi ({voucher_no: izoh})."""
    if not voucher_nos:
        return {}
    voucher_nos = list(set(voucher_nos))
    rows = frappe.db.sql("""
        SELECT payment_entry, payment_entry_receive, payment_entry_supplier,
               journal_entry, primechaniya
        FROM `tabKassa`
        WHERE primechaniya IS NOT NULL AND primechaniya != ''
          AND (
              payment_entry IN %(vouchers)s
              OR payment_entry_receive IN %(vouchers)s
              OR payment_entry_supplier IN %(vouchers)s
              OR journal_entry IN %(vouchers)s
          )
    """, {"vouchers": voucher_nos}, as_dict=True)

    result = {}
    for r in rows:
        for field in ("payment_entry", "payment_entry_receive", "payment_entry_supplier", "journal_entry"):
            voucher = r.get(field)
            if voucher:
                result[voucher] = r.primechaniya
    return result


def get_party_names_batch(rows):
    """Party nomlarini bitta query bilan oladi."""
    party_keys = {}
    for r in rows:
        if r.party_type and r.party:
            party_keys.setdefault(r.party_type, set()).add(r.party)

    result = {}
    name_field_map = {
        "Customer": "customer_name",
        "Supplier": "supplier_name",
        "Employee": "employee_name",
    }

    for party_type, parties in party_keys.items():
        name_field = name_field_map.get(party_type)
        if not name_field:
            for p in parties:
                result[(party_type, p)] = p
            continue

        try:
            records = frappe.db.sql(f"""
                SELECT name, {name_field} AS display_name
                FROM `tab{party_type}`
                WHERE name IN %s
            """, (list(parties),), as_dict=True)
            for rec in records:
                result[(party_type, rec.name)] = rec.display_name or rec.name
        except Exception:
            for p in parties:
                result[(party_type, p)] = p

    return result


# ============================================================
# SUMMARY HTML — DDS uslubidagi kategoriya bo'yicha guruhlangan
# ============================================================

# Kategoriya konfiguratsiyasi
CATEGORY_DEFINITIONS = [
    {
        "key": "income",
        "label": "Даромад",
        "root_type": "Income",
        "party_type": None,
        "no_party": False,
        "color": "#388e3c",
        "is_income": True,
    },
    {
        "key": "supplier_expense",
        "label": "Поставщиклар (Харажат)",
        "root_type": "Expense",
        "party_type": "Supplier",
        "no_party": False,
        "color": "#d32f2f",
        "is_income": False,
    },
    {
        "key": "employee_expense",
        "label": "Ходимлар (Зарплата, харажатлар)",
        "root_type": "Expense",
        "party_type": "Employee",
        "no_party": False,
        "color": "#d32f2f",
        "is_income": False,
    },
    {
        "key": "customer_expense",
        "label": "Мижозлар билан (Харажат)",
        "root_type": "Expense",
        "party_type": "Customer",
        "no_party": False,
        "color": "#d32f2f",
        "is_income": False,
    },
    {
        "key": "other_expense",
        "label": "Бошқа харажатлар",
        "root_type": "Expense",
        "party_type": None,
        "no_party": True,
        "color": "#d32f2f",
        "is_income": False,
    },
]


def categorize_account(account_data):
    """Account ma'lumotini kategoriyaga biriktiradi."""
    root_type = account_data.get("root_type")
    party_type = account_data.get("party_type")

    if root_type == "Income":
        return "income"

    if root_type == "Expense":
        if party_type == "Supplier":
            return "supplier_expense"
        elif party_type == "Employee":
            return "employee_expense"
        elif party_type == "Customer":
            return "customer_expense"
        else:
            return "other_expense"

    return "other_expense"


def get_category_breakdown(filters, from_date, to_date):
    """
    Account + party_type kombinatsiyasi bo'yicha jami summalar.
    Bir account turli party_type bilan tranzaksiyalarga ega bo'lishi mumkin —
    har birini alohida olish kerak.
    """
    conditions = ["gle.is_cancelled = 0"]
    params = {
        "from_date": from_date,
        "to_date": to_date,
        "voucher_types": ALLOWED_VOUCHER_TYPES,
        "root_types": PNL_ROOT_TYPES,
    }

    if filters.get("expense_account"):
        conditions.append("gle.account = %(expense_account)s")
        params["expense_account"] = filters["expense_account"]

    if filters.get("party_type"):
        conditions.append("gle.party_type = %(party_type)s")
        params["party_type"] = filters["party_type"]

    if filters.get("party"):
        conditions.append("gle.party = %(party)s")
        params["party"] = filters["party"]

    if filters.get("cost_center"):
        conditions.append("gle.cost_center = %(cost_center)s")
        params["cost_center"] = filters["cost_center"]

    root_type_filter = filters.get("root_type")
    if root_type_filter and root_type_filter in PNL_ROOT_TYPES:
        conditions.append("acc.root_type = %(specific_root_type)s")
        params["specific_root_type"] = root_type_filter

    category = filters.get("category")
    if category and category in CATEGORY_FILTERS:
        cat_cfg = CATEGORY_FILTERS[category]
        if cat_cfg.get("no_party"):
            conditions.append("(gle.party_type IS NULL OR gle.party_type = '')")
        elif cat_cfg.get("party_type"):
            conditions.append("gle.party_type = %(category_party_type)s")
            params["category_party_type"] = cat_cfg["party_type"]

    where_clause = " AND ".join(conditions)

    rows = frappe.db.sql(f"""
        SELECT
            gle.account,
            acc.account_name,
            acc.root_type,
            COALESCE(NULLIF(gle.party_type, ''), '') AS party_type,
            CASE
                WHEN acc.root_type = 'Income' THEN SUM(gle.credit) - SUM(gle.debit)
                ELSE SUM(gle.debit) - SUM(gle.credit)
            END AS total,
            COUNT(*) AS txn_count
        FROM `tabGL Entry` gle
        INNER JOIN `tabAccount` acc ON acc.name = gle.account
        WHERE {where_clause}
          AND gle.posting_date BETWEEN %(from_date)s AND %(to_date)s
          AND gle.voucher_type IN %(voucher_types)s
          AND acc.root_type IN %(root_types)s
          AND (gle.debit > 0 OR gle.credit > 0)
        GROUP BY gle.account, acc.account_name, acc.root_type, gle.party_type
        HAVING total <> 0
        ORDER BY acc.root_type, total DESC
    """, params, as_dict=True)

    return rows


def get_summary_html(filters):
    """DDS uslubida kategoriya bo'yicha guruhlangan summary."""
    current_rows = get_category_breakdown(filters, filters["from_date"], filters["to_date"])

    prior_from, prior_to = get_prior_period(filters)
    prior_rows = get_category_breakdown(filters, prior_from, prior_to)

    if not current_rows and not prior_rows:
        return render_empty_summary(filters, prior_from, prior_to)

    # Kategoriya bo'yicha guruhlash
    # category_data[category_key] = {
    #   "total_current", "total_prior", "txn_count",
    #   "accounts": {account_name: {"current", "prior", "account_id", "txn_count"}}
    # }
    category_data = {cat["key"]: {
        "total_current": 0,
        "total_prior": 0,
        "txn_count": 0,
        "accounts": {},
    } for cat in CATEGORY_DEFINITIONS}

    def merge_rows(rows, period_key):
        for r in rows:
            cat_key = categorize_account(r)
            if cat_key not in category_data:
                continue

            total = flt(r.total)
            category_data[cat_key][f"total_{period_key}"] += total

            if period_key == "current":
                category_data[cat_key]["txn_count"] += int(r.txn_count)

            acc_id = r.account
            if acc_id not in category_data[cat_key]["accounts"]:
                category_data[cat_key]["accounts"][acc_id] = {
                    "account_id": acc_id,
                    "account_name": r.account_name,
                    "current": 0,
                    "prior": 0,
                    "txn_count": 0,
                }

            category_data[cat_key]["accounts"][acc_id][period_key] += total
            if period_key == "current":
                category_data[cat_key]["accounts"][acc_id]["txn_count"] += int(r.txn_count)

    merge_rows(current_rows, "current")
    merge_rows(prior_rows, "prior")

    return render_dds_summary(filters, prior_from, prior_to, category_data)


def render_empty_summary(filters, prior_from, prior_to):
    return f"""
    <div style="margin-top: 15px; padding: 20px; background: #fafafa; border-radius: 6px; text-align: center; color: #888;">
        <strong>Маълумот топилмади</strong><br>
        Танланган давр учун маълумот йўқ.<br>
        <small>Жорий давр: {formatdate(filters['from_date'])} — {formatdate(filters['to_date'])}</small>
    </div>
    """


def render_dds_summary(filters, prior_from, prior_to, category_data):
    """DDS-style summary jadval: kategoriya bo'yicha, har biri expandable account'lar bilan."""

    def fmt(v):
        v = flt(v)
        if v == 0:
            return "—"
        return f"{v:,.2f}"

    def fmt_pct(v):
        if v == 0:
            return "—"
        sign = "+" if v > 0 else ""
        return f"{sign}{flt(v):.1f}%"

    def delta_color(delta, is_income=False):
        # Income uchun teskari: oshgan yashil, kamaygan qizil
        # Expense uchun: oshgan qizil, kamaygan yashil
        if delta == 0:
            return "#666"
        if is_income:
            return "#388e3c" if delta > 0 else "#d32f2f"
        return "#d32f2f" if delta > 0 else "#388e3c"

    def trend_arrow(delta):
        if delta > 0:
            return '<span style="color: inherit; font-size: 13px;">▲</span>'
        elif delta < 0:
            return '<span style="color: inherit; font-size: 13px;">▼</span>'
        return '<span style="color: #999;">—</span>'

    def anomaly_badge(delta_pct, prior, is_income=False):
        if prior == 0 or abs(delta_pct) < ANOMALY_THRESHOLD:
            return ""
        is_bad = (delta_pct > 0 and not is_income) or (delta_pct < 0 and is_income)
        if is_bad:
            label = "⚠ ОШГАН" if not is_income else "⚠ КАМАЙГАН"
            return f'<span style="background: #ffebee; color: #c62828; padding: 2px 7px; border-radius: 10px; font-size: 10px; margin-left: 5px; font-weight: 600;">{label}</span>'
        else:
            label = "✓ КАМАЙГАН" if not is_income else "✓ ОШГАН"
            return f'<span style="background: #e8f5e9; color: #2e7d32; padding: 2px 7px; border-radius: 10px; font-size: 10px; margin-left: 5px; font-weight: 600;">{label}</span>'

    # Umumiy summalar
    total_income_cur = category_data.get("income", {}).get("total_current", 0)
    total_income_pri = category_data.get("income", {}).get("total_prior", 0)
    total_expense_cur = sum(category_data.get(c["key"], {}).get("total_current", 0)
                            for c in CATEGORY_DEFINITIONS if not c["is_income"])
    total_expense_pri = sum(category_data.get(c["key"], {}).get("total_prior", 0)
                            for c in CATEGORY_DEFINITIONS if not c["is_income"])

    net_cur = total_income_cur - total_expense_cur
    net_pri = total_income_pri - total_expense_pri
    net_delta = net_cur - net_pri
    net_delta_pct = (net_delta / abs(net_pri) * 100) if net_pri != 0 else 0

    expense_delta = total_expense_cur - total_expense_pri
    expense_delta_pct = (expense_delta / total_expense_pri * 100) if total_expense_pri > 0 else (100.0 if total_expense_cur > 0 else 0)

    # Top movers (faqat xarajatlar bo'yicha — har bir account)
    all_expense_accounts = []
    for cat in CATEGORY_DEFINITIONS:
        if cat["is_income"]:
            continue
        cat_obj = category_data.get(cat["key"], {})
        for acc_id, acc in cat_obj.get("accounts", {}).items():
            cur = flt(acc["current"])
            pri = flt(acc["prior"])
            delta = cur - pri
            delta_pct = (delta / pri * 100) if pri > 0 else (100.0 if cur > 0 else 0)
            all_expense_accounts.append({
                "account_name": acc["account_name"],
                "current": cur,
                "prior": pri,
                "delta": delta,
                "delta_pct": delta_pct,
            })

    increases = sorted([a for a in all_expense_accounts if a["delta"] > 0 and a["prior"] > 0],
                       key=lambda x: x["delta_pct"], reverse=True)[:3]
    decreases = sorted([a for a in all_expense_accounts if a["delta"] < 0 and a["prior"] > 0],
                       key=lambda x: x["delta_pct"])[:3]

    def render_movers(items, kind):
        if not items:
            return f'<div style="color: #999; font-size: 12px; padding: 8px 0;">Маълумот йўқ</div>'
        out = ""
        for it in items:
            color = "#d32f2f" if kind == "up" else "#388e3c"
            arrow = "▲" if kind == "up" else "▼"
            out += f"""
                <div style="padding: 6px 0; border-bottom: 1px dashed #e0e0e0; display: flex; justify-content: space-between; align-items: center;">
                    <div style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding-right: 10px;" title="{frappe.utils.escape_html(it['account_name'])}">
                        <span style="color: {color}; margin-right: 5px;">{arrow}</span>
                        {frappe.utils.escape_html(it['account_name'])}
                    </div>
                    <div style="color: {color}; font-weight: 600; font-size: 13px;">{fmt_pct(it['delta_pct'])}</div>
                </div>
            """
        return out

    # Kategoriya qatorlari (DDS uslubidagi expandable)
    category_rows_html = ""
    cat_counter = 0
    for cat_def in CATEGORY_DEFINITIONS:
        cat_obj = category_data.get(cat_def["key"], {})
        cur = flt(cat_obj.get("total_current", 0))
        pri = flt(cat_obj.get("total_prior", 0))

        # Bo'sh kategoriyalarni o'tkazib yuborish
        if cur == 0 and pri == 0:
            continue

        cat_counter += 1
        delta = cur - pri
        delta_pct = (delta / pri * 100) if pri > 0 else (100.0 if cur > 0 else 0)
        accounts = cat_obj.get("accounts", {})

        is_anomaly = abs(delta_pct) >= ANOMALY_THRESHOLD and pri > 0
        is_bad = (delta_pct > 0 and not cat_def["is_income"]) or (delta_pct < 0 and cat_def["is_income"])
        row_bg = "#fff5f5" if is_anomaly and is_bad else ("#f1f8e9" if cat_def["is_income"] else "")

        has_accounts = len(accounts) > 0
        arrow_html = '<span class="exp-arrow exp-arrow-' + str(cat_counter) + '" style="display:inline-block; margin-right:6px; font-size:10px; transition: transform 0.2s; color:#666;">▶</span>' if has_accounts else '<span style="display:inline-block; width:14px; margin-right:6px;"></span>'
        cursor_style = "cursor: pointer;" if has_accounts else ""

        onclick = ""
        if has_accounts:
            onclick = f"""onclick="(function(){{
                var rows = document.querySelectorAll('.exp-sub-{cat_counter}');
                var arrow = document.querySelector('.exp-arrow-{cat_counter}');
                if (!rows.length) return;
                var visible = rows[0].style.display !== 'none';
                for (var i = 0; i < rows.length; i++) {{
                    rows[i].style.display = visible ? 'none' : 'table-row';
                }}
                if (arrow) arrow.innerHTML = visible ? '▶' : '▼';
            }})()" """

        category_rows_html += f"""
            <tr style="background-color: {row_bg}; {cursor_style}" {onclick}>
                <td style="padding: 12px; border-bottom: 1px solid #eee; font-weight: 500;">
                    {arrow_html}{frappe.utils.escape_html(cat_def['label'])}
                </td>
                <td style="padding: 12px; border-bottom: 1px solid #eee; text-align: right; font-weight: 600; color: {cat_def['color']};">{fmt(cur)}</td>
                <td style="padding: 12px; border-bottom: 1px solid #eee; text-align: right; color: #888;">{fmt(pri)}</td>
                <td style="padding: 12px; border-bottom: 1px solid #eee; text-align: right; color: {delta_color(delta, cat_def['is_income'])}; font-weight: 500;">
                    {trend_arrow(delta) if delta != 0 else ''} {fmt(delta) if delta != 0 else '—'}
                </td>
                <td style="padding: 12px; border-bottom: 1px solid #eee; text-align: right; color: {delta_color(delta, cat_def['is_income'])}; font-weight: 600;">
                    {fmt_pct(delta_pct)}{anomaly_badge(delta_pct, pri, cat_def['is_income'])}
                </td>
                <td style="padding: 12px; border-bottom: 1px solid #eee; text-align: center; color: #666;">{cat_obj.get('txn_count', 0)}</td>
            </tr>
        """

        # Subkategoriya account qatorlari (yashirin, kategoriya bosilganda ko'rinadi)
        sorted_accounts = sorted(accounts.values(), key=lambda x: flt(x["current"]), reverse=True)
        for acc in sorted_accounts:
            a_cur = flt(acc["current"])
            a_pri = flt(acc["prior"])
            a_delta = a_cur - a_pri
            a_delta_pct = (a_delta / a_pri * 100) if a_pri > 0 else (100.0 if a_cur > 0 else 0)

            a_anomaly = abs(a_delta_pct) >= ANOMALY_THRESHOLD and a_pri > 0
            a_bad = (a_delta_pct > 0 and not cat_def["is_income"]) or (a_delta_pct < 0 and cat_def["is_income"])
            a_bg = "#fff8e1" if a_anomaly and a_bad else "#fafafa"

            category_rows_html += f"""
                <tr class="exp-sub-{cat_counter}" style="display: none; background-color: {a_bg};">
                    <td style="padding: 8px 12px 8px 36px; border-bottom: 1px solid #f0f0f0; font-size: 12px;">
                        <div>{frappe.utils.escape_html(acc['account_name'])}</div>
                        <div style="font-size: 10px; color: #aaa; margin-top: 1px;">{frappe.utils.escape_html(acc['account_id'])}</div>
                    </td>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #f0f0f0; text-align: right; font-size: 12px;">{fmt(a_cur)}</td>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #f0f0f0; text-align: right; color: #999; font-size: 12px;">{fmt(a_pri)}</td>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #f0f0f0; text-align: right; color: {delta_color(a_delta, cat_def['is_income'])}; font-size: 12px;">
                        {trend_arrow(a_delta) if a_delta != 0 else ''} {fmt(a_delta) if a_delta != 0 else '—'}
                    </td>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #f0f0f0; text-align: right; color: {delta_color(a_delta, cat_def['is_income'])}; font-size: 12px; font-weight: 500;">
                        {fmt_pct(a_delta_pct)}{anomaly_badge(a_delta_pct, a_pri, cat_def['is_income'])}
                    </td>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #f0f0f0; text-align: center; color: #999; font-size: 12px;">{acc.get('txn_count', 0)}</td>
                </tr>
            """

    period_days = date_diff(getdate(filters["to_date"]), getdate(filters["from_date"])) + 1

    # 3 ta header karta
    header_label = "Соф фойда/зарар" if total_income_cur > 0 else "Жами харажат"
    main_metric_cur = net_cur if total_income_cur > 0 else total_expense_cur
    main_metric_pri = net_pri if total_income_pri > 0 else total_expense_pri
    main_metric_delta = main_metric_cur - main_metric_pri
    main_metric_delta_pct = (main_metric_delta / abs(main_metric_pri) * 100) if main_metric_pri != 0 else 0

    # Asosiy ko'rsatkich uchun rang
    if total_income_cur > 0:
        # Net profit: musbat yashil, manfiy qizil
        delta_card_color = "#388e3c" if main_metric_delta > 0 else "#d32f2f" if main_metric_delta < 0 else "#666"
        delta_card_bg = "#e8f5e9" if main_metric_delta > 0 else "#ffebee" if main_metric_delta < 0 else "#f5f5f5"
        delta_card_border = "#c8e6c9" if main_metric_delta > 0 else "#ffcdd2" if main_metric_delta < 0 else "#e0e0e0"
        delta_caption = "Фойда ошган" if main_metric_delta > 0 else "Фойда камайган" if main_metric_delta < 0 else "Ўзгармаган"
    else:
        # Expense only
        delta_card_color = "#d32f2f" if main_metric_delta > 0 else "#388e3c" if main_metric_delta < 0 else "#666"
        delta_card_bg = "#ffebee" if main_metric_delta > 0 else "#e8f5e9" if main_metric_delta < 0 else "#f5f5f5"
        delta_card_border = "#ffcdd2" if main_metric_delta > 0 else "#c8e6c9" if main_metric_delta < 0 else "#e0e0e0"
        delta_caption = "Харажатлар ошган" if main_metric_delta > 0 else "Харажатлар камайган" if main_metric_delta < 0 else "Ўзгармаган"

    html = f"""
    <div style="margin: 15px 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">

        <!-- HEADER KARTALAR -->
        <div style="display: flex; gap: 15px; margin-bottom: 20px; flex-wrap: wrap;">
            <div style="flex: 1; min-width: 220px; background: linear-gradient(135deg, #1976d2 0%, #1565c0 100%); color: white; padding: 18px; border-radius: 8px;">
                <div style="font-size: 12px; opacity: 0.85; text-transform: uppercase; letter-spacing: 0.5px;">Жорий давр</div>
                <div style="font-size: 13px; margin-top: 4px; opacity: 0.95;">{formatdate(filters['from_date'])} — {formatdate(filters['to_date'])}</div>
                <div style="font-size: 24px; font-weight: 700; margin-top: 10px;">{flt(main_metric_cur):,.2f}</div>
                <div style="font-size: 11px; opacity: 0.85; margin-top: 4px;">{header_label} · {period_days} кун</div>
            </div>

            <div style="flex: 1; min-width: 220px; background: #f5f5f5; padding: 18px; border-radius: 8px; border: 1px solid #e0e0e0;">
                <div style="font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: 0.5px;">Олдинги давр</div>
                <div style="font-size: 13px; margin-top: 4px; color: #777;">{formatdate(prior_from)} — {formatdate(prior_to)}</div>
                <div style="font-size: 24px; font-weight: 700; margin-top: 10px; color: #444;">{flt(main_metric_pri):,.2f}</div>
                <div style="font-size: 11px; color: #888; margin-top: 4px;">{header_label} · {period_days} кун</div>
            </div>

            <div style="flex: 1; min-width: 220px; background: {delta_card_bg}; padding: 18px; border-radius: 8px; border: 1px solid {delta_card_border};">
                <div style="font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: 0.5px;">Ўзгариш</div>
                <div style="font-size: 13px; margin-top: 4px; color: {delta_card_color}; font-weight: 600;">{trend_arrow(main_metric_delta)} {fmt_pct(main_metric_delta_pct)}</div>
                <div style="font-size: 24px; font-weight: 700; margin-top: 10px; color: {delta_card_color};">{flt(main_metric_delta):,.2f}</div>
                <div style="font-size: 11px; color: #888; margin-top: 4px;">{delta_caption}</div>
            </div>
        </div>

        <!-- TOP MOVERS -->
        <div style="display: flex; gap: 15px; margin-bottom: 20px; flex-wrap: wrap;">
            <div style="flex: 1; min-width: 300px; background: white; border: 1px solid #ffcdd2; border-radius: 8px; padding: 14px;">
                <div style="font-size: 13px; font-weight: 600; color: #c62828; margin-bottom: 8px; padding-bottom: 8px; border-bottom: 2px solid #ffebee;">
                    ⚠ Энг кўп ошган харажатлар
                </div>
                {render_movers(increases, "up")}
            </div>
            <div style="flex: 1; min-width: 300px; background: white; border: 1px solid #c8e6c9; border-radius: 8px; padding: 14px;">
                <div style="font-size: 13px; font-weight: 600; color: #2e7d32; margin-bottom: 8px; padding-bottom: 8px; border-bottom: 2px solid #e8f5e9;">
                    ✓ Энг кўп камайган харажатлар
                </div>
                {render_movers(decreases, "down")}
            </div>
        </div>

        <!-- DDS-STYLE KATEGORIYA JADVAL -->
        <div style="background: white; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden;">
            <div style="padding: 12px 16px; background: #fafafa; border-bottom: 1px solid #e0e0e0; font-weight: 600; font-size: 14px;">
                Категория бўйича умумий ҳисобот
                <span style="font-weight: 400; color: #888; font-size: 12px; margin-left: 8px;">— қатор устига босинг, ичидаги счётлар очилади</span>
            </div>
            <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
                <thead>
                    <tr style="background: #f9f9f9;">
                        <th style="padding: 10px 12px; text-align: left; border-bottom: 2px solid #e0e0e0; font-weight: 600; color: #555;">Категория</th>
                        <th style="padding: 10px 12px; text-align: right; border-bottom: 2px solid #e0e0e0; font-weight: 600; color: #555;">Жорий</th>
                        <th style="padding: 10px 12px; text-align: right; border-bottom: 2px solid #e0e0e0; font-weight: 600; color: #555;">Олдинги</th>
                        <th style="padding: 10px 12px; text-align: right; border-bottom: 2px solid #e0e0e0; font-weight: 600; color: #555;">Δ Сумма</th>
                        <th style="padding: 10px 12px; text-align: right; border-bottom: 2px solid #e0e0e0; font-weight: 600; color: #555;">Δ %</th>
                        <th style="padding: 10px 12px; text-align: center; border-bottom: 2px solid #e0e0e0; font-weight: 600; color: #555;">Транз.</th>
                    </tr>
                </thead>
                <tbody>
                    {category_rows_html}
                </tbody>
                <tfoot>
                    <tr style="background: #1976d2; color: white; font-weight: 600;">
                        <td style="padding: 12px;">ЖАМИ ХАРАЖАТЛАР</td>
                        <td style="padding: 12px; text-align: right;">{flt(total_expense_cur):,.2f}</td>
                        <td style="padding: 12px; text-align: right; opacity: 0.9;">{flt(total_expense_pri):,.2f}</td>
                        <td style="padding: 12px; text-align: right;">{flt(expense_delta):,.2f}</td>
                        <td style="padding: 12px; text-align: right;">{fmt_pct(expense_delta_pct)}</td>
                        <td style="padding: 12px; text-align: center;">—</td>
                    </tr>
                </tfoot>
            </table>
        </div>

        <div style="margin-top: 12px; padding: 10px 14px; background: #fffde7; border-left: 3px solid #fbc02d; border-radius: 4px; font-size: 12px; color: #6d4c00;">
            <strong>Эслатма:</strong> Категория устига босиб, унинг ичидаги счётларни кенгайтиринг.
            {ANOMALY_THRESHOLD:.0f}%дан кўпроқ ёмон томонга ўзгарганлар <span style="background: #ffebee; padding: 2px 6px; border-radius: 3px;">қизил билан</span> белгиланган.
            Пастда ҳар бир транзакция бўйича тўлиқ рўйхат.
        </div>
    </div>
    """

    return html
