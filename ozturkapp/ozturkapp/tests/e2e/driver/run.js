// E2E haydovchi: senariylarni ketma-ket yuritadi, hisobot yozadi.
const fs = require("fs");
const { Harness, sleep } = require("./lib.js");
const scenarios = require("./scenarios.js");

const BUG_TYPES = /TypeError|KeyError|AttributeError|NameError|IndexError|ValueError|ImportError|OperationalError|ProgrammingError|Traceback/;

async function main() {
	const env = process.env;
	const wanted = (env.E2E_SCENARIOS || "").split(",").map((s) => s.trim()).filter(Boolean);
	const ORDER = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "k", "l", "m", "n", "o", "p", "q", "j"];
	const order = ORDER.filter((k) => scenarios[k]).filter((k) => !wanted.length || wanted.includes(k));
	const h = new Harness(env);
	let fatal = null;
	try {
		await h.start(1366, 768);
		await h.login();
		await h.op("step", { name: "boot" });
		await h.op("actor", { user: "e2e-cashier@example.com" });
		h.scenario = "boot";
		h.stepName = "openCashier";
		await h.openCashier();
		for (const key of order) {
			const sc = scenarios[key];
			h.scenario = key;
			console.log(`\n== ${key}) ${sc.title}`);
			try {
				h.stepName = "prepare";
				if (key !== "a") await scenarios.prepare(h);
				await sc.run(h);
			} catch (e) {
				if (!e.stepFailed) {
					console.log("  FAIL  senariy xatosi:", String(e.message || e).split("\n")[0]);
					h.results.push({ scenario: key, step: h.stepName || "(senariy)", ok: false, error: String(e.message || e).slice(0, 600), console: [], failed: [], failedChecks: [] });
					await h.shot(`${key}_crash`);
				}
			}
			await recover(h);
		}
	} catch (e) {
		fatal = e;
		console.log("FATAL:", e && e.stack || e);
		if (h.cdp) await h.shot("fatal");
	}

	let log = [];
	try {
		log = await h.op("log", { since: 0 });
	} catch (e) {}
	const contract = classify(h, log);
	const failedSteps = h.results.filter((r) => !r.ok);
	const report = {
		ok: !fatal && !failedSteps.length && !contract.bugs.length,
		fatal: fatal ? String(fatal.message || fatal) : "",
		steps: h.results,
		checks: { total: h.checks.length, failed: h.checks.filter((c) => !c.ok) },
		contract,
		requests: log.length,
		browserRequests: h.browserRequests.length,
	};
	fs.writeFileSync(env.E2E_REPORT, JSON.stringify(report, null, 1));
	printSummary(h, report);
	await h.stop();
	process.exit(report.ok ? 0 : 1);
}

async function recover(h) {
	try {
		await h.op("actor", { user: "e2e-cashier@example.com" });
		await h.openCashier();
	} catch (e) {
		console.log("  (tiklash: sahifani qayta ochib bo'lmadi)", String(e.message).split("\n")[0]);
	}
}

/** Har bir non-2xx javob: kutilgan (`h.expect`) yoki NOSOZLIK. */
function classify(h, log) {
	const rows = [];
	const bugs = [];
	for (const r of log.filter((x) => x.status >= 400)) {
		const scenario = (r.step || "").split(" / ")[0];
		const stepName = (r.step || "").split(" / ").slice(1).join(" / ");
		const expected = h.expected.find(
			(e) => e.scenario === scenario && e.step === stepName && r.method.endsWith(e.method) && e.status === r.status && (!e.excType || e.excType === r.exc_type)
		);
		const isBug = !expected || r.status >= 500 || BUG_TYPES.test(r.exc_type + " " + r.exc);
		const row = { n: r.n, step: r.step, actor: r.actor, call: `${r.http} ${r.method}`, status: r.status, exc: r.exc_type, msg: r.msg, verdict: isBug ? "BUG?" : "expected" };
		rows.push(row);
		if (isBug) bugs.push(row);
	}
	return { total: log.length, nonOk: rows, bugs };
}

function printSummary(h, report) {
	console.log("\n========== HAYDOVCHI XULOSASI ==========");
	const byScenario = {};
	for (const r of h.results) {
		const s = (byScenario[r.scenario] = byScenario[r.scenario] || { ok: 0, fail: 0 });
		r.ok ? s.ok++ : s.fail++;
	}
	for (const [k, v] of Object.entries(byScenario)) console.log(`  ${k.padEnd(8)} ok=${v.ok} fail=${v.fail}`);
	console.log(`  tekshiruvlar: ${report.checks.total}, xato: ${report.checks.failed.length}`);
	report.checks.failed.forEach((c) => console.log(`    - [${c.scenario}/${c.step}] ${c.label} ${c.detail}`));
	console.log(`  brauzer so'rovlari (shim orqali): ${report.browserRequests}, shim jurnali: ${report.requests}`);
	console.log("  non-2xx:");
	report.contract.nonOk.forEach((r) => console.log(`    #${r.n} [${r.verdict}] ${r.status} ${r.call} ${r.exc} | ${r.step} | ${r.actor} | ${r.msg}`));
}

main().catch((e) => {
	console.error(e);
	process.exit(2);
});
