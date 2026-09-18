"""常驻解释器 Worker 池 —— 海量 python 脚本吞吐优化（免每任务进程冷启动）。

现状：每任务 spawn 一个解释器(~20-40ms) → 吞吐 ~200/s，瓶颈在进程创建。
本模块：预起 N 个常驻解释器，通过 stdin/stdout JSON 行协议执行代码，
消除解释器冷启动 → 吞吐提升数倍。

资源纪律（用户定调）：
  - **默认关闭**（size=0 不 spawn，零进程零占用）；显式开启才起 worker。
  - size 即常驻进程数 = 内存/CPU 上界，受控。
  - 单任务超时：进程级超时 kill 该 worker 并自动补位重启（不堵池）。
  - **每个 worker 持独立锁**：保证同一 worker 同一时刻只处理一个请求（无应答交错）。

协议（python worker）：
  父→子: {"id": "...", "code": "..."} \n
  子→父: {"id": "...", "ok": true/false, "result": "...", "error": "..."} \n
代码里约定 `_result` 作为返回值。
"""

from __future__ import annotations

import json
import select
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

_WORKER_PY = """
import sys, json, traceback
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    try:
        ns = {"_result": None}
        exec(compile(req.get("code", ""), "<persistent>", "exec"), ns)
        out = {"id": req["id"], "ok": True, "result": repr(ns.get("_result"))}
    except BaseException:
        out = {"id": req["id"], "ok": False, "error": traceback.format_exc()[-2000:]}
    sys.stdout.write(json.dumps(out) + "\\n")
    sys.stdout.flush()
"""


@dataclass
class _Worker:
    proc: subprocess.Popen
    lock: threading.Lock = field(default_factory=threading.Lock)


class PersistentPool:
    """常驻解释器 worker 池。默认 size=0 不 spawn；execute 时惰性启动。"""

    def __init__(self, runtime: str = "python", size: int = 0,
                 cwd: str | Path | None = None, interpreter: Optional[str] = None):
        self.runtime = runtime
        self.size = max(0, size)
        self.cwd = str(cwd or Path.cwd())
        self.interpreter = interpreter
        self._workers: List[_Worker] = []
        self._idx = 0
        self._lock = threading.Lock()   # 保护 _workers/_idx
        self._closed = False

    def _spawn(self) -> _Worker:
        if self.runtime == "python":
            args = [self.interpreter or "python3", "-c", _WORKER_PY]
        else:
            raise ValueError(f"persistent worker 暂只支持 python (got {self.runtime!r})")
        proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, bufsize=1, cwd=self.cwd)
        return _Worker(proc)

    def _ensure(self) -> None:
        if self._closed:
            raise RuntimeError("pool closed")
        while len(self._workers) < self.size:
            self._workers.append(self._spawn())

    # ---- 执行 ----
    def execute(self, code: str, timeout_s: float = 5.0) -> dict:
        """把 code 送常驻 worker 执行（独占该 worker）。超时 kill 补位。返回 {ok, result/error}。"""
        if self.size <= 0:
            return {"ok": False, "error": "persistent pool 未启用(size=0)"}
        with self._lock:
            self._ensure()
            w = self._workers[self._idx]
            self._idx = (self._idx + 1) % len(self._workers)
            if w.proc.poll() is not None:   # 已死 → 补位重启
                w.proc.kill()
                self._workers[self._workers.index(w)] = w = self._spawn()
        # 独占该 worker（同一 worker 同一刻只一个请求，防应答交错）
        with w.lock:
            proc = w.proc
            rid = uuid.uuid4().hex[:8]
            try:
                assert proc.stdin is not None and proc.stdout is not None
                proc.stdin.write(json.dumps({"id": rid, "code": code}) + "\n")
                proc.stdin.flush()
            except Exception as e:
                self._replace(w)
                return {"ok": False, "error": f"worker 写失败: {e}"}
            return self._read(w, rid, timeout_s)

    def _read(self, w: _Worker, rid: str, timeout_s: float) -> dict:
        proc = w.proc
        try:
            ready, _, _ = select.select([proc.stdout], [], [], timeout_s)
        except Exception as e:
            self._replace(w)
            return {"ok": False, "error": f"select 失败: {e}"}
        if not ready:
            self._replace(w)
            return {"ok": False, "error": f"persistent timeout ({timeout_s}s)"}
        try:
            line = proc.stdout.readline()
        except Exception as e:
            self._replace(w)
            return {"ok": False, "error": f"worker 读失败: {e}"}
        try:
            resp = json.loads(line)
        except Exception:
            self._replace(w)
            return {"ok": False, "error": "worker 返回非 JSON"}
        if resp.get("id") != rid:
            self._replace(w)
            return {"ok": False, "error": "worker 应答 id 不匹配"}
        if not resp.get("ok"):
            return {"ok": False, "error": resp.get("error", "unknown")}
        return {"ok": True, "result": resp.get("result")}

    def _replace(self, w: _Worker) -> None:
        """kill 并重启一个 worker（超时/异常后补位，不堵池）。"""
        try:
            w.proc.kill()
        except Exception:
            pass
        with self._lock:
            try:
                idx = self._workers.index(w)
                self._workers[idx] = self._spawn()
            except ValueError:
                pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for w in self._workers:
            try:
                w.proc.kill()
            except Exception:
                pass
        self._workers.clear()