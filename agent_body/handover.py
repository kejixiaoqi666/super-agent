"""跨会话交接 Handover —— 新会话自动恢复上下文（"/new 也不失忆"）。

分工：交接内容存**大脑记忆**（persistent，跨进程），身体负责生成/注入简报。
闭环：
  1. 会话结束 → 把当前状态写进大脑记忆（tags=["handover"], 高 importance）
  2. 新会话首轮 → 自动 recall 相关记忆（含上次交接）→ 生成简报注入上下文
  3. 于是新会话"知道"上次在哪、进行中什么、有什么技能，无需手动账本
全程可注入 brain（duck-type：recall/remember），便于测试与换内核。
"""
from __future__ import annotations

from typing import List

_HANDOVER_QUERY = "当前项目 进行中任务 待办 交接 handover 状态 下一步"


def write_handover(brain, summary: str, tags=None,
                   importance: float = 1.0) -> None:
    """把会话状态写进大脑记忆（跨进程持久，供下次召回）。"""
    if not summary or not str(summary).strip():
        return
    brain.remember(str(summary).strip(), tags=list(tags or ["handover"]),
                   importance=importance, tier="recall")


def build_briefing(brain, project_desc: str = "",
                   skills_store=None, k: int = 6) -> str:
    """生成新会话简报：项目上下文 + 相关记忆(含上次交接) + 已有技能。"""
    parts: List[str] = []
    if project_desc:
        parts.append(project_desc)
    try:
        hits = brain.recall(_HANDOVER_QUERY, k=k) or []
    except Exception:
        hits = []
    seen: List[str] = []
    for h in hits:
        content = (h.get("content") or "") if isinstance(h, dict) else str(h)
        if content and content not in seen:
            seen.append(content)
    if seen:
        parts.append("记忆线索:\n" + "\n".join(f"- {c[:150]}" for c in seen))
    if skills_store is not None:
        try:
            top = [s.name for s in skills_store.list()][:5]
        except Exception:
            top = []
        if top:
            parts.append("已有技能: " + ", ".join(top))
    return "\n\n".join(parts)
