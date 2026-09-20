// Minimal CDP client + Chrome launcher (headless, unique port, temp profile).
const WebSocket = require("/home/sherzod/frappe-bench/apps/frappe/node_modules/ws");
const { spawn } = require("child_process");
const fs = require("fs");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

class CDP {
	constructor(ws) {
		this.ws = ws;
		this.id = 0;
		this.pending = new Map();
		this.handlers = [];
		ws.on("message", (raw) => {
			const msg = JSON.parse(raw);
			if (msg.id && this.pending.has(msg.id)) {
				const { resolve, reject } = this.pending.get(msg.id);
				this.pending.delete(msg.id);
				msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
			} else if (msg.method) {
				this.handlers.forEach((handler) => handler(msg));
			}
		});
	}
	send(method, params = {}) {
		const id = ++this.id;
		this.ws.send(JSON.stringify({ id, method, params }));
		return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
	}
	on(handler) {
		this.handlers.push(handler);
	}
}

async function launch({ port, profile, width, height, headed }) {
	fs.mkdirSync(profile, { recursive: true });
	const args = [
		`--remote-debugging-port=${port}`,
		`--user-data-dir=${profile}`,
		`--window-size=${width},${height}`,
		"--no-first-run",
		"--no-default-browser-check",
		"--disable-gpu",
		"--disable-extensions",
		"--disable-background-networking",
		"--disable-component-update",
		"--disable-sync",
		"--hide-scrollbars",
		"--mute-audio",
		"about:blank",
	];
	if (!headed) args.unshift("--headless=new");
	const chrome = spawn("/usr/bin/google-chrome", args, { stdio: "ignore" });
	for (let i = 0; i < 80; i++) {
		try {
			const r = await fetch(`http://127.0.0.1:${port}/json/version`);
			if (r.ok) break;
		} catch (e) {}
		await sleep(250);
	}
	const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
	const page = targets.find((t) => t.type === "page");
	const ws = new WebSocket(page.webSocketDebuggerUrl, { perMessageDeflate: false });
	await new Promise((resolve) => ws.on("open", resolve));
	return { chrome, cdp: new CDP(ws), ws };
}

module.exports = { CDP, launch, sleep };
