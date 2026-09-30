# Copyright (c) 2026, Ozturkapp
# License: MIT
"""
Ish vaqti hisobotlari uchun umumiy mantiq
(Employee Daily Hours va Employee Period Hours bir xil hisoblashi uchun).

Qoidalar:
- Bo'sh log_type: xronologik tartibda IN/OUT almashinuvi bilan aniqlanadi.
- Ish vaqti = IN→OUT juftliklari yig'indisi; juftliklar orasidagi bo'shliq = tanaffus
  (checkin_reason TEMP_OUT/RETURN ham qo'llab-quvvatlanadi).
- Smena birinchi IN sanasiga tegishli; OUT faqat IN dan keyin bo'lishi mumkin;
  tungi smena mumkin, lekin smena uzunligi MAX_SHIFT_HOURS bilan cheklangan.
"""

from datetime import datetime, timedelta, time as dt_time

import frappe
from frappe import _
from frappe.utils import add_days, fmt_money, getdate, now_datetime

from ozturkapp.ozturkapp.utils.helpers import has_full_hr_access

MAX_SHIFT_HOURS = 16  # smenaning maksimal uzunligi (birinchi IN dan)
MAX_BREAK_HOURS = 4  # OUT dan keyingi IN shu vaqt ichida bo'lsa - tanaffus, aks holda yangi smena

STATUS_OK = "OK"
STATUS_MISSING_OUT = "MISSING_OUT"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_MISSING_IN = "MISSING_IN"
STATUS_NO_LOG = "NO_LOG"


# ═══════════════════════════════════════════════════════════════
# XODIMLAR / RUXSAT
# ═══════════════════════════════════════════════════════════════
def get_allowed_employees(company=None, extra_fields=None):
    """Faol xodimlar ro'yxati (filial manager faqat o'z filialini ko'radi)."""
    user = frappe.session.user
    full_access = has_full_hr_access(user)
    emp_filters = {"status": "Active"}

    if not full_access:
        user_companies = frappe.get_all(
            "User Permission",
            filters={"user": user, "allow": "Company"},
            pluck="for_value",
        )
        if user_companies:
            if company and company in user_companies:
                emp_filters["company"] = company
            else:
                emp_filters["company"] = ["in", user_companies]
        elif company:
            emp_filters["company"] = company
    elif company:
        emp_filters["company"] = company

    fields = ["name", "employee_name", "designation", "company", "holiday_list"]
    fields += [f for f in (extra_fields or []) if f not in fields]

    return frappe.get_list(
        "Employee",
        filters=emp_filters,
        fields=fields,
        order_by="employee_name",
        ignore_permissions=full_access,
    )


def validate_employee(employee):
    """Xodim mavjudligi va ruxsatni tekshirish."""
    if not frappe.db.exists("Employee", employee):
        frappe.throw(_("Xodim topilmadi: {0}").format(employee), title=_("Xodim topilmadi"))
    if not has_full_hr_access() and not frappe.has_permission("Employee", "read", employee):
        frappe.throw(_("Bu xodim ma'lumotlarini ko'rishga ruxsatingiz yo'q"), frappe.PermissionError)


# ═══════════════════════════════════════════════════════════════
# LOGLAR
# ═══════════════════════════════════════════════════════════════
def fetch_logs(employees, from_date, to_date):
    """Xodimlar loglari: {employee: [log, ...]} (vaqt bo'yicha tartiblangan).

    Oyna: from_date - 1 kun (oldingi tungi smena dumini to'g'ri bog'lash uchun)
    dan to_date + 2 kun gacha (tungi smena OUT ni topish uchun).
    """
    employees = [e for e in (employees or []) if e]
    if not employees:
        return {}

    start = datetime.combine(add_days(getdate(from_date), -1), dt_time.min)
    end = datetime.combine(add_days(getdate(to_date), 2), dt_time.min)

    rows = frappe.db.sql(
        """
        SELECT name, employee, time, log_type, checkin_reason
        FROM `tabEmployee Checkin`
        WHERE employee IN %(employees)s
          AND time >= %(start)s
          AND time < %(end)s
        ORDER BY employee, time ASC, creation ASC
        """,
        {"employees": tuple(employees), "start": start, "end": end},
        as_dict=True,
    )

    logs_by_employee = {}
    for row in rows:
        logs_by_employee.setdefault(row.employee, []).append(row)
    return logs_by_employee


def normalize_logs(logs):
    """Loglarni hodisalarga aylantirish: type (IN/OUT), reason, inferred."""
    cap = timedelta(hours=MAX_SHIFT_HOURS)
    events = []
    prev = None

    for log in logs:
        log_type = (log.get("log_type") or "").strip().upper()
        reason = (log.get("checkin_reason") or "").strip().upper()
        inferred = False

        if reason == "TEMP_OUT":
            typ = "OUT"
        elif reason == "RETURN":
            typ = "IN"
        elif log_type in ("IN", "OUT"):
            typ = log_type
        elif reason in ("IN", "OUT"):
            typ = reason
        else:
            # Bo'sh log_type - almashinuv bo'yicha aniqlash
            inferred = True
            if prev and prev["type"] == "IN" and (log.time - prev["time"]) <= cap:
                typ = "OUT"
            else:
                typ = "IN"

        event = {
            "name": log.get("name"),
            "time": log.time,
            "type": typ,
            "reason": reason or typ,
            "inferred": inferred,
        }
        events.append(event)
        prev = event

    return events


def _new_shift(event):
    return {
        "start": event["time"],
        "open_in": event["time"],
        "last_out": None,
        "segments": [],
        "events": [event],
    }


def _finish_shift(shift, now):
    cap = timedelta(hours=MAX_SHIFT_HOURS)
    worked_seconds = sum((end - start).total_seconds() for start, end in shift["segments"])
    break_seconds = 0
    for prev_seg, next_seg in zip(shift["segments"], shift["segments"][1:]):
        break_seconds += max(0, (next_seg[0] - prev_seg[1]).total_seconds())

    if shift["open_in"] is not None:
        if shift["start"] <= now and now - shift["start"] <= cap:
            status = STATUS_IN_PROGRESS
        else:
            status = STATUS_MISSING_OUT
    else:
        status = STATUS_OK

    return {
        "date": shift["start"].date(),
        "first_in": shift["start"],
        "last_out": shift["last_out"] if status == STATUS_OK else None,
        "worked_minutes": int(worked_seconds // 60),
        "break_minutes": int(break_seconds // 60),
        "status": status,
        "events": shift["events"],
    }


def build_shifts(events, now=None):
    """Hodisalardan smenalar va 'yetim' OUT larni (oldidan IN yo'q) yig'ish."""
    now = now or now_datetime()
    cap = timedelta(hours=MAX_SHIFT_HOURS)
    max_break = timedelta(hours=MAX_BREAK_HOURS)

    shifts = []
    orphans = []
    cur = None

    for ev in events:
        t = ev["time"]

        # Smena limitdan oshdi - yopamiz
        if cur and t - cur["start"] > cap:
            shifts.append(_finish_shift(cur, now))
            cur = None

        if ev["type"] == "IN":
            if cur is None:
                cur = _new_shift(ev)
            elif cur["open_in"] is not None:
                # Ketma-ket IN - takroriy, e'tiborsiz (ro'yxatda ko'rinadi)
                cur["events"].append(ev)
            elif t - cur["last_out"] <= max_break:
                # Tanaffusdan qaytdi
                cur["open_in"] = t
                cur["events"].append(ev)
            else:
                shifts.append(_finish_shift(cur, now))
                cur = _new_shift(ev)
        else:  # OUT
            if cur is None:
                orphans.append(ev)
            elif cur["open_in"] is not None:
                cur["segments"].append([cur["open_in"], t])
                cur["open_in"] = None
                cur["last_out"] = t
                cur["events"].append(ev)
            elif t - cur["last_out"] <= max_break:
                # Ketma-ket OUT - oxirgisi haqiqiy chiqish
                cur["segments"][-1][1] = t
                cur["last_out"] = t
                cur["events"].append(ev)
            else:
                shifts.append(_finish_shift(cur, now))
                cur = None
                orphans.append(ev)

    if cur:
        shifts.append(_finish_shift(cur, now))

    return shifts, orphans


def analyze_logs(logs, now=None):
    """Loglarni tahlil qilish: {date: {"shifts": [...], "orphans": [...]}}"""
    shifts, orphans = build_shifts(normalize_logs(logs), now=now)
    by_date = {}
    for s in shifts:
        by_date.setdefault(s["date"], {"shifts": [], "orphans": []})["shifts"].append(s)
    for o in orphans:
        by_date.setdefault(o["time"].date(), {"shifts": [], "orphans": []})["orphans"].append(o)
    return by_date


def get_day_result(analysis, selected_date):
    """Bitta kun natijasi (smena birinchi IN sanasiga tegishli)."""
    selected_date = getdate(selected_date)
    bucket = analysis.get(selected_date) or {"shifts": [], "orphans": []}
    shifts = bucket["shifts"]
    orphans = bucket["orphans"]

    result = {
        "first_in": None,
        "last_out": None,
        "worked_minutes": 0,
        "break_minutes": 0,
        "status": STATUS_NO_LOG,
        "events": [],
    }

    events = [ev for s in shifts for ev in s["events"]] + list(orphans)
    result["events"] = sorted(events, key=lambda e: e["time"])

    if shifts:
        result["first_in"] = shifts[0]["first_in"]
        result["worked_minutes"] = sum(s["worked_minutes"] for s in shifts)
        result["break_minutes"] = sum(s["break_minutes"] for s in shifts)
        statuses = [s["status"] for s in shifts]
        if STATUS_MISSING_OUT in statuses:
            result["status"] = STATUS_MISSING_OUT
        elif STATUS_IN_PROGRESS in statuses:
            result["status"] = STATUS_IN_PROGRESS
        else:
            result["status"] = STATUS_OK
        result["last_out"] = shifts[-1]["last_out"]
    elif orphans:
        result["last_out"] = orphans[-1]["time"]
        result["status"] = STATUS_MISSING_IN

    return result


# ═══════════════════════════════════════════════════════════════
# DAM OLISH KUNLARI / VALYUTA / FORMAT
# ═══════════════════════════════════════════════════════════════
def get_holiday_list(employee_holiday_list=None, company=None):
    if employee_holiday_list:
        return employee_holiday_list
    if company:
        return frappe.get_cached_value("Company", company, "default_holiday_list")
    return None


def get_holiday_dates(holiday_list, from_date, to_date, _cache=None):
    """Dam olish kunlari to'plami (holiday list bo'lmasa - bo'sh, ya'ni har kun ish kuni)."""
    if not holiday_list:
        return set()
    if _cache is not None and holiday_list in _cache:
        return _cache[holiday_list]
    dates = frappe.db.sql_list(
        """
        SELECT holiday_date FROM `tabHoliday`
        WHERE parent = %s AND parenttype = 'Holiday List'
          AND holiday_date BETWEEN %s AND %s
        """,
        (holiday_list, getdate(from_date), getdate(to_date)),
    )
    result = {getdate(d) for d in dates}
    if _cache is not None:
        _cache[holiday_list] = result
    return result


def get_currency(company=None):
    currency = None
    if company:
        currency = frappe.get_cached_value("Company", company, "default_currency")
    return currency or frappe.defaults.get_global_default("currency") or "UZS"


def format_money(amount, currency):
    return f"{fmt_money(amount or 0, precision=0)} {currency}"


def format_minutes(minutes):
    """Minutlarni HH:MM formatga o'girish"""
    minutes = int(minutes or 0)
    if minutes <= 0:
        return "00:00"
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def format_time(dt, ref_date):
    """Vaqt; boshqa kunga tegishli bo'lsa sana bilan."""
    if not dt:
        return "—"
    if dt.date() != getdate(ref_date):
        return dt.strftime("%d-%m %H:%M")
    return dt.strftime("%H:%M")
