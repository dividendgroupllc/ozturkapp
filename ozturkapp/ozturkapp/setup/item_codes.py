# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Eski Item kodlarini `ITEM-####` ga o'tkazish (bir martalik).

NEGA KERAK
==========
Yangi Item'lar endi avtomatik `ITEM-####` oladi (`utils/item_naming.py`),
lekin bazadagi eskilarining kodi taom nomining o'zi (`GOBIT DÖNER`). Bu
skript ularni yaratilgan tartibida `ITEM-0001`, `ITEM-0002`, ... ga
o'tkazadi. Allaqachon `ITEM-####` bo'lganlarga tegmaydi — qayta ishga
tushirsa bo'ladi (to'xtab qolgan joydan davom etadi).

ISHLATISH
=========
    # 1) Faqat REJA — bazaga TEGMAYDI, nima nimaga o'zgarishini ko'rsatadi:
    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.item_codes.renumber_items

    # 2) Haqiqiy o'zgartirish:
    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.item_codes.renumber_items \
        --kwargs '{"dry_run": 0}'

    # Hech narsa o'zgartirmay, Item'ga bog'liq havolalar butunligini tekshirish:
    bench --site ozturk.local execute \
        ozturkapp.ozturkapp.setup.item_codes.check_links

NIMA O'ZGARADI
==============
`frappe.rename_doc` Item'ga `Link` bo'lgan BARCHA maydonlarni (POS Invoice
Item, BOM, Stock Ledger Entry, Bin, Item Price, URY Menu Item, ...) va
soliq jadvallaridagi `item_wise_tax_detail` ni yangilaydi. Har bir Item'ga
"renamed from ... to ..." izohi yoziladi.

`POS Profile.custom_quick_items` JSON matn — uni `rename_doc` bilmaydi,
shuning uchun bu yerda alohida yangilanadi.

DIQQAT
======
* `Item.before_rename`: `item_name` eski kodga TENG bo'lsa, uni ham yangi
  kodga almashtirib yuboradi. Bizda barcha Item'larda shunday edi — taom
  nomlari `ITEM-0007` bo'lib qolardi. Har rename'dan keyin nom qaytariladi.
* Kassa/ofitsant ilovalari menyuni xotirada saqlaydi. Eski kod bilan
  yuborilgan buyurtma (masalan internetsiz yozilgan) serverda topilmaydi.
  Shu sabab kassa yopilgandan keyin, ochiq buyurtmalar yo'qligida bajaring.
* `commit_each=1` (default) — har bir Item alohida commit qilinadi: jadvallar
  qulflanishi qisqa bo'ladi va xato bo'lsa ham oldingilari saqlanib qoladi.
"""

import json

import frappe
from frappe.model.rename_doc import rename_doc
from frappe.utils import cint

from ozturkapp.ozturkapp.utils import item_naming
from ozturkapp.ozturkapp.utils.item_naming import CODE_PATTERN, PREFIX, next_item_code


# ═══════════════════════════════════════════════════════════════════
#  Reja
# ═══════════════════════════════════════════════════════════════════

def build_plan() -> list:
    """Nima nimaga o'zgarishi: `[(eski_kod, yangi_kod, item_name, item_group)]`.

    Bazaga yozmaydi. Tartib — yaratilgan vaqt bo'yicha, shuning uchun eng
    eski Item `ITEM-0001` bo'ladi.
    """
    rows = frappe.get_all(
        "Item",
        fields=["name", "item_name", "item_group"],
        order_by="creation asc, name asc",
    )
    taken = {row.name for row in rows}
    counter = max(item_naming.series_current(), max(item_naming.existing_numbers(), default=0))

    plan = []
    for row in rows:
        if CODE_PATTERN.match(row.name):
            continue
        counter += 1
        while (code := f"{PREFIX}{counter:04d}") in taken:
            counter += 1
        taken.add(code)
        plan.append((row.name, code, row.item_name, row.item_group))
    return plan


# ═══════════════════════════════════════════════════════════════════
#  O'tkazish
# ═══════════════════════════════════════════════════════════════════

def renumber_items(dry_run=1, commit_each=1):
    """Eski kodlarni `ITEM-####` ga o'tkazadi.

    Args:
        dry_run: 1 (default) — faqat reja, bazaga yozilmaydi.
        commit_each: 1 (default) — har Item'dan keyin commit.
    """
    dry_run, commit_each = cint(dry_run), cint(commit_each)

    plan = build_plan()
    _print_preflight()
    baseline = check_links()

    if not plan:
        print(f"✅ Barcha Item'lar allaqachon {PREFIX}#### formatida")
        return

    print(f"\n{len(plan)} ta Item o'zgaradi:")
    for old, code, _item_name, item_group in plan:
        print(f"  {code}  <-  {old}   [{item_group}]")

    if dry_run:
        print("\nℹ️  Bu FAQAT reja — baza o'zgarmadi.")
        print("   Haqiqiy o'zgartirish:  --kwargs '{\"dry_run\": 0}'")
        return

    item_naming.ensure_series_floor()
    frappe.db.commit()

    done = 0
    for old, *_rest in plan:
        try:
            code = rename_item(old)
        except Exception:
            frappe.db.rollback()
            print(f"❌ {old} o'tkazilmadi (shundan oldingi {done} tasi saqlangan).")
            print("   Sababni bartaraf etib, buyruqni qayta ishga tushiring — qolganlaridan davom etadi.")
            raise

        done += 1
        if commit_each:
            frappe.db.commit()
        print(f"  [{done}/{len(plan)}] {old}  ->  {code}")

    _finish()
    frappe.db.commit()
    print(f"\n✅ {done} ta Item {PREFIX}#### ga o'tkazildi")

    after = check_links()
    if len(after) > len(baseline):
        print("⚠️  Yangi yetim havolalar paydo bo'ldi — yuqoridagi ro'yxatni tekshiring.")


def rename_item(old: str) -> str:
    """Bitta Item'ni seriyadagi keyingi `ITEM-####` kodga o'tkazadi.

    Commit qilmaydi.

    Returns:
        str: yangi kod.
    """
    item_name = frappe.db.get_value("Item", old, "item_name")
    code = next_item_code()

    rename_doc(
        "Item",
        old,
        code,
        force=True,
        ignore_permissions=True,
        rebuild_search=False,
        show_alert=False,
    )
    # `Item.before_rename` item_name == eski kod bo'lsa nomni ham yangi kodga
    # almashtirib yuborgan — taom nomini qaytaramiz.
    frappe.db.set_value("Item", code, "item_name", item_name, update_modified=False)
    _remap_quick_items({old: code})
    return code


def _remap_quick_items(mapping: dict):
    """`POS Profile.custom_quick_items` (`{"1": "<kod>", ...}` JSON) dagi kodlar."""
    if not frappe.get_meta("POS Profile").has_field("custom_quick_items"):
        return

    for profile in frappe.get_all("POS Profile", fields=["name", "custom_quick_items"]):
        try:
            data = json.loads(profile.custom_quick_items or "null")
        except ValueError:
            continue

        if isinstance(data, dict):
            updated = {slot: mapping.get(code, code) for slot, code in data.items()}
        elif isinstance(data, list):
            updated = [mapping.get(code, code) if isinstance(code, str) else code for code in data]
        else:
            continue

        if updated != data:
            frappe.db.set_value(
                "POS Profile",
                profile.name,
                "custom_quick_items",
                json.dumps(updated, ensure_ascii=False),
                update_modified=False,
            )


def _finish():
    """Keshni tozalash va ilovalarga menyu o'zgargani haqida xabar."""
    from ozturkapp.ozturkapp.utils.menu_realtime import emit_menu_change

    frappe.clear_cache()
    for branch in frappe.get_all("URY Restaurant", pluck="branch"):
        emit_menu_change(branch, "MENU_UPDATED")

    # Global qidiruv indeksi (fon vazifasi)
    frappe.enqueue("frappe.utils.global_search.rebuild_for_doctype", doctype="Item")


def _print_preflight():
    drafts = frappe.db.count("POS Invoice", {"docstatus": 0})
    if drafts:
        print(f"⚠️  {drafts} ta ochiq (draft) POS Invoice bor — ulardagi kodlar ham yangilanadi,")
        print("   lekin kassa/ofitsant ilovalari eski kod bilan buyurtma yuborsa, u topilmaydi.")
        print("   Ochiq buyurtmalar yo'qligida bajaring.\n")


# ═══════════════════════════════════════════════════════════════════
#  Butunlikni tekshirish
# ═══════════════════════════════════════════════════════════════════

def check_links() -> list:
    """`Item`ga Link bo'lgan maydonlarda mavjud bo'lmagan kodga havolalar.

    Returns:
        list: `[(doctype, fieldname, soni)]` — bo'sh bo'lsa hammasi joyida.
    """
    pairs = set(
        frappe.get_all(
            "DocField",
            filters={"fieldtype": "Link", "options": "Item"},
            fields=["parent", "fieldname"],
            as_list=True,
        )
    )
    pairs |= set(
        frappe.get_all(
            "Custom Field",
            filters={"fieldtype": "Link", "options": "Item"},
            fields=["dt", "fieldname"],
            as_list=True,
        )
    )

    broken = []
    for doctype, fieldname in sorted(pairs):
        if not frappe.db.exists("DocType", doctype):
            continue
        meta = frappe.get_meta(doctype)
        if meta.issingle or meta.is_virtual or not frappe.db.table_exists(doctype):
            continue
        if not frappe.db.has_column(doctype, fieldname):
            continue

        count = frappe.db.sql(
            f"""select count(*) from `tab{doctype}` t
            where ifnull(t.`{fieldname}`, '') != ''
            and not exists (select 1 from `tabItem` i where i.name = t.`{fieldname}`)"""
        )[0][0]
        if count:
            broken.append((doctype, fieldname, count))

    if broken:
        for doctype, fieldname, count in broken:
            print(f"⚠️  {doctype}.{fieldname}: {count} ta yozuv mavjud bo'lmagan Item'ga havola qiladi")
    else:
        print(f"✅ Havolalar butun ({len(pairs)} ta maydon tekshirildi)")
    return broken
