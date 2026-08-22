#!/usr/bin/env python3
"""
POD store email-capture + funnel-analytics backend (stdlib ONLY, zero deps).

Endpoints
---------
GET  /                 -> landing page (production-quality HTML)
GET  /form-snippet     -> copy-paste embeddable form HTML (for external pages)
POST /subscribe        -> email + name + UTM capture; stores to SQLite; forwards to ESP
GET  /pixel.gif        -> 1x1 transparent conversion pixel; logs UTM/pageview events
GET  /__mock_esp       -> local mock ESP receiver (used only when ESP_TARGET=mock)
GET  /health           -> ok

Hardening added for t_bcf37257 (public deploy):
  * CORS on /subscribe so a cross-origin static site (GitHub Pages) can POST.
  * Server-side consent enforcement: /subscribe requires an explicit
    `consent` field (GDPR Art. 7(2) — no consent, no store).

ESP forwarding
--------------
  mock        -> local receiver (default; verifies the pipeline end-to-end)
  mailchimp   -> Mailchimp API v3 (free tier: 500 contacts)
  convertkit  -> ConvertKit/Kit API v3 (free tier)
Run:
  python app.py                  # serves :8000, ESP_TARGET=mock
  ESP_TARGET=mailchimp MAILCHIMP_API_KEY=... MAILCHIMP_LIST_ID=... python app.py
"""
import http.server
import socketserver
import threading
import urllib.parse
import urllib.request
import json
import sqlite3
import os
import re
import datetime
import html as _html

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "pod_leads.db")
PORT = int(os.environ.get("POD_PORT", "8000"))
ESP_TARGET = os.environ.get("ESP_TARGET", "mock").lower()
MAILCHIMP_API = os.environ.get("MAILCHIMP_API_KEY", "")
MAILCHIMP_LIST = os.environ.get("MAILCHIMP_LIST_ID", "")
CONVERTKIT_FORM = os.environ.get("CONVERTKIT_FORM_ID", "")
CONVERTKIT_SECRET = os.environ.get("CONVERTKIT_API_SECRET", "")
ESP_MOCK_URL = os.environ.get("ESP_MOCK_URL", "http://127.0.0.1:9000/__mock_esp")
PIXEL = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9"
         b"\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
         b"\x00\x02\x02D\x01\x00;")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

UTM_KEYS = ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"]

# Allow the static front-end (GitHub Pages) to call this backend cross-origin.
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")  # e.g. https://lexxautomates.github.io


def init_db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT, name TEXT, source TEXT, consent TEXT,
        utm_source TEXT, utm_medium TEXT, utm_campaign TEXT,
        utm_content TEXT, utm_term TEXT,
        created_at TEXT, esp_status TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT, utm_source TEXT, utm_medium TEXT, utm_campaign TEXT,
        utm_content TEXT, utm_term TEXT, referrer TEXT, ts TEXT)""")
    con.commit()
    con.close()


def log_event(kind, utm):
    try:
        con = sqlite3.connect(DB)
        con.execute(
            "INSERT INTO events (kind,utm_source,utm_medium,utm_campaign,"
            "utm_content,utm_term,referrer,ts) VALUES (?,?,?,?,?,?,?,?)",
            (kind, utm.get("utm_source"), utm.get("utm_medium"),
             utm.get("utm_campaign"), utm.get("utm_content"),
             utm.get("utm_term"), utm.get("referrer"), _now()))
        con.commit()
        con.close()
    except Exception as e:
        print("[events] log failed:", e)


def store_lead(email, name, source, consent, utm, esp_status):
    con = sqlite3.connect(DB)
    cur = con.execute(
        "INSERT INTO leads (email,name,source,consent,utm_source,utm_medium,"
        "utm_campaign,utm_content,utm_term,created_at,esp_status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (email, name, source, consent, utm.get("utm_source"), utm.get("utm_medium"),
         utm.get("utm_campaign"), utm.get("utm_content"), utm.get("utm_term"),
         _now(), esp_status))
    lead_id = cur.lastrowid
    con.commit()
    con.close()
    return lead_id


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def parse_utm(qs):
    return {k: qs.get(k, [""])[0] for k in UTM_KEYS}


def forward_to_esp(email, name, source, utm):
    """Returns (ok: bool, detail: str). Builds the exact request each ESP expects."""
    payload = {
        "email": email, "name": name, "source": source,
        "utm": utm, "timestamp": _now(),
    }
    if ESP_TARGET == "mock":
        try:
            req = urllib.request.Request(
                ESP_MOCK_URL,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                return True, f"mock ESP 200: {r.read().decode().strip()}"
        except Exception as e:
            return False, f"mock ESP error: {e}"

    if ESP_TARGET == "mailchimp":
        if not MAILCHIMP_API or not MAILCHIMP_LIST:
            return False, "mailchimp: missing MAILCHIMP_API_KEY / MAILCHIMP_LIST_ID"
        dc = MAILCHIMP_API.split("-")[-1]
        url = (f"https://{dc}.api.mailchimp.com/3.0/lists/{MAILCHIMP_LIST}/members")
        body = {
            "email_address": email,
            "status": "subscribed",
            "merge_fields": {"FNAME": name or "", "SOURCE": source or ""},
            "tags": [f"pod_{utm.get('utm_source') or 'direct'}"],
        }
        try:
            req = urllib.request.Request(
                url, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            import base64
            tok = base64.b64encode(f"x:{MAILCHIMP_API}".encode()).decode()
            req.add_header("Authorization", f"Basic {tok}")
            with urllib.request.urlopen(req, timeout=10) as r:
                return True, f"mailchimp 200: {r.status}"
        except urllib.error.HTTPError as e:
            return False, f"mailchimp HTTP {e.code}: {e.read().decode()[:300]}"
        except Exception as e:
            return False, f"mailchimp error: {e}"

    if ESP_TARGET == "convertkit":
        if not CONVERTKIT_FORM and not CONVERTKIT_SECRET:
            return False, "convertkit: missing CONVERTKIT_FORM_ID or CONVERTKIT_API_SECRET"
        if CONVERTKIT_FORM:
            url = "https://api.convertkit.com/v3/forms/{}/subscribe".format(CONVERTKIT_FORM)
            body = {"api_secret": CONVERTKIT_SECRET, "email": email,
                    "first_name": name or "", "fields": {"source": source or ""}}
        else:
            url = "https://api.convertkit.com/v3/subscribers"
            body = {"api_secret": CONVERTKIT_SECRET, "email": email,
                    "first_name": name or "", "fields": {"source": source or ""}}
        try:
            req = urllib.request.Request(
                url, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                return True, f"convertkit 200: {r.status}"
        except urllib.error.HTTPError as e:
            return False, f"convertkit HTTP {e.code}: {e.read().decode()[:300]}"
        except Exception as e:
            return False, f"convertkit error: {e}"

    return False, f"unknown ESP_TARGET={ESP_TARGET}"


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # quiet

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self._cors()
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            with open(os.path.join(ROOT, "landing.html"), "rb") as f:
                self._send(200, f.read())
        elif u.path == "/form-snippet":
            with open(os.path.join(ROOT, "form_snippet.html"), "rb") as f:
                self._send(200, f.read(), ctype="text/html; charset=utf-8")
        elif u.path == "/pixel.gif":
            qs = urllib.parse.parse_qs(u.query)
            utm = parse_utm(qs)
            utm["referrer"] = qs.get("ref", [""])[0]
            log_event("pageview", utm)
            self._send(200, PIXEL, ctype="image/gif",
                       extra={"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"})
        elif u.path == "/__mock_esp":
            self._send(200, b"OK mock ESP received", ctype="text/plain")
        elif u.path == "/health":
            self._send(200, b"ok")
        else:
            self._send(404, b"not found")

    def do_POST(self):
        try:
            self._post()
        except Exception as e:
            # Never let an exception produce an empty reply (that is exactly
            # the failure mode that broke the live tunnel). Return a real 500.
            try:
                self._send(500, json.dumps({"ok": False, "error": "server_error"}).encode(),
                           ctype="application/json")
            except Exception:
                pass

    def _post(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/subscribe":
            self._send(404, b"not found")
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8", "replace")
        ctype = self.headers.get("Content-Type", "")
        if "application/json" in ctype:
            data = json.loads(raw)
            get = data.get
        else:
            data = urllib.parse.parse_qs(raw)
            get = lambda k, d="": (data.get(k, [d])[0] if isinstance(data.get(k), list) else data.get(k, d))
        email = (get("email") or "").strip().lower()
        name = (get("name") or "").strip()
        consent = (get("consent") or "").strip().lower()
        source = (get("source") or "pod_landing").strip()
        # UTM may arrive as flat fields or nested under 'utm'
        utm = {}
        for k in UTM_KEYS:
            v = get(k)
            if v:
                utm[k] = v
        if isinstance(data, dict) and isinstance(data.get("utm"), dict):
            utm.update({k: data["utm"].get(k, "") for k in UTM_KEYS})

        # GDPR Art. 7(2): explicit, separate, affirmative consent required.
        if consent not in ("on", "yes", "true", "1", "checked"):
            self._send(400, json.dumps({"ok": False, "error": "consent_required"}).encode(),
                       ctype="application/json")
            return

        if not EMAIL_RE.match(email):
            self._send(400, json.dumps({"ok": False, "error": "invalid_email"}).encode(),
                       ctype="application/json")
            return

        # STORE FIRST, always. The ESP forward runs in a background thread so a
        # slow/unreachable ESP can never drop the user's success response.
        lead_id = store_lead(email, name, source, consent, utm, "pending")
        log_event("conversion", utm)
        self._send(200, json.dumps({"ok": True, "lead_id": lead_id}).encode(),
                   ctype="application/json")
        self.wfile.flush()

        def _forward():
            try:
                ok, detail = forward_to_esp(email, name, source, utm)
                try:
                    con = sqlite3.connect(DB)
                    con.execute("UPDATE leads SET esp_status=? WHERE id=?", (detail, lead_id))
                    con.commit()
                    con.close()
                except Exception:
                    pass
            except Exception:
                pass

        threading.Thread(target=_forward, daemon=True).start()


def main():
    init_db()
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print(f"POD funnel server on http://127.0.0.1:{PORT}  (ESP_TARGET={ESP_TARGET})")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("stopped")


if __name__ == "__main__":
    main()
