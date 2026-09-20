/**
 * UI to'plami (`ozturk.cashier.ui`) — sensorli oyna, forma, ekran klaviaturasi,
 * menejer tasdig'i, tanlov tugmalari, bildirishnoma.
 *
 * Kassa ichida `frappe.prompt/confirm/msgprint` o'rniga FAQAT shular
 * ishlatiladi. Imzolar va misollar: `public/js/cashier/README.md`.
 */

import { ApprovalCancelled } from "../core/api.js";
import { requestApproval, withApproval } from "./approval.js";
import { amountInput, chips, toast } from "./controls.js";
import { kit } from "./context.js";
import { alert, closeTop, confirm, dialog, form, hasDialog } from "./dialog.js";
import { insertText, backspace, numpad, TextKeyboard } from "./keyboard.js";

export const ui = {
	/** `screen` yuklangach bir marta: elementlar joyi va klaviatura bayrog'i. */
	configure: (options) => kit.configure(options),

	dialog,
	form,
	confirm,
	alert,
	closeTop,
	hasDialog,
	toast,
	chips,
	amountInput,
	requestApproval,
	withApproval,
	ApprovalCancelled,

	keyboard: { numpad, TextKeyboard, insertText, backspace },

	/** Ekran klaviaturasi yoqilganmi (POS Profile bayrog'i). */
	get virtualKeyboard() {
		return kit.virtualKeyboard;
	},
};
