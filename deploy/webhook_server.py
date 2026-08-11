#!/usr/bin/env python3
"""GitHub push webhook tinglovchisi — ozturkapp deploy uchun.

Faqat standart kutubxonadan foydalanadi (qo'shimcha paket kerak emas).
GitHub `push` hodisasini HMAC-SHA256 imzosi bilan tekshiradi va mos kelsa
deploy.sh ni alohida jarayonda ishga tushiradi.

Ishga tushirish:
    python3 deploy/webhook_server.py            # deploy/deploy.env dan o'qiydi
    DEPLOY_CONFIG=/path/deploy.env python3 deploy/webhook_server.py

Odatda systemd orqali xizmat sifatida ishlaydi — deploy/README.md ga qarang.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MAX_BODY = 5 * 1024 * 1024  # GitHub payload'i bundan katta bo'lmaydi

log = logging.getLogger("ozturkapp-webhook")


def load_config() -> dict[str, str]:
	"""deploy.env ni o'qiydi. Muhit o'zgaruvchilari fayldagidan ustun turadi."""
	path = Path(os.environ.get("DEPLOY_CONFIG") or SCRIPT_DIR / "deploy.env")
	if not path.is_file():
		sys.exit(f"Config topilmadi: {path}\ndeploy.env.example dan nusxa olib to'ldiring.")

	cfg: dict[str, str] = {}
	for raw in path.read_text(encoding="utf-8").splitlines():
		line = raw.strip()
		if not line or line.startswith("#") or "=" not in line:
			continue
		key, _, value = line.partition("=")
		value = value.strip()
		if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
			value = value[1:-1]
		cfg[key.strip()] = value

	cfg.update({k: v for k, v in os.environ.items() if k in cfg or k.startswith("WEBHOOK_")})
	cfg["DEPLOY_CONFIG"] = str(path)
	return cfg


class DeployRunner:
	"""deploy.sh ni ketma-ket (bir vaqtda bittadan) ishga tushiradi."""

	def __init__(self, script: Path, config_path: str):
		self.script = script
		self.config_path = config_path
		self._lock = threading.Lock()

	def trigger(self, commit: str, pusher: str) -> None:
		thread = threading.Thread(target=self._run, args=(commit, pusher), daemon=False)
		thread.start()

	def _run(self, commit: str, pusher: str) -> None:
		# deploy.sh ning o'zida ham flock bor; bu yerdagi lock ortiqcha
		# jarayon yaratmaslik uchun.
		with self._lock:
			log.info("Deploy boshlandi: %s (push: %s)", commit[:8], pusher)
			env = {**os.environ, "DEPLOY_CONFIG": self.config_path}
			try:
				result = subprocess.run(
					["bash", str(self.script)],
					env=env,
					stdout=subprocess.PIPE,
					stderr=subprocess.STDOUT,
					text=True,
					timeout=1800,
				)
			except subprocess.TimeoutExpired:
				log.error("Deploy 30 daqiqada tugamadi — to'xtatildi")
				return

			tail = "\n".join(result.stdout.strip().splitlines()[-25:])
			if result.returncode == 0:
				log.info("Deploy muvaffaqiyatli tugadi: %s\n%s", commit[:8], tail)
			else:
				log.error("Deploy XATO bilan tugadi (kod %s):\n%s", result.returncode, tail)


class Handler(BaseHTTPRequestHandler):
	server_version = "ozturkapp-deploy"
	sys_version = ""

	config: dict[str, str]
	runner: DeployRunner

	def log_message(self, fmt: str, *args) -> None:
		log.info("%s %s", self.address_string(), fmt % args)

	def _reply(self, code: int, message: str) -> None:
		body = message.encode("utf-8")
		self.send_response(code)
		self.send_header("Content-Type", "text/plain; charset=utf-8")
		self.send_header("Content-Length", str(len(body)))
		self.end_headers()
		self.wfile.write(body)

	def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
		if self.path.split("?")[0] == "/health":
			self._reply(200, "ok")
		else:
			self._reply(404, "not found")

	def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
		if self.path.split("?")[0] != self.config.get("WEBHOOK_PATH", "/hook"):
			self._reply(404, "not found")
			return

		try:
			length = int(self.headers.get("Content-Length", "0"))
		except ValueError:
			self._reply(400, "bad content-length")
			return
		if length <= 0 or length > MAX_BODY:
			self._reply(400, "bad content-length")
			return

		body = self.rfile.read(length)
		if len(body) != length:
			self._reply(400, "incomplete body")
			return

		secret = self.config.get("WEBHOOK_SECRET", "")
		expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
		received = self.headers.get("X-Hub-Signature-256", "")
		if not hmac.compare_digest(expected, received):
			log.warning("Imzo mos kelmadi — so'rov rad etildi (%s)", self.address_string())
			self._reply(401, "invalid signature")
			return

		event = self.headers.get("X-GitHub-Event", "")
		if event == "ping":
			self._reply(200, "pong")
			return
		if event != "push":
			self._reply(202, f"'{event}' hodisasi e'tiborsiz qoldirildi")
			return

		try:
			payload = json.loads(body)
		except json.JSONDecodeError:
			self._reply(400, "invalid json")
			return

		branch = self.config.get("BRANCH", "main")
		if payload.get("ref") != f"refs/heads/{branch}":
			self._reply(202, f"'{payload.get('ref')}' branch e'tiborsiz qoldirildi")
			return
		if payload.get("deleted"):
			self._reply(202, "branch o'chirilgan — e'tiborsiz qoldirildi")
			return

		commit = payload.get("after", "")
		pusher = (payload.get("pusher") or {}).get("name", "?")
		self.runner.trigger(commit, pusher)
		self._reply(202, f"deploy navbatga qo'yildi: {commit[:8]}")


def main() -> None:
	logging.basicConfig(
		level=logging.INFO,
		format="%(asctime)s %(levelname)s %(message)s",
		datefmt="%Y-%m-%d %H:%M:%S",
	)

	cfg = load_config()
	if not cfg.get("WEBHOOK_SECRET"):
		sys.exit("deploy.env da WEBHOOK_SECRET bo'sh. Yaratish: openssl rand -hex 32")

	script = SCRIPT_DIR / "deploy.sh"
	if not script.is_file():
		sys.exit(f"deploy.sh topilmadi: {script}")

	bind = cfg.get("WEBHOOK_BIND", "127.0.0.1")
	port = int(cfg.get("WEBHOOK_PORT", "9987"))
	path = cfg.get("WEBHOOK_PATH", "/hook")

	Handler.config = cfg
	Handler.runner = DeployRunner(script, cfg["DEPLOY_CONFIG"])

	server = ThreadingHTTPServer((bind, port), Handler)
	log.info("Webhook tinglanmoqda: http://%s:%s%s (branch: %s)", bind, port, path, cfg.get("BRANCH", "main"))
	try:
		server.serve_forever()
	except KeyboardInterrupt:
		log.info("To'xtatilmoqda")
	finally:
		server.server_close()


if __name__ == "__main__":
	main()
