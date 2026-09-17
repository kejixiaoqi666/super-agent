"""可观测性 Observe —— 结构化 trace + 执行轨迹 + 统一日志。

成熟 agent 有完整可观测性。本模块提供轻量但真实的结构化追踪：
  - Tracer: 用 trace_id/span 记录每次对话/工具调用/任务的执行轨迹
  - 输出到 data_dir/observe/traces/ 的 JSONL（追加、可重放、可按 trace_id 检索）
  - 上下文管理器 `tracer.span(name)` 自动记录进入/退出/耗时/错误
  - 统一 logger：日志同时写 stderr + 可选的 observe 文件
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional


def _now() -> float:
    return time.time()


class Tracer:
    """结构化执行追踪：每次对话/工具调用/任务记一条带 trace_id 的 span。"""

    def __init__(self, data_dir: str | Path):
        self.dir = Path(data_dir) / "observe" / "traces"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._stack: list = []
        self._last_trace_id: Optional[str] = None

    def _write(self, record: dict) -> None:
        trace_id = record.get("trace_id", "unknown")
        path = self.dir / f"{trace_id}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def trace_id(self) -> str:
        return self._last_trace_id or ""

    @contextmanager
    def span(self, name: str, trace_id: Optional[str] = None,
             **attrs: Any) -> Iterator[None]:
        """记录一个执行 span。自动生成/沿用 trace_id，记录进入/退出/耗时/错误。"""
        tid = trace_id or self._last_trace_id or uuid.uuid4().hex[:12]
        self._last_trace_id = tid
        start = _now()
        parent = self._stack[-1] if self._stack else None
        span_id = uuid.uuid4().hex[:8]
        self._stack.append(span_id)
        self._write({"ts": start, "trace_id": tid, "span_id": span_id,
                     "parent": parent, "event": "start", "name": name, **attrs})
        try:
            yield
            self._write({"ts": _now(), "trace_id": tid, "span_id": span_id,
                         "event": "end", "name": name,
                         "elapsed_ms": round((_now() - start) * 1000, 2)})
        except Exception as exc:
            self._write({"ts": _now(), "trace_id": tid, "span_id": span_id,
                         "event": "error", "name": name,
                         "error": str(exc),
                         "elapsed_ms": round((_now() - start) * 1000, 2)})
            raise
        finally:
            if self._stack:
                self._stack.pop()

    def log(self, name: str, **attrs: Any) -> None:
        """记一条无耗时的事件（工具调用、消息等）。"""
        tid = attrs.pop("trace_id", None) or self._last_trace_id \
            or uuid.uuid4().hex[:12]
        self._last_trace_id = tid
        self._write({"ts": _now(), "trace_id": tid, "event": name, **attrs})

    def read_trace(self, trace_id: str) -> list:
        """读取某 trace 的全部记录（重放轨迹）。"""
        path = self.dir / f"{trace_id}.jsonl"
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out


def get_logger(name: str = "super-agent",
               data_dir: Optional[str | Path] = None) -> logging.Logger:
    """统一 logger：stderr + 可选 observe 文件。幂等，多次调用同一 name 不重复加 handler。"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    if data_dir is not None:
        log_dir = Path(data_dir) / "observe"
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(str(log_dir / "agent.log"), encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger
