# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa oynasi uchun server tomonidagi ruxsat va ko'lam (scope) nazorati.

PRINSIP
=======
Frontend'ga ISHONMAYMIZ (TZ §17). Tugmani yashirish — himoya emas. Har bir
whitelisted metod ishga tushishidan oldin shu moduldagi tekshiruvlardan
o'tadi:

    1. `require_cashier()`      — rolga ruxsat bormi
    2. `resolve_scope()`        — foydalanuvchi qaysi filial/POS Profile'ga tegishli
    3. `assert_table_in_scope()`— so'ralgan stol AYNAN shu filialnikimi
    4. `assert_invoice_in_scope()` — chek shu filialnikimi va holati mosmi
    5. `assert_can_bill()`      — hisob/to'lov huquqi (POS Profile'dan)

Uchinchi va to'rtinchi qadam eng muhimi: ularsiz A filial kassiri B
filialning stolini ochib, chekini to'lay olardi — chunki barcha metodlar
`name` ni parametr sifatida qabul qiladi.
"""

import frappe
from frappe import _

#: Kassa oynasiga kira oladigan rollar.
#: URY o'z rollarini `fixtures` orqali beradi — yangisini yaratmaymiz (TZ §18).
CASHIER_ROLES = ("URY Cashier", "URY Manager", "System Manager")

#: Stolni majburan bo'shatish / buyurtmani bekor qilish kabi amallar.
SUPERVISOR_ROLES = ("URY Manager", "System Manager")


class CashierPermissionError(frappe.PermissionError):
    """Kassa amallariga ruxsat yo'qligini bildiradi."""


# ═══════════════════════════════════════════════════════════════════
#  Rol tekshiruvlari
# ═══════════════════════════════════════════════════════════════════

def has_cashier_role(user: str = None) -> bool:
    roles = set(frappe.get_roles(user or frappe.session.user))
    return bool(roles.intersection(CASHIER_ROLES))


def has_supervisor_role(user: str = None) -> bool:
    roles = set(frappe.get_roles(user or frappe.session.user))
    return bool(roles.intersection(SUPERVISOR_ROLES))


def require_cashier():
    """Kassa oynasining HAR BIR whitelisted metodi shu bilan boshlanadi."""
    if frappe.session.user == "Guest":
        raise CashierPermissionError(_("Iltimos, tizimga kiring"))

    if not has_cashier_role():
        raise CashierPermissionError(
            _("Kassa oynasiga ruxsat yo'q. Kerakli rollardan biri: {0}").format(
                ", ".join(CASHIER_ROLES)
            )
        )


def require_supervisor(action: str):
    if not has_supervisor_role():
        raise CashierPermissionError(
            _("'{0}' amali uchun menejer huquqi kerak").format(action)
        )


# ═══════════════════════════════════════════════════════════════════
#  Ko'lam (scope): filial + POS Profile + restoran
# ═══════════════════════════════════════════════════════════════════

def _branch_from_ury_user(user: str):
    """`Branch.user` (URY User) bolalar jadvalidan filialni topish."""
    return frappe.db.get_value(
        "URY User", {"user": user, "parenttype": "Branch"}, "parent"
    )


def _branch_from_pos_profile(user: str):
    """POS Profile User jadvalidan filialni topish."""
    profile = frappe.db.get_value(
        "POS Profile User", {"user": user, "parenttype": "POS Profile"}, "parent"
    )
    return frappe.db.get_value("POS Profile", profile, "branch") if profile else None


def _only_branch_on_site():
    """Saytda bitta filial bo'lsa — noaniqlik yo'q, o'shani qaytaramiz."""
    branches = frappe.get_all("Branch", pluck="name", limit=2)
    return branches[0] if len(branches) == 1 else None


def resolve_branch(user: str = None) -> str:
    """Joriy foydalanuvchining filiali.

    Upstream `ury.ury_pos.api.getBranch()` dan farqi — u `URY User` topilmasa
    darhol `throw` qiladi. Bizga bu yaramaydi: `ozturk.local` da kassirning
    `URY User.room` maydoni bo'sh va Administrator umuman `URY User` da yo'q.
    Shuning uchun bosqichma-bosqich zaxira variantlar ishlatiladi.
    """
    user = user or frappe.session.user

    branch = (
        _branch_from_ury_user(user)
        or _branch_from_pos_profile(user)
        or _only_branch_on_site()
    )

    if not branch:
        frappe.throw(
            _(
                "Foydalanuvchi '{0}' hech qanday filialga biriktirilmagan. "
                "Branch → URY User jadvaliga qo'shing."
            ).format(user),
            title=_("Filial topilmadi"),
        )
    return branch


def resolve_pos_profile(branch: str) -> str:
    """Filialning faol POS Profile'i (TZ §10 — dublikat DocType yaratmaymiz)."""
    profile = frappe.db.get_value("POS Profile", {"branch": branch, "disabled": 0}, "name")
    if not profile:
        profile = frappe.db.get_value("POS Profile", {"branch": branch}, "name")
    if not profile:
        frappe.throw(
            _("'{0}' filiali uchun POS Profile topilmadi").format(branch),
            title=_("POS Profile topilmadi"),
        )
    return profile


def resolve_scope(user: str = None) -> frappe._dict:
    """Kassa metodlari ishlatadigan yagona kontekst obyekti.

    SO'ROV ICHIDA KESHLANADI
    ========================
    Bu funksiya deyarli har bir whitelisted metodda chaqiriladi, ba'zilarida
    esa bir necha marta (`open_shift_name()` uni yana ichkaridan chaqiradi).
    Har chaqiruv 5 ta so'rov — bitta HTTP so'rovida 20+ ortiqcha so'rov
    demakdir.

    Kesh FAQAT joriy so'rov umriga (`frappe.local`) — global emas. Global
    keshda POS Profile almashtirilganda yoki `frappe.set_user()` bilan rol
    almashganda eski ko'lam osilib qolardi. Kalitga foydalanuvchi ham
    kiritiladi, chunki `resolve_scope(user=...)` argumenti bor.
    """
    cache = getattr(frappe.local, "_ozturk_scope_cache", None)
    if cache is None:
        cache = frappe.local._ozturk_scope_cache = {}

    key = user or frappe.session.user
    if key in cache:
        return cache[key]

    scope = _build_scope(user)
    cache[key] = scope
    return scope


def _build_scope(user: str = None) -> frappe._dict:
    branch = resolve_branch(user)
    pos_profile = resolve_pos_profile(branch)

    profile = frappe.db.get_value(
        "POS Profile",
        pos_profile,
        ["company", "currency", "restaurant", "warehouse", "cost_center", "customer"],
        as_dict=True,
    )

    restaurant = profile.restaurant or frappe.db.get_value(
        "URY Restaurant", {"branch": branch}, "name"
    )
    if not restaurant:
        frappe.throw(
            _("'{0}' filialiga bog'langan URY Restaurant topilmadi").format(branch),
            title=_("Restoran topilmadi"),
        )

    return frappe._dict(
        user=user or frappe.session.user,
        branch=branch,
        pos_profile=pos_profile,
        restaurant=restaurant,
        company=profile.company,
        currency=profile.currency or frappe.db.get_default("currency"),
        warehouse=profile.warehouse,
        cost_center=profile.cost_center,
        default_customer=profile.customer,
    )


# ═══════════════════════════════════════════════════════════════════
#  Obyekt darajasidagi ko'lam tekshiruvi
# ═══════════════════════════════════════════════════════════════════

def assert_table_in_scope(table: str, scope=None) -> frappe._dict:
    """Stol mavjudligini va joriy filialga tegishliligini tasdiqlaydi.

    Bunisiz A filial kassiri B filialning stolini boshqara olardi.
    """
    scope = scope or resolve_scope()

    row = frappe.db.get_value(
        "URY Table",
        table,
        ["name", "branch", "restaurant", "restaurant_room", "occupied", "merged_with"],
        as_dict=True,
    )
    if not row:
        frappe.throw(_("'{0}' stoli topilmadi").format(table), frappe.DoesNotExistError)

    if row.branch != scope.branch:
        raise CashierPermissionError(
            _("'{0}' stoli boshqa filialga tegishli").format(table)
        )
    return row


def assert_invoice_in_scope(invoice: str, scope=None, docstatus=None) -> frappe._dict:
    """Chek mavjudligini, filialini va (ixtiyoriy) holatini tasdiqlaydi.

    Args:
        docstatus: berilsa — chek AYNAN shu holatda bo'lishi shart
                   (0 = qoralama/to'lanmagan, 1 = to'langan).
    """
    scope = scope or resolve_scope()

    row = frappe.db.get_value(
        "POS Invoice",
        invoice,
        [
            "name",
            "branch",
            "docstatus",
            "restaurant_table",
            "custom_merged_tables",
            "invoice_printed",
            "pos_profile",
            "order_type",
            "custom_cancelled",
        ],
        as_dict=True,
    )
    if not row:
        frappe.throw(_("'{0}' cheki topilmadi").format(invoice), frappe.DoesNotExistError)

    if row.branch and row.branch != scope.branch:
        raise CashierPermissionError(
            _("'{0}' cheki boshqa filialga tegishli").format(invoice)
        )

    if docstatus is not None and row.docstatus != docstatus:
        frappe.throw(
            _("'{0}' cheki kutilgan holatda emas (joriy holat: {1})").format(
                invoice, _status_label(row.docstatus)
            ),
            title=_("Chek holati mos emas"),
        )
    return row


def _status_label(docstatus: int) -> str:
    return {0: _("to'lanmagan"), 1: _("to'langan"), 2: _("bekor qilingan")}.get(
        int(docstatus or 0), str(docstatus)
    )


# ═══════════════════════════════════════════════════════════════════
#  Kassani kim ocha/yopa oladi (POS Profile'ga biriktirilgan kassir)
# ═══════════════════════════════════════════════════════════════════
#
# NEGA BU KERAK
# =============
# ERPNext smena hisobotini `POS Invoice.owner` bo'yicha yig'adi:
#
#     pos_closing_entry.get_pos_invoices():
#         where owner = <POS Opening Entry.user>
#
# Ya'ni smenani KIM OCHGANI chekni hisobotga kiritadi yoki chiqarib
# tashlaydi. Smenani Administrator ochib, cheklar kassir nomiga yozilsa,
# o'sha cheklar Z-hisobotga UMUMAN tushmaydi: kutilayotgan summa kam
# ko'rinadi, cheklar esa `consolidated_invoice` siz osilib qoladi va
# buxgalteriyaga (GL) hech qachon yetib bormaydi. Keyingi yopilish ham
# ularni olmaydi — uning oynasi yangi smena ochilgan vaqtdan boshlanadi.
#
# ERPNext buni O'ZI tekshirmaydi (`POSOpeningEntry.validate_pos_profile_and_cashier`
# faqat kompaniya va user `enabled` ligini ko'radi), shuning uchun
# cheklovni shu yerda qo'yamiz.
#
# Ruxsatlar ro'yxati KODDA emas — `POS Profile.applicable_for_users` da.
# Filial qo'shilsa yoki kassir almashsa kod tegishi shart emas.


def pos_profile_users(pos_profile: str) -> list:
    """POS Profile'ga biriktirilgan kassirlar (`applicable_for_users`)."""
    if not pos_profile:
        return []

    return frappe.get_all(
        "POS Profile User",
        filters={"parent": pos_profile, "parenttype": "POS Profile"},
        pluck="user",
        order_by="idx asc",
    )


def can_operate_shift(pos_profile: str, user: str = None) -> bool:
    """Smenani ochish/yopish huquqi bormi?

    Ro'yxat BO'SH bo'lsa cheklov qo'llanmaydi — `assert_can_bill()` bilan
    bir xil kelishuv: sozlanmagan profil butun kassani to'xtatib
    qo'ymasligi kerak.
    """
    allowed = pos_profile_users(pos_profile)
    if not allowed:
        return True

    return (user or frappe.session.user) in allowed


def assert_shift_operator(pos_profile: str, action: str):
    """Smena amalini faqat biriktirilgan kassir bajarishini majburlaydi."""
    if can_operate_shift(pos_profile):
        return

    raise CashierPermissionError(
        _("Kassani {0} faqat unga biriktirilgan kassir bajaradi: {1}").format(
            action, shift_operator_names(pos_profile) or _("(ro'yxat bo'sh)")
        )
    )


def shift_operator_names(pos_profile: str) -> str:
    """«Kassa, Aziz» — xatoda va ekranda ko'rsatish uchun."""
    return ", ".join(_user_label(user) for user in pos_profile_users(pos_profile))


def _user_label(user: str) -> str:
    return frappe.db.get_value("User", user, "full_name") or user


def assert_can_bill(pos_profile: str):
    """POS Profile'dagi `role_allowed_for_billing` cheklovini qo'llaydi.

    Ro'yxat bo'sh bo'lsa — URY'ning o'zida ham cheklov yo'q, ya'ni har qanday
    kassir hisob ocha oladi. Ro'yxat to'ldirilgan bo'lsa — faqat o'sha rollar.
    """
    allowed = frappe.get_all(
        "Role Permitted",
        filters={"parent": pos_profile, "parentfield": "role_allowed_for_billing"},
        pluck="role",
    )
    if not allowed:
        return

    if not set(frappe.get_roles()).intersection(allowed):
        raise CashierPermissionError(
            _("Hisob ochish uchun ruxsat yo'q. Kerakli rollar: {0}").format(
                ", ".join(allowed)
            )
        )


# ═══════════════════════════════════════════════════════════════════
#  Kassa smenasi
# ═══════════════════════════════════════════════════════════════════

def open_shift_name(scope=None) -> str:
    """Ochiq `POS Opening Entry` nomi, bo'lmasa bo'sh satr.

    Yagona manba: kassa sahifasi, ofitsant ilovasi va sotuv guardi —
    uchalasi ham shu funksiyadan o'qiydi, ya'ni "kassa ochiqmi?" degan
    savolga tizimda BITTA javob bo'ladi.
    """
    from ozturkapp.ozturkapp.api.desktop_pos import _get_user_room, _open_opening_entry

    scope = scope or resolve_scope()

    room = ""
    try:
        room = _get_user_room(scope.branch)
    except Exception:
        # `URY User.room` bo'sh bo'lishi mumkin — smena qidiruvi busiz ham ishlaydi.
        pass

    return _open_opening_entry(scope.branch, scope.pos_profile, room) or ""


def assert_shift_open(scope=None):
    """Ochiq kassa smenasi bo'lmasa amalni to'xtatadi.

    NEGA BU KERAK
    =============
    Smena ochilishida kassir kassadagi NAQD PULNI sanab kiritadi
    (`POS Opening Entry.balance_details`). Bu summa — smena oxiridagi
    solishtiruvning boshlang'ich nuqtasi. Smenasiz sotuv qilinsa,
    hisobot qaysi kassaga tegishli ekani yo'qoladi va yopilishda
    kutilayotgan summani hisoblab bo'lmaydi.

    ERPNext ham buni majburlaydi (`POS Invoice.validate_pos_opening_entry`),
    lekin u xatoni INGLIZCHA va chuqurroq bosqichda beradi:

        No open POS Opening Entry found for POS Profile kassa.

    Ofitsant taomlarni tanlab bo'lgandan keyin shu xatoni ko'radi va nima
    qilishni bilmaydi. Shuning uchun tekshiruvni O'ZIMIZ, eng boshida va
    tushunarli tilda qilamiz.

    Args:
        scope: `resolve_scope()` natijasi. Berilmasa o'zi aniqlaydi.
    """
    if open_shift_name(scope):
        return

    frappe.throw(
        _(
            "Kassa smenasi ochilmagan. Kassir kassadagi naqd pulni sanab "
            "kiritishi va «Kassani ochish» tugmasini bosishi kerak — "
            "shundan keyin sotuv boshlanadi."
        ),
        title=_("Kassa yopiq"),
    )
