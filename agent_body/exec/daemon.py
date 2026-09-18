"""执行 daemon —— 高并发 · 省资源 · 清洁。

设计（用户定调：不臃肿，省内存/硬盘，高并发但清洁）：
  - **高并发不靠多线程/多进程堆**，靠「有界 worker 池 + 内存队列」。
    几千任务是队列里的轻量项，不是几千个进程/线程 → 峰值内存受 pool_size 约束。
  - **多运行时**：任务声明 runtime(shell/python/node)，语言无关，惰性按需构造。
  - **资源上限**：timeout_s(沙箱) + mem_limit_mb(沙箱 setrlimit)。
  - **清洁**：复用 Sandbox 自动清理工作目录；结果内存态，不落盘。
  - **错误分类**：结构化 error_class，供智能体自动修复闭环（读错→改→重跑）。

沙箱在 data_dir/sandbox/<task_id>/ 跑命令，结束自动回收。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .sandbox import CommandError, Sandbox

# 任务 id：时间戳+进程内自增，保证同毫秒内批量建任务也不撞 id（撞 id 会同沙箱目录踩踏）
_id_seq = count(1)


def _new_task_id() -> str:
    return f"t{int(time.time() * 1000)}_{next(_id_seq)}"

# 多运行时：command 按运行时包装成实际执行命令（shell=True 下用 heredoc 传脚本体）
RUNTIMES: Dict[str, Callable[[str], str]] = {
    "shell": lambda c: c,
    "python": lambda c: f"python3 - <<'PYEOF'\n{c}\nPYEOF",
    "node": lambda c: f"node - <<'NODEEOF'\n{c}\nNODEEOF",
}


def _classify(code: int, output: str) -> str:
    """把执行失败归到结构化错误类别（供智能体诊断/修复）。"""
    if code == -9:
        return "timeout"
    if code == -1:
        return "permission"      # 危险命令被沙箱门禁拦截
    if code == -2:
        return "resource"        # 重试预算耗尽
    out = (output or "").lower()
    if "syntaxerror" in out or "syntax error" in out:
        return "syntax"
    if "modulenotfounderror" in out or "module not found" in out \
            or "no such file" in out or "command not found" in out:
        return "deps"
    if "memoryerror" in out or "out of memory" in out or "killed" in out:
        return "resource"
    if "traceback" in out:
        return "runtime"
    return "runtime"


@dataclass
class Task:
    """一次执行任务。command 是该 runtime 的脚本/命令体。"""
    id: str = field(default_factory=_new_task_id)
    runtime: str = "shell"
    command: str = ""
    timeout_s: float = 60.0
    mem_limit_mb: Optional[int] = None
    keep: bool = False      # 保留工作目录与产物（默认不保留，清洁）
    retries: int = 0        # 幂等重试次数
    persistent: bool = False  # 走常驻解释器 worker 池(海量 python 脚本提速, 需 executor persistent_size>0)

    def __post_init__(self):
        if self.runtime not in RUNTIMES:
            raise ValueError(f"未知 runtime: {self.runtime!r}（可用: {list(RUNTIMES)}）")


@dataclass
class ExecResult:
    task_id: str
    status: str                       # done | error
    code: int
    output: str
    elapsed_ms: int
    error_class: Optional[str] = None  # timeout/syntax/deps/resource/permission/runtime/unknown
    error_msg: Optional[str] = None
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return self.status == "done"

    def to_dict(self) -> dict:
        return {"task_id": self.task_id, "status": self.status, "code": self.code,
                "output": self.output, "elapsed_ms": self.elapsed_ms,
                "error_class": self.error_class, "error_msg": self.error_msg,
                "attempts": self.attempts}


class ExecutorDaemon:
    """常驻执行器：有界线程池 = worker 池 + 内部任务队列。

    run_many 一次提交数千任务，由池并发调度；保持输入顺序返回结果。
    pool_size 即并发上限 = 峰值内存/CPU 受控；默认 4~8，避免臃肿。
    """

    def __init__(self, data_dir: str | Path, pool_size: int = 4,
                 default_timeout: float = 60.0, persistent_size: int = 0):
        self.sandbox = Sandbox(data_dir)
        self.pool_size = max(1, pool_size)
        self.default_timeout = default_timeout
        self._pool = ThreadPoolExecutor(max_workers=self.pool_size,
                                        thread_name_prefix="exec")
        # 常驻解释器 worker 池（默认 0=关闭，不 spawn；海量 python 脚本才开启）
        self.persistent_size = max(0, persistent_size)
        self._persistent = None
        if persistent_size > 0:
            from .persistent import PersistentPool
            self._persistent = PersistentPool("python", size=persistent_size)
        self._stats = {"submitted": 0, "done": 0, "error": 0, "total_ms": 0}
        self._closed = False

    # ---- 执行 ----
    def _render(self, task: Task) -> str:
        return RUNTIMES[task.runtime](task.command)

    def _execute(self, task: Task) -> ExecResult:
        start = time.time()
        # 常驻 worker 快速路径：python + persistent + 池已启用 → 免解释器冷启动
        if task.persistent and task.runtime == "python" \
                and self._persistent is not None:
            return self._exec_persistent(task, start)
        cmd = self._render(task)
        try:
            r = self.sandbox.run(
                cmd, task_id=task.id, timeout=task.timeout_s, keep=task.keep,
                retries=task.retries, mem_limit_mb=task.mem_limit_mb)
            return ExecResult(
                task_id=task.id, status="done", code=r["code"],
                output=r["output"], elapsed_ms=int((time.time() - start) * 1000),
                error_class=None, error_msg=None, attempts=r.get("attempts", 1))
        except CommandError as e:
            ecls = _classify(e.code, e.output)
            return ExecResult(
                task_id=task.id,
                status="timeout" if e.code == -9 else "error",
                code=e.code, output=e.output[:4000],
                elapsed_ms=int((time.time() - start) * 1000),
                error_class=ecls, error_msg=str(e), attempts=1)
        except Exception as e:
            return ExecResult(
                task_id=task.id, status="error", code=-1, output="",
                elapsed_ms=int((time.time() - start) * 1000),
                error_class="unknown", error_msg=f"{type(e).__name__}: {e}",
                attempts=1)

    def _exec_persistent(self, task: Task, start: float) -> ExecResult:
        """常驻 worker 执行：免解释器冷启动，超时 kill 补位。"""
        assert self._persistent is not None
        try:
            r = self._persistent.execute(task.command, timeout_s=task.timeout_s)
            ms = int((time.time() - start) * 1000)
            if r.get("ok"):
                return ExecResult(
                    task_id=task.id, status="done", code=0,
                    output=str(r.get("result", ""))[:4000],
                    elapsed_ms=ms, error_class=None, error_msg=None, attempts=1)
            return ExecResult(
                task_id=task.id, status="error", code=-1,
                output=str(r.get("error", ""))[:4000],
                elapsed_ms=ms, error_class="runtime",
                error_msg=str(r.get("error", "")), attempts=1)
        except Exception as e:
            return ExecResult(
                task_id=task.id, status="error", code=-1, output="",
                elapsed_ms=int((time.time() - start) * 1000),
                error_class="unknown", error_msg=f"{type(e).__name__}: {e}",
                attempts=1)

    def execute(self, task: Task) -> ExecResult:
        """单个任务同步执行（阻塞到完成）。"""
        return self.run_many([task])[0]

    def run_many(self, tasks: List[Task], keep_order: bool = True) -> List[ExecResult]:
        """批量并发执行：一次提交全部到池，并发调度，保持输入顺序返回。

        数千任务 = 数千个轻量 future 排队，由 pool_size 并发上限控制资源。
        """
        if self._closed:
            raise RuntimeError("executor already closed")
        futures = [self._pool.submit(self._execute, t) for t in tasks]
        results = [f.result() for f in futures]   # 保持输入顺序
        for r in results:
            self._stats["done" if r.ok else "error"] += 1
            self._stats["total_ms"] += r.elapsed_ms
        self._stats["submitted"] += len(tasks)
        return results

    # ---- 生命周期 ----
    def shutdown(self) -> None:
        if not self._closed:
            self._pool.shutdown(wait=True)
            if self._persistent is not None:
                self._persistent.close()
            self._closed = True

    def stats(self) -> dict:
        s = dict(self._stats)
        s["pool_size"] = self.pool_size
        return s

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.shutdown()
