"""自动续接 Auto-Continuity —— 长上下文自动开新会话并无缝衔接。

触发依据：当轮**真实输入**（向量压缩后喂给模型的 token）占模型窗口比例达阈值。
这与 Hermes 的累计上下文触发不同：上下文长但输入聚焦时不会误触发。

衔接不靠模型压缩摘要（有损、可能偏移/幻觉），而是写**精确锚点**进向量记忆
（原词句：目标/决定/文件/阻断项/偏好）。后继会话经现有 handover/briefing
自动召回这些锚点 → 无缝知道上一会话在哪、做过什么、下一步做什么。

严格约束（用户要求）：
  - 不丢语义 / 不幻觉：锚点只来自调用方传入的**真实事实**（meta），无匹配不写；
    从不生成替代描述。
  - 不偏移：锚点是原词句，非模型改写。
默认不主动改会话状态；execute 只写向量记忆 + 返回后继会话 id，由上层决定启动。
"""

from __future__ import annotations

import re
import time
from typing import List, Optional


def successor_id(session: str) -> str:
    """下一会话 id：`<session>#N` 递增；无后缀则 append `#2`。"""
    m = re.search(r"(.*?)#(\d+)$", str(session))
    if m:
        return f"{m.group(1)}#{int(m.group(2)) + 1}"
    return f"{session}#2"


class ContinuityManager:
    """判断 + 生成锚点 + 写向量记忆 + 给出后继会话。

    budget: 需有 over_compressed(session) 和 continuity_status(session)。
    brain: 需有 remember(content, tags, importance, tier) —— 匹配 handover 约定。
    """

    def __init__(self, budget, brain=None,
                 context_length: int = 256000, continuity_at: float = 0.5,
                 cooldown_seconds: int = 600):
        self.budget = budget
        self.brain = brain
        self.context_length = context_length
        self.continuity_at = continuity_at
        self.cooldown_seconds = cooldown_seconds
        self._last_fired: dict = {}  # session -> epoch，防重复触发刷屏

    # ---- 判断 ----
    def should_continue(self, session: str) -> bool:
        # 冷却期内不重复触发（上次 execute 后 cooldown 秒内静默）
        if time.time() - self._last_fired.get(str(session), 0.0) \
                < self.cooldown_seconds:
            return False
        try:
            return self.budget.over_compressed(session)
        except Exception:
            return False

    def status(self, session: str) -> dict:
        if hasattr(self.budget, "continuity_status"):
            try:
                return self.budget.continuity_status(session)
            except Exception:
                pass
        return {"session": session,
                "needs_continuity": self.should_continue(session)}

    # ---- 锚点生成（只取自真实事实，绝不编造）----
    def make_anchors(self, meta: Optional[dict] = None) -> List[str]:
        """把 meta（真实事实）拼成锚点句子。只收确认的字段，无则跳过。"""
        meta = meta or {}
        lines: List[str] = [f"[会话延续锚点 for {meta.get('session','')}]"]
        pick = [("goal", "当前目标"), ("project", "项目"),
                ("decisions", "已定决定"), ("files", "关键文件"),
                ("blockers", "阻断项"), ("preferences", "用户偏好"),
                ("next", "下一步")]
        for key, label in pick:
            val = meta.get(key)
            if val is None:
                continue
            text = ", ".join(str(v) for v in val) if isinstance(val, list) else str(val)
            text = text.strip()
            if text:
                lines.append(f"{label}: {text}")
        # 至少要有内容才返回（避免空锚点污染向量库）
        return lines if len(lines) > 1 else []

    # ---- 执行：写向量记忆 + 返回后继 ----
    def execute(self, session: str, meta: Optional[dict] = None,
                brain=None) -> dict:
        """写精确锚点进向量记忆（brain.remember），返回后继会话 id + 说明。

        仅在两者具备时写：有 brain、锚点非空。never 编造。
        """
        brain = brain or self.brain
        anchors = self.make_anchors(dict(meta or {}, session=session))
        note = ""
        wrote = False
        if brain is not None and anchors:
            try:
                text = "\n".join(anchors)
                node = brain.remember(
                    text, tags=["continuity", "session", session],
                    importance=1.0, tier="recall")
                note = (f"已写入 {len(anchors)} 条精确锚点到向量记忆"
                        f"{' (node ' + node + ')' if isinstance(node, str) and node else ''}")
                wrote = True
            except Exception as e:
                note = f"锚点写入失败（不编造内容）: {type(e).__name__}"
        # 仅当锚点真正写入成功才进入冷却——失败不压制后续重试（防刷屏但不吞失效）
        if wrote:
            self._last_fired[str(session)] = time.time()
        succ = successor_id(session)
        return {
            "successor_session": succ,
            "anchors": anchors,
            "note": note,
            # 语义：execute 只在本该续接时被调用 → 已触发，应启动后继会话
            "needs_continuity": True,
            "anchors_written": wrote,
        }