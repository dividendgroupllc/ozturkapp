# Copyright (c) 2026, Ozturkapp and contributors
# For license information, please see license.txt

"""
PL Obshi — O'zturk guruhi bo'yicha UMUMIY (konsolidatsiyalangan) P&L.

Jazira'ning shu nomli hisobotidan moslashtirilgan. Jazira'ga xos narsalar
(hisob raqamlari 52001/52002, egalar/dividend formulalari, kompaniya nomlari)
OLIB TASHLANGAN: hisoblar faqat `root_type` / `account_type` va ota-hisob
bo'yicha toifalanadi, kompaniyalar Company daraxtidan (tanlangan guruh
kompaniyaning ishchi farzandlari) olinadi — yangi filial ochilsa o'zi chiqadi.

Ichki aylanmani chiqarib tashlash (elimination)
───────────────────────────────────────────────
Sklad tovarni filialga TAN NARXDA beradi (Branch Stock Transfer): Sklad
kitobida — ichki mijozga Sales Invoice (Выручка + shu summada Себестоимость),
filial kitobida — Purchase Invoice (omborga kirim; taom sotilganda filial
Себестоимость'i). Guruh darajasida bu sotuv emas, shuning uchun:

    Выручка       = Σ(kompaniyalar tushumi)  − ichki sotuv
    Себестоимость = Σ(kompaniyalar tannarxi) − (ichki sotuv − ichki xarid xarajati)
    Операционные  = Σ(kompaniyalar xarajati) − ichki xarid xarajati

  * ichki sotuv — ichki mijozga (represents_company shu guruhda, o'zidan
    boshqa kompaniya) yozilgan SUBMIT qilingan Sales Invoice'ning Income
    GL qatorlari;
  * ichki xarid xarajati — ichki ta'minotchidan Purchase Invoice'ning
    to'g'ridan-to'g'ri XARAJAT hisobiga tushgan qatorlari (masalan xizmat).
    Tovar PI'si (update_stock) omborga tushadi, P&L'ga emas — u 0 bo'ladi.

Jami ayirilgan daromad = jami ayirilgan xarajat ⇒ guruh foydasi kompaniyalar
foydasi yig'indisiga TENG (tekshirish oson). Tan narxda o'tkazma bo'lgani
uchun filial omborida qolgan tovarda ichki foyda yo'q — qo'shimcha tuzatish
kerak emas.

Xarajat taqsimoti (Expense Allocation)
──────────────────────────────────────
`Expense Allocation` JE'lari Sklad xarajatini filiallarga qayta yozadi
(Sklad: Кт xarajat; filial: Дт xarajat). Hisob qatorlarida xarajat
TAQSIMLASHDAN OLDINGI holida (qayerda yuzaga kelgan bo'lsa) ko'rsatiladi,
taqsimot esa kompaniyalar kesimida alohida qator: Sklad −X, filial +X,
guruh jami ≈ 0 — ya'ni ikki marta sanalmaydi ham, yo'qolmaydi ham.
Kompaniya foydasi taqsimotdan KEYINGI holda (filial haqiqiy natijasi).
"""

from datetime import date, timedelta

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from ozturkapp.ozturkapp.report.internal_parties import (
    ALLOCATION_LABEL_CYR,
    allocation_gl_sql,
    get_default_group_company,
    get_leaf_companies,
)

TOTAL_KEY = "__total__"

# Tannarx hisob turlari (account_type) — qolgan barcha Expense hisoblar operatsion
COGS_TYPE = "Cost of Goods Sold"
ADJ_TYPE = "Stock Adjustment"

# "Омбор тафовути" (Stock Adjustment) ichidagi sabablar — hujjat turidan
ADJ_KIND_LABELS = [
    ("recon", "инвентаризация фарқи (Stock Reconciliation)"),
    ("writeoff", "ҳисобдан чиқариш / брак (Material Issue)"),
    ("mfg", "ишлаб чиқариш фарқи (Manufacture/Repack)"),
    ("other", "бошқа омбор тафовути"),
]


# ─── Entry point ─────────────────────────────────────────────────────────────

def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not filters.get("company"):
        filters.company = get_default_group_company()

    period_list = build_period_list(filters)
    if not period_list:
        return [], []

    from_date = str(period_list[0]["from_date"])
    to_date = str(period_list[-1]["to_date"])

    companies = get_leaf_companies(filters.company)
    if not companies:
        frappe.throw(_("Ишчи компания топилмади"))

    show_internal = cint(filters.get("show_internal"))

    gl_rows = fetch_gl(companies, from_date, to_date)
    internal_sales = fetch_internal_sales(companies, from_date, to_date)
    internal_purchase_exp = fetch_internal_purchase_expense(companies, from_date, to_date)

    periods = period_list + ([_total_period(period_list)] if len(period_list) > 1 else [])
    pdata = aggregate(periods, companies, gl_rows, internal_sales, internal_purchase_exp)

    columns = get_columns(periods)
    data = build_rows(periods, companies, pdata, show_internal, filters.company)
    return columns, data


# ─── Davrlar (Jazira PL Hisoboti dvigateli) ──────────────────────────────────

def build_period_list(filters):
    today = date.today()
    from_date = getdate(filters.get("from_date") or date(today.year, 1, 1))
    to_date = getdate(filters.get("to_date") or today)
    periodicity = filters.get("periodicity") or "Monthly"

    periods = []
    current = from_date
    while current <= to_date:
        y, m = current.year, current.month
        if periodicity == "Monthly":
            p_start = date(y, m, 1)
            nm = m + 1
            p_end = (date(y, nm, 1) - timedelta(days=1)) if nm <= 12 else date(y, 12, 31)
            label = p_start.strftime("%b %Y")
            next_cur = date(y, nm, 1) if nm <= 12 else date(y + 1, 1, 1)
        elif periodicity == "Quarterly":
            q = (m - 1) // 3
            qs, qe = q * 3 + 1, q * 3 + 3
            p_start = date(y, qs, 1)
            p_end = (date(y, qe + 1, 1) - timedelta(days=1)) if qe < 12 else date(y, 12, 31)
            label = f"Q{q + 1} {y}"
            next_cur = date(y, qe + 1, 1) if qe < 12 else date(y + 1, 1, 1)
        elif periodicity == "Half-Yearly":
            if m <= 6:
                p_start, p_end, label, next_cur = date(y, 1, 1), date(y, 6, 30), f"H1 {y}", date(y, 7, 1)
            else:
                p_start, p_end, label, next_cur = date(y, 7, 1), date(y, 12, 31), f"H2 {y}", date(y + 1, 1, 1)
        else:  # Yearly
            p_start, p_end, label, next_cur = date(y, 1, 1), date(y, 12, 31), str(y), date(y + 1, 1, 1)

        actual_start, actual_end = max(p_start, from_date), min(p_end, to_date)
        if actual_start <= actual_end:
            periods.append({"key": label, "label": label, "from_date": actual_start, "to_date": actual_end})
        current = next_cur
    return periods


def _total_period(period_list):
    return {
        "key": TOTAL_KEY,
        "label": _("Жами"),
        "from_date": period_list[0]["from_date"],
        "to_date": period_list[-1]["to_date"],
    }


def _fk(period_key):
    return "v_" + period_key.replace(" ", "_").replace("-", "_")


def _period_keys(posting_date, periods):
    """Sana tushadigan barcha davrlar (oddiy davr + Жами ustuni)."""
    return [p["key"] for p in periods if p["from_date"] <= posting_date <= p["to_date"]]


# ─── Ma'lumot ────────────────────────────────────────────────────────────────

def fetch_gl(companies, from_date, to_date):
    """Kompaniyalarning Income/Expense GL harakati (hisob, ota-hisob, sabab bilan)."""
    return frappe.db.sql(
        f"""
        SELECT
            gle.company,
            gle.posting_date,
            gle.account,
            acc.account_name,
            acc.root_type,
            acc.account_type,
            IFNULL(parent_acc.account_name, '') AS parent_name,
            CASE
                WHEN acc.account_type != 'Stock Adjustment' THEN ''
                WHEN gle.voucher_type = 'Stock Reconciliation' THEN 'recon'
                WHEN se.purpose = 'Material Issue' THEN 'writeoff'
                WHEN se.purpose IN ('Manufacture', 'Repack') THEN 'mfg'
                ELSE 'other'
            END AS adj_kind,
            {allocation_gl_sql("gle")} AS is_allocation,
            SUM(gle.debit) AS debit,
            SUM(gle.credit) AS credit
        FROM `tabGL Entry` gle
        JOIN `tabAccount` acc ON acc.name = gle.account
        LEFT JOIN `tabAccount` parent_acc ON parent_acc.name = acc.parent_account
        LEFT JOIN `tabStock Entry` se
            ON se.name = gle.voucher_no AND gle.voucher_type = 'Stock Entry'
        WHERE gle.company IN %(companies)s
          AND gle.is_cancelled = 0
          AND gle.posting_date BETWEEN %(from_date)s AND %(to_date)s
          -- Yil yopilganda PCV butun natijani teskari yozadi — hisobot nolga tushmasin
          AND gle.voucher_type != 'Period Closing Voucher'
          AND acc.root_type IN ('Income', 'Expense')
        GROUP BY gle.company, gle.posting_date, gle.account, adj_kind, is_allocation
        """,
        {"companies": tuple(companies), "from_date": from_date, "to_date": to_date},
        as_dict=True,
    )


def fetch_internal_sales(companies, from_date, to_date):
    """Guruh ichidagi sotuv — ichki mijozga yozilgan SI'ning Income qatorlari.

    Ikkala tomon ham tanlangan guruh ichida bo'lishi shart; o'z-o'ziga
    (represents_company = company) ichki o'tkazmada daromad yo'q."""
    return frappe.db.sql(
        """
        SELECT gle.posting_date, SUM(gle.credit - gle.debit) AS amount
        FROM `tabGL Entry` gle
        JOIN `tabAccount` acc ON acc.name = gle.account
        JOIN `tabSales Invoice` si ON si.name = gle.voucher_no
        JOIN `tabCustomer` cust ON cust.name = si.customer
        WHERE gle.voucher_type = 'Sales Invoice'
          AND gle.company IN %(companies)s
          AND gle.is_cancelled = 0
          AND si.docstatus = 1
          AND gle.posting_date BETWEEN %(from_date)s AND %(to_date)s
          AND acc.root_type = 'Income'
          AND cust.is_internal_customer = 1
          AND cust.represents_company IN %(companies)s
          AND cust.represents_company != si.company
        GROUP BY gle.posting_date
        """,
        {"companies": tuple(companies), "from_date": from_date, "to_date": to_date},
        as_dict=True,
    )


def fetch_internal_purchase_expense(companies, from_date, to_date):
    """Ichki ta'minotchidan PI'ning to'g'ridan-to'g'ri xarajat qatorlari
    (tannarx/ombor hisoblaridan tashqari)."""
    return frappe.db.sql(
        """
        SELECT gle.posting_date, SUM(gle.debit - gle.credit) AS amount
        FROM `tabGL Entry` gle
        JOIN `tabAccount` acc ON acc.name = gle.account
        JOIN `tabPurchase Invoice` pi ON pi.name = gle.voucher_no
        JOIN `tabSupplier` sup ON sup.name = pi.supplier
        WHERE gle.voucher_type = 'Purchase Invoice'
          AND gle.company IN %(companies)s
          AND gle.is_cancelled = 0
          AND pi.docstatus = 1
          AND gle.posting_date BETWEEN %(from_date)s AND %(to_date)s
          AND acc.root_type = 'Expense'
          AND IFNULL(acc.account_type, '') NOT IN ('Cost of Goods Sold', 'Stock Adjustment')
          AND sup.is_internal_supplier = 1
          AND sup.represents_company IN %(companies)s
          AND sup.represents_company != pi.company
        GROUP BY gle.posting_date
        """,
        {"companies": tuple(companies), "from_date": from_date, "to_date": to_date},
        as_dict=True,
    )


# ─── Yig'ish ─────────────────────────────────────────────────────────────────

def _empty_company():
    return {
        "revenue": 0.0,
        "cogs_raw": 0.0,     # Cost of Goods Sold
        "cogs_adj": 0.0,     # Stock Adjustment (omborni tafovut)
        "adj": {},           # adj_kind -> summa
        "opex": {},          # (ota-hisob, hisob) -> summa — taqsimotdan OLDIN
        "alloc": 0.0,        # Expense Allocation (Sklad −, filial +)
    }


def aggregate(periods, companies, gl_rows, internal_sales, internal_purchase_exp):
    pdata = {
        p["key"]: {
            "companies": {c: _empty_company() for c in companies},
            "internal_sales": 0.0,
            "internal_exp": 0.0,
            "acc_labels": {},
        }
        for p in periods
    }

    for r in gl_rows:
        for pk in _period_keys(r.posting_date, periods):
            d = pdata[pk]
            cd = d["companies"].get(r.company)
            if cd is None:
                continue
            net = flt(r.debit) - flt(r.credit)

            if r.root_type == "Income":
                cd["revenue"] -= net
            elif cint(r.is_allocation):
                cd["alloc"] += net
            elif r.account_type == COGS_TYPE:
                cd["cogs_raw"] += net
            elif r.account_type == ADJ_TYPE:
                kind = r.adj_kind or "other"
                cd["cogs_adj"] += net
                cd["adj"][kind] = cd["adj"].get(kind, 0) + net
            else:
                # Hisob nomi bo'yicha kalit: kompaniyalarda bir xil nomli hisoblar
                # ("Salary - OMG", "Salary - OSK") bitta qatorga birlashadi.
                key = (r.parent_name or _("Бошқа"), r.account_name)
                cd["opex"][key] = cd["opex"].get(key, 0) + net

    for rows, field in ((internal_sales, "internal_sales"), (internal_purchase_exp, "internal_exp")):
        for r in rows:
            for pk in _period_keys(r.posting_date, periods):
                pdata[pk][field] += flt(r.amount)

    return pdata


# ─── Hisoblash yordamchilari ─────────────────────────────────────────────────

def _co_cogs(cd):
    return flt(cd["cogs_raw"]) + flt(cd["cogs_adj"])


def _co_opex_raw(cd):
    return sum(cd["opex"].values())


def _co_opex(cd):
    """Kompaniya operatsion xarajati — taqsimotdan KEYIN."""
    return _co_opex_raw(cd) + flt(cd["alloc"])


def _co_marginal(cd):
    return cd["revenue"] - _co_cogs(cd)


def _co_profit(cd):
    return _co_marginal(cd) - _co_opex(cd)


def _cogs_elimination(d):
    return flt(d["internal_sales"]) - flt(d["internal_exp"])


def _total_revenue(d):
    return sum(c["revenue"] for c in d["companies"].values()) - flt(d["internal_sales"])


def _total_cogs(d):
    return sum(_co_cogs(c) for c in d["companies"].values()) - _cogs_elimination(d)


def _total_marginal(d):
    return _total_revenue(d) - _total_cogs(d)


def _total_opex(d):
    return sum(_co_opex(c) for c in d["companies"].values()) - flt(d["internal_exp"])


def _total_profit(d):
    return _total_marginal(d) - _total_opex(d)


# ─── Ustunlar ────────────────────────────────────────────────────────────────

def get_columns(periods):
    cols = [{"fieldname": "label", "label": _("Кўрсаткич"), "fieldtype": "Data", "width": 340}]
    for p in periods:
        cols.append({
            "fieldname": _fk(p["key"]),
            "label": p["label"],
            "fieldtype": "Currency",
            "options": "currency",
            "width": 150,
        })
    return cols


def company_label(company, group_company=None):
    """Ko'rinadigan qisqa nom: guruh nomi prefiksi olib tashlanadi
    ("O'zturk Maksim Gorkiy" → "Maksim Gorkiy"). Nomlar qattiq yozilmaydi."""
    parent = group_company or frappe.db.get_value("Company", company, "parent_company")
    if parent and company.startswith(parent) and company != parent:
        short = company[len(parent):].strip(" -—")
        if short:
            return short
    return company


# ─── Qatorlar ────────────────────────────────────────────────────────────────

def build_rows(periods, companies, pdata, show_internal=0, group_company=None):
    fkeys = [_fk(p["key"]) for p in periods]
    labels = {c: company_label(c, group_company) for c in companies}

    def per_period(fn):
        return {_fk(p["key"]): fn(pdata[p["key"]]) for p in periods}

    def mk(label, value_map, row_type="detail", indent=0, is_cost=False):
        r = {
            "label": label,
            "row_type": row_type,
            "indent": indent,
            "is_percent": 1 if row_type == "percent" else 0,
            "is_cost": 1 if is_cost else 0,
        }
        r.update(value_map)
        return r

    def divider():
        r = {"label": "", "row_type": "divider", "indent": 0}
        r.update({fk: None for fk in fkeys})
        return r

    def all_zero(vm):
        return all(abs(flt(v)) < 0.005 for v in vm.values())

    def pct(num_fn, den_fn):
        return per_period(lambda d: (num_fn(d) / den_fn(d) * 100) if den_fn(d) else 0)

    rows = []

    # ── ВЫРУЧКА ──────────────────────────────────────────────────────────────
    rows.append(mk("Итого выручка", per_period(_total_revenue), "root", 0))
    for co in companies:
        vm = per_period(lambda d, c=co: d["companies"][c]["revenue"])
        if not all_zero(vm):
            rows.append(mk(f"Выручка {labels[co]}", vm, "detail", 1))
    ivm = per_period(lambda d: -flt(d["internal_sales"]))
    if show_internal and not all_zero(ivm):
        rows.append(mk("(−) Ички айланма (склад → филиал)", ivm, "detail", 1))

    # ── СЕБЕСТОИМОСТЬ ────────────────────────────────────────────────────────
    rows.append(mk("Итого себестоимость", per_period(_total_cogs), "root", 0, is_cost=True))
    for bucket, bucket_label in (("cogs_raw", "Сырьевая себестоимость"), ("cogs_adj", "Омбор тафовути")):
        bvm = per_period(lambda d, b=bucket: sum(flt(cd[b]) for cd in d["companies"].values()))
        if all_zero(bvm):
            continue
        rows.append(mk(bucket_label, bvm, "sub", 1, is_cost=True))
        if bucket == "cogs_adj":
            for kind, kind_label in ADJ_KIND_LABELS:
                kvm = per_period(lambda d, k=kind: sum(flt(cd["adj"].get(k, 0)) for cd in d["companies"].values()))
                if not all_zero(kvm):
                    rows.append(mk(kind_label, kvm, "detail", 2, is_cost=True))
        for co in companies:
            cvm = per_period(lambda d, b=bucket, c=co: flt(d["companies"][c][b]))
            if not all_zero(cvm):
                rows.append(mk(f"{bucket_label} {labels[co]}", cvm, "detail", 2, is_cost=True))
    evm = per_period(lambda d: -_cogs_elimination(d))
    if show_internal and not all_zero(evm):
        rows.append(mk("(−) Ички айланма (склад → филиал)", evm, "sub", 1, is_cost=True))

    # ── МАРЖИНАЛЬНАЯ ПРИБЫЛЬ ─────────────────────────────────────────────────
    rows.append(mk("Маржинальная прибыль", per_period(_total_marginal), "result", 0))
    rows.append(mk("маржа", pct(_total_marginal, _total_revenue), "percent", 1))
    for co in companies:
        vm = per_period(lambda d, c=co: _co_marginal(d["companies"][c]))
        if all_zero(vm):
            continue
        rows.append(mk(f"Маржинальная прибыль {labels[co]}", vm, "detail", 1))
        rows.append(mk(
            f"рентабельность % {labels[co]}",
            pct(lambda d, c=co: _co_marginal(d["companies"][c]), lambda d, c=co: d["companies"][c]["revenue"]),
            "percent", 2,
        ))
    rows.append(divider())

    # ── ОПЕРАЦИОННЫЕ РАСХОДЫ ─────────────────────────────────────────────────
    rows.append(mk("Итого операционные расходы", per_period(_total_opex), "root", 0, is_cost=True))

    # Ota-hisob (guruh) → hisob; summa — barcha kompaniyalar, taqsimotdan OLDIN
    groups = {}
    for p in periods:
        for cd in pdata[p["key"]]["companies"].values():
            for (grp, acc), amt in cd["opex"].items():
                groups.setdefault(grp, {}).setdefault(acc, 0)
                groups[grp][acc] += abs(flt(amt))
    for grp in sorted(groups, key=lambda g: -sum(groups[g].values())):
        gvm = per_period(lambda d, g=grp: sum(
            flt(v) for cd in d["companies"].values() for (gg, _a), v in cd["opex"].items() if gg == g))
        if all_zero(gvm):
            continue
        rows.append(mk(grp, gvm, "sub", 1, is_cost=True))
        for acc in sorted(groups[grp], key=lambda a: -groups[grp][a]):
            avm = per_period(lambda d, g=grp, a=acc: sum(
                flt(cd["opex"].get((g, a), 0)) for cd in d["companies"].values()))
            if not all_zero(avm):
                rows.append(mk(acc, avm, "detail", 2, is_cost=True))

    # Taqsimot: guruh jami ≈ 0 (Sklad −X, filiallar +X) — kompaniyalar kesimida
    alloc_total = per_period(lambda d: sum(flt(cd["alloc"]) for cd in d["companies"].values()))
    alloc_rows = []
    for co in companies:
        avm = per_period(lambda d, c=co: flt(d["companies"][c]["alloc"]))
        if not all_zero(avm):
            alloc_rows.append(mk(f"{ALLOCATION_LABEL_CYR} {labels[co]}", avm, "detail", 2, is_cost=True))
    if alloc_rows:
        rows.append(mk(ALLOCATION_LABEL_CYR, alloc_total, "sub", 1, is_cost=True))
        rows.extend(alloc_rows)

    xvm = per_period(lambda d: -flt(d["internal_exp"]))
    if show_internal and not all_zero(xvm):
        rows.append(mk("(−) Ички харид харажати (элиминация)", xvm, "sub", 1, is_cost=True))

    # Kompaniyalar kesimida — taqsimotdan KEYIN
    for co in companies:
        vm = per_period(lambda d, c=co: _co_opex(d["companies"][c]))
        if not all_zero(vm):
            rows.append(mk(f"Операционные расходы {labels[co]}", vm, "sub", 1, is_cost=True))
    rows.append(divider())

    # ── ОПЕРАЦИОННАЯ ПРИБЫЛЬ ─────────────────────────────────────────────────
    rows.append(mk(
        "Операционная прибыль (компаниялар кесимида)",
        per_period(lambda d: sum(_co_profit(c) for c in d["companies"].values())),
        "root", 0,
    ))
    for co in companies:
        vm = per_period(lambda d, c=co: _co_profit(d["companies"][c]))
        if not all_zero(vm):
            rows.append(mk(f"Операционная прибыль {labels[co]}", vm, "detail", 1))
    rows.append(divider())

    rows.append(mk("Операционная прибыль", per_period(_total_profit), "result", 0))
    rows.append(mk("Рентабельность по операционной прибыли", pct(_total_profit, _total_revenue), "percent", 1))

    return rows
