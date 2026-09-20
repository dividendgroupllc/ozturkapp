/**
 * Kassa oynasi — Frappe (esbuild) yig'masi.
 *
 * `bench build --app ozturkapp` uni `dist/js/cashier.bundle.<hash>.js` ga
 * yig'adi va `assets.json` ga yozadi; sahifa esa
 * `frappe.require("cashier.bundle.js")` bilan yuklaydi. Hash fayl nomida
 * bo'lgani uchun brauzer keshi har deployda o'zi yangilanadi.
 *
 * Funksiya modullari (`features/cashier_*.bundle.js`) alohida yig'malar —
 * ular yadroga ES import bilan EMAS, quyidagi `ozturk.cashier` nomlar
 * fazosi orqali ulanadi (aks holda har biri yadro nusxasini o'zi bilan
 * olib kelardi va reestrlar bir-biridan ajralib qolardi).
 */

import {
	ApprovalCancelled,
	call,
	errorText,
	isApprovalRequired,
	maskApprovalInErrorReports,
} from "./core/api.js";
import { bundleVersion } from "./core/build.js";
import { features } from "./core/features.js";
import { CashierScreen } from "./core/screen.js";
import { slots } from "./core/slots.js";
import { ui } from "./kit/index.js";
import {
	bindAmountInput,
	elapsedHtml,
	elapsedLevel,
	esc,
	formatElapsed,
	fmtQty,
	groupAmount,
	hhmm,
	money,
	num,
	parseAmount,
} from "./util/format.js";

frappe.provide("ozturk.cashier");
maskApprovalInErrorReports();

Object.assign(ozturk.cashier, {
	Screen: CashierScreen,
	BUILD: bundleVersion(),
	features,
	slots,
	ui,
	api: { call, errorText, isApprovalRequired, ApprovalCancelled },
	util: {
		esc,
		money,
		num,
		fmtQty,
		groupAmount,
		hhmm,
		parseAmount,
		bindAmountInput,
		elapsedHtml,
		elapsedLevel,
		formatElapsed,
	},
});
