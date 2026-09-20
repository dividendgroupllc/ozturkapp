# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Kassa funksiyalarini POS Profile darajasida yoqish/o'chirish — YAGONA REESTR.

NEGA ALOHIDA MODUL
==================
`bill_split_setup` va `virtual_keyboard_setup` har biri bitta bayroq uchun
alohida modul edi. Bayroqlar soni o'nlab bo'lganda har biri uchun modul,
`after_migrate` yozuvi va kontekstga qo'lda qo'shish — xatoga moyil. Endi
yangi funksiya BITTA joyda — `FEATURES` jadvalida — e'lon qilinadi:

    1. POS Profile'da Check maydoni avtomatik yaratiladi,
    2. `get_cashier_context()` uni `features` lug'atida frontend'ga beradi,
    3. server tomonida `assert_enabled()` bilan majburlanadi.

Eski ikki bayroq (`bill_split`, `virtual_keyboard`) o'z modullarida qoladi —
ularga TEGILMAYDI (mavjud kassalar shunga tayanadi). Bu yerda ular faqat
`get_features()` natijasiga qo'shiladi, shunda frontend hammasini bitta
joydan o'qiydi.

PRINSIP: YANGI FUNKSIYA STANDART BO'YICHA O'CHIRILGAN
====================================================
Ishlab turgan restoranda migratsiyadan keyin hech narsa o'zgarmasligi
kerak. Menejer funksiyani POS Profile'da ongli ravishda yoqadi. Istisno —
faqat o'qish-faqat amal (`shift_reports`): u pul yoki hisobga tegmaydi.

FRONTEND'GA ISHONILMAYDI
========================
Tugmani yashirish — himoya emas (TZ §17). Har bir server amali
`assert_enabled()` ni chaqirishi SHART.

    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.cashier_features.setup
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, flt

SECTION_FIELD = "custom_cashier_features_section"
INSERT_AFTER = "custom_enable_virtual_keyboard"

#: `key` — frontend va server ishlatadigan barqaror nom; `fieldname` — POS
#: Profile'dagi Check maydoni. TARTIB — Desk formasidagi tartib.
FEATURES = {
    "split_payment": {
        "fieldname": "custom_enable_split_payment",
        "label": "Aralash to'lovga ruxsat berish",
        "default": 0,
        "description": (
            "Yoqilsa: to'lov oynasida bitta chekni bir necha usul bilan "
            "(masalan naqd + karta) to'lash mumkin — har bir usulning yoniga "
            "o'z summasi kiritiladi. O'chiq bo'lsa — chek faqat bitta usul bilan to'lanadi."
        ),
    },
    # DIQQAT: `custom_enable_discount` — URY'ning O'Z maydoni (Desktop POS chegirmasi,
    # `ury_pos/api.py`). Unga TEGILMAYDI: bizning veb-kassa bayrog'i alohida nomda.
    "discount": {
        "fieldname": "custom_enable_cashier_discount",
        "label": "Kassada chegirmaga ruxsat berish",
        "default": 0,
        "description": (
            "Yoqilsa: kassir chekka chegirma qo'ya oladi (foiz yoki summa, "
            "sabab majburiy). «Kassir chegirmasi chegarasi»dan oshgan "
            "chegirma uchun menejer PIN-kodi so'raladi."
        ),
    },
    "table_transfer": {
        "fieldname": "custom_enable_table_transfer",
        "label": "Stolni ko'chirish va birlashtirishga ruxsat",
        "default": 0,
        "description": (
            "Yoqilsa: kassir buyurtmani boshqa bo'sh stolga ko'chira oladi va "
            "bir necha stolni bitta hisobga birlashtira/ajrata oladi."
        ),
    },
    "cashier_orders": {
        "fieldname": "custom_enable_cashier_orders",
        "label": "Kassadan buyurtma qabul qilish",
        "default": 0,
        "description": (
            "Yoqilsa: kassir olib ketish / yetkazib berish buyurtmasini ocha "
            "oladi va hisobi hali berilmagan buyurtmaga taom qo'sha oladi. "
            "O'chiq bo'lsa — buyurtmani faqat ofitsant qabul qiladi."
        ),
    },
    "customer_attach": {
        "fieldname": "custom_enable_customer_attach",
        "label": "Mijoz biriktirishga ruxsat",
        "default": 0,
        "description": (
            "Yoqilsa: kassir chekka mijozni qidirib biriktira yoki yangisini "
            "yarata oladi (yetkazib berishda telefon/manzil uchun ham kerak)."
        ),
    },
    "refunds": {
        "fieldname": "custom_enable_refunds",
        "label": "To'langan chekni qaytarishga ruxsat",
        "default": 0,
        "description": (
            "Yoqilsa: kassa tarixidan to'langan chekni to'liq yoki qisman "
            "qaytarish mumkin. Har bir qaytarish MENEJER PIN-KODI va sabab "
            "talab qiladi — bu sozlab bo'lmaydi."
        ),
    },
    "cash_drawer": {
        "fieldname": "custom_enable_cash_drawer",
        "label": "Kassa g'aladonini ochish",
        "default": 0,
        "description": (
            "Yoqilsa: naqd to'lov tasdiqlanganda va «G'aladon» tugmasi "
            "bosilganda kassa printeri g'aladonni ochadi. Printerga g'aladon "
            "ulangan bo'lishi shart."
        ),
    },
    "cash_movements": {
        "fieldname": "custom_enable_cash_movements",
        "label": "Kassaga pul kiritish/chiqarishga ruxsat",
        "default": 0,
        "description": (
            "Yoqilsa: smena davomida kassaga pul kiritish (kirim) va undan "
            "chiqarish (xarajat, inkassatsiya) qayd etiladi va smena yopilishida "
            "kutilayotgan naqd summaga hisobga olinadi."
        ),
    },
    "tips": {
        "fieldname": "custom_enable_tips",
        "label": "Choychaqa (tip) qabul qilish",
        "default": 0,
        "description": (
            "Yoqilsa: to'lov oynasida choychaqa summasini kiritish mumkin. "
            "Choychaqa xizmat haqi va soliqqa kirmaydi."
        ),
    },
    "shift_reports": {
        "fieldname": "custom_enable_shift_reports",
        "label": "Smena hisobotini (X/Z) chop etish",
        "default": 1,
        "description": (
            "Yoqilsa: kassani yopganda Z-hisobot avtomatik chop etiladi va "
            "smena davomida «X-hisobot» (oraliq) chiqarish mumkin."
        ),
    },
}

#: Raqamli/matnli sozlamalar — faqat tegishli funksiya yoqilganda ko'rinadi.
SETTINGS = {
    "max_cashier_discount_percent": {
        "fieldname": "custom_max_cashier_discount_percent",
        "label": "Kassir chegirmasi chegarasi (%)",
        "fieldtype": "Percent",
        "default": "10",
        "depends_on": "custom_enable_cashier_discount",
        "description": (
            "Kassir menejer tasdig'isiz qo'ya oladigan eng katta chegirma. "
            "Undan oshsa menejer PIN-kodi kerak. 0 — har qanday chegirma "
            "menejer tasdig'ini talab qiladi."
        ),
    },
    "cash_payout_approval_limit": {
        "fieldname": "custom_cash_payout_approval_limit",
        "label": "Kassadan chiqarish — tasdiq chegarasi",
        "fieldtype": "Currency",
        "default": "0",
        "depends_on": "custom_enable_cash_movements",
        "description": (
            "Shu summadan oshgan chiqim menejer PIN-kodini talab qiladi. "
            "0 — har bir chiqim tasdiqlanadi."
        ),
    },
    "tip_percent_options": {
        "fieldname": "custom_tip_percent_options",
        "label": "Choychaqa foizlari (vergul bilan)",
        "fieldtype": "Data",
        "default": "5,10,15",
        "depends_on": "custom_enable_tips",
        "description": "To'lov oynasidagi tez tanlash tugmalari, masalan: 5,10,15",
    },
}


def _fields() -> list:
    """Custom Field ta'riflari — TARTIB muhim (`insert_after` zanjiri)."""
    fields = [
        {
            "fieldname": SECTION_FIELD,
            "label": "Restoran kassasi — qo'shimcha funksiyalar",
            "fieldtype": "Section Break",
            "collapsible": 0,
            "insert_after": INSERT_AFTER,
        }
    ]
    previous = SECTION_FIELD

    for feature in FEATURES.values():
        fields.append(
            {
                "fieldname": feature["fieldname"],
                "label": feature["label"],
                "fieldtype": "Check",
                "default": str(feature["default"]),
                "description": feature["description"],
                "insert_after": previous,
            }
        )
        previous = feature["fieldname"]

    for setting in SETTINGS.values():
        fields.append(
            {
                "fieldname": setting["fieldname"],
                "label": setting["label"],
                "fieldtype": setting["fieldtype"],
                "default": setting["default"],
                "depends_on": setting["depends_on"],
                "description": setting["description"],
                "insert_after": previous,
            }
        )
        previous = setting["fieldname"]

    return fields


def setup():
    """Custom field'lar (idempotent)."""
    create_fields()
    frappe.db.commit()


#: Reestrdan olib tashlangan bayroqlarning maydonlari. Eski versiya ishlagan saytda
#: ular Custom Field bo'lib qolib ketmasin: `create_fields()` ularni o'chiradi.
#: `custom_enable_kiosk_mode` — to'liq ekran (kiosk) rejimi: foydalanuvchi talabi bilan
#: butunlay olib tashlandi, sahifa doim oddiy Desk sarlavhasi bilan ochiladi.
OBSOLETE_FIELDS = ("custom_enable_kiosk_mode",)


def _remove_obsolete_fields():
    for fieldname in OBSOLETE_FIELDS:
        name = frappe.db.get_value(
            "Custom Field", {"dt": "POS Profile", "fieldname": fieldname}, ["name", "module"], as_dict=True
        )
        # Faqat BIZNIKI (module bo'sh) — boshqa ilovaning maydoniga tegilmaydi.
        if name and not name.module:
            frappe.delete_doc("Custom Field", name.name, ignore_permissions=True, force=True)
            print(f"🧹 Eskirgan maydon o'chirildi: POS Profile.{fieldname}")


def _assert_no_foreign_fields(fields: list):
    """Boshqa ilova (masalan URY) yaratgan Custom Field'ni ustidan yozib yubormaslik.

    `create_custom_fields` mavjud maydonni JIMGINA yangilaydi (yorliq, tartib,
    tavsif). Nomlar to'qnashsa URY'ning o'z sozlamasi buziladi va har `bench
    migrate` da ikki ilova maydonni navbatma-navbat qayta yozadi.
    """
    for field in fields:
        owner = frappe.db.get_value(
            "Custom Field", {"dt": "POS Profile", "fieldname": field["fieldname"]}, "module"
        )
        if owner and owner != "Ozturkapp":
            frappe.throw(
                f"POS Profile.{field['fieldname']} maydoni '{owner}' ilovasiga tegishli — "
                "reestrdagi nomni o'zgartiring (setup/cashier_features.py)."
            )


def create_fields():
    fields = _fields()
    _assert_no_foreign_fields(fields)
    _remove_obsolete_fields()
    create_custom_fields({"POS Profile": fields}, ignore_validate=True)
    print(
        f"✅ Kassa funksiyalari maydonlari tayyor "
        f"({len(FEATURES)} bayroq, {len(SETTINGS)} sozlama)"
    )


# ═══════════════════════════════════════════════════════════════════
#  O'qish
# ═══════════════════════════════════════════════════════════════════

def _existing_columns() -> set:
    """POS Profile'da haqiqatan mavjud ustunlar.

    Maydon shu modul o'rnatilmagan saytda bo'lmasligi mumkin — usiz SQL
    `Unknown column` xatosini berardi.
    """
    return set(frappe.db.get_table_columns("POS Profile"))


def _read(pos_profile: str, fieldnames: list) -> dict:
    if not pos_profile:
        return {}

    present = _existing_columns()
    wanted = [name for name in fieldnames if name in present]
    if not wanted:
        return {}

    return frappe.db.get_value("POS Profile", pos_profile, wanted, as_dict=True) or {}


def get_features(pos_profile: str) -> dict:
    """Barcha funksiyalarning holati: `{"discount": True, "refunds": False, ...}`.

    Maydon yaratilmagan saytda funksiya `default` qiymati bilan emas,
    O'CHIQ deb qaytadi — ishlamaydigan tugmani ko'rsatishdan ko'ra
    yashirish xavfsizroq.
    """
    row = _read(pos_profile, [f["fieldname"] for f in FEATURES.values()])

    features = {
        key: bool(cint(row.get(feature["fieldname"]))) for key, feature in FEATURES.items()
    }

    # Eski ikki bayroq — o'z modullarida.
    from ozturkapp.ozturkapp.setup import bill_split_setup, virtual_keyboard_setup

    features["bill_split"] = bill_split_setup.is_enabled(pos_profile)
    features["virtual_keyboard"] = virtual_keyboard_setup.is_enabled(pos_profile)

    return features


def get_settings(pos_profile: str) -> dict:
    """Sonli/matnli sozlamalar (chegaralar, foizlar)."""
    row = _read(pos_profile, [s["fieldname"] for s in SETTINGS.values()])

    tips = [
        flt(part)
        for part in str(
            row.get("custom_tip_percent_options") or SETTINGS["tip_percent_options"]["default"]
        ).split(",")
        if part.strip() and flt(part) > 0
    ]

    max_discount = row.get("custom_max_cashier_discount_percent")

    return {
        # `None` (maydon yo'q) va `0` (ataylab nol) farqlanadi: birinchisida
        # standart 10%, ikkinchisida har qanday chegirma tasdiq talab qiladi.
        "max_cashier_discount_percent": flt(
            SETTINGS["max_cashier_discount_percent"]["default"]
            if max_discount is None
            else max_discount
        ),
        "cash_payout_approval_limit": flt(row.get("custom_cash_payout_approval_limit")),
        "tip_percent_options": tips,
    }


def is_enabled(pos_profile: str, key: str) -> bool:
    """Bitta funksiya yoqilganmi. Noma'lum kalit — dasturchi xatosi."""
    if key in ("bill_split", "virtual_keyboard"):
        return get_features(pos_profile)[key]

    if key not in FEATURES:
        raise KeyError(f"Noma'lum kassa funksiyasi: {key}")

    feature = FEATURES[key]
    row = _read(pos_profile, [feature["fieldname"]])
    return bool(cint(row.get(feature["fieldname"])))


def assert_enabled(pos_profile: str, key: str):
    """Funksiya o'chiq bo'lsa amalni RAD ETADI (server tomonida majburlash)."""
    if is_enabled(pos_profile, key):
        return

    label = FEATURES[key]["label"] if key in FEATURES else key
    frappe.throw(
        _(
            "«{0}» bu kassa uchun yoqilmagan. "
            "Yoqish uchun: POS Profile → Restoran kassasi — qo'shimcha funksiyalar."
        ).format(_(label)),
        exc=frappe.PermissionError,
        title=_("Funksiya o'chiq"),
    )
