"""上下文预算 Budget —— token 计量 + 超预算信号。

你要的"上下文不能只增不减" + "要算每轮真实输入"：
  - 累计成本（cost）：跨所有消息的 prompt+completion 加总，衡量会话总开销。
  - **每轮真实输入（input）**：当轮真正喂给模型的 token（向量压缩后），
    这才是"每次思考的量"，也是自动续接/压缩的触发依据。
    它可能远小于累计成本——因为我们用向量库精自压缩，上下文长但输入聚焦。

当前架构里多轮会话的组装在 superbrain 内核（系统+状态+向量召回记忆+工具+prompt），
所以"真实输入"最准的来源是内核 llm.chat 返回的 usage.prompt_tokens；身体在收不到时
用自己构造的精简 prompt（含简报）做下限代理。

估算策略：中文≈1 token/字，英文≈1 token/4字符（与内核 estimate_tokens 对齐）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


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
                 archive_at: float = 0.8,
                 context_length: int = 256000,
                 continuity_at: float = 0.5,
                 warn_ratio: float = 0.8):
        """max_tokens: 预算上限；archive_at: 达到该比例触发压缩信号。
        context_length: 模型上下文窗口（触发自动续接的分母）；
        continuity_at: 当轮真实输入占窗口比例达到它 → over_compressed（自动续接）。
        warn_ratio: 预警线 = continuity_at*warn_ratio（预动——在到阈值前提醒，非被动临界）。
        """
        self.max_tokens = max_tokens
        self.archive_at = archive_at
        self.context_length = max(1, context_length)
        self.continuity_at = max(0.1, min(0.95, continuity_at))
        self.warn_ratio = max(0.1, min(1.0, warn_ratio))
        self.warn_at = self.continuity_at * self.warn_ratio   # 预警线占窗口比例
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
    def record(self, session: str, prompt_tokens: int, completion_tokens: int,
               input_tokens: Optional[int] = None) -> None:
        """记账一轮。

        prompt/completion 计入累计成本（cost）；input_tokens 是该轮**真实输入**
        （向量压缩后喂给模型的量），作为"本轮思考量"的高水位更新。
        input_tokens 缺省时用 prompt_tokens 兜底（身体构造的精简 prompt）。
        """
        s = self.usage["sessions"].setdefault(
            session, {"prompt": 0, "completion": 0, "messages": 0,
                      "last_input": 0, "max_input": 0})
        s["prompt"] += prompt_tokens
        s["completion"] += completion_tokens
        s["messages"] += 1
        cur_input = int(input_tokens) if input_tokens is not None \
            else int(prompt_tokens)
        s["last_input"] = cur_input
        if cur_input > s.get("max_input", 0):
            s["max_input"] = cur_input
        s["last_at"] = time.time()
        self._save()

    def session_usage(self, session: str) -> dict:
        return self.usage["sessions"].get(
            session, {"prompt": 0, "completion": 0, "messages": 0,
                      "last_input": 0, "max_input": 0})

    def over_budget(self, session: str) -> bool:
        """当前会话是否已达压缩触发线（累计成本信号，供调用方决定是否归档）。"""
        u = self.session_usage(session)
        total = u["prompt"] + u["completion"]
        return total >= self.max_tokens * self.archive_at

    def over_compressed(self, session: str) -> bool:
        """当轮**真实输入**是否已占模型窗口太多 → 该自动续接新会话。

        依据是向量压缩后的每轮输入（input），而非累计成本——
        上下文长但输入聚焦时不会误触发。连续触发防抖：需至少见过一轮输入。
        """
        u = self.session_usage(session)
        last = u.get("last_input", 0)
        if last <= 0:
            return False
        return last >= self.context_length * self.continuity_at

    def continuity_status(self, session: str) -> dict:
        """该会话的续接判断详情（供 CLI /continuity 展示）。

        level: ok(健康) / warn(接近预警线, 建议留意) / critical(应续接新会话)。
        预动：在到触发阈值前按 warn_ratio 提前预警，而非被动临界才动作。
        """
        u = self.session_usage(session)
        window = self.context_length
        last = u.get("last_input", 0)
        pct = round(last / window, 4) if window else 0.0
        if self.over_compressed(session):
            level = "critical"
        elif last > 0 and pct >= self.warn_at:
            level = "warn"
        else:
            level = "ok"
        return {
            "session": session,
            "window": window,
            "continuity_at": self.continuity_at,
            "warn_at": round(self.warn_at, 4),
            "last_input": last,
            "max_input": u.get("max_input", 0),
            "cum_cost": u.get("prompt", 0) + u.get("completion", 0),
            "input_pct": pct,
            "level": level,
            "warning": level in ("warn", "critical"),
            "needs_continuity": self.over_compressed(session),
        }

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
