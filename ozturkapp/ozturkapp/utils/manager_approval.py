# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Menejer tasdig'i (PIN) — kassir o'zi qila olmaydigan amallar uchun.

QAYSI AMALLAR
=============
chegirma chegarasidan oshsa, to'langan chekni qaytarish, kassadan katta
summa chiqarish. Qoida har amalning O'Z modulida; bu modul faqat "kim
tasdiqladi" savoliga javob beradi.

OQIM
====
    1. Kassir amalni yuboradi (tasdiqsiz).
    2. Server `require()` da `ApprovalRequired` tashlaydi.
    3. Frontend menejerlar ro'yxatini (`api/approval.get_approvers`) ko'rsatadi:
       menejer ismini bosadi va PIN kiritadi.
    4. Frontend AYNI amalni `approval={"user": ..., "pin": ...}` bilan qayta
       yuboradi; `require()` PIN'ni tekshirib, tasdiqlagan menejerni qaytaradi.

Ikki bosqichli token YO'Q: PIN va amal bitta so'rovda keladi, ya'ni tasdiq
boshqa amalga "ko'chirilib" ishlatilmaydi.

Menejer o'zi kassa oynasida ishlayotgan bo'lsa (URY Manager / System Manager)
PIN so'ralmaydi — u allaqachon o'sha huquqqa ega.

XAVFSIZLIK
==========
  - PIN `User.custom_pos_pin` (Password maydoni) da SHIFRLANGAN saqlanadi.
  - Solishtirish `hmac.compare_digest` bilan (vaqt bo'yicha sizib chiqmaydi).
  - Noto'g'ri urinishlar menejer BOSHIGA sanaladi: `MAX_ATTEMPTS` dan keyin
    `LOCK_SECONDS` davomida bloklanadi (kassir PIN'ni terib topa olmasin).
  - Shuningdek SO'RASHGA HAM sanaladi (`REQUESTER_MAX_ATTEMPTS`): faqat menejer
    boshiga sanalsa, kassir har menejerga 5 tadan taxmin qilib urinishlarni
    menejerlar soniga ko'paytirardi.
  - Har bir tasdiq (va rad etilgan urinish) logga, tasdiq esa hujjat
    tarixiga (Comment) yoziladi.
"""

import hmac
import json
import re

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils.password import get_decrypted_password

from ozturkapp.ozturkapp.utils import cashier_permissions

PIN_FIELD = "custom_pos_pin"
#: Faqat ASCII raqamlar: `\d` Unicode raqamlarni (masalan arab-hind) ham qabul qilardi.
PIN_PATTERN = re.compile(r"^[0-9]{4,8}$")

MAX_ATTEMPTS = 5
LOCK_SECONDS = 600

#: Bitta kassir (so'rovchi) barcha menejerlar bo'yicha jami shuncha noto'g'ri
#: urinish qila oladi (`LOCK_SECONDS` oynasida). 4 xonali PIN uchun bu
#: 10 000 dan 10 ta taxmin — oynada 0.1%.
REQUESTER_MAX_ATTEMPTS = 10

PIN_CUSTOM_FIELD = {
    "User": [
        {
            "fieldname": PIN_FIELD,
            "label": "POS PIN (menejer tasdig'i)",
            "fieldtype": "Password",
            "length": 32,
            "no_copy": 1,
            "insert_after": "last_name",
            "description": (
                "4–8 raqam. Kassir chegirma chegarasidan oshganda, chekni "
                "qaytarganda yoki kassadan katta summa chiqarganda menejer shu "
                "kod bilan tasdiqlaydi."
            ),
        }
    ]
}


class ApprovalRequired(frappe.ValidationError):
    """Menejer tasdig'i kerak (yoki kiritilgan PIN noto'g'ri).

    Frontend `exc_type == "ApprovalRequired"` ni ko'rib PIN oynasini ochadi
    va amalni `approval` bilan qayta yuboradi.
    """

    http_status_code = 403


def _deny(message: str):
    """`ApprovalRequired` ni FOYDALANUVCHI MATNI bilan tashlaydi.

    Oddiy `_deny(...)` ishlatilmaydi: u `_server_messages`siz
    403 qaytaradi, shunda Desk o'zining «Not permitted» oynasini chiqaradi
    (`silent: true` ham to'smaydi) va PIN oynasida matn ko'rinmaydi.
    `frappe.throw` xabarni javobga qo'shadi — Desk oyna ochmaydi, frontend
    esa `exc_type` bo'yicha PIN oynasini ochib, shu matnni ko'rsatadi.
    """
    frappe.throw(message, exc=ApprovalRequired, title=_("Menejer tasdig'i"))


# ═══════════════════════════════════════════════════════════════════
#  Sozlash
# ═══════════════════════════════════════════════════════════════════

def setup():
    create_fields()
    frappe.db.commit()


def create_fields():
    create_custom_fields(PIN_CUSTOM_FIELD, ignore_validate=True)
    print("✅ Menejer PIN maydoni tayyor (User.custom_pos_pin)")


def validate_pin_format(doc, method=None):
    """`User` saqlanganda PIN faqat 4–8 raqamdan iborat bo'lsin.

    Password maydoni o'zgarmagan bo'lsa Frappe uni `*****` deb qaytaradi —
    bunday qiymat tekshirilmaydi.
    """
    value = (doc.get(PIN_FIELD) or "").strip()
    if not value or set(value) == {"*"}:
        return

    if not PIN_PATTERN.match(value):
        frappe.throw(
            _("POS PIN faqat 4–8 ta raqamdan iborat bo'lishi kerak."),
            title=_("PIN noto'g'ri"),
        )


# ═══════════════════════════════════════════════════════════════════
#  Menejerlar ro'yxati
# ═══════════════════════════════════════════════════════════════════

def _pin_of(user: str) -> str:
    try:
        return get_decrypted_password("User", user, PIN_FIELD, raise_exception=False) or ""
    except Exception:
        return ""


def list_approvers(branch: str = None) -> list:
    """PIN'i o'rnatilgan, faol menejerlar.

    Filial berilsa — o'sha filial menejerlari (`URY User`) va System Manager'lar.
    PIN'ning o'zi HECH QACHON qaytarilmaydi.
    """
    users = frappe.get_all(
        "Has Role",
        filters={
            "role": ["in", list(cashier_permissions.SUPERVISOR_ROLES)],
            "parenttype": "User",
        },
        pluck="parent",
        distinct=True,
    )

    result = []
    for user in sorted(set(users)):
        if user in ("Administrator", "Guest"):
            continue

        info = frappe.db.get_value("User", user, ["enabled", "full_name"], as_dict=True)
        if not info or not info.enabled or not _pin_of(user):
            continue

        if branch:
            own_branch = cashier_permissions._branch_from_ury_user(user)
            is_sysman = "System Manager" in frappe.get_roles(user)
            if own_branch != branch and not is_sysman:
                continue

        result.append({"user": user, "full_name": info.full_name or user})

    return result


# ═══════════════════════════════════════════════════════════════════
#  Tasdiqlash
# ═══════════════════════════════════════════════════════════════════

def _attempts_key(user: str) -> str:
    return f"ozturk_approval_fail:{user}"


# Hisobchi ATOMAR Redis `INCR` bilan yuritiladi. `frappe.cache().get_value/
# set_value` ishlatilmaydi: u qiymatni so'rov ichida (`frappe.local.cache`)
# eslab qoladi va yangilamaydi, ya'ni bir so'rovdagi ketma-ket urinishlar
# eskirgan sonni ko'rardi. Parallel so'rovlarda ham `INCR` to'g'ri sanaydi.

def failed_attempts(user: str) -> int:
    cache = frappe.cache()
    return int(cache.get(cache.make_key(_attempts_key(user))) or 0)


def _register_failure(user: str) -> int:
    cache = frappe.cache()
    key = cache.make_key(_attempts_key(user))
    count = int(cache.incr(key))
    if count == 1:
        cache.expire(key, LOCK_SECONDS)
    return count


def reset_attempts(user: str):
    frappe.cache().delete_value(_attempts_key(user))


def _requester_key(user: str) -> str:
    return f"ozturk_approval_fail_by:{user}"


def requester_failures(user: str = None) -> int:
    cache = frappe.cache()
    return int(cache.get(cache.make_key(_requester_key(user or frappe.session.user))) or 0)


def _register_requester_failure(user: str = None) -> int:
    cache = frappe.cache()
    key = cache.make_key(_requester_key(user or frappe.session.user))
    count = int(cache.incr(key))
    if count == 1:
        cache.expire(key, LOCK_SECONDS)
    return count


def reset_requester(user: str):
    frappe.cache().delete_value(_requester_key(user))


def _text(value, allow_number: bool = False) -> str:
    """JSON'dan kelgan qiymatni matnga aylantiradi; ro'yxat/lug'at/None — bo'sh matn."""
    if isinstance(value, str):
        return value.strip()
    if allow_number and isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return ""


def _parse(approval) -> dict:
    if not approval:
        return {}
    if isinstance(approval, str):
        try:
            approval = json.loads(approval)
        except ValueError:
            return {}
    return approval if isinstance(approval, dict) else {}


def require(
    action: str,
    approval=None,
    reference_doctype: str = None,
    reference_name: str = None,
    details: str = "",
) -> str:
    """Amalga menejer tasdig'ini talab qiladi va tasdiqlagan foydalanuvchini qaytaradi.

    Args:
        action: amal nomi (xabar va logda ko'rinadi), masalan «Chegirma 25%».
        approval: `{"user": "...", "pin": "1234"}` (dict yoki JSON satr).
        reference_doctype / reference_name: tasdiq izi qaysi hujjat tarixiga
            yozilsin (masalan `POS Invoice`).
        details: izohga qo'shimcha matn (sabab, summa).

    Raises:
        ApprovalRequired: tasdiq berilmagan yoki PIN noto'g'ri/bloklangan.
    """
    logger = frappe.logger("ozturk_approval")

    # Menejerning o'zi ishlayapti — PIN keraksiz.
    if cashier_permissions.has_supervisor_role():
        _audit(frappe.session.user, action, reference_doctype, reference_name, details, self_=True)
        return frappe.session.user

    data = _parse(approval)
    approver = _text(data.get("user"))
    pin = _text(data.get("pin"), allow_number=True)

    if not approver or not pin:
        _deny(_("«{0}» uchun menejer tasdig'i kerak.").format(action))

    if approver == frappe.session.user:
        # Kassir o'z-o'zini tasdiqlay olmaydi (o'zi menejer bo'lsa yuqorida o'tgan).
        _deny(_("Amalni o'zingiz tasdiqlay olmaysiz."))

    if requester_failures() >= REQUESTER_MAX_ATTEMPTS or failed_attempts(approver) >= MAX_ATTEMPTS:
        logger.warning(
            "approval: bloklangan (user=%s, approver=%s, action=%s)",
            frappe.session.user, approver, action,
        )
        _deny(
            _("PIN ko'p marta noto'g'ri kiritildi. {0} daqiqadan keyin urinib ko'ring.").format(
                LOCK_SECONDS // 60
            )
        )

    stored = _pin_of(approver)
    is_manager = bool(
        set(frappe.get_roles(approver)) & set(cashier_permissions.SUPERVISOR_ROLES)
    )
    enabled = frappe.db.get_value("User", approver, "enabled")

    # Solishtirish HAR DOIM bajariladi (PIN yo'q bo'lsa ham) — javob vaqti
    # foydalanuvchida PIN borligini bildirmasin.
    pin_matches = hmac.compare_digest(stored.encode(), pin.encode())

    # Foydalanuvchi mavjud emas / menejer emas / PIN yo'q holatlari ham
    # "PIN noto'g'ri" bilan bir xil javob beradi — kim menejer ekani sizib chiqmasin.
    if not (stored and is_manager and enabled and pin_matches):
        # Menejer hisobchisi faqat HAQIQIY foydalanuvchi uchun: cheksiz turli
        # nom bilan kesh kalitlari to'ldirilmasin. So'rovchi hisobchisi esa doim.
        if enabled is not None:
            _register_failure(approver)
        count = _register_requester_failure()
        logger.warning(
            "approval: RAD (user=%s, approver=%s, action=%s, urinish=%s)",
            frappe.session.user, approver, action, count,
        )
        _deny(_("Menejer PIN-kodi noto'g'ri."))

    reset_attempts(approver)
    _audit(approver, action, reference_doctype, reference_name, details)
    logger.info(
        "approval: OK (user=%s, approver=%s, action=%s)", frappe.session.user, approver, action
    )
    return approver


def _audit(approver, action, reference_doctype, reference_name, details, self_=False):
    """Tasdiq izini hujjat tarixiga yozadi (hisobotda ko'rinadi)."""
    if not (reference_doctype and reference_name):
        return

    who = frappe.utils.escape_html(
        frappe.db.get_value("User", approver, "full_name") or approver
    )
    text = _("{0}: «{1}»").format(
        _("Menejer amali") if self_ else _("Menejer tasdiqladi"), frappe.utils.escape_html(action)
    )
    if not self_:
        text += _(" — tasdiqlagan: {0}, so'ragan kassir: {1}").format(
            who, frappe.utils.escape_html(frappe.session.user)
        )
    if details:
        text += f" ({frappe.utils.escape_html(details)})"

    frappe.get_doc(
        {
            "doctype": "Comment",
            "comment_type": "Info",
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "content": text,
        }
    ).insert(ignore_permissions=True)
