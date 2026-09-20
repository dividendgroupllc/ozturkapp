# -*- coding: utf-8 -*-
# Copyright (c) 2026, Ozturkapp
# License: MIT

"""Rollback kafolatini ISBOTLAYDI: jarayon ochiq tranzaksiyada o'ldiriladi, baza o'zgarmaydi.

    cd /home/sherzod/frappe-bench/sites && flock /tmp/ozturk_tests.lock \\
        ../env/bin/python -m ozturkapp.ozturkapp.tests.e2e.verify_guarantee

Har signal uchun (SIGINT, SIGTERM, SIGKILL): `run --selftest --hold` fikstura yozuvlari bilan
(yuzlab qator, ochiq tranzaksiya) to'xtab turadi; tashqaridan signal yuboriladi; butun bazaning
barmoq izi oldin va keyin solishtiriladi.
"""

import os
import signal
import subprocess
import sys
import time

import frappe

from ozturkapp.ozturkapp.tests.e2e.guard import diff_fingerprints, fingerprint

SITES_PATH = "/home/sherzod/frappe-bench/sites"
SITE = "ozturk.local"


def main():
    os.chdir(SITES_PATH)
    frappe.init(site=SITE, sites_path=SITES_PATH)
    frappe.connect()
    failures = []
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            before = fingerprint()
            child = subprocess.Popen(
                [sys.executable, "-m", "ozturkapp.ozturkapp.tests.e2e.run", "--selftest", "--hold", "120"],
                cwd=SITES_PATH, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            for line in child.stdout:
                if "E2E_HOLDING" in line:
                    break
            else:
                failures.append(f"{sig.name}: jarayon ushlab turish nuqtasiga yetmadi")
                continue

            open_writes = fingerprint()
            visible = diff_fingerprints(before, open_writes)
            os.kill(child.pid, sig)
            try:
                child.wait(60)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            time.sleep(1)

            after = fingerprint()
            changed = diff_fingerprints(before, after)
            status = "OK" if not changed and before["counts"] == after["counts"] else "XATO"
            print(f"{sig.name:8s} tranzaksiya paytida boshqa ulanishda ko'rinadigan farq: {visible or 'yo`q'}; "
                  f"o'ldirilgandan keyin farq: {changed or 'yo`q'} -> {status}")
            if status != "OK":
                failures.append(f"{sig.name}: {changed}")
    finally:
        frappe.destroy()

    print("NATIJA:", "OK" if not failures else f"XATO {failures}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
