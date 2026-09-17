"""MCP 协议层专项测试：客户端/服务器握手 + 发现 + 调用 + 超时。"""
import sys
import tempfile
import unittest
from pathlib import Path

from agent_body.mcp import MCPServer, MCPClient


class MCPServerTest(unittest.TestCase):
    def _make_server(self):
        def echo(args):
            return {"echo": args.get("text", "")}
        def add(args):
            return {"sum": args.get("a", 0) + args.get("b", 0)}
        return MCPServer({"echo": echo, "add": add},
                         server_info={"name": "test-server", "version": "1.0"})

    def test_tools_list(self):
        s = self._make_server()
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp = s._handle(req)
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertEqual(names, {"echo", "add"})

    def test_call_tool(self):
        s = self._make_server()
        req = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
               "params": {"name": "add", "arguments": {"a": 2, "b": 3}}}
        resp = s._handle(req)
        self.assertEqual(resp["id"], 2)
        content = resp["result"]["content"][0]["text"]
        self.assertIn("5", content)

    def test_call_unknown_tool(self):
        s = self._make_server()
        req = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
               "params": {"name": "nope"}}
        resp = s._handle(req)
        self.assertIn("error", resp)

    def test_listen_loop(self):
        """整条 stdio 循环：多请求顺序处理。"""
        import io
        s = self._make_server()
        stdin = io.StringIO(
            '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n'
            '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":'
            '{"name":"echo","arguments":{"text":"hi"}}}\n')
        out = io.StringIO()
        n = s.listen(stdin, out)
        self.assertEqual(n, 2)
        lines = out.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 2)


class MCPClientTest(unittest.TestCase):
    """端到端：起一个真实子进程 MCP 服务器，客户端连上并调用。"""

    def _start_server_proc(self, workdir: str) -> MCPClient:
        # 写一个脚本式服务器到临时目录
        server_script = Path(workdir) / "test_mcp_server.py"
        server_script.write_text(
            "import sys, json\n"
            "sys.path.insert(0, %r)\n" % str(Path(__file__).resolve().parents[1])
            + "from agent_body.mcp import MCPServer\n"
            "def echo(args): return {'echo': args.get('text','')}\n"
            "def add(args): return {'sum': args.get('a',0)+args.get('b',0)}\n"
            "MCPServer({'echo': echo, 'add': add}).listen()\n",
            encoding="utf-8")
        client = MCPClient(sys.executable, [str(server_script)])
        return client

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = self._start_server_proc(tmp)
            try:
                info = client.connect()
                self.assertIn("tools", info)
                tools = client.list_tools()
                names = {t["name"] for t in tools}
                self.assertEqual(names, {"echo", "add"})
                res = client.call_tool("add", {"a": 20, "b": 22})
                self.assertIn("42", res.get("_text", ""))
            finally:
                client.close()


if __name__ == "__main__":
    unittest.main()
