import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from memory_plane.model_router import (
    EchoProvider, FallbackRouter, ModelRequest, ModelRouter,
    OpenAIProvider, ProviderConfig, ProviderError,
)


class _Handler(BaseHTTPRequestHandler):
    mode = "ok"
    captured = {}

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        _Handler.captured = {
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "body": body,
        }
        if _Handler.mode == "500":
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"boom"}')
            return
        resp = {
            "choices": [{"message": {
                "role": "assistant",
                "content": "你好呀",
                "tool_calls": [{"id": "t1", "type": "function",
                                "function": {"name": "ls", "arguments": "{}"}}]
                if _Handler.mode == "tool" else None,
            }}],
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(resp).encode())


class _ErrHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(503)
        self.end_headers()
        self.wfile.write(b'{"error":"unavailable"}')


def _server(handler_cls=_Handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"


class OpenAIProviderTest(unittest.TestCase):
    def setUp(self):
        self.srv, self.base = _server()
        self.cfg = ProviderConfig("real", self.base, api_key="sk-test",
                                  model="gpt-x")
        self.p = OpenAIProvider(self.cfg)

    def tearDown(self):
        self.srv.shutdown()

    def test_hits_chat_completions_with_auth_and_parses(self):
        req = ModelRequest(messages=({"role": "user", "content": "ping"},))
        out = self.p.complete(req)
        self.assertEqual(out["provider"], "real")
        self.assertEqual(out["content"], "你好呀")
        self.assertTrue(_Handler.captured["path"].endswith("/chat/completions"))
        self.assertEqual(_Handler.captured["auth"], "Bearer sk-test")
        self.assertEqual(_Handler.captured["body"]["model"], "gpt-x")
        self.assertEqual(_Handler.captured["body"]["messages"][0]["content"], "ping")

    def test_parses_tool_calls(self):
        _Handler.mode = "tool"
        req = ModelRequest(messages=({"role": "user", "content": "hi"},))
        out = self.p.complete(req)
        self.assertTrue(out["tool_calls"])
        self.assertEqual(out["tool_calls"][0]["function"]["name"], "ls")

    def test_http_error_raises_provider_error(self):
        _Handler.mode = "500"
        req = ModelRequest(messages=({"role": "user", "content": "x"},))
        with self.assertRaises(ProviderError) as ctx:
            self.p.complete(req)
        self.assertIn("HTTP 500", str(ctx.exception))

    def test_connection_error_raises_provider_error(self):
        bad = OpenAIProvider(ProviderConfig("bad", "http://127.0.0.1:1/v1"))
        req = ModelRequest(messages=({"role": "user", "content": "x"},))
        with self.assertRaises(ProviderError):
            bad.complete(req)

    def _parse(self, **usage):
        return self.p._parse({
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": usage,
        })

    def test_parses_real_usage(self):
        out = self._parse(prompt_tokens=1234, completion_tokens=56,
                          total_tokens=1290)
        self.assertEqual(out["usage"]["prompt_tokens"], 1234)
        self.assertEqual(out["usage"]["completion_tokens"], 56)
        self.assertEqual(out["usage"]["total_tokens"], 1290)
        self.assertEqual(out["content"], "hi")

    def test_malformed_usage_does_not_kill_response(self):
        # 坏 usage（非数字/缺失）绝不能让有效补全被拒或触发 fallback
        out = self._parse(prompt_tokens="N/A", completion_tokens=None)
        self.assertEqual(out["usage"]["prompt_tokens"], 0)
        self.assertEqual(out["usage"]["completion_tokens"], 0)
        self.assertEqual(out["content"], "hi")

    def test_no_usage_still_parses(self):
        out = self.p._parse({
            "choices": [{"message": {"role": "assistant", "content": "ok"}}]})
        # 无 usage 时安全兜底 0（保持一致形状，不报错）
        self.assertEqual(out["usage"], {"prompt_tokens": 0,
                                        "completion_tokens": 0,
                                        "total_tokens": 0})
        self.assertEqual(out["content"], "ok")


class FallbackRouterTest(unittest.TestCase):
    def test_primary_succeeds(self):
        srv, base = _server()
        primary = OpenAIProvider(ProviderConfig("primary", base, model="m"))
        backup = EchoProvider()
        r = FallbackRouter([primary, backup], routes={"default": "primary"})
        out = r.complete(ModelRequest(messages=({"role": "user", "content": "z"},)))
        self.assertEqual(out["provider"], "primary")
        srv.shutdown()

    def test_falls_back_when_primary_fails(self):
        srv, base = _server(_ErrHandler)  # primary 503
        primary = OpenAIProvider(ProviderConfig("primary", base, model="m"))
        backup = EchoProvider()
        r = FallbackRouter([primary, backup], routes={"default": "primary"})
        out = r.complete(ModelRequest(messages=({"role": "user", "content": "fallback me"},)))
        self.assertEqual(out["provider"], "echo")
        self.assertEqual(out["content"], "fallback me")
        srv.shutdown()

    def test_all_fail_raises(self):
        srv, base = _server(_ErrHandler)
        a = OpenAIProvider(ProviderConfig("a", base, model="m"))
        b = OpenAIProvider(ProviderConfig("b", base, model="m"))
        r = FallbackRouter([a, b], routes={"default": "a"})
        with self.assertRaises(ProviderError):
            r.complete(ModelRequest(messages=({"role": "user", "content": "x"},)))
        srv.shutdown()


class EchoTest(unittest.TestCase):
    def test_echo_returns_last_message(self):
        r = ModelRouter([EchoProvider()])
        out = r.complete(ModelRequest(messages=({"role": "user", "content": "hi"},)))
        self.assertEqual(out["content"], "hi")


if __name__ == "__main__":
    unittest.main()
