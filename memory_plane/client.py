"""Small authenticated client used by future Bot/desktop channel adapters."""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler


class ClientError(RuntimeError):
    def __init__(self, status, payload):
        super().__init__(payload.get("message", "memory API error"))
        self.status, self.payload = status, payload


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, new):
        raise ClientError(code, {"message": "redirect refused by loopback client"})


class MemoryClient:
    def __init__(self, base_url="http://127.0.0.1:8765", token=None, timeout=10):
        if not base_url.startswith("http://127.0.0.1:"):
            raise ValueError("client only permits loopback HTTP in MVP")
        self.base_url = base_url.rstrip("/")
        self.token = token or os.environ.get("AGENTWORKBENCH_TOKEN", "")
        if len(self.token) < 32:
            raise ValueError("token must contain at least 32 characters")
        self.timeout = timeout
        self.opener = build_opener(NoRedirect())

    def request(self, method, path, body=None):
        raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = Request(self.base_url + path, data=raw, method=method)
        req.add_header("Authorization", "Bearer " + self.token)
        if raw is not None: req.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            try: payload = json.loads(exc.read())
            except Exception: payload = {"message": "invalid API error"}
            raise ClientError(exc.code, payload) from None
        except URLError as exc:
            raise ClientError(0, {"message": f"memory API unavailable: {exc.reason}"}) from None

    def health(self): return self.request("GET", "/health")
    def list(self, project=None):
        if project is not None:
            raise ValueError("project-filtered list is not exposed by the MVP API yet")
        return self.request("GET", "/memory")
    def create(self, memory, project=None, fact_key=None):
        return self.request("POST", "/memory", {"memory":memory,"project":project,"fact_key":fact_key})
    def retrieve(self, query, project=None, task_context="", budget=12000, entities=None):
        return self.request("POST", "/memory/retrieve", {"query":query,"project":project,
            "task_context":task_context,"budget":budget,"entities":entities or []})
    def feedback(self, memory_id, revision, task_id, outcome, evidence_ref):
        return self.request("POST", "/memory/feedback", {"memory_id":memory_id,"revision":revision,
            "task_id":task_id,"outcome":outcome,"evidence_ref":evidence_ref})
    def checkpoint(self, session, state, expected_revision=0):
        return self.request("POST", "/memory/checkpoint", {"session":session,"state":state,
            "expected_revision":expected_revision})
