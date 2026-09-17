"""MCP 协议层 —— Model Context Protocol 客户端/服务器。

用纯标准库实现 MCP（JSON-RPC 2.0 over stdio，newline-delimited JSON framing），
不依赖外部包，真实可跑。提供：
  - MCPClient: 连接一个 MCP 服务器(stdio)，initialize 握手 → list_tools 发现 → call_tool 调用
  - MCPServer: 最小 MCP 服务器，暴露一组工具，供客户端调用

MCP 语义（2024-11-25 协议）：
  initialize / notifications/initialized / tools/list / tools/call
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from typing import Callable, Dict, List, Optional

# ---- JSON-RPC 2.0 基础 ----

import itertools as _itertools
_rpc_counter = _itertools.count(1)


def _rpc_id() -> int:
    return next(_rpc_counter)


def _make_request(method: str, params: Optional[dict],
                  req_id: Optional[int] = None) -> dict:
    return {"jsonrpc": "2.0", "id": req_id if req_id is not None else _rpc_id(),
            "method": method, "params": params or {}}


def _make_response(req_id, result=None, error=None) -> dict:
    msg = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    return msg


# ---- 客户端 ----

class MCPError(RuntimeError):
    pass


class MCPClient:
    """stdio MCP 客户端：启动并连接一个 MCP 服务器进程。"""

    def __init__(self, command: str, args: Optional[List[str]] = None,
                 cwd: Optional[str] = None, env: Optional[dict] = None,
                 name: str = "mcp-server", timeout: float = 10.0):
        self.name = name
        self.timeout = timeout
        self._pending: Dict[int, threading.Event] = {}
        self._responses: Dict[int, dict] = {}
        self._init_lock = threading.Lock()
        env_all = dict(os.environ)
        if env:
            env_all.update(env)
        self._proc = subprocess.Popen(
            [command] + (args or []), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=cwd, env=env_all, text=True)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._stdin = self._proc.stdin

    def _read_loop(self) -> None:
        if self._proc.stdout is None:
            return
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and "id" in msg:
                self._responses[msg["id"]] = msg
                ev = self._pending.pop(msg["id"], None)
                if ev is not None:
                    ev.set()

    def _request(self, method: str, params: Optional[dict] = None) -> dict:
        req_id = _rpc_id()
        if self._stdin is None:
            raise MCPError(f"{self.name}: stdin 不可用")
        self._stdin.write(json.dumps(_make_request(method, params, req_id)) + "\n")
        self._stdin.flush()
        ev = threading.Event()
        self._pending[req_id] = ev
        if not ev.wait(self.timeout):
            raise MCPError(f"{self.name}: 请求 {method} 超时")
        msg = self._responses.pop(req_id, {})
        if "error" in msg:
            raise MCPError(f"{self.name}: {method} 错误: {msg['error']}")
        return msg.get("result", {})

    def connect(self) -> dict:
        """initialize 握手。"""
        with self._init_lock:
            res = self._request("initialize", {
                "protocolVersion": "2024-11-25",
                "capabilities": {},
                "clientInfo": {"name": "super-agent", "version": "0.3.0"},
            })
            # 服务器能力/工具列表
            try:
                tools = self._request("tools/list", {})
            except MCPError:
                tools = {}
            return {"server": res, "tools": tools}

    def list_tools(self) -> List[dict]:
        res = self._request("tools/list", {})
        return res.get("tools", [])

    def call_tool(self, name: str, arguments: Optional[dict] = None) -> dict:
        """调用工具。返回 MCP 规范 result（含 content 数组）。"""
        res = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        # 归一化 content 数组 → 纯文本
        content = res.get("content", [])
        if isinstance(content, list):
            text = "".join(c.get("text", "") for c in content
                           if isinstance(c, dict))
            res["_text"] = text
        return res

    def close(self) -> None:
        if self._stdin is not None:
            try:
                self._stdin.close()
            except Exception:
                pass
        self._proc.terminate()
        try:
            self._proc.wait(timeout=3)
        except Exception:
            self._proc.kill()


# ---- 服务器 ----

class MCPServer:
    """最小 MCP 服务器：暴露一组工具，通过 stdio 服务。

    serve(handle) 由调用方传入「单次请求处理函数」或在子进程里运行：
    典型用法是 fork 一个子进程跑 server.listen()，客户端连上它。
    """

    def __init__(self, tools: Optional[Dict[str, Callable]] = None,
                 server_info: Optional[dict] = None):
        # tools: {name: callable(args_dict)->result}
        self.tools = tools or {}
        self.server_info = server_info or {"name": "super-agent-server",
                                           "version": "0.3.0"}

    def _handle(self, msg: dict) -> Optional[dict]:
        if not isinstance(msg, dict) or "method" not in msg:
            return None
        method = msg["method"]
        req_id = msg.get("id")
        params = msg.get("params", {}) or {}
        if method == "initialize":
            return _make_response(req_id, {
                "protocolVersion": "2024-11-25",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": self.server_info,
            })
        if method == "tools/list":
            return _make_response(req_id, {
                "tools": [{"name": n, "description": getattr(f, "__doc__", "") or "",
                           "inputSchema": {"type": "object"}}
                          for n, f in self.tools.items()],
            })
        if method == "tools/call":
            name = params.get("name", "")
            arguments = params.get("arguments", {}) or {}
            if name not in self.tools:
                return _make_response(
                    req_id, error={"code": -32602, "message": f"未知工具: {name}"})
            try:
                result = self.tools[name](arguments)
                return _make_response(req_id, {"content": [{"type": "text",
                                                            "text": json.dumps(result, ensure_ascii=False)}]})
            except Exception as e:
                return _make_response(req_id, error={"code": -32000,
                                                     "message": str(e)})
        # notifications 无 id，不回
        return None

    def listen(self, stdin=None, stdout=None) -> int:
        """在主循环读取 stdio 并响应。返回处理的请求数。"""
        import sys
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        n = 0
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            resp = self._handle(msg)
            if resp is not None:
                stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                stdout.flush()
                n += 1
        return n

    # 便捷：在本进程内跑一个「脚本式服务器」（供测试用）
    def run_forever(self) -> None:
        self.listen()
