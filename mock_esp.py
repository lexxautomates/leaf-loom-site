#!/usr/bin/env python3
"""
Local MOCK ESP receiver for verifying the POD funnel integration end-to-end
without any paid account. It records every POST it receives to mock_esp.log.
Run in a second terminal:  python mock_esp.py
"""
import http.server
import json
import datetime

LOG = "mock_esp.log"
PORT = 9000


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode("utf-8", "replace")
        line = f"[{datetime.datetime.now().isoformat()}] FROM {self.client_address} BODY {body}\n"
        with open(LOG, "a") as f:
            f.write(line)
        print("MOCK ESP RECEIVED:", body)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(b"OK mock ESP received")))
        self.end_headers()
        self.wfile.write(b"OK mock ESP received")

    def do_GET(self):
        if self.path.startswith("/__mock_esp"):
            self.send_response(200)
            self.send_header("Content-Length", str(len(b"OK mock ESP received")))
            self.end_headers()
            self.wfile.write(b"OK mock ESP received")
        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    with http.server.HTTPServer(("127.0.0.1", PORT), H) as s:
        print(f"mock ESP on http://127.0.0.1:{PORT}/__mock_esp")
        s.serve_forever()
