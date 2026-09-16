"""上下文预算 Budget —— token 计量 + 超预算自动压缩归档。

你要的"上下文不能只增不减"：给每次会话设 token 预算，超了自动压缩/归档旧消息，
把空间留给真正重要的新信息。

估算策略：中文≈1 token/字，英文≈1 token/4字符（与内核 estimate_tokens 对齐）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（中文≈1，英文≈4字符1个）。"""
    if not text:
        return 0
    # 简单启发：非ASCII字符算1，ASCII每4字符算1
    cjk = sum(1 for ch in text if ord(ch) > 127)
    ascii_count = len(text) - cjk
    return cjk + ascii_count // 4 + (1 if ascii_count % 4 else 0)


class ContextBudget:
    """会话级 token 预算管理。"""

    def __init__(self, data_dir: str | Path, max_tokens: int = 16000,
                 archive_at: float = 0.8):
        """max_tokens: 预算上限；archive_at: 达到该比例触发归档（默认80%）。"""
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
        """当前会话是否已达归档触发线。"""
        u = self.session_usage(session)
        total = u["prompt"] + u["completion"]
        return total >= self.max_tokens * self.archive_at

    # ---- 归档 ----
    def archive(self, session: str) -> dict:
        """归档旧会话：把当前 usage 移到历史，重置当前（腾出预算）。"""
        u = self.usage["sessions"].pop(session, None) or {}
        self.usage.setdefault("archive", []).append({
            "session": session, "usage": u, "archived_at": time.time()})
        # 只保留最近50个归档
        self.usage["archive"] = self.usage["archive"][-50:]
        self._save()
        return u

    def trim_messages(self, history: List[dict], keep_last: int = 20) -> List[dict]:
        """超预算时压缩历史：只保留系统提示 + 最近 keep_last 条。"""
        if len(history) <= keep_last:
            return history
        # 保留系统消息（role=system）在最前，其余只留最近 keep_last
        system = [m for m in history if m.get("role") == "system"]
        tail = history[-keep_last:]
        return system + tail

    def status(self) -> dict:
        return {
            "max_tokens": self.max_tokens,
            "archive_at": self.archive_at,
            "sessions": len(self.usage.get("sessions", {})),
            "archives": len(self.usage.get("archive", [])),
        }
