# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""URY'ning KOT tekshiruv jobini (`kotValidationThread`) to'xtatib qo'yish.

MUAMMO
======
URY har daqiqada `ury.ury.api.ury_kot_validation.kotValidationThread` ni
ishga tushiradi: 1–5 daqiqa oldin yaratilgan draft invoyslarni ko'rib, KOT'i
yo'qlariga "zaxira" KOT yaratadi. Lekin job `pos_profile.kot_naming_series`
maydonini o'qiydi, saytda esa (URY fixture'i) u `custom_kot_naming_series`
deb yaratilgan:

    AttributeError: 'POSProfile' object has no attribute 'kot_naming_series'

Natijada draft invoys bor har daqiqada job yiqiladi (production'da 20–21
sentyabr: 48 ta `Failed` yozuv) va haqiqiy xatolar shovqin ichida yo'qoladi.
`sync_order` esa KOT xatosini `Error Log`'ga «KOT Error» sarlavhasi bilan
yozadi — u yerda hech qachon bo'lmagan, ya'ni zaxira hali kerak bo'lmagan.

NEGA MAYDON NOMINI TUZATIB QO'YA QOLMAYMIZ
==========================================
Job tuzalsa, u KOT'siz HAR QANDAY itemli draft invoysga `type="Duplicate"`
KOT yaratadi. Bizda esa bunday invoyslar QONUNIY ravishda bor:

  * Hisobni bo'lish (`custom_enable_bill_split`): URY `split_bill()` yangi
    draft invoys yaratadi va unga KOT YARATMAYDI — taomlar oshxonaga asl
    invoys bilan ketgan (`setup/bill_split_setup.py`).
  * Desk'dan qo'lda yaratilgan draft POS Invoice.

Bizda `Duplicate` KOT «pishirilayotgan» hisoblanadi
(`utils/kitchen_status.COOKING_KOT_TYPES`) va oshxona ekraniga chiqadi —
oshpaz bo'lingan chek uchun ikkinchi marta pishirishi mumkin. Bundan tashqari
job `production_items` ro'yxatini production unit'lar orasida tozalamaydi
(Bar KOT'iga Oshxona taomlari tushadi).

Shuning uchun job to'xtatiladi (`Scheduled Job Type.stopped = 1`). Amalda
hech narsa yo'qolmaydi: u hozir ham ishlamayapti (har chaqiruvda yiqiladi).

Zaxira mexanizmi kerak bo'lsa — ozturkapp'da bo'lingan (`custom_split_from`) va
qo'lda yaratilgan invoyslarni chetlab o'tadigan O'Z jobini yozing; URY'nikini
yoqmang.

MIGRATE BILAN TO'QNASHMAYDI
===========================
`sync_jobs` mavjud job yozuvining faqat `frequency`/`cron_format` maydonini
yangilaydi, `stopped` ga tegmaydi. Job turi o'chirilib qayta yaratilsa
(masalan, `ury` qayta o'rnatilganda) `after_migrate` uni yana to'xtatadi.

    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.kot_failsafe_setup.setup
"""

import frappe

JOB_METHOD = "ury.ury.api.ury_kot_validation.kotValidationThread"


def setup():
    """URY KOT tekshiruv jobini to'xtatadi (idempotent, boshqa joblarga tegmaydi)."""
    changed = stop_validation_job()
    frappe.db.commit()

    if changed:
        print(f"✅ URY KOT tekshiruv jobi to'xtatildi ({changed} ta)")
    else:
        print("⏭️  URY KOT tekshiruv jobi allaqachon to'xtatilgan (yoki yo'q)")
    return changed


def stop_validation_job(method: str = JOB_METHOD) -> int:
    """`method` jobining hali ishlayotgan yozuvlarini to'xtatadi.

    Qaytadi: nechta yozuv o'zgargani. `frappe.db.set_value` — `Scheduled Job Type`
    ning `validate`/`on_update` zanjirini (cron tekshiruvi) ishga tushirmaydi.
    """
    if not frappe.db.exists("DocType", "Scheduled Job Type"):
        return 0

    names = frappe.get_all(
        "Scheduled Job Type", filters={"method": method, "stopped": 0}, pluck="name"
    )
    for name in names:
        frappe.db.set_value("Scheduled Job Type", name, "stopped", 1)
    return len(names)
