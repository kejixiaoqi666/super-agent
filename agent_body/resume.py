"""断点续跑 Resume —— 未完成清单 + 恢复入口。

AgentLoop.run(task_id) 已支持从 next_pending_step() 继续（不重头）。
本模块补上「可续跑的未完成清单」+ 便捷恢复：
  - resume_list(): 列出 FAILED/CANCELED 的可续跑任务（未完成清单）
  - resume(loop, task_id, brain, ...): 把一个未完成任务重新推进到 DONE/FAILED
  - 供 CLI / TUI / Bot 的 /resume 与 /pending 命令复用
"""
from __future__ import annotations

from typing import Callable, Optional

from .loop.loop import AgentLoop
from .loop.task import TaskState, TaskStatus


def _is_resumable(t: TaskState) -> bool:
    """未完成且可续跑：FAILED(可重试) 或 CANCELED(被暂停)。DONE/执行中不算。"""
    return t.status in (TaskStatus.FAILED, TaskStatus.CANCELED)


def resume_list(loop: AgentLoop) -> list:
    """未完成清单：所有可续跑任务的摘要。"""
    out = []
    for t in loop.store.list():
        if _is_resumable(t):
            out.append(t.summary())
    return out


def resume_one(loop: AgentLoop, task_id: str, brain,
               planner: Optional[Callable] = None,
               verifier=None) -> dict:
    """把一个未完成任务重新推进到 DONE/FAILED。返回最终 summary。"""
    t = loop.get(task_id)
    if t is None:
        raise KeyError(f"task {task_id} not found")
    if not _is_resumable(t):
        raise ValueError(
            f"task {task_id} 状态为 {t.status.value}，不可续跑（需 FAILED/CANCELED）")
    # 把 CANCELED 拉回可执行态（直接 run 会从 EXECUTING 续）
    return loop.run(task_id, brain, planner=planner, verifier=verifier)


def resume_all(loop: AgentLoop, brain,
               planner: Optional[Callable] = None,
               verifier=None) -> list:
    """续跑全部未完成任务，返回各任务最终摘要。"""
    results = []
    for t in loop.store.list():
        if _is_resumable(t):
            try:
                results.append(resume_one(loop, t.task_id, brain,
                                          planner=planner, verifier=verifier))
            except Exception as exc:
                results.append({"task_id": t.task_id, "error": str(exc)})
    return results
