"""Reel Check local server.

Serves index.html and runs api/checklist.py, the same function Vercel runs.
The API key stays in .env and never reaches the browser.

    python server.py        ->  http://localhost:5178
"""
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
PORT = int(os.environ.get("PORT", 5178))

env = ROOT / ".env"
if env.exists():
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from api.checklist import model, respond  # noqa: E402  (after .env is loaded)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def do_POST(self):
        if self.path == "/api/checklist":
            return respond(self)
        self.send_error(404)


if __name__ == "__main__":
    key = "set" if os.environ.get("OPENROUTER_API_KEY") else "MISSING"
    print(f"Reel Check on http://localhost:{PORT}  (model: {model()}, key {key})")
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
