# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa/oshxona sahifalari uchun chop etish API'si.

    print_bill(invoice)   -> mijoz chekini kassa printeriga navbatga qo'yadi
    reprint(job)          -> mavjud topshiriqni qayta chop etish
    test_print(printer)   -> sinov cheki
    get_status()          -> agent onlaynmi, navbatda nechta, printerlar
    open_drawer(reason)   -> «G'aladon» tugmasi: kassa g'aladonini ochish
    print_shift_report(kind) -> X/Z smena hisobotini chop etish

Printer sozlanmagan bo'lsa `print_bill` `{"queued": null, "reason": "no_printer"}`
qaytaradi. Sahifa bunda brauzer oynasini O'ZI OCHMAYDI — sababni kassirga
ko'rsatadi va brauzer yo'lini faqat kassir tanlasa ishlatadi
(`restaurant_cashier.js: printReceipt`).
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime

from ozturkapp.ozturkapp.setup import cashier_features
from ozturkapp.ozturkapp.utils import cashier_permissions, print_queue, shift_report

#: Bir foydalanuvchi bir daqiqada qo'lda (savdosiz) ochishi mumkin bo'lgan g'aladon
#: soni. Amalda qaytim uchun 1-2 marta; undan ko'pi printerni bosadi va smena
#: hisobotini axlatga to'ldiradi.
DRAWER_MAX_PER_MINUTE = 5

#: Holat/topshiriqlar ro'yxatini ko'ra oladigan rollar: kassa + oshxona ekrani.
DIAGNOSTIC_ROLES = (*cashier_permissions.CASHIER_ROLES, "URY Kitchen")


def _resolve_branch_for_user():
    """Kassir/oshxona/menejer uchun filial — `resolve_branch` orqali."""
    return cashier_permissions.resolve_branch()


def _diagnostic_branch(branch=None) -> str:
    """Holat va topshiriqlar ro'yxati uchun filial: rol shart, faqat O'Z filiali.

    Ilgari `branch` parametri tekshirilmasdi: rolsiz istalgan kirgan foydalanuvchi
    istalgan filialning topshiriqlari (chek raqamlari, xatolar) ro'yxatini o'qirdi.
    System Manager istalgan filialni ko'ra oladi.
    """
    roles = set(frappe.get_roles())
    if frappe.session.user == "Guest" or not roles.intersection(DIAGNOSTIC_ROLES):
        raise cashier_permissions.CashierPermissionError(_("Ruxsat yo'q"))

    own = _resolve_branch_for_user()
    if branch and branch != own and "System Manager" not in roles:
        raise cashier_permissions.CashierPermissionError(_("Bu filialga ruxsat yo'q"))
    return branch or own


@frappe.whitelist()
def print_bill(invoice):
    """Mijoz chekini navbatga qo'yish (giveBill, reprint, split).

    HAR BIR CHAQIRUV LOGGA YOZILADI (`logs/ozturk_print.log`). Sababi:
    kassa sahifasi nosozlikni jimgina yutib yuborgan holat bo'lgan —
    chek chiqmasdi, Error Log esa bo'sh qolardi va so'rov serverga
    yetib kelgan-kelmagani ham noma'lum edi. Log shu savolga javob
    beradi: yozuv bor bo'lsa muammo serverda, yo'q bo'lsa — brauzerda.
    """
    logger = frappe.logger("ozturk_print")
    logger.info("print_bill: invoice=%s user=%s", invoice, frappe.session.user)

    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    # docstatus tekshirilmaydi: to'langan (submit) chekni ham qayta chop etish mumkin.
    cashier_permissions.assert_invoice_in_scope(invoice, scope)

    job = print_queue.enqueue_bill(invoice, scope)
    if not job:
        logger.warning("print_bill: kassa printeri topilmadi (filial=%s)", scope.branch)
        return {"queued": None, "reason": "no_printer"}
    agent = print_queue.agent_status(scope.branch)
    logger.info("print_bill: %s -> %s (agent_online=%s)", invoice, job, agent["online"])
    return {"queued": job, "agent_online": agent["online"]}


@frappe.whitelist()
def reprint(job):
    """Topshiriqni qayta navbatga qo'yish (Failed yoki Done bo'lsa ham).

    Faqat kassa rollari: rolsiz foydalanuvchi bu orqali kassa printeriga chek
    chiqara olmasligi kerak. G'aladon va hisobot topshiriqlari `requeue` da rad etiladi.
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    job_branch = frappe.db.get_value("Ozturk Print Job", job, "branch")
    if not job_branch:
        frappe.throw(_("Topshiriq topilmadi: {0}").format(job))
    if job_branch != scope.branch and "System Manager" not in frappe.get_roles():
        frappe.throw(_("Bu topshiriq boshqa filialga tegishli"), frappe.PermissionError)
    return {"queued": print_queue.requeue(job)}


@frappe.whitelist(methods=["POST"])
def open_drawer(reason="manual"):
    """«G'aladon» tugmasi — savdosiz qo'lda ochish.

    Naqd to'lovdan keyingi ochilish alohida (`billing` -> `kick_drawer`);
    bu yerdagi ochilish HECH QANDAY chekka bog'lanmaydi, shuning uchun
    firibgarlikka eng moyil amal: kim va nima uchun ochgani topshiriqda
    (`Ozturk Print Job`: `owner`, `creation`, `reason`) saqlanadi va smena
    hisobotida «G'aladon (savdosiz)» sifatida SANALADI.

    Args:
        reason: ochish sababi. Bo'sh qoldirilsa «manual».
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_features.assert_enabled(scope.pos_profile, "cash_drawer")
    # Smenasiz g'aladon ochish hech qaysi hisobotga tushmaydi — kim ochganini
    # keyin aniqlab bo'lmasligi mumkin.
    cashier_permissions.assert_shift_open(scope)

    # Boshqaruv belgilari sababda bo'lmasin: u hisobotga bosiladi va ro'yxatda chiqadi.
    reason = "".join(ch for ch in str(reason or "") if ch.isprintable()).strip()[:140] or "manual"
    if not print_queue.cashier_printer(scope.branch):
        return {"queued": None, "reason": "no_printer"}

    _assert_drawer_rate(scope)

    job = print_queue.kick_drawer(scope, reason=reason)
    if not job:
        return {"queued": None, "reason": "failed"}
    return {"queued": job, "agent_online": print_queue.agent_status(scope.branch)["online"]}


def _assert_drawer_rate(scope):
    """Bir foydalanuvchi daqiqada `DRAWER_MAX_PER_MINUTE` martadan ko'p qo'lda ocha olmaydi.

    Naqd to'lovdan keyingi ochilish (chekka bog'liq, `ref_name` bor) sanalmaydi —
    u savdoning o'zi bilan cheklangan.
    """
    recent = frappe.db.count(
        "Ozturk Print Job",
        {
            "job_type": print_queue.JOB_DRAWER,
            "branch": scope.branch,
            "owner": frappe.session.user,
            "ref_name": ["is", "not set"],
            "creation": [">", add_to_date(now_datetime(), seconds=-60)],
        },
    )
    if recent >= DRAWER_MAX_PER_MINUTE:
        frappe.throw(
            _("G'aladon juda ko'p ochildi. Bir daqiqadan keyin urinib ko'ring."),
            title=_("Juda tez-tez"),
        )


@frappe.whitelist(methods=["POST"])
def print_shift_report(kind):
    """X (oraliq) yoki Z (oxirgi yopilgan smena) hisobotini kassa printeriga chiqarish.

    Hisobot `shift_report.build_report()` dan keladi — ko'r sanoq qoidasi
    (kassir kutilgan summani va savdo jamini ko'rmaydi) qog'ozga ham
    o'tadi, chunki qog'ozni kassirning o'zi o'qiydi.
    """
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    cashier_features.assert_enabled(scope.pos_profile, "shift_reports")

    report = shift_report.build_report(kind, scope)
    job = print_queue.enqueue_shift_report(report, scope)
    if not job:
        frappe.logger("ozturk_print").warning(
            "print_shift_report: kassa printeri topilmadi (filial=%s)", scope.branch
        )
        return {"queued": None, "reason": "no_printer", "kind": report["kind"]}
    return {
        "queued": job,
        "kind": report["kind"],
        "agent_online": print_queue.agent_status(scope.branch)["online"],
    }


@frappe.whitelist()
def test_print(printer):
    """Sinov cheki — printer sozlamasini tekshirish (faqat o'z filiali printeri)."""
    cashier_permissions.require_cashier()
    scope = cashier_permissions.resolve_scope()
    printer_branch = frappe.db.get_value("Ozturk Printer", printer, "branch")
    if printer_branch and printer_branch != scope.branch and "System Manager" not in frappe.get_roles():
        frappe.throw(_("Bu printer boshqa filialga tegishli"), frappe.PermissionError)
    label = f"Yubordi: {frappe.db.get_value('User', frappe.session.user, 'full_name') or frappe.session.user}"
    return {"queued": print_queue.enqueue_test(printer, label)}


@frappe.whitelist()
def get_status(branch=None):
    """Kassa sahifasi uchun: agent holati, navbat, printerlar."""
    branch = _diagnostic_branch(branch)
    agent = print_queue.agent_status(branch)
    summary = print_queue.queue_summary(branch)
    printers = [
        {"name": p["name"], "role": p["role"], "production_unit": p["production_unit"],
         "host": p["ip_address"]}
        for p in print_queue.get_printers(branch)
    ]
    return {
        "branch": branch,
        "agent_online": agent["online"],
        "agent": agent.get("agent"),
        "agent_seen_at": agent.get("at"),
        "pending": summary["pending"],
        "failed": summary["failed"],
        "printers": printers,
        "has_cashier_printer": any(p["role"] == print_queue.ROLE_CASHIER for p in printers),
    }


@frappe.whitelist()
def recent_jobs(branch=None, limit=20):
    """Oxirgi topshiriqlar (kassa oynasidagi 'Printer' paneli uchun)."""
    branch = _diagnostic_branch(branch)
    return frappe.get_all(
        "Ozturk Print Job",
        filters={"branch": branch},
        fields=["name", "title", "job_type", "printer", "status", "attempts", "error",
                "creation", "printed_at"],
        order_by="creation desc",
        limit=min(max(cint(limit) or 20, 1), 100),
    )
