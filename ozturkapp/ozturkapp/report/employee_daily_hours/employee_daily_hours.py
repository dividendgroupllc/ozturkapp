# Copyright (c) 2026, Ozturkapp
# License: MIT
"""
Kunlik Ish Vaqti Hisoboti (Employee Daily Hours Report)

Sodda va tushunarli format:
- Keldi/Ketdi vaqtlari
- Ish vaqti (soat:minut) = IN→OUT juftliklari yig'indisi
- Tanaffus vaqti (juftliklar orasidagi bo'shliq)
- Kunlik daromad

Hisob-kitob mantiqi: ozturkapp.ozturkapp.report.employee_hours_utils
"""

import frappe
from frappe import _
from frappe.utils import getdate, flt

from ozturkapp.ozturkapp.report.employee_hours_utils import (
    STATUS_MISSING_IN,
    STATUS_MISSING_OUT,
    STATUS_NO_LOG,
    STATUS_OK,
    STATUS_IN_PROGRESS,
    analyze_logs,
    fetch_logs,
    format_minutes,
    format_money,
    format_time,
    get_allowed_employees,
    get_day_result,
    get_holiday_dates,
    get_holiday_list,
    validate_employee,
)


def execute(filters=None):
    if not filters:
        filters = {}

    if not filters.get("date"):
        frappe.throw(_("Sanani tanlang"))

    # Agar xodim tanlanmagan bo'lsa - barcha xodimlar jadvali
    if not filters.get("employee"):
        return get_all_employees_report(filters)

    # Xodim tanlangan - batafsil hisobot
    validate_employee(filters.get("employee"))
    columns = get_columns()
    data = get_data(filters)

    return columns, data


def get_all_employees_columns():
    return [
        {"label": _("F.I.O"), "fieldname": "employee_name", "fieldtype": "Data", "width": 180},
        {"label": _("Lavozim"), "fieldname": "designation", "fieldtype": "Data", "width": 130},
        {"label": _("Keldi"), "fieldname": "first_in", "fieldtype": "Data", "width": 80},
        {"label": _("Ketdi"), "fieldname": "last_out", "fieldtype": "Data", "width": 100},
        {"label": _("Ishladi"), "fieldname": "worked", "fieldtype": "Data", "width": 80},
        {"label": _("Tanaffus"), "fieldname": "breaks", "fieldtype": "Data", "width": 80},
        {"label": _("Holat"), "fieldname": "status", "fieldtype": "Data", "width": 170},
    ]


def get_all_employees_report(filters):
    """Barcha xodimlar uchun kunlik hisobot - sodda jadval"""
    selected_date = getdate(filters.get("date"))
    columns = get_all_employees_columns()

    employees = get_allowed_employees(filters.get("company"))

    if not employees:
        frappe.msgprint(
            _("Tanlangan filial bo'yicha faol xodimlar topilmadi"),
            title=_("Ma'lumot yo'q"),
            indicator="orange",
        )
        return columns, []

    logs_by_employee = fetch_logs([e.name for e in employees], selected_date, selected_date)
    holiday_cache = {}

    data = []
    for emp in employees:
        analysis = analyze_logs(logs_by_employee.get(emp.name, []))
        result = get_day_result(analysis, selected_date)

        holiday_list = get_holiday_list(emp.holiday_list, emp.company)
        is_holiday = selected_date in get_holiday_dates(
            holiday_list, selected_date, selected_date, holiday_cache
        )

        first_in_str = format_time(result["first_in"], selected_date)
        if result["last_out"]:
            last_out_str = format_time(result["last_out"], selected_date)
        elif result["status"] in (STATUS_MISSING_OUT, STATUS_IN_PROGRESS):
            last_out_str = "?"
        else:
            last_out_str = "—"

        data.append({
            "employee_name": emp.employee_name,
            "designation": emp.designation or "—",
            "first_in": first_in_str,
            "last_out": last_out_str,
            "worked": format_minutes(result["worked_minutes"]) if result["worked_minutes"] > 0 else "—",
            "breaks": format_minutes(result["break_minutes"]) if result["break_minutes"] > 0 else "—",
            "status": get_status_text(result["status"], is_holiday),
        })

    return columns, data


def get_columns():
    """Keng ustunlar - biznes uchun qulay"""
    return [
        {
            "label": _("#"),
            "fieldname": "row_num",
            "fieldtype": "Data",
            "width": 60
        },
        {
            "label": _("Vaqt / Sarlavha"),
            "fieldname": "time",
            "fieldtype": "Data",
            "width": 150
        },
        {
            "label": _("Qiymat / Turi"),
            "fieldname": "log_type",
            "fieldtype": "Data",
            "width": 200
        },
        {
            "label": _("Izoh / Qo'shimcha"),
            "fieldname": "description",
            "fieldtype": "Data",
            "width": 280
        },
        {
            "label": _("Davomiylik"),
            "fieldname": "duration",
            "fieldtype": "Data",
            "width": 120
        }
    ]


def get_data(filters):
    employee = filters.get("employee")
    selected_date = getdate(filters.get("date"))

    # Xodim ma'lumotlari
    emp = frappe.db.get_value(
        "Employee",
        employee,
        ["employee_name", "hourly_rate", "designation", "company", "holiday_list"],
        as_dict=True
    ) or {}

    employee_name = emp.get("employee_name") or employee
    hourly_rate = flt(emp.get("hourly_rate") or 0)
    designation = emp.get("designation") or ""
    company = emp.get("company") or ""

    holiday_list = get_holiday_list(emp.get("holiday_list"), company)
    is_holiday = selected_date in get_holiday_dates(holiday_list, selected_date, selected_date)

    # Loglarni olish va tahlil
    logs = fetch_logs([employee], selected_date, selected_date).get(employee, [])
    result = get_day_result(analyze_logs(logs), selected_date)

    data = []

    # ═══════════════════════════════════════════════════════════════
    # SARLAVHA - ism, lavozim, filial alohida qatorlarda
    # ═══════════════════════════════════════════════════════════════
    data.append({
        "row_num": "👤",
        "time": "XODIM:",
        "log_type": employee_name,
        "description": f"📅 Sana: {selected_date.strftime('%d-%m-%Y')}",
        "duration": ""
    })

    # Lavozim qatori
    if designation:
        data.append({
            "row_num": "",
            "time": "💼 Lavozim:",
            "log_type": designation,
            "description": "",
            "duration": ""
        })

    # Filial qatori
    if company:
        data.append({
            "row_num": "",
            "time": "🏢 Filial:",
            "log_type": company,
            "description": "",
            "duration": ""
        })

    data.append({})  # Bo'sh qator

    if result["status"] == STATUS_NO_LOG:
        # Log yo'q
        data.append({
            "row_num": "🔵" if is_holiday else "⚠️",
            "time": "",
            "log_type": "DAM OLISH KUNI" if is_holiday else "LOG YO'Q",
            "description": "Bu sana uchun hech qanday kirish/chiqish qayd etilmagan",
            "duration": ""
        })
        return data

    # ═══════════════════════════════════════════════════════════════
    # HISOB-KITOB
    # ═══════════════════════════════════════════════════════════════
    first_in_str = format_time(result["first_in"], selected_date)
    if result["last_out"]:
        last_out_str = format_time(result["last_out"], selected_date)
    elif result["status"] in (STATUS_MISSING_OUT, STATUS_IN_PROGRESS):
        last_out_str = "?"
    else:
        last_out_str = "—"

    worked_str = format_minutes(result["worked_minutes"])
    break_str = format_minutes(result["break_minutes"]) if result["break_minutes"] > 0 else "—"

    # Daromad
    earnings = result["worked_minutes"] / 60.0 * hourly_rate

    # ═══════════════════════════════════════════════════════════════
    # XULOSA QATORI
    # ═══════════════════════════════════════════════════════════════
    data.append({
        "row_num": "📊",
        "time": "XULOSA",
        "log_type": "",
        "description": "",
        "duration": ""
    })

    data.append({
        "row_num": "",
        "time": "🟢 Keldi",
        "log_type": first_in_str,
        "description": f"🔴 Ketdi: {last_out_str}",
        "duration": ""
    })

    data.append({
        "row_num": "",
        "time": "⏱️ Ish vaqti",
        "log_type": worked_str,
        "description": f"☕ Tanaffus: {break_str}",
        "duration": ""
    })

    if hourly_rate > 0:
        data.append({
            "row_num": "",
            "time": "💰 Daromad",
            "log_type": format_money(earnings),
            "description": f"Stavka: {format_money(hourly_rate)}/soat",
            "duration": ""
        })

    # Status
    status_icon = "✅" if result["status"] == STATUS_OK else "⚠️"
    data.append({
        "row_num": "",
        "time": f"{status_icon} Holat",
        "log_type": get_status_text(result["status"], is_holiday),
        "description": "",
        "duration": ""
    })

    data.append({})  # Bo'sh qator

    # ═══════════════════════════════════════════════════════════════
    # LOGLAR RO'YXATI
    # ═══════════════════════════════════════════════════════════════
    events = result["events"]
    data.append({
        "row_num": "📋",
        "time": "LOGLAR",
        "log_type": f"({len(events)} ta)",
        "description": "",
        "duration": ""
    })

    data.append({
        "row_num": "#",
        "time": "Vaqt",
        "log_type": "Turi",
        "description": "Izoh",
        "duration": ""
    })

    prev = None
    for i, ev in enumerate(events, 1):
        description = get_log_description(ev["type"], ev["reason"])
        if ev["inferred"]:
            description = (description + " " if description else "") + "(turi taxminiy)"

        # Davomiylik: OUT - ishlagan qism, IN (OUT dan keyin) - tanaffus
        duration_str = ""
        if prev:
            minutes = int((ev["time"] - prev["time"]).total_seconds() // 60)
            if minutes > 0:
                if ev["type"] == "OUT" and prev["type"] == "IN":
                    duration_str = format_minutes(minutes)
                elif ev["type"] == "IN" and prev["type"] == "OUT":
                    duration_str = f"☕ {format_minutes(minutes)}"

        data.append({
            "row_num": str(i),
            "time": format_time(ev["time"], selected_date),
            "log_type": get_log_type_display(ev["type"], ev["reason"]),
            "description": description,
            "duration": duration_str
        })

        prev = ev

    return data


def get_log_type_display(log_type, reason):
    """Log turini chiroyli ko'rsatish"""
    if reason == "TEMP_OUT":
        return "🟠 CHIQDI (tanaffus)"
    elif reason == "RETURN":
        return "🟣 QAYTDI"
    elif log_type == "IN":
        return "🟢 KELDI"
    elif log_type == "OUT":
        return "🔴 KETDI"
    return log_type or ""


def get_log_description(log_type, reason):
    """Log uchun izoh"""
    if reason == "TEMP_OUT":
        return "Tanaffusga chiqdi"
    elif reason == "RETURN":
        return "Tanaffusdan qaytdi"
    elif log_type == "IN":
        return "Ishga keldi"
    elif log_type == "OUT":
        return "Ishdan ketdi"
    return ""


def get_status_text(status, is_holiday=False):
    """Status matnini o'zbek tilida"""
    if status == STATUS_NO_LOG:
        return "Dam olish" if is_holiday else "Log yo'q"
    status_map = {
        STATUS_OK: "Normada ✓",
        STATUS_MISSING_OUT: "Chiqish vaqti qayd etilmagan",
        STATUS_IN_PROGRESS: "Ishda (hali chiqmagan)",
        STATUS_MISSING_IN: "Kirish vaqti qayd etilmagan",
    }
    return status_map.get(status, status)
