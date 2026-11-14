"""A tiny fake of the Chess.com public API for end-to-end tests (standard library only).

CI must never call the real Chess.com (decision D4), so the e2e stack points the worker
here instead. It serves synthetic data from chesscom_fixture.json and behaves like the
real API where our code depends on it:
- GET /pub/player/{username}                → profile, or 404
- GET /pub/player/{username}/games/archives → list of archive URLs
- GET /pub/player/{username}/games/YYYY/MM  → games, with an ETag; 304 on If-None-Match

    python stub_chesscom.py   # listens on 0.0.0.0:8080
"""

import hashlib
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURE = json.loads((Path(__file__).parent / "chesscom_fixture.json").read_text())
PLAYER = FIXTURE["profile"]["username"]
ARCHIVE = re.compile(rf"^/pub/player/{PLAYER}/games/(\d{{4}}/\d{{2}})$")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.lower()
        if path == "/health":
            return self._json(200, {"status": "ok"})
        if path == f"/pub/player/{PLAYER}":
            return self._json(200, FIXTURE["profile"])
        if path == f"/pub/player/{PLAYER}/games/archives":
            return self._json(200, {"archives": FIXTURE["archives"]})
        match = ARCHIVE.match(path)
        if match and match[1] in FIXTURE["games"]:
            body = json.dumps({"games": FIXTURE["games"][match[1]]}).encode()
            etag = f'W/"{hashlib.sha256(body).hexdigest()[:16]}"'
            if self.headers.get("If-None-Match") == etag:
                return self._send(304, b"", etag=etag)
            return self._send(200, body, etag=etag)
        return self._json(404, {"code": 0, "message": "User not found"})

    def _json(self, status: int, data: object) -> None:
        self._send(status, json.dumps(data).encode())

    def _send(self, status: int, body: bytes, etag: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if etag:
            self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"stub Chess.com API serving player {PLAYER!r} on :8080", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
