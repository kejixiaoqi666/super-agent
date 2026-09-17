"""Loopback JSON API for the chat client/Bot Gateway.

This deliberately has no public bind, login, model, Telegram, or vault code.
Those are separate adapters; a production gateway must authenticate before IPC.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
import threading
from .config import Config
from .core import Store, MemoryError, Conflict, BudgetExceeded, NotFound


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentMemory/0.1"

    def setup(self):
        super().setup()
        self.store = Store(self.server.cfg.data_root, max_records=self.server.cfg.max_records_per_owner, initialize=False)

    def finish(self):
        try:
            super().finish()
        finally:
            self.store.close()

    def _json(self, status, body):
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get("Content-Length", "0"))
        if n > 256_000:
            raise MemoryError("request too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def _authorized(self):
        expected = self.server.token
        actual = self.headers.get("Authorization", "")
        supplied = actual[7:] if actual.startswith("Bearer ") else ""
        return bool(expected) and hmac.compare_digest(supplied, expected)

    def _guard(self):
        if not self._authorized():
            self._json(401, {"error":"Unauthorized","message":"Bearer token required"})
            return False
        return True

    def do_GET(self):
        try:
            if not self._guard(): return
            if self.path == "/health":
                return self._json(200, self.store.health(self.server.cfg.owner))
            if self.path == "/memory":
                return self._json(200, {"items": self.store.list(self.server.cfg.owner)})
            if self.path.startswith("/checkpoint/"):
                return self._json(200, self.store.read_checkpoint(self.server.cfg.owner, self.path.split("/",2)[2]))
            if self.path.startswith("/memory/manifest/"):
                return self._json(200, self.store.read_manifest(self.server.cfg.owner, self.path.rsplit("/",1)[1]))
            if self.path.startswith("/memory/"):
                return self._json(200, self.store.get(self.server.cfg.owner, self.path.split("/",2)[2]))
            return self._json(404, {"error":"not found"})
        except Exception as exc:
            return self._error(exc)

    def do_POST(self):
        try:
            if not self._guard(): return
            body = self._body()
            path = self.path.rstrip("/")
            owner = self.server.cfg.owner
            if path == "/memory":
                return self._json(201, self.store.create(owner, body["memory"], body.get("project"), body.get("fact_key")))
            if path == "/memory/retrieve":
                return self._json(200, self.store.retrieve(owner, body["query"], body.get("project"), body.get("budget", self.server.cfg.context_budget_bytes), body.get("task_context", ""), body.get("entities")))
            if path == "/memory/feedback":
                return self._json(200, self.store.feedback(owner, body["memory_id"], body["revision"], body["task_id"], body["outcome"], body["evidence_ref"]))
            if path == "/memory/rebuild-index":
                return self._json(200, self.store.rebuild_index(owner))
            if path.startswith("/memory/manifest/"):
                return self._json(200, self.store.read_manifest(owner, path.rsplit("/",1)[1]))
            if path == "/memory/checkpoint":
                return self._json(201, self.store.checkpoint(owner, body["session"], body["state"], body.get("expected_revision",0)))
            parts = path.split("/")
            if len(parts) == 4 and parts[1] == "memory":
                mid = parts[2]
                rev = body["revision"]
                if parts[3] == "accept":
                    return self._json(200, self.store.accept(owner, mid, rev, body.get("replaces")))
                if parts[3] == "revise":
                    return self._json(200, self.store.revise(owner, mid, rev, body["memory"]))
                if parts[3] == "forget":
                    return self._json(200, self.store.forget(owner, mid, rev))
            return self._json(404, {"error":"not found"})
        except Exception as exc:
            return self._error(exc)

    def _error(self, exc):
        status = 404 if isinstance(exc, NotFound) else 409 if isinstance(exc, Conflict) else 422 if isinstance(exc, (MemoryError, BudgetExceeded, KeyError, ValueError)) else 500
        return self._json(status, {"error": type(exc).__name__, "message": str(exc)})

    def log_message(self, *_):
        return


def serve(config_path):
    cfg = Config.load(config_path)
    bootstrap = Store(cfg.data_root, max_records=cfg.max_records_per_owner)
    bootstrap.close()
    token = os.environ.get(cfg.token_env, "")
    if len(token) < 32:
        raise RuntimeError(f"{cfg.token_env} must contain at least 32 random characters")
    # Each HTTP request gets its own SQLite connection. This is safe with WAL and
    # avoids passing a connection across ThreadingHTTPServer worker threads.
    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True
        request_queue_size = 64
    httpd = Server((cfg.host, cfg.port), Handler)
    httpd.cfg, httpd.token = cfg, token
    httpd._semaphore = threading.BoundedSemaphore(cfg.max_workers)
    def process_request_thread(request, client_address):
        # Queue above the configured worker count; never reset a Bot request.
        httpd._semaphore.acquire()
        try:
            ThreadingHTTPServer.process_request_thread(httpd, request, client_address)
        finally:
            httpd._semaphore.release()
    httpd.process_request_thread = process_request_thread
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.toml")
    serve(parser.parse_args().config)
