"""上下文预算 Budget —— token 计量 + 超预算信号。

你要的"上下文不能只增不减"：当前架构里多轮会话上下文由大脑内核统一管理
（身体每次只发单个 prompt，不持有历史列表），所以真正的压缩/归档由内核负责。
身体侧的 Budget 职责 = 精确记账 + 暴露 over_budget 信号（供前端/后续内核压缩钩子用），
不做身体层历史裁剪（body 无历史可裁，避免留下死代码假象）。

估算策略：中文≈1 token/字，英文≈1 token/4字符（与内核 estimate_tokens 对齐）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（中文≈1，英文≈4字符1个）。"""
    if not text:
        return 0
    # 简单启发：非ASCII字符算1，ASCII每4字符算1
    cjk = sum(1 for ch in text if ord(ch) > 127)
    ascii_count = len(text) - cjk
    return cjk + ascii_count // 4 + (1 if ascii_count % 4 else 0)


class ContextBudget:
    """会话级 token 预算管理：记账 + 超预算信号。

    max_tokens: 预算上限；archive_at: 达到该比例触发压缩信号（默认80%）。
    注：身体不持有对话历史，故不做 body 层历史裁剪；over_budget 作为信号
    暴露给调用方（未来可对接内核压缩钩子 / 前端展示）。
    """

    def __init__(self, data_dir: str | Path, max_tokens: int = 16000,
                 archive_at: float = 0.8):
        """max_tokens: 预算上限；archive_at: 达到该比例触发压缩信号（默认80%）。"""
        self.max_tokens = max_tokens
        self.archive_at = archive_at
        self.path = Path(data_dir) / "budget.json"
        self.usage = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"sessions": {}}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.usage, ensure_ascii=False),
                       encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.path)

    # ---- 会话 token 记账 ----
    def record(self, session: str, prompt_tokens: int, completion_tokens: int) -> None:
        s = self.usage["sessions"].setdefault(
            session, {"prompt": 0, "completion": 0, "messages": 0})
        s["prompt"] += prompt_tokens
        s["completion"] += completion_tokens
        s["messages"] += 1
        s["last_at"] = time.time()
        self._save()

    def session_usage(self, session: str) -> dict:
        return self.usage["sessions"].get(
            session, {"prompt": 0, "completion": 0, "messages": 0})

    def over_budget(self, session: str) -> bool:
        """当前会话是否已达压缩触发线（信号，供调用方决定是否压缩/归档）。"""
        u = self.session_usage(session)
        total = u["prompt"] + u["completion"]
        return total >= self.max_tokens * self.archive_at

    # ---- 归档（保留：移动当前 usage 到历史，供前端查看历史用量） ----
    def archive(self, session: str) -> dict:
        """把当前会话 usage 归档到历史并重置（供前端/审计查看，非上下文压缩）。"""
        u = self.usage["sessions"].pop(session, None) or {}
        self.usage.setdefault("archive", []).append({
            "session": session, "usage": u, "archived_at": time.time()})
        # 只保留最近50个归档
        self.usage["archive"] = self.usage["archive"][-50:]
        self._save()
        return u

    def status(self) -> dict:
        return {
            "max_tokens": self.max_tokens,
            "archive_at": self.archive_at,
            "sessions": len(self.usage.get("sessions", {})),
            "archives": len(self.usage.get("archive", [])),
        }
