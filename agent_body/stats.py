"""Token 计费 + 上下文统计（身体层）

每次对话记录输入/输出 token 与估算成本、上下文消息数/长度，落盘 JSON（0600）。
提供汇总视图：总用量 / 按日 / 成本 / 上下文长度。不影响工作，但排查/控费很有用。

注意：统计是「估算/记账」，真实 cost 以模型通道 usage 为准（内核未暴露 usage 时用估算）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

# 常用模型单价（元/百万 token：输入, 输出）。可在配置里覆盖，不硬编码生效。
DEFAULT_PRICES: Dict[str, tuple] = {
    "deepseek-v4-flash": (1.0, 4.0),
    "deepseek-v4-pro": (2.0, 8.0),
    "deepseek-v3": (0.27, 1.1),
    "gpt-6": (3.0, 12.0),
    "claude-haiku-4-5": (3.0, 15.0),
    "MiniMax-M3": (2.0, 8.0),
    "default": (1.0, 2.0),
}


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（与内核一致：中文≈1，英文≈4字符/token）。"""
    if not text:
        return 0
    zh = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - zh
    return zh + max(1, other // 4)


class TokenStats:
    """会话 token 流水账。落盘 data_dir/token_stats.json（0600）。"""

    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "token_stats.json"
        self._rows: List[Dict] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._rows = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self._rows = []

    def save(self) -> None:
        from .persist import json_atomic_write
        json_atomic_write(self.path, self._rows)

    def record(self, session: str, input_text: str, output_text: str,
               model: str = "default", price: Optional[tuple] = None,
               context_msgs: int = 0) -> Dict:
        """记录一次对话的 token 用量与估算成本。"""
        in_tok = estimate_tokens(input_text) or 1
        out_tok = estimate_tokens(output_text)
        p = price or DEFAULT_PRICES.get(model, DEFAULT_PRICES["default"])
        cost = round(in_tok * p[0] / 1_000_000 + out_tok * p[1] / 1_000_000, 6)
        row = {
            "ts": time.time(), "day": time.strftime("%Y-%m-%d"),
            "session": session, "model": model,
            "prompt_tokens": in_tok, "completion_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
            "context_msgs": context_msgs,
            "cost_cny": cost,
        }
        self._rows.append(row)
        self.save()
        return row

    # ---- 汇总视图 ----
    def summary(self, days: int = 30) -> Dict:
        cutoff = time.time() - days * 86400
        recent = [r for r in self._rows if r["ts"] >= cutoff]
        by_day: Dict[str, Dict] = {}
        for r in recent:
            d = by_day.setdefault(r["day"], {"prompt": 0, "completion": 0,
                                             "cost": 0.0, "count": 0})
            d["prompt"] += r["prompt_tokens"]
            d["completion"] += r["completion_tokens"]
            d["cost"] += r["cost_cny"]
            d["count"] += 1
        return {
            "days": days,
            "conversations": len(recent),
            "total_tokens": sum(r["total_tokens"] for r in recent),
            "prompt_tokens": sum(r["prompt_tokens"] for r in recent),
            "completion_tokens": sum(r["completion_tokens"] for r in recent),
            "context_avg_msgs": round(
                sum(r["context_msgs"] for r in recent) / max(len(recent), 1), 1),
            "cost_cny": round(sum(r["cost_cny"] for r in recent), 4),
            "by_day": by_day,
            "average_per_turn": round(
                sum(r["total_tokens"] for r in recent) / max(len(recent), 1), 0)
            if recent else 0,
        }

    def context_length_last(self) -> Optional[Dict]:
        """最近一次对话的上下文统计。"""
        if not self._rows:
            return None
        r = self._rows[-1]
        return {"session": r["session"], "context_msgs": r["context_msgs"],
                "prompt_tokens": r["prompt_tokens"],
                "total_tokens": r["total_tokens"]}