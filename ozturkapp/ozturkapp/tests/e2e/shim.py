# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""E2E HTTP shim: brauzer so'rovlarini HAQIQIY whitelisted funksiyalarga yo'naltiradi.

Bir oqimli (single-threaded) server. U FAQAT `ozturkapp.ozturkapp.*` chaqiruvlarini
o'zi bajaradi; Desk, aktivlar, login — haqiqiy bench serverda (`:8000`) qoladi.

NEGA `frappe.app.application` EMAS
==================================
U har so'rovda `frappe.init` + YANGI DB ulanishi ochadi va oxirida COMMIT qiladi —
bizga bitta uzun tranzaksiya kerak. Shuning uchun `frappe.app`ning ICHKI
qadamlari qayta ishlatiladi (`make_form_dict`, `frappe.api.handle`,
`handle_exception`, `sync_database` mantig'i), lekin ulanish va commit bizniki:

    so'rov boshi  ->  frappe.local tozalanadi (DB ulanishi saqlanadi, `frappe.destroy` kabi)
    bajarish      ->  frappe.api.handle(request)  -> frappe.handler.execute_cmd
                      (whitelist, HTTP metod cheklovi, xato shakli Frappe'ning o'zi)
    muvaffaqiyat  ->  POST/PUT/DELETE: virtual commit; GET: rollback (haqiqiy Frappe kabi)
    xato          ->  so'rov SAVEPOINT'iga qaytish + `handle_exception` javobi
"""

import json
import time
import traceback
from urllib.parse import unquote, urlparse

import frappe
import frappe.api
from werkzeug.exceptions import HTTPException
from werkzeug.local import release_local
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from ozturkapp.ozturkapp.tests.e2e.guard import ForbiddenStatement

#: Shim faqat shu prefiksdagi metodlarni bajaradi (brauzerda Fetch shabloni ham shu).
METHOD_PREFIX = "ozturkapp.ozturkapp."

UNSAFE = ("POST", "PUT", "DELETE", "PATCH")


class Shim:
    def __init__(self, guard, site: str, sites_path: str):
        self.guard = guard
        self.site = site
        self.sites_path = sites_path
        self.actor = "Administrator"
        self.step = ""
        self.log = []
        self.ops = {}
        self.violations = []

    # ── Frappe so'rov konteksti ────────────────────────────────────────

    def _fresh_context(self, user: str):
        """`frappe.destroy()` + `frappe.init(force=True)`: ulanishni saqlab, `local` ni tozalash."""
        self.guard.check_connection()
        db = frappe.local.db
        release_local(frappe.local)
        frappe.local.db = db
        frappe.init(site=self.site, sites_path=self.sites_path, force=True)
        frappe.set_user(user)
        frappe.local.request_ip = "127.0.0.1"

    def run_as(self, user: str, fn, *args, **kwargs):
        """Yordamchi (fikstura / DB-haqiqat) kodini alohida "so'rov" sifatida bajaradi."""
        self._fresh_context(user)
        try:
            result = fn(*args, **kwargs)
            frappe.db.commit()
            return result
        except BaseException:
            frappe.db.rollback()
            raise

    # ── Brauzer so'rovi ────────────────────────────────────────────────

    def api_request(self, http_method: str, url: str, headers: dict, body, user: str = None):
        from frappe.app import handle_exception, make_form_dict
        from frappe.utils import CallbackManager

        parsed = urlparse(url)
        path = parsed.path
        if not path.startswith("/api/method/" + METHOD_PREFIX):
            raise ValueError(f"shim faqat {METHOD_PREFIX}* ni bajaradi: {path}")
        method_name = unquote(path[len("/api/method/"):])

        started = time.time()
        actor = user or self.actor
        self._fresh_context(actor)

        forward = {k: v for k, v in (headers or {}).items()
                   if k.lower() in ("content-type", "accept", "x-requested-with")}
        env = EnvironBuilder(
            path=path,
            query_string=parsed.query,
            method=http_method,
            headers=forward,
            data=body.encode("utf-8") if isinstance(body, str) else body,
            content_type=forward.get("Content-Type") or forward.get("content-type"),
        ).get_environ()
        request = Request(env)
        request.after_response = CallbackManager()
        frappe.local.request = request
        frappe.local.is_ajax = frappe.get_request_header("X-Requested-With") == "XMLHttpRequest"

        rollback = True
        response = None
        try:
            make_form_dict(request)
            response = frappe.api.handle(request)
        except HTTPException as exc:
            response = exc.get_response()
        except Exception as exc:  # noqa: BLE001 — Frappe'ning o'z xato javobi
            if isinstance(exc, ForbiddenStatement):
                self.violations.append({"step": self.step, "method": method_name, "error": str(exc)})
            response = handle_exception(exc)
        else:
            if frappe.local.flags.commit or http_method in UNSAFE:
                frappe.db.commit()
            else:
                frappe.db.rollback()
            rollback = False
        finally:
            if rollback:
                frappe.db.rollback()

        try:
            request.after_response.run()
        except Exception:  # noqa: BLE001
            traceback.print_exc()

        self.drain_jobs()
        payload = response.get_data(as_text=True)
        status = response.status_code
        self._record(http_method, method_name, status, payload, started, request, actor)
        return {
            "status": status,
            "headers": {"Content-Type": response.headers.get("Content-Type", "application/json")},
            "body": payload,
        }

    def drain_jobs(self):
        """Fon ishchisi: `ozturkapp.*` ishlari o'z tranzaksiyasida (COMMITdan KEYIN) bajariladi.

        Haqiqiy ishchi alohida jarayon va ulanishda ishlaydi; bu yerda shim ulanishida (rolled-back
        cheklar boshqa jarayonda ko'rinmaydi). `frappe.*` / `erpnext.*` ishlari BAJARILMAYDI.
        """
        guard = self.guard
        while guard.pending_jobs:
            job = guard.pending_jobs.pop(0)
            if not job["method"].startswith(("ozturkapp.", "erpnext.accounts.doctype.pos_invoice_merge_log.")):
                guard.skipped_jobs.append(job["method"])
                continue
            try:
                self._fresh_context(job["user"] or "Administrator")
                frappe.call(job["method"], **job["kwargs"])
                frappe.db.commit()
            except Exception as exc:  # noqa: BLE001
                frappe.db.rollback()
                guard.job_errors.append({"method": job["method"], "error": f"{type(exc).__name__}: {exc}"})

    def _record(self, http_method, method_name, status, payload, started, request, actor):
        exc_type, exc_text, messages = "", "", ""
        if status >= 400:
            try:
                data = json.loads(payload)
            except ValueError:
                data = {}
            exc_type = data.get("exc_type") or ""
            exc_text = (data.get("exception") or data.get("exc") or "")[:400]
            raw = data.get("_server_messages")
            if raw:
                try:
                    messages = " | ".join(
                        json.loads(m).get("message", "") for m in json.loads(raw)
                    )[:300]
                except (ValueError, AttributeError):
                    messages = str(raw)[:300]

        args = {}
        try:
            args = {k: str(v)[:80] for k, v in (frappe.local.form_dict or {}).items() if k != "cmd"}
        except Exception:  # noqa: BLE001
            pass
        args.pop("approval", None)  # PIN jurnalga tushmasin

        self.log.append(
            {
                "n": len(self.log) + 1,
                "step": self.step,
                "actor": actor,
                "http": http_method,
                "method": method_name.replace(METHOD_PREFIX, "", 1),
                "status": status,
                "exc_type": exc_type,
                "exc": exc_text,
                "msg": messages,
                "ms": int((time.time() - started) * 1000),
                "args": args,
            }
        )

    # ── Boshqaruv amallari (haydovchi <-> shim) ───────────────────────

    def control(self, op: str, args: dict):
        if op == "actor":
            self.actor = args["user"]
            return {"actor": self.actor}
        if op == "step":
            self.step = args.get("name", "")
            return {"step": self.step}
        if op == "log":
            since = int(args.get("since") or 0)
            return self.log[since:]
        if op == "realtime":
            return json.loads(json.dumps(self.guard.realtime[int(args.get("since") or 0):], default=str))
        if op == "sql":
            return self.run_as("Administrator", self._select, args["query"], args.get("values") or [])
        if op in self.ops:
            return self.run_as(args.get("as") or "Administrator", self.ops[op], **{
                k: v for k, v in args.items() if k != "as"})
        raise ValueError(f"noma'lum amal: {op}")

    @staticmethod
    def _select(query, values):
        text = str(query).lstrip().lower()
        if not text.startswith(("select", "with")) or ";" in text.rstrip("; \n"):
            raise ValueError("faqat bitta SELECT ruxsat etiladi")
        rows = frappe.db.sql(query, values, as_dict=True)
        return json.loads(json.dumps(rows, default=str))


# ═══════════════════════════════════════════════════════════════════
#  HTTP qatlami (bir oqimli: bitta DB ulanishi — bitta vaqtda bitta so'rov)
# ═══════════════════════════════════════════════════════════════════

from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402


def make_server(shim: Shim, port: int) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0: har javobdan keyin ulanish yopiladi — keep-alive bir oqimli serverni qotirib qo'yardi.
        protocol_version = "HTTP/1.0"

        def log_message(self, *_args):
            pass

        def _reply(self, status: int, payload):
            data = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._reply(200, {"ok": True, "actor": shim.actor})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            request = json.loads(self.rfile.read(length) or b"{}")
            try:
                if self.path == "/__api":
                    result = shim.api_request(
                        request["method"], request["url"], request.get("headers") or {}, request.get("body"),
                        user=request.get("as"),
                    )
                elif self.path == "/__op":
                    result = shim.control(request["op"], request.get("args") or {})
                else:
                    self._reply(404, {"error": "no route"})
                    return
                self._reply(200, {"result": result})
            except Exception as exc:  # noqa: BLE001
                self._reply(500, {"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-1500:]})

    return HTTPServer(("127.0.0.1", port), Handler)
