"""Loopback-only HTTP adapter for synthetic integration testing."""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from core import DomainError, Store


class Handler(BaseHTTPRequestHandler):
    store = None

    def send_json(self, code, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 1_000_000:
                raise DomainError("body_too_large", 413)
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, json.JSONDecodeError):
            raise DomainError("invalid_json")

    def actor(self):
        value = self.headers.get("Authorization", "")
        token = value[7:] if value.startswith("Bearer ") else ""
        return self.store.user_for_token(token)["id"]

    def route(self, method):
        path = [unquote(x) for x in urlsplit(self.path).path.split("/") if x]
        if method == "GET" and path == ["health"]:
            return {"status": "ok", "mode": "sandbox"}
        if method == "POST" and path == ["v1", "auth", "fixture-login"]:
            return self.store.login_sandbox(self.read_json().get("subject"))
        actor = self.actor()
        if method == "GET" and path == ["v1", "me"]:
            return self.store.profile(actor)
        if method == "POST" and path == ["v1", "onboarding"]:
            body = self.read_json()
            return self.store.onboard(actor, body.get("adult"), body.get("consentVersion"), body.get("track"), body.get("goalMinutes"))
        if method == "POST" and path == ["v1", "privacy", "withdraw"]:
            return self.store.withdraw(actor)
        if method == "GET" and path == ["v1", "course"]:
            return self.store.course(actor)
        if method == "POST" and path == ["v1", "course", "complete-fixture"]:
            return self.store.complete_day(actor, self.read_json().get("evidenceId"))
        if method == "GET" and len(path) == 5 and path[:2] == ["v1", "content"]:
            return self.store.public_content(actor, path[2], path[3], path[4])
        if method == "POST" and len(path) == 4 and path[:2] == ["v1", "admin"] and path[2] == "import":
            if actor != "fixture:editor":
                raise DomainError("forbidden", 403)
            return self.store.import_content(actor, path[3], self.read_json())
        if method == "POST" and len(path) == 6 and path[:3] == ["v1", "admin", "review"]:
            if actor != "fixture:reviewer":
                raise DomainError("forbidden", 403)
            return self.store.review_content(actor, path[3], path[4], path[5])
        raise DomainError("not_found", 404)

    def handle_request(self, method):
        try:
            self.send_json(200, self.route(method))
        except DomainError as exc:
            self.send_json(exc.status, {"error": exc.code})
        except Exception:
            self.send_json(500, {"error": "internal_error"})
            raise

    def do_GET(self):
        if urlsplit(self.path).path == "/ops":
            body = (Path(__file__).resolve().parent.parent / "ops" / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        self.handle_request("GET")

    def do_POST(self):
        self.handle_request("POST")


def main():
    if os.environ.get("DV_SANDBOX") != "1":
        raise SystemExit("DV_SANDBOX=1 is required; this adapter has fixture identity")
    Handler.store = Store(os.environ.get("DV_DB", ":memory:"))
    HTTPServer(("127.0.0.1", int(os.environ.get("DV_PORT", "8080"))), Handler).serve_forever()


if __name__ == "__main__":
    main()
