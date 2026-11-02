"""Test-only HTTP platform fixture: independent signature validation and revocable sessions."""

import hashlib
import hmac
import json
import secrets
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class AuthPlatformServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], ak: str, sk: str):
        super().__init__(address, Handler)
        self.ak, self.sk = ak, sk
        self.nonces: set[str] = set()
        self.sessions: set[str] = set()
        self.csrf_token = secrets.token_urlsafe(24)
        self.issue_login_cookie = True


class Handler(BaseHTTPRequestHandler):
    server: AuthPlatformServer

    def log_message(self, format: str, *args: object) -> None:
        pass  # No credentials or request headers in test logs.

    def reply(self, status: int, code: int, cookies: list[str] | None = None) -> None:
        body = json.dumps({"code": code}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/v1/certification/get_token":
            self.reply(200, 0, [f"csrf_token={self.server.csrf_token}; Path=/; HttpOnly"])
            return
        if path not in {"/v1/rtt", "/events"}:
            self.reply(404, 404)
            return
        jar = SimpleCookie(self.headers.get("Cookie", ""))
        token = jar.get("soc-token")
        csrf = self.headers.get("X-CSRFToken") or self.headers.get("x-csrf-token")
        valid = token is not None and token.value in self.server.sessions and csrf == self.server.csrf_token
        self.reply(200 if valid else 401, 0 if valid else 9348)

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.last_login_headers = dict(self.headers)
        self.server.last_login_raw = raw
        self.server.last_login_body = json.loads(raw) if raw else None
        try:
            valid = self.verify()
        except (ValueError, KeyError):
            valid = False
        if not valid:
            self.reply(200, 9348)  # Deliberately HTTP 200 like MSSP.
            return
        if not self.server.issue_login_cookie:
            self.reply(200, 0, [f"csrf_token={self.server.csrf_token}; Path=/"])
            return
        token = secrets.token_urlsafe(24)
        self.server.sessions.add(token)
        self.reply(200, 0, [f"soc-token={token}; Path=/; HttpOnly", "login-source=agent; Path=/"])

    def verify(self) -> bool:
        if urlsplit(self.path).path != "/gateway/mss-auth-acl-service/v1/certification/login_agent":
            return False
        if self.headers.get("x-csrftoken") != self.server.csrf_token:
            return False
        if getattr(self.server, "json_login", False):
            return self.server.last_login_body == self.server.expected_login_body
        fields = dict(piece.split("=", 1) for piece in self.headers.get("Authorization", "").split(","))
        stamp = fields["sign-date"]
        nonce = self.headers.get("Agent-Nonce", "")
        if abs(time.time() - int(stamp)) > 60 or not nonce or nonce in self.server.nonces:
            return False
        if fields["Access"] != self.server.ak or fields["algorithm"] != "HMAC-SHA256":
            return False
        if fields["SignedHeaders"] != "content-type;sign-date":
            return False
        if self.headers.get("X-Branch-Tag") not in {"MSSW-ADAPTER", "MSSP-ADAPTER"}:
            return False
        parts = urlsplit(self.path)
        path = "/" + parts.path.split("/", 3)[3]
        query = "&".join(sorted(("?" + parts.query).split("&"))) + "\n" if parts.query else ""
        canonical = (
            f"POST\n{path}\n{query}content-type:{self.headers.get('Content-Type')}\n"
            f"sign-date:{stamp}\ncontent-type;sign-date"
        )
        if self.server.last_login_raw:
            canonical += "\n" + hashlib.sha256(self.server.last_login_raw).hexdigest().upper()
        digest = hashlib.sha256(canonical.encode()).hexdigest().upper()
        signature = (
            hmac.new(self.server.sk.encode(), f"HMAC-SHA256\n{stamp}\n{digest}".encode(), hashlib.sha256)
            .hexdigest()
            .upper()
        )
        if not hmac.compare_digest(signature, fields["Signature"]):
            return False
        self.server.last_branch_tag = self.headers.get("X-Branch-Tag")
        self.server.nonces.add(nonce)
        return True
