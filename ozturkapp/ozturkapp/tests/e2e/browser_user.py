# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Brauzer kirishi uchun BITTA vaqtinchalik (commit qilinadigan) foydalanuvchi.

Desk sessiyasi haqiqiy bench serverda yaratiladi, shuning uchun bu foydalanuvchi
haqiqiy bazaga yoziladi. Shim uni ishlatmaydi (u senariy foydalanuvchisi nomidan
ishlaydi), sahifa ochishga ruxsat uchun `URY Cashier` roli yetarli.

Tozalash tranzaksiyadan TASHQARI, mustaqil ulanishda `DELETE` bilan bajariladi
(`frappe.delete_doc` `Deleted Document` qoldirardi). Qoldiq bor-yo'qligini
`guard.fingerprint()` farqi tekshiradi.
"""

import secrets
import string

import frappe
import pymysql

from ozturkapp.ozturkapp.tests.e2e.guard import fingerprint_connection

PREFIX = "e2e-browser-tmp"


def _table(doctype):
    return doctype if doctype.startswith("__") else f"tab{doctype}"


def new_credentials():
    token = secrets.token_hex(3)
    alphabet = string.ascii_letters + string.digits
    password = "E2e#" + "".join(secrets.choice(alphabet) for _ in range(14))
    return {"email": f"{PREFIX}-{token}@example.com", "password": password}


def create(creds):
    """Foydalanuvchini YARATADI va COMMIT qiladi (guard o'rnatilishidan OLDIN chaqiriladi)."""
    frappe.set_user("Administrator")
    doc = frappe.get_doc(
        {
            "doctype": "User",
            "email": creds["email"],
            "first_name": "E2E Browser",
            "send_welcome_email": 0,
            "enabled": 1,
            "new_password": creds["password"],
            "roles": [{"role": "URY Cashier"}],
        }
    )
    doc.flags.no_welcome_mail = True
    doc.user_image = "/assets/frappe/images/frappe-favicon.svg"  # gravatar'ga tashqi so'rov ketmasin
    # `create_contact` navbatga qo'yilsa ishchi UNI kechroq (tozalashdan keyin) bajarib, qoldiq
    # qoldirishi mumkin. `in_test` — Frappe shu ishlarni darhol (o'sha tranzaksiyada) bajaradi.
    frappe.flags.in_test = True
    try:
        doc.insert(ignore_permissions=True)
    finally:
        frappe.flags.in_test = False
    frappe.db.commit()


#: Foydalanuvchi nomi (yagona, noyob e-pochta) bilan bog'lanishi mumkin bo'lgan ustunlar.
_LINK_COLUMNS = ("user", "owner", "modified_by", "for_user", "parent", "email_id", "reference_name",
                 "docname", "share_name")


def _sweep(cur, email, removed):
    """Butun bazada e-pochtaga bog'langan qatorlarni o'chiradi (e-pochta noyob, shuning uchun xavfsiz)."""
    cur.execute(
        "select table_name, column_name from information_schema.columns c "
        "join information_schema.tables t using (table_schema, table_name) "
        "where table_schema = database() and t.table_type = 'BASE TABLE' and column_name in %s "
        "and table_name not in ('tabUser')",
        (list(_LINK_COLUMNS),),
    )
    for table, column in cur.fetchall():
        cur.execute(f"delete from `{table}` where `{column}` = %s", (email,))
        if cur.rowcount:
            key = table[3:] if table.startswith("tab") else table
            removed[key] = removed.get(key, 0) + cur.rowcount


def cleanup(email):
    """Foydalanuvchi va u qoldirgan hamma narsani o'chiradi. Nima o'chirilganini qaytaradi."""
    removed = {}
    conn = fingerprint_connection()
    try:
        cur = conn.cursor()
        cur.execute("select distinct parent from `tabContact Email` where email_id = %s", (email,))
        for (contact,) in cur.fetchall():
            for table in ("Dynamic Link", "Contact Phone", "Contact Email"):
                cur.execute(f"delete from `tab{table}` where parent = %s", (contact,))
            cur.execute("delete from `tabContact` where name = %s", (contact,))
            removed["Contact"] = removed.get("Contact", 0) + cur.rowcount

        _sweep(cur, email, removed)
        for table, where in (("tabNotification Settings", "name = %s"), ("__Auth", "name = %s"),
                             ("tabUser", "name = %s")):
            cur.execute(f"delete from `{table}` where {where}", (email,))
            if cur.rowcount:
                removed[table] = cur.rowcount
        return removed
    finally:
        conn.close()


def leftovers(email):
    """Tozalashdan keyin hamon qolgan qatorlar (bo'sh bo'lishi kerak)."""
    conn = fingerprint_connection()
    try:
        cur = conn.cursor()
        found = {}
        cur.execute(
            "select table_name, column_name from information_schema.columns "
            "where table_schema = database() and column_name in %s",
            (list(_LINK_COLUMNS) + ["name"],),
        )
        for table, column in cur.fetchall():
            try:
                cur.execute(f"select count(*) from `{table}` where `{column}` = %s", (email,))
            except pymysql.Error:
                continue  # ko'rinish (view) yoki ma'nosiz ustun
            count = cur.fetchone()[0]
            if count:
                found[f"{table}.{column}"] = count
        return found
    finally:
        conn.close()
