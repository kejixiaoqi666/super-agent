import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from memory_plane.client import MemoryClient, ClientError


ROOT = Path(__file__).parents[1]


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.port = 18765
        cls.token = "T" * 48
        cfg = Path(cls.tmp.name) / "config.toml"
        cfg.write_text(f'''[memory]\ndata_root = "{(Path(cls.tmp.name)/"data").as_posix()}"\nowner = "test-owner"\ncontext_budget_bytes = 12000\nmax_records_per_owner = 50000\n[server]\nhost = "127.0.0.1"\nport = {cls.port}\ntoken_env = "AGENTWORKBENCH_TOKEN"\nmax_workers = 8\nrequest_timeout = 10\n''', encoding="utf-8")
        env = os.environ.copy(); env["AGENTWORKBENCH_TOKEN"] = cls.token
        cls.proc = subprocess.Popen([sys.executable, "-m", "memory_plane", "--config", str(cfg)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=.1): break
            except OSError: time.sleep(.05)
        else:
            err = cls.proc.stderr.read().decode(errors="replace")
            raise RuntimeError(err)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(timeout=3); cls.tmp.cleanup()

    def call(self, method, path, body=None, token=None):
        raw = None if body is None else json.dumps(body).encode()
        req = Request(f"http://127.0.0.1:{self.port}{path}", data=raw, method=method)
        if token is not None: req.add_header("Authorization", "Bearer " + token)
        if raw: req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, timeout=3) as res:
                return res.status, json.loads(res.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_authentication_and_concurrent_request_local_connections(self):
        status, _ = self.call("GET", "/health")
        self.assertEqual(status, 401)
        status, health = self.call("GET", "/health", token=self.token)
        self.assertEqual((status, health["schema"]), (200, 1))
        with self.assertRaises(ValueError):
            MemoryClient(base_url="http://127.0.0.1:8765", token="short")

        def create(i):
            return self.call("POST", "/memory", {"project":"concurrent", "fact_key":f"k-{i}", "memory": {
                "title":f"Fact {i}", "content":f"verified fact {i}", "priority":"P2", "kind":"semantic",
                "importance":.5, "confidence":.8, "entities":["concurrent"], "source_ref":f"task://{i}"}}, self.token)[0]
        with ThreadPoolExecutor(max_workers=16) as pool:
            statuses = list(pool.map(create, range(24)))
        self.assertEqual(statuses, [201] * 24)
        status, body = self.call("GET", "/memory", token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(len(body["items"]), 24)
        client = MemoryClient(f"http://127.0.0.1:{self.port}", self.token)
        self.assertEqual(client.health()["schema"], 1)
        self.assertEqual(len(client.list()["items"]), 24)


if __name__ == "__main__":
    unittest.main()
