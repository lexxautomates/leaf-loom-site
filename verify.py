#!/usr/bin/env python3
"""End-to-end verification of the POD funnel (app.py + mock ESP, in-process).

Tests the three behaviors the capture backend must guarantee:
  * valid POST with explicit consent -> {"ok": true}
  * POST without consent            -> 400 consent_required
  * POST with consent but bad email -> 400 invalid_email
  * CORS preflight returns the Pages origin
"""
import subprocess, time, urllib.request, urllib.error, urllib.parse, json, sqlite3, os, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import importlib.util
def load(mod, path):
    spec = importlib.util.spec_from_file_location(mod, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

esp = load("mock_esp", os.path.join(ROOT, "mock_esp.py"))
from http.server import HTTPServer
esp_srv = HTTPServer(("127.0.0.1", 9000), esp.H)
import threading
threading.Thread(target=esp_srv.serve_forever, daemon=True).start()

env = dict(os.environ); env["ESP_TARGET"] = "mock"; env["ALLOWED_ORIGIN"] = "https://lexxautomates.github.io"; env["POD_PORT"] = "8000"
app_p = subprocess.Popen([sys.executable, os.path.join(ROOT, "app.py")], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
time.sleep(2.5)

BASE = "http://127.0.0.1:8000"

def get(path):
    return urllib.request.urlopen(BASE + path, timeout=5).read()

def post(path, data, ctype="application/x-www-form-urlencoded"):
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": ctype, "Origin": "https://lexxautomates.github.io"},
                                 method="POST")
    try:
        return urllib.request.urlopen(req, timeout=5).read().decode()
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.read().decode()}"

def options(path):
    req = urllib.request.Request(BASE + path, headers={"Origin": "https://lexxautomates.github.io"}, method="OPTIONS")
    try:
        urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as e:
        return e.headers
    return urllib.request.urlopen(req, timeout=5).headers

log = []
html = get("/")
log.append(("GET /", f"200, bytes={len(html)}, has_form={b'leadForm' in html}"))

# 1) valid + consent
body = urllib.parse.urlencode({
    "email": "test.buyer@example.com", "name": "Sam", "source": "pod_landing",
    "consent": "on", "utm_source": "pinterest", "utm_campaign": "spring_drop", "utm_medium": "social"}).encode()
r_ok = post("/subscribe", body)
log.append(("POST /subscribe (valid+consent)", r_ok))

# 2) JSON valid + consent
rj = post("/subscribe", json.dumps({"email": "json.lead@example.com", "name": "Jo",
                                    "source": "embed", "consent": "on"}).encode(),
          ctype="application/json")
log.append(("POST /subscribe (json+consent)", rj))

# 3) no consent -> must be rejected
rn = post("/subscribe", urllib.parse.urlencode({"email": "no.consent@example.com", "name": "X"}).encode())
log.append(("POST /subscribe (no consent)", rn))

# 4) bad email WITH consent -> invalid_email
ri = post("/subscribe", urllib.parse.urlencode({"email": "not-an-email", "name": "X", "consent": "on"}).encode())
log.append(("POST /subscribe (bad email+consent)", ri))

# 5) CORS preflight
hdrs = options("/subscribe")
aco = hdrs.get("Access-Control-Allow-Origin")
log.append(("OPTIONS /subscribe CORS", f"ACAO={aco}"))

# 6) pixel
px = get("/pixel.gif?utm_source=pinterest&utm_campaign=spring_drop&ref=https://pinterest.com")
log.append(("GET /pixel.gif", f"bytes={len(px)}, is_gif={px[:3]==b'GIF'}"))

time.sleep(0.5)
con = sqlite3.connect(os.path.join(ROOT, "pod_leads.db"))
leads = con.execute("SELECT email,name,source,utm_source,utm_campaign,esp_status FROM leads ORDER BY id DESC LIMIT 5").fetchall()
events = con.execute("SELECT kind,utm_source,utm_campaign FROM events ORDER BY id DESC LIMIT 5").fetchall()
con.close()

print("\n===== VERIFICATION LOG =====")
for name, res in log:
    print(f"[{name}] {res}")
print("\n-- recent leads --"); [print(row) for row in leads]
print("\n-- recent events --"); [print(row) for row in events]

# assertions
assert b"leadForm" in html, "landing form missing"
assert px[:3] == b"GIF", "pixel not a GIF"
assert '"ok": true' in r_ok, "valid+consent submit failed"
assert '"ok": true' in rj, "json+consent submit failed"
assert "consent_required" in rn, "no-consent NOT rejected"
assert "invalid_email" in ri, "bad email (with consent) NOT rejected"
assert aco == "https://lexxautomates.github.io", f"CORS origin wrong: {aco}"
assert len([e for e in events if e[0] == "conversion"]) >= 1, "no conversion event"
print("\nALL ASSERTIONS PASSED")
app_p.terminate(); esp_srv.shutdown()
