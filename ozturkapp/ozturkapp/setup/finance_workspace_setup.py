# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""«Kassa qoldiqlari» HTML bloki va uni «Finance Manager» workspace'iga qo'shish.

Jazira'dagi «Kassa qoldigini» blokining Öztürk varianti (bitta kompaniya).
Blok `Custom HTML Block` yozuvi — kod shu yerda saqlanadi va har `migrate` da
yangilanadi (UI'da qilingan tahrir ustidan yoziladi). Workspace'ga blok faqat
bir marta qo'shiladi; workspace yo'q bo'lsa — hech narsa qilinmaydi.
"""

import json

import frappe

BLOCK = "Kassa qoldiqlari"
WORKSPACES = ("Finance Manager", "Financial Manager")
ROLES = ("Accounts Manager", "Accounts User", "System Manager")

HTML = """
<div class="oz-kq-head">
  <label>Sana (shu kun oxiridagi qoldiq):</label>
  <input type="date" class="oz-kq-date">
</div>
<div class="oz-kq-body"><div class="text-muted">Yuklanmoqda...</div></div>
"""

STYLE = """
.oz-kq-head { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.oz-kq-head label { font-size: 13px; color: var(--text-muted); margin: 0; }
.oz-kq-date { padding: 5px 8px; border: 1px solid var(--border-color); border-radius: 6px;
  background: var(--card-bg); color: var(--text-color); }
.oz-kq-cols { display: flex; gap: 12px; flex-wrap: wrap; align-items: stretch; }
.oz-kq-col { flex: 1; min-width: 220px; display: flex; flex-direction: column; }
.oz-kq-title { font-weight: 700; font-size: 15px; margin-bottom: 8px; text-align: center; color: var(--text-color); }
.oz-kq-card { background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 8px;
  padding: 10px 12px; margin-bottom: 8px; }
.oz-kq-label { font-size: 12px; color: var(--text-muted); }
.oz-kq-value { font-weight: 600; color: var(--text-color); font-variant-numeric: tabular-nums; }
.oz-kq-neg { color: #e24c4c; }
.oz-kq-sum { border: 1px solid #1f78d1; margin-top: auto; }
.oz-kq-sum .oz-kq-value { font-weight: 700; }
.oz-kq-grand { margin-top: 15px; background: #1f78d1; color: #fff; border-radius: 8px; padding: 14px 16px; }
.oz-kq-grand div { font-size: 13px; opacity: .9; }
.oz-kq-grand h3 { margin: 0; font-weight: 700; color: #fff; }
"""

SCRIPT = """
(() => {
  const root = root_element.querySelector(".oz-kq-body");
  const dateInput = root_element.querySelector(".oz-kq-date");
  if (!root || !dateInput) return;
  if (!dateInput.value) dateInput.value = frappe.datetime.get_today();

  const COLORS = ["#1f78d1", "#e83e8c", "#28a745", "#17a2b8", "#fd7e14", "#6f42c1", "#20c997", "#6610f2"];
  const fmt = (v) => new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(v || 0);
  const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
  const value = (v, cls = "") => `<div class="oz-kq-value ${cls} ${v < 0 ? "oz-kq-neg" : ""}">${fmt(v)} so'm</div>`;

  async function render() {
    root.innerHTML = `<div class="text-muted">Yuklanmoqda...</div>`;
    try {
      const r = await frappe.call({
        method: "ozturkapp.ozturkapp.api.cash_balances.get_cash_balances",
        args: { date: dateInput.value },
      });
      const data = r.message || { groups: [], total: 0 };
      let i = 0;
      const cols = data.groups.map((g) => `
        <div class="oz-kq-col">
          <div class="oz-kq-title">${esc(g.label)}</div>
          ${g.rows.map((row) => `
            <div class="oz-kq-card" style="border-left: 4px solid ${COLORS[i++ % COLORS.length]};">
              <div class="oz-kq-label">${esc(row.mode_of_payment)}</div>
              ${value(row.balance)}
            </div>`).join("")}
          <div class="oz-kq-card oz-kq-sum">
            <div class="oz-kq-label">Жами</div>
            ${value(g.total)}
          </div>
        </div>`).join("");
      root.innerHTML = `
        <div class="oz-kq-cols">${cols || `<div class="text-muted">To'lov turlari topilmadi</div>`}</div>
        <div class="oz-kq-grand">
          <div>ОБЩИЙ ҚОЛДИҚ (${esc(frappe.datetime.str_to_user(data.date))} kun oxirigacha)</div>
          <h3>${fmt(data.total)} so'm</h3>
        </div>`;
    } catch (e) {
      root.innerHTML = `<div style="color:#e24c4c;">Xatolik: ${esc(e.message || e)}</div>`;
    }
  }

  dateInput.addEventListener("change", render);
  render();
})();
"""


def ensure_block() -> str:
    doc = frappe.get_doc("Custom HTML Block", BLOCK) if frappe.db.exists("Custom HTML Block", BLOCK) \
        else frappe.new_doc("Custom HTML Block")
    if doc.is_new():
        doc.name = BLOCK
    doc.html, doc.script, doc.style, doc.private = HTML.strip(), SCRIPT.strip(), STYLE.strip(), 0
    have = {r.role for r in doc.get("roles") or []}
    for role in ROLES:
        if role not in have and frappe.db.exists("Role", role):
            doc.append("roles", {"role": role})
    if doc.is_new():
        doc.insert(ignore_permissions=True, set_name=BLOCK)
    else:
        doc.save(ignore_permissions=True)
    return doc.name


def add_to_workspace(workspace: str) -> bool:
    """Blokni workspace oxiriga qo'shadi (bir marta). Qo'shilgan bo'lsa — True."""
    ws = frappe.get_doc("Workspace", workspace)
    content = json.loads(ws.content or "[]")
    if any(b.get("type") == "custom_block" and b.get("data", {}).get("custom_block_name") == BLOCK for b in content):
        return False
    content.append({
        "id": frappe.generate_hash(length=10),
        "type": "custom_block",
        "data": {"custom_block_name": BLOCK, "col": 12},
    })
    ws.content = json.dumps(content)
    if not any(r.custom_block_name == BLOCK for r in ws.get("custom_blocks") or []):
        ws.append("custom_blocks", {"custom_block_name": BLOCK, "label": BLOCK})
    ws.save(ignore_permissions=True)
    return True


def setup():
    ensure_block()
    for workspace in WORKSPACES:
        if frappe.db.exists("Workspace", workspace):
            add_to_workspace(workspace)
    frappe.db.commit()
    print(f"✅ «{BLOCK}» HTML bloki tayyor")
