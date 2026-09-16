"""进度条 —— 长任务可视化（当前/总步数/每步状态）。

供 TUI / CLI / 通知使用：把 TaskState 渲染成进度行。
"""
from __future__ import annotations

from typing import List, Optional

from .task import TaskState


def render_progress(t: TaskState, width: int = 20) -> str:
    """渲染一行进度：▓▓▓░░░░ 3/5 步骤名称(状态)"""
    if not t.plan:
        return f"{t.status.value}  (未规划)"
    total = len(t.plan)
    done = sum(1 for s in t.steps if s.get("ok"))
    filled = int(round(width * done / total)) if total else 0
    bar = "▓" * filled + "░" * (width - filled)
    nxt = t.next_pending_step()
    current = (t.plan[nxt][:28] if nxt is not None and nxt < len(t.plan)
               else ("✓ 完成" if done == total else t.goal))
    state = {
        "pending": "⏳待开始", "planning": "🧭规划中", "executing": "🔄执行中",
        "verifying": "🔍验证中", "done": "✅完成", "failed": "❌失败",
        "canceled": "⏹已取消",
    }.get(t.status.value, t.status.value)
    return f"{bar} {done}/{total} [{state}] {current[:28]}"


def render_step_list(t: TaskState) -> List[str]:
    """列出每一步及其状态。"""
    status_by_idx = {s["index"]: ("✅" if s["ok"] else "❌")
                     for s in t.steps}
    out = []
    for i, step in enumerate(t.plan):
        mark = status_by_idx.get(i, "⏳")
        out.append(f"{mark} 步骤{i + 1}: {step[:40]}")
    return out


def summarize(t: Optional[TaskState]) -> str:
    """给通知/上报用的一句话摘要。"""
    if t is None:
        return "(任务不存在)"
    line = f"[{t.task_id[:8]}] {t.goal[:36]} → {t.status.value}"
    if t.failure:
        line += f" ({t.failure.get('category')})"
    return line
