"""Static server for the dashboard, plus /latest so the page can find the
newest run without the operator pasting a call id.

    uv run python serve.py          # foreground, http://localhost:8765/dashboard.html
    from serve import start_in_thread; start_in_thread()
"""

from __future__ import annotations

import http.server
import json
import socketserver
import threading
from pathlib import Path

from recorder import latest_run

PORT = 8765
ROOT = Path(__file__).parent


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def do_GET(self):  # noqa: N802 - stdlib naming
        if self.path.split("?")[0] == "/latest":
            path = latest_run(ROOT / "runs")
            body = json.dumps(
                {
                    "events": f"/runs/{path.parent.name}/events.jsonl" if path else None,
                    "disposition": f"/runs/{path.parent.name}/disposition.json" if path else None,
                    "call_id": path.parent.name if path else None,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *a):  # quiet; the agent's logs are the signal
        pass


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_in_thread(port: int = PORT) -> threading.Thread:
    srv = _Server(("", port), Handler)
    t = threading.Thread(target=srv.serve_forever, name="dashboard", daemon=True)
    t.start()
    print(f"dashboard -> http://localhost:{port}/dashboard.html")
    return t


if __name__ == "__main__":
    srv = _Server(("", PORT), Handler)
    print(f"dashboard -> http://localhost:{PORT}/dashboard.html")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
