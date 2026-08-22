#!/usr/bin/env python3
"""End-to-end verification of the POD funnel. Runs mock ESP + app in-process."""
import subprocess, time, urllib.request, urllib.parse, json, sqlite3, os, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

# 1) start mock ESP
import importlib.util
def load(mod, path):
    spec = importlib.util.spec_from_file_location(mod, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

esp = load("mock_esp", os.path.join(ROOT, "mock_esp.py"))
from http.server import HTTPServer
esp_srv = HTTPServer(("127.0.0.1", 9000), esp.H)
import threading
t_esp = threading.Thread(target=esp_srv.serve_forever, daemon=True); t_esp.start()

# 2) start app server in a subprocess so its env is clean
env = dict(os.environ); env["ESP_TARGET"] = "mock"; env["POD_PORT"] = "8000"
app_p = subprocess.Popen([sys.executable, os.path.join(ROOT, "app.py")],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
time.sleep(2.5)

def get(path):
    return urllib.request.urlopen("http://127.0.0.1:8000"+path, timeout=5).read()

def post(path, data, ctype="application/x-www-form-urlencoded"):
    req = urllib.request.Request("http://127.0.0.1:8000"+path,
            data=data, headers={"Content-Type": ctype}, method="POST")
    try:
        return urllib.request.urlopen(req, timeout=5).read().decode()
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.read().decode()}"

log = []
# 3) landing page loads
html = get("/")
log.append(("GET /", "200, bytes=%d, has_form_id=%s" % (len(html), b"leadForm" in html)))

# 4) pageview pixel with UTM
px = get("/pixel.gif?utm_source=pinterest&utm_campaign=spring_drop&ref=https://pinterest.com")
log.append(("GET /pixel.gif (pinterest)", "bytes=%d, is_gif=%s" % (len(px), px[:3]==b"GIF")))

# 5) VALID subscribe (form-urlencoded, with UTM)
body = urllib.parse.urlencode({
    "email":"test.buyer@example.com", "name":"Sam", "source":"pod_landing",
    "utm_source":"pinterest", "utm_campaign":"spring_drop", "utm_medium":"social"}).encode()
r = post("/subscribe", body)
log.append(("POST /subscribe (valid)", r))

# 6) VALID subscribe (JSON body, no UTM)
rj = post("/subscribe", json.dumps({"email":"json.lead@example.com","name":"Jo","source":"embed"}).encode(),
          ctype="application/json")
log.append(("POST /subscribe (json)", rj))

# 7) INVALID email -> must be rejected
ri = post("/subscribe", urllib.parse.urlencode({"email":"not-an-email","name":"X"}).encode())
log.append(("POST /subscribe (invalid email)", ri))

time.sleep(0.5)

# 8) inspect SQLite
con = sqlite3.connect(os.path.join(ROOT, "pod_leads.db"))
leads = con.execute("SELECT email,name,source,utm_source,utm_campaign,esp_status FROM leads ORDER BY id").fetchall()
events = con.execute("SELECT kind,utm_source,utm_campaign FROM events ORDER BY id").fetchall()
con.close()

print("\n===== VERIFICATION LOG =====")
for name, res in log:
    print(f"[{name}] {res}")
print("\n-- leads table --")
for row in leads:
    print(row)
print("\n-- events table --")
for row in events:
    print(row)

# 9) assertions
assert b"leadForm" in html, "landing form missing"
assert px[:3]==b"GIF", "pixel not a GIF"
assert '"ok": true' in r, "valid submit failed"
assert '"ok": true' in rj, "json submit failed"
assert "invalid_email" in ri, "invalid email NOT rejected"
assert len(leads)==2, f"expected 2 leads, got {len(leads)}"
assert len([e for e in events if e[0]=='conversion'])>=1, "no conversion event"
print("\nALL ASSERTIONS PASSED ✅")

# cleanup
app_p.terminate(); esp_srv.shutdown()
