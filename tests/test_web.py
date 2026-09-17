import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from agent_body import web
from agent_body.web import _parse_search_results, web_extract, web_search


class _PageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        html = ("<html><head><title>Test Page</title>"
                "<script>var x = \"noise\";</script><style>.hide{color:red}</style></head>"
                "<body><h1>Hello 世界</h1><p>这是  一段 正文。</p><p>second para</p></body></html>")
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)


class _ErrHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(404)
        self.end_headers()


def _server(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


class WebExtractTest(unittest.TestCase):
    def test_extract_strips_tags_and_collapses_ws(self):
        srv, base = _server(_PageHandler)
        text = web_extract(base, max_chars=2000)
        self.assertIn("Hello 世界", text)
        self.assertIn("正文", text)
        self.assertNotIn("<script>", text)
        self.assertNotIn("noise", text)
        self.assertNotIn("  ", text)  # 空白已折叠
        srv.shutdown()

    def test_error_on_non200(self):
        srv, base = _server(_ErrHandler)
        with self.assertRaises(web.WebError):
            web_extract(base)
        srv.shutdown()

    def test_empty_extract_raises(self):
        # 纯 JS/无文本页 -> 无可抽取文本
        class _JsPage(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<html><body><div id='app'></div><script>render()</script></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)
        srv, base = _server(_JsPage)
        with self.assertRaises(web.WebError):
            web_extract(base)
        srv.shutdown()

    def test_oversized_page_raises(self):
        # 超过下载上限 → WebError（防超大页吃内存）
        class _BigPage(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"x" * 1024
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        srv, base = _server(_BigPage)
        old = web._MAX_BYTES
        web._MAX_BYTES = 128
        try:
            with self.assertRaises(web.WebError):
                web_extract(base)
        finally:
            web._MAX_BYTES = old
            srv.shutdown()


class SearchParseTest(unittest.TestCase):
    # 用含跳转链接的样例 HTML 测解析，避免网络依赖
    _HTML = ('<div class="result results_links a"></div>'
             '<div class="result results_links b">'
             '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpath&amp;rut=x">'
             'Example <b>Site</b></a>'
             '<a class="result__snippet">一段 <b>摘要</b> 文字</a>'
             '</div>'
             '<div class="result results_links c">'
             '<a class="result__a" href="https://plain.org">Plain</a>'
             '</div>')

    def test_parse_results_decodes_redirect_url(self):
        res = _parse_search_results(self._HTML, limit=5)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["title"], "Example Site")       # <b> 被剥
        self.assertEqual(res[0]["url"], "https://example.com/path")  # uddg 解码
        self.assertEqual(res[0]["snippet"], "一段 摘要 文字")
        self.assertEqual(res[1]["url"], "https://plain.org")

    def test_parse_respects_limit(self):
        res = _parse_search_results(self._HTML, limit=1)
        self.assertEqual(len(res), 1)

    def test_parse_empty(self):
        self.assertEqual(_parse_search_results("<html></html>"), [])


class SearchFunctionTest(unittest.TestCase):
    def test_search_fails_cleanly_on_no_results(self):
        # 覆盖解析为空 -> WebError
        with mock.patch.object(web, "_get", return_value="<html></html>"):
            with self.assertRaises(web.WebError):
                web_search("x")


if __name__ == "__main__":
    unittest.main()