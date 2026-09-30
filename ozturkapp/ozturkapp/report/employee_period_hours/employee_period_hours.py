# Copyright (c) 2026, Ozturkapp
# License: MIT
"""
Davriy Ish Vaqti Hisoboti (Employee Period Hours Report)

Xodimning tanlangan davr uchun kunlik ish vaqti va maosh hisoboti
+ Designation (lavozim) ko'rsatiladi
+ Company filter faqat admin uchun
+ Dam olish kunlari - Holiday List bo'yicha (bo'lmasa har kun ish kuni)

Hisob-kitob mantiqi: ozturkapp.ozturkapp.report.employee_hours_utils
"""

import frappe
from frappe import _
from frappe.utils import getdate, add_days, date_diff, flt

from ozturkapp.ozturkapp.report.employee_hours_utils import (
    STATUS_IN_PROGRESS,
    STATUS_MISSING_IN,
    STATUS_MISSING_OUT,
    STATUS_NO_LOG,
    STATUS_OK,
    analyze_logs,
    fetch_logs,
    format_minutes,
    format_money,
    get_allowed_employees,
    get_currency,
    get_day_result,
    get_holiday_dates,
    get_holiday_list,
    validate_employee,
)


def execute(filters=None):
    if not filters:
        filters = {}

    if not filters.get("from_date"):
        frappe.throw(_("Boshlanish sanasini tanlang"))
    if not filters.get("to_date"):
        frappe.throw(_("Tugash sanasini tanlang"))

    # Agar xodim tanlanmagan bo'lsa - barcha xodimlar jadvali (har kun ustunda)
    if not filters.get("employee"):
        columns, data = get_all_employees_report(filters)
        return columns, data, None, None, None

    # Xodim tanlangan - batafsil hisobot
    validate_employee(filters.get("employee"))
    columns = get_columns()
    data, report_summary, chart = get_data(filters)

    return columns, data, None, chart, report_summary


def get_date_list(from_date, to_date):
    dates = []
    current_date = from_date
    while current_date <= to_date:
        dates.append(current_date)
        current_date = add_days(current_date, 1)
    return dates


def get_date_label(d, from_date, to_date):
    """Davr bir necha oyni qamrasa - oy ham ko'rsatiladi"""
    if (from_date.year, from_date.month) != (to_date.year, to_date.month):
        return d.strftime("%d.%m")
    return d.strftime("%d")


def get_all_employees_report(filters):
    """Barcha xodimlar uchun davriy hisobot - har kun alohida ustun"""
    from_date = getdate(filters.get("from_date"))
    to_date = getdate(filters.get("to_date"))

    # Validatsiya
    if from_date > to_date:
        frappe.throw(_("Boshlanish sanasi tugash sanasidan keyin bo'lishi mumkin emas"))

    days_count = date_diff(to_date, from_date) + 1
    if days_count > 31:
        frappe.throw(_("Maksimum 31 kun tanlash mumkin (jadval uchun)"))

    # Ustunlarni yaratish
    columns = [
        {"label": _("F.I.O"), "fieldname": "employee_name", "fieldtype": "Data", "width": 160},
    ]

    dates = []
    for current_date in get_date_list(from_date, to_date):
        date_key = current_date.strftime("%Y%m%d")
        date_str = current_date.strftime("%d.%m")
        dates.append({"date": current_date, "key": date_key, "label": date_str})
        columns.append({
            "label": date_str,
            "fieldname": f"d_{date_key}",
            "fieldtype": "Data",
            "width": 120,
        })

    # Jami ustuni
    columns.append({"label": _("Jami"), "fieldname": "total_hours", "fieldtype": "Data", "width": 80})

    employees = get_allowed_employees(filters.get("company"))

    if not employees:
        frappe.msgprint(
            _("Tanlangan filial bo'yicha faol xodimlar topilmadi"),
            title=_("Ma'lumot yo'q"),
            indicator="orange",
        )
        return columns, []

    # Faqat tanlangan xodimlar loglari (bir so'rovda), xodim bo'yicha guruhlangan
    logs_by_employee = fetch_logs([e.name for e in employees], from_date, to_date)
    holiday_cache = {}

    data = []

    for emp in employees:
        analysis = analyze_logs(logs_by_employee.get(emp.name, []))
        holidays = get_holiday_dates(
            get_holiday_list(emp.holiday_list, emp.company), from_date, to_date, holiday_cache
        )

        row = {
            "employee_name": emp.employee_name,
        }

        total_worked = 0

        # Har bir kun uchun
        for d in dates:
            day_result = get_day_result(analysis, d["date"])
            row[f"d_{d['key']}"] = format_matrix_cell(day_result, d["date"], d["date"] in holidays)
            total_worked += day_result["worked_minutes"]

        row["total_hours"] = format_minutes(total_worked) if total_worked > 0 else "—"
        data.append(row)

    return columns, data


def format_matrix_cell(day_result, day, is_holiday):
    """Jadval katakchasi: "08:30-17:45", "22:00-06:00 (+1)", "09:00-?", "?-18:00", "—", "Dam" """
    status = day_result["status"]
    first_in = day_result["first_in"].strftime("%H:%M") if day_result["first_in"] else "?"

    if status == STATUS_OK:
        lo = day_result["last_out"]
        last_out = lo.strftime("%H:%M")
        if lo.date() != day:
            last_out += f" (+{(lo.date() - day).days})"
        return f"{first_in}-{last_out}"
    if status in (STATUS_MISSING_OUT, STATUS_IN_PROGRESS):
        return f"{first_in}-?"
    if status == STATUS_MISSING_IN:
        return f"?-{day_result['last_out'].strftime('%H:%M')}"
    return "Dam" if is_holiday else "—"


def get_columns():
    """Sodda ustunlar"""
    return [
        {
            "label": _("Sana"),
            "fieldname": "date",
            "fieldtype": "Date",
            "width": 100
        },
        {
            "label": _("Kun"),
            "fieldname": "day_name",
            "fieldtype": "Data",
            "width": 90
        },
        {
            "label": _("Keldi"),
            "fieldname": "first_in",
            "fieldtype": "Data",
            "width": 80
        },
        {
            "label": _("Ketdi"),
            "fieldname": "last_out",
            "fieldtype": "Data",
            "width": 80
        },
        {
            "label": _("Ishladi"),
            "fieldname": "worked",
            "fieldtype": "Data",
            "width": 80
        },
        {
            "label": _("Tanaffus"),
            "fieldname": "breaks",
            "fieldtype": "Data",
            "width": 80
        },
        {
            "label": _("Maosh"),
            "fieldname": "earnings",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 120
        },
        {
            "label": _("Holat"),
            "fieldname": "status",
            "fieldtype": "Data",
            "width": 140
        }
    ]


def get_data(filters):
    employee = filters.get("employee")
    from_date = getdate(filters.get("from_date"))
    to_date = getdate(filters.get("to_date"))

    # Validatsiya
    if from_date > to_date:
        frappe.throw(_("Boshlanish sanasi tugash sanasidan keyin bo'lishi mumkin emas"))

    if date_diff(to_date, from_date) > 62:
        frappe.throw(_("Maksimum 2 oy (62 kun) tanlash mumkin"))

    # Xodim ma'lumotlari
    emp = frappe.db.get_value(
        "Employee",
        employee,
        ["employee_name", "designation", "hourly_rate", "company", "holiday_list"],
        as_dict=True
    ) or {}

    employee_name = emp.get("employee_name") or employee
    designation = emp.get("designation") or ""
    hourly_rate = flt(emp.get("hourly_rate") or 0)
    company = emp.get("company") or ""
    currency = get_currency(company)

    holidays = get_holiday_dates(
        get_holiday_list(emp.get("holiday_list"), company), from_date, to_date
    )

    # Loglarni olish va tahlil (kunlik hisobot bilan bir xil mantiq)
    logs = fetch_logs([employee], from_date, to_date).get(employee, [])
    analysis = analyze_logs(logs)

    # Kun nomlari
    day_names = {
        0: "Dushanba",
        1: "Seshanba",
        2: "Chorshanba",
        3: "Payshanba",
        4: "Juma",
        5: "Shanba",
        6: "Yakshanba"
    }

    data = []
    total_worked = 0
    total_breaks = 0
    total_earnings = 0.0
    days_worked = 0
    working_days = 0

    # Chart uchun ma'lumotlar
    chart_labels = []
    chart_worked = []

    for current_date in get_date_list(from_date, to_date):
        day_result = get_day_result(analysis, current_date)

        day_name = day_names.get(current_date.weekday(), "")
        is_holiday = current_date in holidays
        if not is_holiday:
            working_days += 1

        # Keldi vaqti
        first_in_str = day_result["first_in"].strftime("%H:%M") if day_result["first_in"] else "—"

        # Ketdi vaqti
        if day_result["last_out"]:
            lo = day_result["last_out"]
            last_out_str = lo.strftime("%d-%m %H:%M") if lo.date() != current_date else lo.strftime("%H:%M")
        elif day_result["status"] in (STATUS_MISSING_OUT, STATUS_IN_PROGRESS):
            last_out_str = "?"
        else:
            last_out_str = "—"

        worked_minutes = day_result["worked_minutes"]
        worked_str = format_minutes(worked_minutes) if worked_minutes > 0 else "—"
        breaks_str = format_minutes(day_result["break_minutes"]) if day_result["break_minutes"] > 0 else "—"

        # Kunlik maosh
        daily_earnings = worked_minutes / 60.0 * hourly_rate

        row = {
            "date": current_date,
            "day_name": day_name,
            "first_in": first_in_str,
            "last_out": last_out_str,
            "worked": worked_str,
            "breaks": breaks_str,
            "earnings": daily_earnings if daily_earnings > 0 else None,
            "currency": currency,
            "status": get_status_display(day_result["status"], is_holiday),
            "is_holiday": is_holiday,
            "worked_minutes": worked_minutes
        }

        data.append(row)

        # Jami hisob
        if worked_minutes > 0:
            total_worked += worked_minutes
            total_breaks += day_result["break_minutes"]
            total_earnings += daily_earnings
            days_worked += 1

        # Chart uchun
        chart_labels.append(get_date_label(current_date, from_date, to_date))
        chart_worked.append(round(worked_minutes / 60, 1))

    # Bo'sh qator
    data.append({})

    # JAMI qatori
    data.append({
        "date": None,
        "day_name": "📊 JAMI:",
        "first_in": f"{days_worked} kun",
        "last_out": "",
        "worked": format_minutes(total_worked),
        "breaks": format_minutes(total_breaks) if total_breaks > 0 else "—",
        "earnings": total_earnings,
        "currency": currency,
        "status": "",
        "is_total": True
    })

    # O'rtacha
    avg_worked = total_worked / days_worked if days_worked > 0 else 0
    avg_earnings = total_earnings / days_worked if days_worked > 0 else 0

    data.append({
        "date": None,
        "day_name": "📈 O'rtacha:",
        "first_in": "",
        "last_out": "",
        "worked": format_minutes(int(avg_worked)),
        "breaks": "",
        "earnings": avg_earnings if avg_earnings > 0 else None,
        "currency": currency,
        "status": "/kun",
        "is_total": True
    })

    # Report summary (yuqorida ko'rinadi)
    report_summary = [
        {
            "label": _("Xodim"),
            "value": employee_name,
            "datatype": "Data",
            "indicator": "blue"
        },
        {
            "label": _("Lavozim"),
            "value": designation or "—",
            "datatype": "Data"
        },
        {
            "label": _("Filial"),
            "value": company or "—",
            "datatype": "Data"
        },
        {
            "label": _("Davr"),
            "value": f"{from_date.strftime('%d-%m')} — {to_date.strftime('%d-%m-%Y')}",
            "datatype": "Data"
        },
        {
            "label": _("Ishlagan kunlar"),
            "value": f"{days_worked} / {working_days}",
            "datatype": "Data",
            "indicator": "green" if days_worked >= working_days * 0.8 else "orange"
        },
        {
            "label": _("Jami soat"),
            "value": format_minutes(total_worked),
            "datatype": "Data",
            "indicator": "blue"
        },
        {
            "label": _("Soatlik stavka"),
            "value": format_money(hourly_rate),
            "datatype": "Data"
        },
        {
            "label": _("💰 JAMI MAOSH"),
            "value": format_money(total_earnings),
            "datatype": "Data",
            "indicator": "green"
        }
    ]

    # Chart
    chart = {
        "data": {
            "labels": chart_labels,
            "datasets": [
                {
                    "name": _("Ishlagan soat"),
                    "values": chart_worked
                }
            ]
        },
        "type": "bar",
        "colors": ["#5e64ff"],
        "barOptions": {
            "spaceRatio": 0.3
        },
        "height": 200
    }

    return data, report_summary, chart


def get_status_display(status, is_holiday=False):
    """Holatni o'zbek tilida"""
    if status == STATUS_OK:
        return "✅ Normada"
    elif status == STATUS_MISSING_OUT:
        return "⚠️ Chiqmagan"
    elif status == STATUS_IN_PROGRESS:
        return "🔵 Ishda"
    elif status == STATUS_MISSING_IN:
        return "⚠️ Kelmagan"
    elif status == STATUS_NO_LOG:
        if is_holiday:
            return "🔵 Dam olish"
        return "⬜ Log yo'q"
    return status
