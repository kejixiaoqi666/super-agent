"""信任模式 TrustMode（阶段⑥ 主权开放架构）——guided(用户主导) vs sovereign(AI 主权)。

mode 只决定「AI 自主体验」，**绝不改变硬约束**：
  - 内核只读（不可撼动）：任何 mode 下 AI 都不能写内核。
  - 底层进化需批准：任何 mode 下 base 提案都必须用户批准。
  - 上层模块/插件自主进化：任何 mode 下 upper 都自主。
mode 影响的是"谁主导"的语义标识 + 未来可选的体验策略，但安全底线固定。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

MODES = ("guided", "sovereign")
DEFAULT_MODE = "guided"


class TrustMode:
    """持久化的信任模式开关。默认 guided（用户主导）。"""

    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "sovereign" / "trust.json"
        self._mode = self._load()

    def _load(self) -> str:
        if self.path.exists():
            try:
                m = json.loads(self.path.read_text(encoding="utf-8")).get("mode")
                if m in MODES:
                    return m
            except Exception:
                pass
        return DEFAULT_MODE

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"mode": self._mode, "updated_at": time.time()},
            ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def set(self, mode: str, by: str = "user") -> dict:
        """切换信任模式。mode 变化不影响硬约束（内核只读/底层批准不变）。"""
        if mode not in MODES:
            return {"ok": False, "error": f"未知模式 {mode!r}（可用: {MODES}）"}
        prev = self._mode
        self._mode = mode
        self._save()
        return {"ok": True, "mode": mode, "prev": prev, "by": by}

    def get(self) -> str:
        return self._mode

    def is_sovereign(self) -> bool:
        return self._mode == "sovereign"

    def is_guided(self) -> bool:
        return self._mode == "guided"

    def autonomy(self) -> dict:
        """当前自主级别 + 不可撼动的硬约束（供展示/审计）。"""
        return {
            "mode": self._mode,
            # 以下硬约束不随 mode 改变（安全底线）
            "kernel_readonly": True,
            "base_requires_approval": True,
            "upper_autonomous": True,
        }