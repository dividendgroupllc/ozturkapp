// pl_pdf_button.js
// "Profit and Loss Statement" report'iga "Ozturk PDF" tugmasini qo'shadi.
// Frappe SPA navigatsiyasida report konfiguratsiyasi qayta yaratiladi —
// shuning uchun frappe.query_reports ustiga trap (defineProperty) o'rnatilib,
// har safar patch qayta qo'llanadi.
(function () {
	"use strict";

	var REPORT_NAME = "Profit and Loss Statement";
	var BTN_CLASS = "btn-ozturk-pl-pdf";

	function _on_click() {
		if (!frappe.query_report) return;

		var filters = frappe.query_report.get_filter_values();
		var mode = filters.filter_based_on || "Fiscal Year";

		if (!filters.company) {
			frappe.show_alert({ message: __("Avval Kompaniyani tanlang"), indicator: "orange" });
			return;
		}

		if (mode === "Date Range") {
			if (!filters.period_start_date || !filters.period_end_date) {
				frappe.show_alert({
					message: __("Avval 'Start Date' va 'End Date' ni tanlang"),
					indicator: "orange",
				});
				return;
			}
		} else if (!filters.from_fiscal_year || !filters.to_fiscal_year) {
			frappe.show_alert({
				message: __("Avval 'Start Year' va 'End Year' ni tanlang"),
				indicator: "orange",
			});
			return;
		}

		frappe.call({
			method: "ozturkapp.ozturkapp.api.pl_pdf_api.generate_pl_pdf",
			args: { filters: JSON.stringify(filters) },
			freeze: true,
			freeze_message: __("PDF yaratilmoqda..."),
			callback: function (r) {
				if (r.message && r.message.file_url) {
					window.open(r.message.file_url);
					frappe.show_alert({ message: __("PDF tayyor!"), indicator: "green" });
				} else {
					frappe.show_alert({ message: __("Fayl URL qaytmadi"), indicator: "red" });
				}
			},
			error: function () {
				frappe.show_alert({ message: __("Server xatoligi"), indicator: "red" });
			},
		});
	}

	function _inject_button(report) {
		if (!report || !report.page) return;
		if (report.page.inner_toolbar.find("." + BTN_CLASS).length) return;

		report.page
			.add_inner_button(__("Ozturk PDF"), _on_click)
			.addClass(BTN_CLASS + " btn-primary-dark");
	}

	// frappe.query_reports[REPORT_NAME] ustiga trap: Frappe navigatsiya
	// paytida bu obyektni qayta yaratadi/o'rnatadi — setter shu daqiqada
	// ishga tushib, tugmani qayta ulaydi.
	function _patch_report(patchFn) {
		var _storage = {};

		function _install_trap() {
			if (!frappe.query_reports) {
				setTimeout(_install_trap, 50);
				return;
			}

			if (frappe.query_reports.hasOwnProperty(REPORT_NAME)) {
				_storage[REPORT_NAME] = frappe.query_reports[REPORT_NAME];
			}

			try {
				delete frappe.query_reports[REPORT_NAME];
			} catch (e) {
				/* no-op */
			}

			Object.defineProperty(frappe.query_reports, REPORT_NAME, {
				configurable: true,
				enumerable: true,
				get: function () {
					return _storage[REPORT_NAME];
				},
				set: function (newVal) {
					_storage[REPORT_NAME] = newVal;
					if (newVal && typeof newVal === "object") {
						try {
							patchFn(newVal);
						} catch (e) {
							console.error("[Ozturk] patch_report error:", e);
						}
					}
				},
			});

			if (_storage[REPORT_NAME]) {
				try {
					patchFn(_storage[REPORT_NAME]);
				} catch (e) {
					console.error("[Ozturk] initial patch error:", e);
				}
			}
		}

		if (document.readyState === "loading") {
			document.addEventListener("DOMContentLoaded", _install_trap);
		} else {
			_install_trap();
		}
	}

	_patch_report(function (reportConfig) {
		var _origOnload = reportConfig.onload;
		var _origRefresh = reportConfig.refresh;

		reportConfig.onload = function (report) {
			if (_origOnload) _origOnload.call(this, report);
			setTimeout(function () {
				_inject_button(report);
			}, 300);
		};

		reportConfig.refresh = function (report) {
			if (_origRefresh) _origRefresh.call(this, report);
			setTimeout(function () {
				_inject_button(report);
			}, 500);
		};
	});
})();
