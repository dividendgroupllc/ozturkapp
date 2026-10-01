// Copyright (c) 2026, Ozturkapp
// License: MIT

frappe.ui.form.on("Expense Allocation", {
	setup(frm) {
		// JE'lar shu hujjat bekor qilinganda O'ZI bekor qiladi — "bog'langan
		// hujjatlarni ham bekor qilish" oynasi ularni alohida urinmasin.
		frm.ignore_doctypes_on_cancel_all = ["Journal Entry"];

		frm.set_query("source_company", () => ({ filters: { is_group: 0 } }));
		frm.set_query("account", "accounts", (doc) => ({
			filters: { company: doc.source_company, root_type: "Expense" },
		}));
		frm.set_query("source_receivable_account", (doc) => ({
			filters: { company: doc.source_company, account_type: "Receivable", is_group: 0 },
		}));
		frm.set_query("source_contra_account", (doc) => ({
			filters: { company: doc.source_company, is_group: 0 },
		}));
		frm.set_query("source_cost_center", (doc) => ({
			filters: { company: doc.source_company, is_group: 0 },
		}));
		frm.set_query("company", "branches", (doc) => ({
			filters: { is_group: 0, name: ["!=", doc.source_company || ""] },
		}));
		frm.set_query("expense_account", "branches", (doc, cdt, cdn) => ({
			filters: { company: locals[cdt][cdn].company, root_type: "Expense", is_group: 0 },
		}));
		frm.set_query("payable_account", "branches", (doc, cdt, cdn) => ({
			filters: { company: locals[cdt][cdn].company, account_type: "Payable", is_group: 0 },
		}));
		frm.set_query("cost_center", "branches", (doc, cdt, cdn) => ({
			filters: { company: locals[cdt][cdn].company, is_group: 0 },
		}));
	},

	refresh(frm) {
		if (frm.doc.docstatus === 0) {
			frm.dashboard.set_headline_alert(
				__("Saqlang — hovuz va filial ulushlari qayta hisoblanadi. Pastda yoziladigan Journal Entry'larni tekshirib, keyin tasdiqlang."),
				"blue"
			);
		}
		const links = [frm.doc.source_journal_entry]
			.concat((frm.doc.branches || []).map((r) => r.journal_entry))
			.filter(Boolean);
		if (links.length) {
			frm.add_custom_button(__("Journal Entry'lar"), () =>
				frappe.set_route("List", "Journal Entry", { name: ["in", links] })
			);
		}
		render_preview(frm);
	},

	source_company(frm) {
		// Hisoblar boshqa kompaniyaniki bo'lib qolmasin — server qayta to'ldiradi.
		frm.set_value("source_receivable_account", null);
		frm.set_value("source_contra_account", null);
		frm.set_value("source_cost_center", null);
		frm.clear_table("accounts");
		frm.clear_table("branches");
		frm.refresh_fields();
	},
});

frappe.ui.form.on("Expense Allocation Branch", {
	company(frm, cdt, cdn) {
		// Eski kompaniyaning hisoblari qolmasin — saqlashda server to'ldiradi.
		["expense_account", "payable_account", "cost_center", "customer"].forEach((f) =>
			frappe.model.set_value(cdt, cdn, f, null)
		);
	},
});

function render_preview(frm) {
	const wrapper = frm.fields_dict.preview_html && frm.fields_dict.preview_html.$wrapper;
	if (!wrapper) return;
	if (frm.is_new() || !(frm.doc.expenses || []).length) {
		wrapper.html(`<div class="text-muted">${__("Saqlangandan keyin bu yerda yoziladigan yozuvlar chiqadi.")}</div>`);
		return;
	}
	frm.call("get_preview").then((r) => {
		const data = r.message || {};
		let html = "";
		(data.warnings || []).forEach((w) => {
			html += `<div class="alert alert-warning" style="margin-bottom:8px">${w}</div>`;
		});
		const entries = [];
		if (data.source) entries.push(data.source);
		(data.branches || []).forEach((b) => entries.push(b));
		entries.forEach((e) => (html += entry_table(e, frm.doc.currency)));
		wrapper.html(html || `<div class="text-muted">${__("Yoziladigan yozuv yo'q.")}</div>`);
	});
}

function entry_table(entry, currency) {
	const fmt = (v) => (v ? format_currency(v, currency) : "");
	let total_dr = 0;
	let total_cr = 0;
	const rows = entry.accounts
		.map((l) => {
			total_dr += l.debit;
			total_cr += l.credit;
			const party = l.party ? `<span class="text-muted"> [${__(l.party_type)}: ${frappe.utils.escape_html(l.party)}]</span>` : "";
			const account = l.account ? frappe.utils.escape_html(l.account) : `<span class="text-danger">${__("hisob yo'q")}</span>`;
			return `<tr><td>${account}${party}</td><td class="text-right">${fmt(l.debit)}</td><td class="text-right">${fmt(l.credit)}</td></tr>`;
		})
		.join("");
	return `
		<div style="margin-bottom:12px">
			<b>${frappe.utils.escape_html(entry.company)}</b>
			<span class="text-muted"> — ${frappe.utils.escape_html(entry.label || "")}</span>
			<table class="table table-bordered table-condensed" style="margin-top:4px">
				<thead><tr><th>${__("Hisob")}</th><th class="text-right" style="width:20%">${__("Debet")}</th><th class="text-right" style="width:20%">${__("Kredit")}</th></tr></thead>
				<tbody>${rows}</tbody>
				<tfoot><tr><th>${__("Jami")}</th><th class="text-right">${fmt(total_dr)}</th><th class="text-right">${fmt(total_cr)}</th></tr></tfoot>
			</table>
		</div>`;
}
