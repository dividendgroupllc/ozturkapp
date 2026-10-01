#!/usr/bin/env python3
"""
One-off data migration: collapse the Öztürk ERPNext setup into ONE company.

    O'zturk (OZ, group)  ─┬─ O'zturk Sklad (OSK)          -> re-created in OMG, then deleted
                          └─ O'zturk Maksim Gorkiy (OMG)  -> the only company left

Everything ends up in ONE warehouse: "Sklad Maksim Gorkiy - OMG".

Run (dry-run is the DEFAULT — everything is executed and then rolled back):

    cd ~/frappe-bench/sites
    ../env/bin/python ../apps/ozturkapp/ozturkapp/ozturkapp/setup/merge_sklad_into_filial.py <site>
    ../env/bin/python ../apps/ozturkapp/ozturkapp/ozturkapp/setup/merge_sklad_into_filial.py <site> --commit

Options:
    --commit        really write (otherwise the whole run is rolled back at the end)
    --verify-only   only print the verification report (read-only)
    --force         continue although the pre-flight check found unexpected documents

Phases (in this order):
    0. Pre-flight check + baseline snapshot (saved to private/merge_sklad_baseline.json on --commit)
    A. ONE database transaction (commits inside ERPNext code are suppressed):
       1. Undo Branch Stock Transfer(s): mark BTR cancelled (db), cancel its PI (OMG) then SI (OSK)
       2. BOMs: copy every submitted OSK BOM to OMG (children first, bom_no re-mapped), submit, default
       3. Opening Stock Reconciliation(s) re-created in OMG (difference acc. = Temporary Opening - OMG)
       4. Production Entries: PE record + its Manufacture Stock Entry re-created in OMG with the new BOMs
       5. Purchase Invoices (update_stock) re-created in OMG, submitted
       6. Draft documents: duplicates skipped, the rest re-created as drafts in OMG
       7a. Cancel OSK originals: PIs, PEs(db)+their SEs, SRs, BOMs (parents first)
    B. 8. Process all pending Repost Item Valuation entries synchronously
    C. 7b. Company clean-up (each step committed on --commit):
       - delete cancelled OMG documents that point to internal parties (e.g. the BTR purchase invoice)
       - Transaction Deletion Record for OSK (single transaction mode)
       - delete internal Customers / Suppliers, Mode of Payment "Нахт Sklad"
       - clear Single-doctype values pointing to OSK/OZ (Ozturk Settings ...), Item Defaults of OSK/OZ
       - OMG.parent_company = None (nested set rebuilt), Transaction Deletion for OZ
       - delete companies OSK and OZ
    B'. repost again (normally nothing to do)
    9. Verification report

The script is idempotent: every step checks whether its work is already done, so it can be re-run
after a crash or a successful run ("nothing to do").

Only ERPNext/Frappe APIs + plain SQL are used — no ozturkapp controller code (Branch Stock Transfer,
Production Entry ...) is called, so it keeps working while ozturkapp is being refactored.
"""

import argparse
import json
import os
import sys
import time
from collections import OrderedDict, defaultdict

import frappe
from frappe.utils import cint, flt

OMG = "O'zturk Maksim Gorkiy"
OSK = "O'zturk Sklad"
OZ = "O'zturk"
OLD_COMPANIES = (OSK, OZ)
OLD_ABBRS = ("OSK", "OZ")
TARGET_WH = "Sklad Maksim Gorkiy - OMG"
TEMP_OPENING = "Temporary Opening - OMG"
BASELINE_FILE = "merge_sklad_baseline.json"

QTY_TOL = 1e-6
VALUE_TOL = 1.0  # UZS

T0 = time.time()
DRY_RUN = True
_real_commit = None
WARNINGS = []


# ───────────────────────────────────────────────────────────── helpers ──


def log(msg="", indent=0):
	print(f"[{time.time() - T0:7.1f}s] " + "  " * indent + str(msg), flush=True)


def warn(msg):
	WARNINGS.append(msg)
	log(f"WARNING: {msg}")


def header(title):
	print("\n" + "=" * 100, flush=True)
	log(title)
	print("=" * 100, flush=True)


def suppress_commits():
	frappe.db.commit = lambda *a, **k: None


def restore_commits():
	frappe.db.commit = _real_commit


def checkpoint(label):
	"""Real commit in --commit mode, nothing in dry-run."""
	if DRY_RUN:
		log(f"(dry-run) checkpoint '{label}' — not committed")
		return
	_real_commit()
	log(f"COMMITTED: {label}")


def company_exists(c):
	return bool(frappe.db.exists("Company", c))


def doctype_exists(dt):
	return bool(frappe.db.exists("DocType", dt)) and frappe.db.table_exists(dt)


def has_column(dt, col):
	try:
		return frappe.db.has_column(dt, col)
	except Exception:
		return False


_map_cache = {}


def map_account(acc):
	if not acc:
		return acc
	if frappe.db.get_value("Account", acc, "company") == OMG:
		return acc
	key = ("Account", acc)
	if key in _map_cache:
		return _map_cache[key]
	cand = None
	for abbr in OLD_ABBRS:
		suf = f" - {abbr}"
		if acc.endswith(suf):
			c = acc[: -len(suf)] + " - OMG"
			if frappe.db.exists("Account", {"name": c, "company": OMG}):
				cand = c
			break
	if not cand:
		acc_name = frappe.db.get_value("Account", acc, "account_name")
		cand = frappe.db.get_value("Account", {"company": OMG, "account_name": acc_name, "is_group": 0}, "name")
	if not cand:
		frappe.throw(f"No OMG account found for {acc}")
	_map_cache[key] = cand
	return cand


def map_cost_center(cc):
	if not cc:
		return cc
	if frappe.db.get_value("Cost Center", cc, "company") == OMG:
		return cc
	for abbr in OLD_ABBRS:
		suf = f" - {abbr}"
		if cc.endswith(suf):
			c = cc[: -len(suf)] + " - OMG"
			if frappe.db.exists("Cost Center", {"name": c, "company": OMG}):
				return c
	return frappe.get_cached_value("Company", OMG, "cost_center")


def remap_doc(doc, bom_map=None):
	"""Re-point every Company/Account/Cost Center/Warehouse/BOM link of doc (and children) to OMG."""
	bom_map = bom_map or {}

	def _remap(d):
		for df in d.meta.get("fields", {"fieldtype": "Link"}):
			val = d.get(df.fieldname)
			if not val:
				continue
			if df.options == "Company" and val in OLD_COMPANIES:
				d.set(df.fieldname, OMG)
			elif df.options == "Warehouse" and frappe.db.get_value("Warehouse", val, "company") != OMG:
				d.set(df.fieldname, TARGET_WH)
			elif df.options == "Account":
				d.set(df.fieldname, map_account(val))
			elif df.options == "Cost Center":
				d.set(df.fieldname, map_cost_center(val))
			elif df.options == "BOM" and val in bom_map:
				d.set(df.fieldname, bom_map[val])

	_remap(doc)
	for child in doc.get_all_children():
		_remap(child)
	return doc


def set_docstatus_db(doctype, name, docstatus, status=None):
	"""Set docstatus (and status) directly — for ozturkapp doctypes whose controller must not run."""
	frappe.db.sql(f"update `tab{doctype}` set docstatus=%s, modified=now() where name=%s", (docstatus, name))
	if status and has_column(doctype, "status"):
		frappe.db.sql(f"update `tab{doctype}` set status=%s where name=%s", (status, name))
	for df in frappe.get_meta(doctype).get_table_fields():
		if frappe.db.table_exists(df.options):
			frappe.db.sql(
				f"update `tab{df.options}` set docstatus=%s where parent=%s and parenttype=%s",
				(docstatus, name, doctype),
			)
	frappe.clear_document_cache(doctype, name)


def fmt_td(t):
	return str(t)


def same_time(a, b):
	from frappe.utils import get_time

	return get_time(a) == get_time(b)


# ─────────────────────────────────────────────────────── preflight / baseline ──

ALLOWED_SUBMITTED_OSK = {
	"BOM",
	"Purchase Invoice",
	"Stock Reconciliation",
	"Stock Entry",
	"Production Entry",
	"Sales Invoice",
	"Branch Stock Transfer",
	"Repost Item Valuation",
	"Process Deferred Accounting",
	"GL Entry",
	"Stock Ledger Entry",
	"Payment Ledger Entry",
	"Repost Payment Ledger",
	"Repost Accounting Ledger",
}


def company_link_fields():
	rows = frappe.get_all(
		"DocField", filters={"fieldtype": "Link", "options": "Company"}, fields=["parent", "fieldname"]
	)
	rows += frappe.get_all(
		"Custom Field", filters={"fieldtype": "Link", "options": "Company"}, fields=["dt as parent", "fieldname"]
	)
	out = []
	for r in rows:
		try:
			meta = frappe.get_meta(r.parent)
		except Exception:
			continue
		if meta.issingle or meta.istable or not frappe.db.table_exists(r.parent):
			continue
		out.append((r.parent, r.fieldname))
	return out


def child_company_link_fields():
	rows = frappe.get_all(
		"DocField", filters={"fieldtype": "Link", "options": "Company"}, fields=["parent", "fieldname"]
	)
	rows += frappe.get_all(
		"Custom Field", filters={"fieldtype": "Link", "options": "Company"}, fields=["dt as parent", "fieldname"]
	)
	out = []
	for r in rows:
		try:
			meta = frappe.get_meta(r.parent)
		except Exception:
			continue
		if meta.istable and frappe.db.table_exists(r.parent) and has_column(r.parent, r.fieldname):
			out.append((r.parent, r.fieldname))
	return out


def delete_orphan_children():
	"""Company.on_trash deletes tax templates with plain SQL and leaves their child rows behind."""
	for parenttype in (
		"Sales Taxes and Charges Template",
		"Purchase Taxes and Charges Template",
		"Item Tax Template",
		"Budget",
		"Mode of Payment",
		"Customer",
		"Supplier",
	):
		for df in frappe.get_meta(parenttype).get_table_fields():
			n = frappe.db.sql(
				f"""select count(*) from `tab{df.options}` c where c.parenttype=%s
				and not exists (select 1 from `tab{parenttype}` p where p.name=c.parent)""",
				parenttype,
			)[0][0]
			if n:
				frappe.db.sql(
					f"""delete c from `tab{df.options}` c where c.parenttype=%s
					and not exists (select 1 from `tab{parenttype}` p where p.name=c.parent)""",
					parenttype,
				)
				log(f"deleted {n} orphan {df.options} rows of deleted {parenttype}", 1)


def preflight():
	header("PHASE 0 — pre-flight")
	comps = frappe.get_all("Company", fields=["name", "abbr", "parent_company", "is_group"])
	for c in comps:
		log(f"company: {c.name} ({c.abbr}) parent={c.parent_company} group={c.is_group}", 1)
	if not company_exists(OMG):
		frappe.throw("Target company OMG does not exist")
	if not frappe.db.exists("Warehouse", {"name": TARGET_WH, "company": OMG}):
		frappe.throw(f"Target warehouse {TARGET_WH} missing")

	problems = []
	for dt, fn in company_link_fields():
		if fn != "company" and dt not in ("Branch Stock Transfer",):
			continue
		if not has_column(dt, "docstatus"):
			continue
		for comp in OLD_COMPANIES:
			if not company_exists(comp):
				continue
			rows = frappe.db.sql(
				f"select docstatus, count(*) n from `tab{dt}` where `{fn}`=%s group by docstatus", comp, as_dict=1
			)
			for r in rows:
				if r.docstatus == 1 and dt not in ALLOWED_SUBMITTED_OSK:
					problems.append(f"{dt}.{fn}={comp}: {r.n} submitted")
				if r.n:
					log(f"{comp:22s} {dt}.{fn}: docstatus {r.docstatus} x {r.n}", 1)

	if company_exists(OSK):
		# only Manufacture stock entries (from Production Entries) are re-created
		ses = frappe.db.sql(
			"select name from `tabStock Entry` where company=%s and docstatus=1 and purpose!='Manufacture'", OSK
		)
		if ses:
			problems.append(f"submitted OSK Stock Entries that are not Manufacture: {[s[0] for s in ses]}")
		sis = frappe.get_all("Sales Invoice", filters={"company": OSK, "docstatus": 1}, pluck="name")
		btr_sis = {si for _pi, si in internal_transfer_pairs()}
		if set(sis) - btr_sis:
			problems.append(f"submitted OSK Sales Invoices that are not inter-company transfers: {set(sis) - btr_sis}")
		pis = frappe.get_all("Purchase Invoice", filters={"company": OSK, "docstatus": 1}, fields=["name", "update_stock", "is_internal_supplier"])
		for p in pis:
			if not p.update_stock or p.is_internal_supplier:
				problems.append(f"OSK Purchase Invoice {p.name} has update_stock=0 or internal supplier")
		paid = frappe.db.sql(
			"""select name from `tabPurchase Invoice` where company=%s and docstatus=1
			and abs(outstanding_amount - rounded_total) > 1 and abs(outstanding_amount - grand_total) > 1""",
			OSK,
		)
		if paid:
			problems.append(f"OSK Purchase Invoices with payments/allocations: {[p[0] for p in paid]}")

	failed = frappe.get_all("Repost Item Valuation", filters={"status": "Failed", "docstatus": 1}, pluck="name")
	if failed:
		warn(f"Failed Repost Item Valuation entries before migration: {failed}")

	if problems:
		for p in problems:
			log(f"PROBLEM: {p}", 1)
	else:
		log("pre-flight OK: no unexpected submitted documents in OSK/OZ", 1)
	return problems


def stock_snapshot(companies):
	rows = frappe.db.sql(
		"""select b.item_code, sum(b.actual_qty) qty, sum(b.stock_value) value
		from tabBin b join tabWarehouse w on w.name=b.warehouse
		where w.company in %s group by b.item_code""",
		(tuple(companies),),
		as_dict=1,
	)
	return {r.item_code: [flt(r.qty, 6), flt(r.value, 2)] for r in rows if abs(r.qty) > QTY_TOL or abs(r.value) > 0.005}


def supplier_outstanding(suppliers):
	out = {}
	for s in suppliers:
		out[s] = flt(
			frappe.db.sql(
				"""select sum(amount) from `tabPayment Ledger Entry`
				where party_type='Supplier' and party=%s and delinked=0 and company in %s""",
				(s, tuple(c for c in (OMG, OSK, OZ))),
			)[0][0],
			2,
		)
	return out


def take_baseline():
	sups = sorted(
		set(
			frappe.get_all(
				"Purchase Invoice", filters={"company": OSK, "docstatus": 1, "is_internal_supplier": 0}, pluck="supplier"
			)
		)
	)
	pi_out = {
		s: flt(
			frappe.db.sql(
				"""select sum(outstanding_amount) from `tabPurchase Invoice`
				where supplier=%s and docstatus=1 and company in %s""",
				(s, (OMG, OSK)),
			)[0][0],
			2,
		)
		for s in sups
	}
	return {
		"taken_at": frappe.utils.now(),
		"stock": stock_snapshot([OSK, OMG]),
		"stock_omg_only": stock_snapshot([OMG]),
		"suppliers": sups,
		"supplier_ple": supplier_outstanding(sups),
		"supplier_pi_outstanding": pi_out,
	}


def save_baseline(b):
	"""Written on --commit only (the file must describe the REAL pre-migration state)."""
	if DRY_RUN:
		return
	path = frappe.get_site_path("private", BASELINE_FILE)
	with open(path, "w") as f:
		json.dump(b, f, indent=1, ensure_ascii=False, default=str)
	log(f"baseline saved to {path}")


def load_or_take_baseline():
	path = frappe.get_site_path("private", BASELINE_FILE)
	if os.path.exists(path):
		log(f"baseline loaded from {path}")
		with open(path) as f:
			return json.load(f)
	if not company_exists(OSK) or not frappe.db.count("Bin", {"warehouse": ["like", "% - OSK"]}) and not frappe.db.count(
		"Purchase Invoice", {"company": OSK, "docstatus": 1}
	):
		warn("OSK already migrated and no baseline file found — verification compares against nothing")
		return None
	b = take_baseline()
	save_baseline(b)
	log(
		f"baseline: {len(b['stock'])} items, qty={sum(v[0] for v in b['stock'].values()):.4f}, "
		f"value={sum(v[1] for v in b['stock'].values()):.2f}; suppliers={b['supplier_pi_outstanding']}"
	)
	return b


# ─────────────────────────────────────────────────────────────── phase 1 ──


def internal_transfer_pairs():
	"""(purchase_invoice in OMG, sales_invoice in OSK) of every inter-company transfer.

	Taken from Branch Stock Transfer records when the table still exists, and ALSO from the
	ERPNext inter-company link (PI.inter_company_invoice_reference) so that the migration works
	even after the Branch Stock Transfer doctype has been removed from ozturkapp."""
	pairs = []
	if doctype_exists("Branch Stock Transfer"):
		for b in frappe.db.sql(
			"select purchase_invoice, sales_invoice from `tabBranch Stock Transfer` order by name", as_dict=1
		):
			pairs.append((b.purchase_invoice, b.sales_invoice))
	for r in frappe.db.sql(
		"""select pi.name, pi.inter_company_invoice_reference si from `tabPurchase Invoice` pi
		join `tabSales Invoice` si on si.name = pi.inter_company_invoice_reference
		where pi.company=%s and si.company=%s and pi.docstatus<2""",
		(OMG, OSK),
		as_dict=1,
	):
		if (r.name, r.si) not in pairs:
			pairs.append((r.name, r.si))
	return pairs


def phase1_undo_btr():
	header("PHASE 1 — undo Branch Stock Transfer (inter-company transfer)")
	if doctype_exists("Branch Stock Transfer"):
		for b in frappe.db.sql(
			"select name, docstatus, status from `tabBranch Stock Transfer` order by name", as_dict=1
		):
			if b.docstatus == 1:
				# mark first: a submitted BTR links to SI/PI and would block their cancellation
				set_docstatus_db("Branch Stock Transfer", b.name, 2, "Cancelled")
				log(f"{b.name}: marked Cancelled (docstatus=2) via db")
			else:
				log(f"{b.name}: already docstatus={b.docstatus} status={b.status}")
	else:
		log("no Branch Stock Transfer table — using PI.inter_company_invoice_reference links only")
	pairs = internal_transfer_pairs()
	if not pairs:
		log("no inter-company transfers")
	for pi, si in pairs:
		for dt, name in (("Purchase Invoice", pi), ("Sales Invoice", si)):
			if not name or not frappe.db.exists(dt, name):
				continue
			ds = frappe.db.get_value(dt, name, "docstatus")
			if ds == 1:
				doc = frappe.get_doc(dt, name)
				doc.flags.ignore_permissions = True
				doc.cancel()
				log(f"{dt} {name}: cancelled", 1)
			else:
				log(f"{dt} {name}: docstatus={ds} — skip", 1)


# ─────────────────────────────────────────────────────────────── phase 2 ──


def old_boms():
	return frappe.get_all("BOM", filters={"company": OSK, "docstatus": 1}, fields=["name", "item"], order_by="creation")


def bom_topo_order(names):
	names = list(names)
	nameset = set(names)
	deps = {}
	for n in names:
		deps[n] = {
			r
			for r in frappe.get_all(
				"BOM Item", filters={"parent": n, "parenttype": "BOM", "bom_no": ["is", "set"]}, pluck="bom_no"
			)
			if r in nameset and r != n
		}
	order, done = [], set()

	def visit(n, stack=()):
		if n in done:
			return
		if n in stack:
			frappe.throw(f"BOM recursion at {n}")
		for d in sorted(deps[n]):
			visit(d, stack + (n,))
		done.add(n)
		order.append(n)

	for n in names:
		visit(n)
	return order


def build_bom_map():
	"""old OSK BOM -> OMG BOM (works also after the OSK BOMs were cancelled)."""
	m = {}
	for b in frappe.get_all("BOM", filters={"company": OSK}, fields=["name", "item"]):
		new = frappe.db.get_value(
			"BOM", {"company": OMG, "item": b.item, "docstatus": 1, "is_active": 1}, "name", order_by="is_default desc, creation desc"
		)
		if new:
			m[b.name] = new
	return m


def phase2_boms():
	header("PHASE 2 — BOMs: copy OSK -> OMG")
	olds = old_boms()
	if not olds:
		log("no submitted OSK BOMs — nothing to do")
		return build_bom_map()
	order = bom_topo_order([b.name for b in olds])
	bom_map = {}
	created = reused = 0
	for name in order:
		old = frappe.get_doc("BOM", name)
		existing = frappe.db.get_value(
			"BOM", {"company": OMG, "item": old.item, "docstatus": 1, "is_active": 1}, "name", order_by="is_default desc, creation desc"
		)
		if existing:
			bom_map[name] = existing
			reused += 1
			log(f"{name} -> {existing} (already exists)", 1)
			continue
		new = frappe.copy_doc(old)
		new.company = OMG
		new.is_active = old.is_active
		new.is_default = old.is_default
		new.rm_cost_as_per = old.rm_cost_as_per
		for it in new.items:
			if it.bom_no:
				if it.bom_no in bom_map:
					it.bom_no = bom_map[it.bom_no]
				elif frappe.db.get_value("BOM", it.bom_no, "company") != OMG:
					frappe.throw(f"{name}: sub-BOM {it.bom_no} has no OMG copy")
		remap_doc(new, bom_map)
		new.flags.ignore_permissions = True
		new.insert()
		new.submit()
		bom_map[name] = new.name
		created += 1
		if not new.is_default or frappe.db.get_value("Item", new.item, "default_bom") != new.name:
			if old.is_default:
				frappe.db.set_value("Item", new.item, "default_bom", new.name)
		log(f"{name} -> {new.name} ({new.item}, qty {new.quantity}, {len(new.items)} rows, cost {flt(new.total_cost, 2)})", 1)
	log(f"BOMs created: {created}, reused: {reused}")
	return bom_map


# ─────────────────────────────────────────────────────────────── phase 3 ──


def sr_key_exists(src, docstatus):
	return frappe.db.get_value(
		"Stock Reconciliation",
		{
			"company": OMG,
			"purpose": src.purpose,
			"posting_date": src.posting_date,
			"posting_time": src.posting_time,
			"docstatus": docstatus,
		},
		"name",
	)


def copy_sr(src_name, submit):
	src = frappe.get_doc("Stock Reconciliation", src_name)
	new = frappe.copy_doc(src)
	remap_doc(new)
	new.company = OMG
	new.set_warehouse = TARGET_WH
	for it in new.items:
		it.warehouse = TARGET_WH
		it.current_qty = it.current_valuation_rate = it.current_amount = 0
		it.serial_and_batch_bundle = None
		it.current_serial_and_batch_bundle = None
	new.posting_date = src.posting_date
	new.posting_time = src.posting_time
	new.set_posting_time = 1
	if src.purpose == "Opening Stock":
		new.expense_account = TEMP_OPENING
	new.cost_center = frappe.get_cached_value("Company", OMG, "cost_center")
	new.flags.ignore_permissions = True
	new.insert()
	if submit:
		new.submit()
	return new


def phase3_stock_reco():
	header("PHASE 3 — Stock Reconciliation(s) -> OMG")
	srs = frappe.get_all(
		"Stock Reconciliation", filters={"company": OSK, "docstatus": 1}, fields=["name", "purpose", "posting_date", "posting_time"],
		order_by="posting_date, posting_time, creation",
	)
	if not srs:
		log("no submitted OSK Stock Reconciliation — nothing to do")
	for s in srs:
		ex = sr_key_exists(s, 1)
		if ex:
			log(f"{s.name} -> {ex} (already exists)", 1)
			continue
		new = copy_sr(s.name, submit=True)
		tot = sum(flt(i.qty) * flt(i.valuation_rate) for i in new.items)
		log(f"{s.name} -> {new.name}: {len(new.items)} items, value {tot:.2f}, {new.posting_date} {new.posting_time}, diff acc {new.expense_account}", 1)


# ─────────────────────────────────────────────────────────────── phase 4 ──


def phase4_production(bom_map):
	header("PHASE 4 — Production Entries / Manufacture Stock Entries -> OMG")
	has_pe = doctype_exists("Production Entry")
	has_link = has_column("Stock Entry", "custom_production_entry")
	if has_pe:
		pes = frappe.db.sql(
			"""select name, stock_entry from `tabProduction Entry`
			where company=%s and docstatus=1 order by posting_date, posting_time, creation, name""",
			OSK,
			as_dict=1,
		)
	else:
		pes = []
	se_names = [p.stock_entry for p in pes if p.stock_entry]
	# Stock entries of OSK without PE (pre-flight normally refuses these)
	other = frappe.get_all(
		"Stock Entry", filters={"company": OSK, "docstatus": 1, "name": ["not in", se_names or [""]]},
		pluck="name", order_by="posting_date, posting_time, creation",
	)
	work = [(p, p.stock_entry) for p in pes] + [(None, s) for s in other]
	if not work:
		log("no submitted OSK production — nothing to do")
	for pe, se_name in work:
		src_se = frappe.get_doc("Stock Entry", se_name)
		new_bom = bom_map.get(src_se.bom_no, src_se.bom_no) if src_se.bom_no else None
		fg = [d for d in src_se.items if d.is_finished_item or (d.t_warehouse and not d.s_warehouse)]
		fg_item = fg[0].item_code if fg else None
		ex = frappe.db.sql(
			"""select se.name from `tabStock Entry` se join `tabStock Entry Detail` d on d.parent=se.name
			where se.company=%s and se.docstatus=1 and se.purpose=%s and se.posting_date=%s and se.posting_time=%s
			and ifnull(se.fg_completed_qty,0)=%s and d.item_code=%s and ifnull(d.t_warehouse,'')!='' limit 1""",
			(OMG, src_se.purpose, src_se.posting_date, src_se.posting_time, flt(src_se.fg_completed_qty), fg_item),
		)
		if ex:
			log(f"{pe.name if pe else ''} {se_name} -> {ex[0][0]} (already exists)", 1)
			continue

		new_pe = None
		if pe and has_pe:
			try:
				src_pe = frappe.get_doc("Production Entry", pe.name)
				new_pe = frappe.copy_doc(src_pe)
				remap_doc(new_pe, bom_map)
				new_pe.company = OMG
				new_pe.target_warehouse = TARGET_WH
				for it in new_pe.items:
					it.source_warehouse = TARGET_WH
				new_pe.stock_entry = None
				new_pe.posting_date, new_pe.posting_time = src_pe.posting_date, src_pe.posting_time
				new_pe.docstatus = 1
				new_pe.status = "Submitted"
				from frappe.model.naming import set_new_name

				set_new_name(new_pe)
				new_pe.set_parent_in_children()
				new_pe.db_insert()
				for ch in new_pe.get_all_children():
					ch.docstatus = 1
					ch.db_insert()
			except Exception as e:
				new_pe = None
				warn(f"could not mirror Production Entry {pe.name} ({e}); Stock Entry is created anyway")

		se = frappe.copy_doc(src_se)
		remap_doc(se, bom_map)
		se.company = OMG
		se.bom_no = new_bom
		se.posting_date, se.posting_time, se.set_posting_time = src_se.posting_date, src_se.posting_time, 1
		se.from_warehouse = TARGET_WH if src_se.from_warehouse else None
		se.to_warehouse = TARGET_WH if src_se.to_warehouse else None
		for d in se.items:
			d.s_warehouse = TARGET_WH if d.s_warehouse else None
			d.t_warehouse = TARGET_WH if d.t_warehouse else None
			d.serial_and_batch_bundle = None
			d.expense_account = map_account(d.expense_account) if d.expense_account else None
		if has_link:
			se.custom_production_entry = new_pe.name if new_pe else None
		se.flags.ignore_permissions = True
		se.insert()
		se.submit()
		if new_pe:
			frappe.db.set_value("Production Entry", new_pe.name, "stock_entry", se.name, update_modified=False)
		log(
			f"{pe.name if pe else '-'}/{se_name} -> {new_pe.name if new_pe else '-'}/{se.name}: {fg_item} x {se.fg_completed_qty}, "
			f"BOM {new_bom}, out {flt(se.total_outgoing_value, 2)} (orig {flt(src_se.total_outgoing_value, 2)}), "
			f"in {flt(se.total_incoming_value, 2)} (orig {flt(src_se.total_incoming_value, 2)})",
			1,
		)
		if abs(flt(se.total_outgoing_value) - flt(src_se.total_outgoing_value)) > VALUE_TOL:
			warn(f"{se.name}: outgoing value differs from original {se_name}")


# ─────────────────────────────────────────────────────────────── phase 5 ──


def phase5_purchase_invoices():
	header("PHASE 5 — Purchase Invoices -> OMG")
	pis = frappe.get_all(
		"Purchase Invoice", filters={"company": OSK, "docstatus": 1}, fields=["name"], order_by="posting_date, posting_time, creation"
	)
	if not pis:
		log("no submitted OSK Purchase Invoices — nothing to do")
	for p in pis:
		src = frappe.get_doc("Purchase Invoice", p.name)
		ex = frappe.db.get_value(
			"Purchase Invoice",
			{
				"company": OMG,
				"docstatus": 1,
				"supplier": src.supplier,
				"posting_date": src.posting_date,
				"posting_time": src.posting_time,
				"grand_total": src.grand_total,
			},
			"name",
		)
		if ex:
			log(f"{src.name} -> {ex} (already exists)", 1)
			continue
		new = frappe.copy_doc(src)
		remap_doc(new)
		new.company = OMG
		new.set_warehouse = TARGET_WH
		new.credit_to = map_account(src.credit_to)
		new.posting_date, new.posting_time, new.set_posting_time = src.posting_date, src.posting_time, 1
		new.bill_no, new.bill_date, new.due_date = src.bill_no, src.bill_date, src.due_date
		new.update_stock = 1
		new.payment_schedule = []
		for it in new.items:
			it.warehouse = TARGET_WH
			it.serial_and_batch_bundle = None
			it.purchase_receipt = it.pr_detail = None
		new.flags.ignore_permissions = True
		new.insert()
		new.submit()
		log(
			f"{src.name} -> {new.name}: {src.supplier}, {src.posting_date} {src.posting_time}, {len(new.items)} items, "
			f"grand {flt(new.grand_total, 2)} (orig {flt(src.grand_total, 2)}), outstanding {flt(new.outstanding_amount, 2)}",
			1,
		)
		if abs(flt(new.grand_total) - flt(src.grand_total)) > 0.01:
			warn(f"{new.name}: grand total differs from {src.name}")


# ─────────────────────────────────────────────────────────────── phase 6 ──


def item_sig(dt, parent, fields="item_code, qty"):
	return sorted(tuple(flt(x, 6) if isinstance(x, float) else x for x in r) for r in frappe.db.sql(f"select {fields} from `tab{dt}` where parent=%s", parent))


def phase6_drafts():
	header("PHASE 6 — draft documents of OSK")
	decisions = []

	# Stock Entries
	sub_sigs = {
		s: item_sig("Stock Entry Detail", s, "item_code, qty, s_warehouse is not null")
		for s in frappe.get_all("Stock Entry", filters={"company": ["in", [OSK, OMG]], "docstatus": 1}, pluck="name")
	}
	for s in frappe.get_all("Stock Entry", filters={"company": OSK, "docstatus": 0}, pluck="name", order_by="name"):
		sig = item_sig("Stock Entry Detail", s, "item_code, qty, s_warehouse is not null")
		dup = [k for k, v in sub_sigs.items() if v == sig]
		if dup:
			decisions.append(f"Stock Entry {s}: duplicate of submitted {dup[0]} — not re-created")
		else:
			d = frappe.copy_doc(frappe.get_doc("Stock Entry", s))
			remap_doc(d, build_bom_map())
			d.company = OMG
			for r in d.items:
				r.s_warehouse = TARGET_WH if r.s_warehouse else None
				r.t_warehouse = TARGET_WH if r.t_warehouse else None
			d.flags.ignore_permissions = True
			d.insert()
			decisions.append(f"Stock Entry {s}: re-created as draft {d.name}")

	# Purchase Receipts
	prs = frappe.get_all("Purchase Receipt", filters={"company": OSK, "docstatus": 0}, fields=["name", "supplier"], order_by="name")
	pi_sigs = {
		p.name: (p.supplier, item_sig("Purchase Invoice Item", p.name, "item_code, qty, rate"))
		for p in frappe.get_all("Purchase Invoice", filters={"company": ["in", [OSK, OMG]], "docstatus": 1}, fields=["name", "supplier"])
	}
	rest = []
	for pr in prs:
		sig = (pr.supplier, item_sig("Purchase Receipt Item", pr.name, "item_code, qty, rate"))
		dup = [k for k, v in pi_sigs.items() if v == sig]
		if dup:
			decisions.append(f"Purchase Receipt {pr.name}: duplicate of submitted {dup[0]} — not re-created")
		else:
			rest.append(pr.name)
	if rest:
		# are the remaining drafts exactly the purchases summarised in a submitted opening reconciliation?
		agg_q, agg_v = defaultdict(float), defaultdict(float)
		for n in rest:
			for r in frappe.db.sql("select item_code, stock_qty, amount from `tabPurchase Receipt Item` where parent=%s", n):
				agg_q[r[0]] += flt(r[1])
				agg_v[r[0]] += flt(r[2])
		covered_by = None
		for sr in frappe.get_all("Stock Reconciliation", filters={"company": ["in", [OSK, OMG]], "docstatus": 1, "purpose": "Opening Stock"}, pluck="name"):
			items = {r[0]: (flt(r[1]), flt(r[2])) for r in frappe.db.sql("select item_code, qty, amount from `tabStock Reconciliation Item` where parent=%s", sr)}
			if set(items) == set(agg_q) and all(
				abs(items[k][0] - agg_q[k]) < QTY_TOL and abs(items[k][1] - agg_v[k]) < 0.05 for k in items
			):
				covered_by = sr
				break
		if covered_by:
			decisions.append(
				f"Purchase Receipts {', '.join(rest)}: their summed qty/amount equal opening Stock Reconciliation "
				f"{covered_by} exactly (superseded) — not re-created"
			)
		else:
			for n in rest:
				d = frappe.copy_doc(frappe.get_doc("Purchase Receipt", n))
				remap_doc(d)
				d.company = OMG
				d.set_warehouse = TARGET_WH
				for r in d.items:
					r.warehouse = TARGET_WH
				d.flags.ignore_permissions = True
				d.insert()
				decisions.append(f"Purchase Receipt {n}: re-created as draft {d.name}")

	# Stock Reconciliations
	sr_sigs = {
		s: item_sig("Stock Reconciliation Item", s, "item_code, qty, valuation_rate")
		for s in frappe.get_all("Stock Reconciliation", filters={"company": ["in", [OSK, OMG]], "docstatus": ["<", 2]}, pluck="name")
	}
	for s in frappe.get_all("Stock Reconciliation", filters={"company": OSK, "docstatus": 0}, pluck="name"):
		sig = sr_sigs[s]
		dup = [k for k, v in sr_sigs.items() if v == sig and k != s and frappe.db.get_value("Stock Reconciliation", k, "company") == OMG]
		if dup:
			decisions.append(f"Stock Reconciliation {s}: already re-created as {dup[0]}")
			continue
		d = copy_sr(s, submit=False)
		decisions.append(
			f"Stock Reconciliation {s}: NOT a duplicate (same qtys, different rates) — re-created as DRAFT {d.name}"
		)

	# other drafts / anything else
	for dt in ("Purchase Invoice", "Sales Invoice", "Payment Entry", "Journal Entry", "Material Request", "Purchase Order"):
		for n in frappe.get_all(dt, filters={"company": OSK, "docstatus": 0}, pluck="name"):
			warn(f"draft {dt} {n} in OSK is NOT migrated (will be deleted with the company)")
	if not decisions:
		log("no OSK drafts — nothing to do")
	for d in decisions:
		log(d, 1)
	return decisions


# ─────────────────────────────────────────────────────────────── phase 7a ──


def phase7a_cancel_originals():
	header("PHASE 7a — cancel OSK originals")
	# Purchase Invoices: newest first
	for n in frappe.get_all("Purchase Invoice", filters={"company": OSK, "docstatus": 1}, pluck="name", order_by="posting_date desc, posting_time desc, creation desc"):
		d = frappe.get_doc("Purchase Invoice", n)
		d.flags.ignore_permissions = True
		d.cancel()
		log(f"Purchase Invoice {n}: cancelled", 1)

	# Production Entries (db only) + their Stock Entries (ERPNext cancel)
	if doctype_exists("Production Entry"):
		for pe in frappe.db.sql(
			"select name, stock_entry from `tabProduction Entry` where company=%s and docstatus=1 order by posting_date desc, posting_time desc, creation desc",
			OSK,
			as_dict=1,
		):
			set_docstatus_db("Production Entry", pe.name, 2, "Cancelled")
			log(f"Production Entry {pe.name}: marked Cancelled via db", 1)
	for n in frappe.get_all("Stock Entry", filters={"company": OSK, "docstatus": 1}, pluck="name", order_by="posting_date desc, posting_time desc, creation desc"):
		d = frappe.get_doc("Stock Entry", n)
		d.flags.ignore_permissions = True
		d.cancel()
		log(f"Stock Entry {n}: cancelled", 1)

	for n in frappe.get_all("Stock Reconciliation", filters={"company": OSK, "docstatus": 1}, pluck="name", order_by="posting_date desc, posting_time desc, creation desc"):
		d = frappe.get_doc("Stock Reconciliation", n)
		d.flags.ignore_permissions = True
		d.cancel()
		log(f"Stock Reconciliation {n}: cancelled", 1)

	olds = [b.name for b in old_boms()]
	if olds:
		n_c = 0
		for n in reversed(bom_topo_order(olds)):
			d = frappe.get_doc("BOM", n)
			d.flags.ignore_permissions = True
			d.cancel()
			n_c += 1
		log(f"BOMs cancelled: {n_c}", 1)
	left = {
		dt: frappe.db.count(dt, {"company": OSK, "docstatus": 1})
		for dt in ("Purchase Invoice", "Stock Entry", "Stock Reconciliation", "BOM", "Sales Invoice")
	}
	log(f"remaining submitted OSK docs: {left}")
	if any(left.values()):
		frappe.throw(f"OSK still has submitted documents: {left}")


# ─────────────────────────────────────────────────────────────── phase 8 ──


def phase8_repost(label):
	header(f"PHASE 8 — Repost Item Valuation ({label})")
	from erpnext.stock.doctype.repost_item_valuation.repost_item_valuation import repost

	done = 0
	for _ in range(20):
		pending = frappe.db.sql(
			"""select name from `tabRepost Item Valuation` where docstatus=1 and status in ('Queued','In Progress')
			order by timestamp(posting_date, posting_time) asc, creation asc""",
			as_dict=1,
		)
		if not pending:
			break
		for r in pending:
			doc = frappe.get_doc("Repost Item Valuation", r.name)
			if doc.status not in ("Queued", "In Progress"):
				continue
			repost(doc)
			st = frappe.db.get_value("Repost Item Valuation", r.name, "status")
			if st == "Failed":
				err = frappe.db.get_value("Repost Item Valuation", r.name, "error_log")
				frappe.throw(f"Repost {r.name} failed: {(err or '')[-2000:]}")
			doc.reload()
			try:
				doc.deduplicate_similar_repost()
			except Exception:
				pass
			done += 1
	left = frappe.db.sql(
		"select status, count(*) from `tabRepost Item Valuation` where docstatus=1 and status in ('Queued','In Progress','Failed') group by status"
	)
	log(f"reposts processed: {done}; still pending/failed: {left or 'none'}")
	return done


# ─────────────────────────────────────────────────────────────── phase 7b ──


def internal_parties():
	cust = frappe.db.sql_list(
		"select name from tabCustomer where is_internal_customer=1 or ifnull(represents_company,'')!=''"
	)
	sup = frappe.db.sql_list(
		"select name from tabSupplier where is_internal_supplier=1 or ifnull(represents_company,'')!=''"
	)
	return cust, sup


def delete_cancelled_docs_of_internal_parties():
	cust, sup = internal_parties()
	todo = []
	for dt, field, parties in (
		("Purchase Invoice", "supplier", sup),
		("Purchase Receipt", "supplier", sup),
		("Sales Invoice", "customer", cust),
		("Delivery Note", "customer", cust),
	):
		if not parties:
			continue
		for r in frappe.get_all(dt, filters={field: ["in", parties], "company": OMG}, fields=["name", "docstatus"]):
			if r.docstatus != 2:
				frappe.throw(f"{dt} {r.name} (OMG, docstatus {r.docstatus}) uses internal party — resolve manually")
			todo.append((dt, r.name))
	if not todo:
		log("no cancelled OMG documents of internal parties", 1)
		return
	old = frappe.db.get_single_value("Accounts Settings", "delete_linked_ledger_entries")
	frappe.db.set_single_value("Accounts Settings", "delete_linked_ledger_entries", 1)
	try:
		for dt, n in todo:
			frappe.delete_doc(dt, n, ignore_permissions=True, force=True)
			log(f"deleted cancelled {dt} {n} (+ its cancelled ledger entries)", 1)
	finally:
		frappe.db.set_single_value("Accounts Settings", "delete_linked_ledger_entries", old or 0)


def run_transaction_deletion(company):
	if not company_exists(company):
		log(f"{company}: company already deleted", 1)
		return
	done = frappe.db.get_value(
		"Transaction Deletion Record", {"company": company, "docstatus": 1, "status": "Completed"}, "name"
	)
	if done:
		log(f"{company}: Transaction Deletion Record {done} already Completed", 1)
		return
	running = frappe.db.get_value(
		"Transaction Deletion Record", {"company": company, "docstatus": 1, "status": ["in", ["Queued", "Running", "Failed"]]}, "name"
	)
	if running:
		tdr = frappe.get_doc("Transaction Deletion Record", running)
		log(f"{company}: resuming {running} ({tdr.status})", 1)
	else:
		tdr = frappe.new_doc("Transaction Deletion Record")
		tdr.company = company
		tdr.process_in_single_transaction = 1
		tdr.populate_doctypes_to_be_ignored_table()  # same default exclusions as the UI
		tdr.flags.ignore_permissions = True
		tdr.insert()
		tdr.submit()
	tdr.process_in_single_transaction = 1
	tdr.db_set("process_in_single_transaction", 1)

	# ERPNext's execute_task() swallows exceptions with a full frappe.db.rollback(); run the
	# task chain ourselves so that an error propagates and is reported properly.
	def _execute_task(task_to_execute=None):
		getattr(tdr, tdr.task_to_internal_method_map[task_to_execute])()

	tdr.execute_task = _execute_task
	tdr.start_deletion_tasks()
	tdr.reload()
	if tdr.status != "Completed":
		frappe.throw(f"Transaction Deletion {tdr.name} for {company}: status {tdr.status}\n{(tdr.error_log or '')[-3000:]}")
	deleted = [(d.doctype_name, d.no_of_docs) for d in tdr.doctypes if d.no_of_docs]
	log(f"{company}: {tdr.name} Completed — deleted {deleted}", 1)


def phase7b_delete_companies():
	header("PHASE 7b — delete OSK / OZ")
	if not company_exists(OSK) and not company_exists(OZ):
		log("companies already deleted — checking leftovers only")

	log("step 1: cancelled OMG documents of internal parties")
	delete_cancelled_docs_of_internal_parties()
	checkpoint("deleted cancelled internal-party documents")

	log("step 2: Transaction Deletion for OSK")
	run_transaction_deletion(OSK)
	checkpoint("transaction deletion OSK")

	log("step 3: internal Customers / Suppliers")
	cust, sup = internal_parties()
	for dt, names in (("Customer", cust), ("Supplier", sup)):
		for n in names:
			frappe.delete_doc(dt, n, ignore_permissions=True, force=True)
			log(f"deleted {dt} {n}", 1)
	if not cust and not sup:
		log("none left", 1)

	log("step 4: Mode of Payment rows / 'Нахт Sklad'")
	for mop in frappe.get_all("Mode of Payment", pluck="name"):
		rows = frappe.get_all("Mode of Payment Account", filters={"parent": mop}, fields=["company"])
		old_rows = [r for r in rows if r.company in OLD_COMPANIES]
		if old_rows and len(old_rows) == len(rows):
			frappe.delete_doc("Mode of Payment", mop, ignore_permissions=True, force=True)
			log(f"deleted Mode of Payment {mop} (only had OSK/OZ accounts)", 1)
		elif old_rows:
			frappe.db.delete("Mode of Payment Account", {"parent": mop, "company": ["in", OLD_COMPANIES]})
			log(f"Mode of Payment {mop}: removed {len(old_rows)} OSK/OZ rows", 1)

	log("step 5: Single doctype values / Item Defaults / Global defaults")
	old_names = set(OLD_COMPANIES)
	old_names |= set(frappe.get_all("Warehouse", filters={"company": ["in", OLD_COMPANIES]}, pluck="name"))
	old_names |= set(frappe.get_all("Account", filters={"company": ["in", OLD_COMPANIES]}, pluck="name"))
	old_names |= set(frappe.get_all("Cost Center", filters={"company": ["in", OLD_COMPANIES]}, pluck="name"))
	if old_names:
		singles = frappe.db.sql(
			"select doctype, field, value from tabSingles where value in %s", (tuple(old_names),), as_dict=1
		)
		for s in singles:
			frappe.db.sql("update tabSingles set value=NULL where doctype=%s and field=%s", (s.doctype, s.field))
			frappe.clear_document_cache(s.doctype, s.doctype)
			log(f"{s.doctype}.{s.field} = {s.value!r} -> NULL", 1)
		n = frappe.db.sql("select count(*) from tabDefaultValue where defvalue in %s", (tuple(old_names),))[0][0]
		if n:
			frappe.db.sql("delete from tabDefaultValue where defvalue in %s", (tuple(old_names),))
			log(f"removed {n} DefaultValue rows", 1)
	# child-table rows that carry an OSK/OZ company (Item Default, Mode of Payment Account,
	# Allowed To Transact With, Party Account, Ozturk Settings.company_cash, ...)
	for dt, fn in child_company_link_fields():
		n = frappe.db.sql(f"select count(*) from `tab{dt}` where `{fn}` in %s", (OLD_COMPANIES,))[0][0]
		if n:
			frappe.db.sql(f"delete from `tab{dt}` where `{fn}` in %s", (OLD_COMPANIES,))
			log(f"{dt}: deleted {n} rows with {fn} in OSK/OZ", 1)
	if doctype_exists("User Permission"):
		for up in frappe.get_all("User Permission", filters={"allow": "Company", "for_value": ["in", OLD_COMPANIES]}, pluck="name"):
			frappe.delete_doc("User Permission", up, ignore_permissions=True)
			log(f"deleted User Permission {up}", 1)

	log("step 6: OMG becomes a stand-alone company")
	for c in (OMG, OSK, OZ):
		if company_exists(c):
			vals = frappe.db.get_value("Company", c, ["parent_company", "existing_company"], as_dict=1)
			upd = {}
			if vals.parent_company in OLD_COMPANIES:
				upd["parent_company"] = None
			if c == OMG and vals.existing_company in OLD_COMPANIES:
				# "Existing Company" CoA source without existing_company fails Company.validate
				upd.update(
					existing_company=None,
					create_chart_of_accounts_based_on="Standard Template",
					chart_of_accounts="Standard",
				)
			if upd:
				frappe.db.set_value("Company", c, upd, update_modified=False)
				log(f"{c}: {upd}", 1)
	from frappe.utils.nestedset import rebuild_tree

	rebuild_tree("Company")
	frappe.clear_cache(doctype="Company")
	checkpoint("parties / MoP / defaults / parent company")

	log("step 7: Transaction Deletion for OZ")
	run_transaction_deletion(OZ)
	checkpoint("transaction deletion OZ")

	log("step 8: delete companies")
	for c in (OSK, OZ):
		if not company_exists(c):
			log(f"{c}: already deleted", 1)
			continue
		frappe.db.set_value("Company", c, "is_group", 0, update_modified=False)
		frappe.delete_doc("Company", c, ignore_permissions=True, force=True)
		log(f"deleted Company {c}", 1)
		checkpoint(f"delete company {c}")
	delete_orphan_children()
	checkpoint("orphan child rows")
	frappe.clear_cache()


# ─────────────────────────────────────────────────────────────── phase 9 ──


def phase9_verify(baseline):
	header("PHASE 9 — verification")
	ok = True

	comps = frappe.get_all("Company", pluck="name")
	log(f"companies: {comps}  {'OK' if comps == [OMG] else 'NOT OK'}")
	ok &= comps == [OMG]
	pc = frappe.db.get_value("Company", OMG, ["parent_company", "is_group", "lft", "rgt"], as_dict=1)
	log(f"OMG parent_company={pc.parent_company!r} is_group={pc.is_group} lft/rgt={pc.lft}/{pc.rgt}")

	# 1. stock per item
	after = stock_snapshot([OMG])
	whs = frappe.db.sql(
		"select b.warehouse, count(*), sum(b.actual_qty), sum(b.stock_value) from tabBin b join tabWarehouse w on w.name=b.warehouse where w.company=%s and (b.actual_qty!=0 or b.stock_value!=0) group by b.warehouse",
		OMG,
	)
	log(f"OMG bins by warehouse: {[(w, n, flt(q, 4), flt(v, 2)) for w, n, q, v in whs]}")
	if baseline and baseline.get("stock_after_btr_undo"):
		ref = baseline["stock_after_btr_undo"]
		vd = []
		for item in sorted(set(ref) | set(after)):
			r = ref.get(item, [0, 0])
			a = after.get(item, [0, 0])
			if abs(r[0] - a[0]) > QTY_TOL or abs(r[1] - a[1]) > VALUE_TOL:
				vd.append((item, r, a))
		log(
			f"stock right after undoing the transfer (OSK+OMG): {len(ref)} items, qty {sum(v[0] for v in ref.values()):.4f}, "
			f"value {sum(v[1] for v in ref.values()):.2f}"
		)
		if vd:
			ok = False
			log(f"MISMATCH vs post-undo state for {len(vd)} items:")
			for d in vd:
				log(f"{d[0]}: expected {d[1]} after {d[2]}", 1)
		else:
			log("per-item qty AND value vs post-undo state: ALL MATCH")
	if baseline:
		before = baseline["stock"]
		log("comparison with the ORIGINAL pre-migration state (qty must match; value differences come only from the")
		log("inter-company transfer having been priced differently from the FIFO layers / negative OSK stock):")
		diffs = []
		for item in sorted(set(before) | set(after)):
			b = before.get(item, [0, 0])
			a = after.get(item, [0, 0])
			if abs(b[0] - a[0]) > QTY_TOL or abs(b[1] - a[1]) > VALUE_TOL:
				diffs.append((item, b, a))
		tb_q = sum(v[0] for v in before.values())
		tb_v = sum(v[1] for v in before.values())
		ta_q = sum(v[0] for v in after.values())
		ta_v = sum(v[1] for v in after.values())
		log(f"stock BEFORE (OSK+OMG): {len(before)} items, qty {tb_q:.4f}, value {tb_v:.2f}")
		log(f"stock AFTER  (OMG)    : {len(after)} items, qty {ta_q:.4f}, value {ta_v:.2f}")
		small = [d for d in diffs if abs(d[1][0] - d[2][0]) <= QTY_TOL]
		big = [d for d in diffs if abs(d[1][0] - d[2][0]) > QTY_TOL]
		if big:
			ok = False
			log(f"QTY MISMATCH for {len(big)} items:")
			for d in big:
				log(f"{d[0]}: before {d[1]} after {d[2]}", 1)
		if small:
			log(f"value-only differences > {VALUE_TOL} UZS for {len(small)} items:")
			for d in small:
				log(f"{d[0]}: before {d[1]} after {d[2]} (diff {d[2][1] - d[1][1]:+.2f})", 1)
		if not diffs:
			log("per-item qty and value: ALL MATCH")
		max_dv = max([abs(after.get(i, [0, 0])[1] - before.get(i, [0, 0])[1]) for i in set(before) | set(after)] or [0])
		log(f"max per-item value difference: {max_dv:.4f} UZS; total value difference {ta_v - tb_v:+.4f}")

	# 2. GL balanced
	for c, d, cr, n in frappe.db.sql(
		"select company, sum(debit), sum(credit), count(*) from `tabGL Entry` where is_cancelled=0 group by company"
	):
		bal = flt(d - cr, 2)
		log(f"GL {c}: {n} entries, debit {flt(d, 2)}, credit {flt(cr, 2)}, diff {bal}  {'OK' if abs(bal) < 0.01 else 'NOT BALANCED'}")
		ok &= abs(bal) < 0.01
	tb = frappe.db.sql(
		"""select a.root_type, sum(g.debit-g.credit) from `tabGL Entry` g join tabAccount a on a.name=g.account
		where g.is_cancelled=0 and g.company=%s group by a.root_type""",
		OMG,
	)
	log(f"OMG trial balance by root type (Dr-Cr): {[(r, flt(v, 2)) for r, v in tb]}")
	accs = frappe.db.sql(
		"""select g.account, sum(g.debit-g.credit) from `tabGL Entry` g where g.is_cancelled=0 and g.company=%s
		group by g.account having abs(sum(g.debit-g.credit))>0.005 order by g.account""",
		OMG,
	)
	for a, v in accs:
		log(f"{a:45s} {flt(v, 2):>18,.2f}", 1)

	# 3. stock vs account
	from erpnext.accounts.utils import get_stock_and_account_balance

	for acc in frappe.db.sql_list(
		"select distinct account from tabWarehouse where company=%s and ifnull(account,'')!=''", OMG
	) or [frappe.get_cached_value("Company", OMG, "default_inventory_account")]:
		acc_bal, stock_bal, _ = get_stock_and_account_balance(acc, frappe.utils.add_days(frappe.utils.today(), 3650), OMG)
		diff = flt(acc_bal - stock_bal, 2)
		log(f"stock vs account {acc}: account {flt(acc_bal, 2)}, stock {flt(stock_bal, 2)}, diff {diff}  {'OK' if abs(diff) < VALUE_TOL else 'MISMATCH'}")
		ok &= abs(diff) < VALUE_TOL

	# 4. negative bins
	neg = frappe.db.sql("select item_code, warehouse, actual_qty from tabBin where actual_qty < -0.000001", as_dict=1)
	log(f"negative bins: {len(neg)}")
	for r in neg:
		log(f"{r.item_code} @ {r.warehouse}: {r.actual_qty}", 1)

	# 5. supplier outstanding
	if baseline:
		for s in baseline["suppliers"]:
			now_pi = flt(
				frappe.db.sql(
					"select sum(outstanding_amount) from `tabPurchase Invoice` where supplier=%s and docstatus=1 and company=%s",
					(s, OMG),
				)[0][0],
				2,
			)
			now_ple = flt(
				frappe.db.sql(
					"select sum(amount) from `tabPayment Ledger Entry` where party_type='Supplier' and party=%s and delinked=0 and company=%s",
					(s, OMG),
				)[0][0],
				2,
			)
			b_pi = baseline["supplier_pi_outstanding"].get(s, 0)
			b_ple = baseline["supplier_ple"].get(s, 0)
			good = abs(now_pi - b_pi) < 1 and abs(now_ple - b_ple) < 1
			ok &= good
			log(f"supplier {s}: PI outstanding before {b_pi} after {now_pi}; ledger before {b_ple} after {now_ple}  {'OK' if good else 'MISMATCH'}")

	# 6. reposts
	rp = frappe.db.sql(
		"select status, count(*) from `tabRepost Item Valuation` where docstatus=1 group by status"
	)
	log(f"Repost Item Valuation by status: {rp}")
	ok &= not any(s in ("Queued", "In Progress", "Failed") for s, _ in rp)

	# 7. leftovers referencing OSK/OZ
	left = []
	for dt, fn in company_link_fields():
		n = frappe.db.sql(f"select count(*) from `tab{dt}` where `{fn}` in %s", (OLD_COMPANIES,))[0][0]
		if n:
			left.append((dt, fn, n))
	for dt, fn, opt in frappe.db.sql(
		"""select parent, fieldname, options from tabDocField where fieldtype='Link' and options in ('Warehouse','Account','Cost Center')
		union select dt, fieldname, options from `tabCustom Field` where fieldtype='Link' and options in ('Warehouse','Account','Cost Center')"""
	):
		try:
			if frappe.get_meta(dt).issingle or not frappe.db.table_exists(dt) or not has_column(dt, fn):
				continue
		except Exception:
			continue
		n = frappe.db.sql(
			f"select count(*) from `tab{dt}` where `{fn}` like '%% - OSK' or `{fn}` like '%% - OZ'"
		)[0][0]
		if n:
			left.append((dt, fn, n))
	sv = frappe.db.sql(
		"select doctype, field, value from tabSingles where value in %s or value like '%% - OSK' or value like '%% - OZ'",
		(OLD_COMPANIES,),
	)
	left += [("Singles", f"{a}.{b}", c) for a, b, c in sv]
	cust, sup = internal_parties()
	if cust or sup:
		left.append(("internal parties", "", cust + sup))
	if frappe.db.exists("Mode of Payment", "Нахт Sklad"):
		left.append(("Mode of Payment", "Нахт Sklad", 1))
	log(f"remaining references to OSK/OZ: {left or 'none'}")
	ok &= not left

	# 8. BOM sanity
	bad_bom = frappe.db.sql(
		"""select i.name, i.default_bom from tabItem i left join tabBOM b on b.name=i.default_bom
		where ifnull(i.default_bom,'')!='' and (b.name is null or b.docstatus!=1 or b.company!=%s)""",
		OMG,
	)
	nb = frappe.db.count("BOM", {"company": OMG, "docstatus": 1, "is_active": 1, "is_default": 1})
	log(f"OMG active default BOMs: {nb}; items with invalid default_bom: {bad_bom or 'none'}")
	ok &= not bad_bom

	# 9. open POS shift ingredient check (simulation only)
	pos_check()

	log(f"VERIFICATION RESULT: {'PASS' if ok else 'FAIL'}")
	return ok


def pos_check():
	shifts = frappe.get_all(
		"POS Opening Entry", filters={"docstatus": 1, "status": "Open"}, fields=["name", "pos_profile", "period_start_date", "user"]
	)
	for sh in shifts:
		wh = frappe.db.get_value("POS Profile", sh.pos_profile, "warehouse")
		invs = frappe.db.sql_list(
			"""select name from `tabPOS Invoice` where docstatus=1 and pos_profile=%s and ifnull(consolidated_invoice,'')=''
			and timestamp(posting_date, posting_time) >= %s""",
			(sh.pos_profile, sh.period_start_date),
		)
		need = defaultdict(float)
		bundles = {
			b.new_item_code: b.name for b in frappe.get_all("Product Bundle", filters={"disabled": 0} if has_column("Product Bundle", "disabled") else {}, fields=["name", "new_item_code"])
		}
		for it in frappe.db.sql(
			"select item_code, stock_qty from `tabPOS Invoice Item` where parent in %s", (tuple(invs) or ("",),), as_dict=1
		):
			if it.item_code in bundles:
				for c in frappe.db.sql(
					"select item_code, qty from `tabProduct Bundle Item` where parent=%s", bundles[it.item_code], as_dict=1
				):
					need[c.item_code] += flt(c.qty) * flt(it.stock_qty)
			elif frappe.get_cached_value("Item", it.item_code, "is_stock_item"):
				need[it.item_code] += flt(it.stock_qty)
		short = []
		for item, q in sorted(need.items()):
			if not frappe.get_cached_value("Item", item, "is_stock_item"):
				continue
			have = flt(frappe.db.get_value("Bin", {"item_code": item, "warehouse": wh}, "actual_qty"))
			if have + QTY_TOL < q:
				short.append((item, frappe.get_cached_value("Item", item, "item_name"), round(q, 4), round(have, 4), round(q - have, 4)))
		log(
			f"open POS shift {sh.name} ({sh.pos_profile}, {len(invs)} unconsolidated invoices, warehouse {wh}): "
			f"{len(need)} stock items needed, shortfalls: {len(short)}"
		)
		for s in short:
			log(f"SHORT {s[0]} {s[1]}: need {s[2]}, have {s[3]}, missing {s[4]}", 1)


# ─────────────────────────────────────────────────────────────── main ──


def phase_a_needed():
	if not company_exists(OSK):
		return False
	checks = {
		"transfer": len(
			[
				p
				for p in internal_transfer_pairs()
				if frappe.db.get_value("Purchase Invoice", p[0], "docstatus") == 1
				or frappe.db.get_value("Sales Invoice", p[1], "docstatus") == 1
			]
		),
		"PI": frappe.db.count("Purchase Invoice", {"company": OSK, "docstatus": 1}),
		"SR": frappe.db.count("Stock Reconciliation", {"company": OSK, "docstatus": 1}),
		"SE": frappe.db.count("Stock Entry", {"company": OSK, "docstatus": 1}),
		"BOM": frappe.db.count("BOM", {"company": OSK, "docstatus": 1}),
	}
	log(f"OSK work left for phase A: {checks}")
	return any(checks.values())


def main():
	global DRY_RUN, _real_commit
	ap = argparse.ArgumentParser()
	ap.add_argument("site")
	ap.add_argument("--commit", action="store_true")
	ap.add_argument("--verify-only", action="store_true")
	ap.add_argument("--force", action="store_true")
	args = ap.parse_args()
	DRY_RUN = not args.commit

	frappe.init(site=args.site, sites_path=os.getcwd())
	frappe.connect()
	frappe.set_user("Administrator")
	frappe.flags.mute_emails = True
	frappe.flags.in_migrate = False
	frappe.db.MAX_WRITES_PER_TRANSACTION = 10_000_000
	_real_commit = frappe.db.commit
	log(f"site={args.site} mode={'COMMIT' if args.commit else 'DRY-RUN (rollback at end)'}")

	if args.verify_only:
		path = frappe.get_site_path("private", BASELINE_FILE)
		baseline = json.load(open(path)) if os.path.exists(path) else None
		phase9_verify(baseline)
		frappe.destroy()
		return

	ok = False
	try:
		suppress_commits()  # ERPNext-internal commits never split a phase
		if company_exists(OSK):
			problems = preflight()
			if problems and not args.force:
				frappe.throw("pre-flight found unexpected documents (use --force to continue)")
		baseline = load_or_take_baseline()

		if phase_a_needed():
			phase1_undo_btr()
			phase8_repost("after undoing the transfer")
			if baseline is not None and "stock_after_btr_undo" not in baseline:
				# reference for VALUES: the inter-company transfer re-priced stock (FIFO + negative
				# stock in OSK), so values are compared with the state right after undoing it.
				baseline["stock_after_btr_undo"] = stock_snapshot([OSK, OMG])
				save_baseline(baseline)
			bom_map = phase2_boms()
			phase3_stock_reco()
			phase4_production(bom_map)
			phase5_purchase_invoices()
			phase6_drafts()
			phase7a_cancel_originals()
			checkpoint("PHASE A (phases 1-7a)")
		else:
			log("PHASE A already done — nothing to do")

		if not DRY_RUN:
			restore_commits()  # repost commits per entry (as the scheduler would)
		phase8_repost("after re-creation")
		if not DRY_RUN:
			suppress_commits()
		checkpoint("repost")

		if company_exists(OSK) or company_exists(OZ):
			phase7b_delete_companies()
		else:
			log("companies OSK / OZ already deleted — nothing to do for phase 7b")
			delete_cancelled_docs_of_internal_parties()
			checkpoint("leftovers")

		if not DRY_RUN:
			restore_commits()
		phase8_repost("final")
		if not DRY_RUN:
			suppress_commits()
		checkpoint("final repost")

		ok = phase9_verify(baseline)
	except Exception:
		import traceback

		traceback.print_exc()
		log("ERROR — rolling back the open transaction")
		restore_commits()
		frappe.db.rollback()
		frappe.clear_cache()
		print_summary(False)
		frappe.destroy()
		sys.exit(1)

	restore_commits()
	if DRY_RUN:
		frappe.db.rollback()
		frappe.clear_cache()
		log("DRY-RUN finished — everything rolled back")
	else:
		frappe.db.commit()
		frappe.clear_cache()
		log("COMMIT run finished")
	print_summary(ok)
	frappe.destroy()


def print_summary(ok):
	header("SUMMARY")
	log(f"result: {'OK' if ok else 'NOT OK'}; runtime {time.time() - T0:.1f}s")
	for w in WARNINGS:
		log(f"warning: {w}", 1)


if __name__ == "__main__":
	main()
