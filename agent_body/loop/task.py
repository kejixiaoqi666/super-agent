"""任务状态机 + 持久化（断点续跑基础）。

TaskState 是任务账本：记录 id/目标/状态/步骤/证据，落盘 JSON（0600）。
断点续跑 = 从 task_id 读回 TaskState，从「未完成的下一步」继续，不重头来。
"""
from __future__ import annotations

import enum
import json
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional


class TaskStatus(enum.Enum):
    PENDING = "pending"        # 已创建，未开始
    PLANNING = "planning"      # 正在规划
    EXECUTING = "executing"    # 执行中
    VERIFYING = "verifying"    # 验证中
    DONE = "done"              # 成功完成
    FAILED = "failed"          # 失败（已分类）
    CANCELED = "canceled"      # 被取消/暂停


# 合法迁移：每个状态能到哪些状态
_TRANSITIONS: Dict[TaskStatus, set] = {
    TaskStatus.PENDING: {TaskStatus.PLANNING, TaskStatus.EXECUTING,
                         TaskStatus.FAILED, TaskStatus.CANCELED},
    TaskStatus.PLANNING: {TaskStatus.EXECUTING, TaskStatus.FAILED, TaskStatus.CANCELED},
    TaskStatus.EXECUTING: {TaskStatus.VERIFYING, TaskStatus.DONE,
                            TaskStatus.FAILED, TaskStatus.CANCELED},
    TaskStatus.VERIFYING: {TaskStatus.DONE, TaskStatus.EXECUTING, TaskStatus.FAILED,
                           TaskStatus.CANCELED},
    # 失败/取消后可续跑：允许拉回 EXECUTING（断点续跑）
    TaskStatus.DONE: set(),
    TaskStatus.FAILED: {TaskStatus.EXECUTING},
    TaskStatus.CANCELED: {TaskStatus.EXECUTING},
}


class TaskState:
    """任务账本（可序列化、可持久化）。"""

    def __init__(self, goal: str, task_id: Optional[str] = None,
                 plan: Optional[List[str]] = None,
                 owner: str = "local", chain_id: Optional[str] = None):
        self.task_id = task_id or uuid.uuid4().hex[:12]
        self.goal = goal
        self.owner = owner
        self.chain_id = chain_id
        self.status = TaskStatus.PENDING
        self.plan: List[str] = plan or []
        self.steps: List[Dict] = []      # 每步: {index, action, result, ok}
        self.evidence: List[Dict] = []   # 验收证据
        self.failure: Optional[Dict] = None   # 失败分类
        self.selfchecks: List[Dict] = []      # 自查记录: {phase, findings, healed, ok}
        self.error: Optional[str] = None
        self.created_at = time.time()
        self.updated_at = self.created_at
        self._cursor = 0  # 续跑游标：下一条未完成的 plan 步骤

    # ---- 状态机 ----
    def transition(self, to: TaskStatus) -> None:
        if to not in _TRANSITIONS[self.status]:
            raise ValueError(
                f"非法状态迁移: {self.status.value} → {to.value}")
        self.status = to
        self.updated_at = time.time()

    def can(self, to: TaskStatus) -> bool:
        return to in _TRANSITIONS[self.status]

    # ---- 步骤 ----
    def add_plan(self, steps: List[str]) -> None:
        """记录规划出的步骤（plan）。"""
        self.plan = list(steps)
        self.updated_at = time.time()

    def next_pending_step(self) -> Optional[int]:
        """返回下一个未执行完的 plan 下标（断点续跑用）。"""
        done = {s.get("index") for s in self.steps if s.get("ok")}
        for i in range(len(self.plan)):
            if i not in done:
                return i
        return None

    def record_step(self, index: int, action: str, result: str, ok: bool) -> None:
        self.steps.append({"index": index, "action": action,
                           "result": result, "ok": ok, "at": time.time()})
        self.updated_at = time.time()

    def add_evidence(self, claim: str, verified: bool, detail: str = "") -> None:
        self.evidence.append({"claim": claim, "verified": verified,
                              "detail": detail, "at": time.time()})
        self.updated_at = time.time()

    def set_failure(self, category: str, reason: str) -> None:
        self.failure = {"category": category, "reason": reason,
                        "at": time.time()}
        self.error = f"[{category}] {reason}"
        self.updated_at = time.time()

    def record_selfcheck(self, report: dict) -> None:
        self.selfchecks.append(dict(report))
        self.updated_at = time.time()

    # ---- 序列化 ----
    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "goal": self.goal, "owner": self.owner,
            "chain_id": self.chain_id, "status": self.status.value,
            "plan": self.plan, "steps": self.steps,
            "evidence": self.evidence, "selfchecks": self.selfchecks,
            "failure": self.failure, "error": self.error,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TaskState":
        t = cls(d["goal"], task_id=d["task_id"], plan=d.get("plan"),
                owner=d.get("owner", "local"), chain_id=d.get("chain_id"))
        t.status = TaskStatus(d["status"])
        t.steps = d.get("steps", [])
        t.evidence = d.get("evidence", [])
        t.selfchecks = d.get("selfchecks", [])
        t.failure = d.get("failure")
        t.error = d.get("error")
        t.created_at = d.get("created_at", time.time())
        t.updated_at = d.get("updated_at", t.created_at)
        return t

    def summary(self) -> dict:
        """人可读的进度摘要（/status 用）。"""
        return {
            "task_id": self.task_id, "status": self.status.value,
            "goal": self.goal, "steps_done": len(self.steps),
            "plan_len": len(self.plan),
            "next_step": self.next_pending_step(),
            "evidence_ok": sum(1 for e in self.evidence if e["verified"]),
            "evidence_total": len(self.evidence),
            "failure": self.failure,
        }


class TaskStore:
    """任务账本持久化：data_dir/tasks/<task_id>.json（0600）。"""

    def __init__(self, data_dir: str | Path):
        self.dir = Path(data_dir) / "tasks"
        self.dir.mkdir(parents=True, exist_ok=True)

    def save(self, task: TaskState) -> None:
        path = self.dir / f"{task.task_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(task.to_dict(), ensure_ascii=False),
                       encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)

    def load(self, task_id: str) -> Optional[TaskState]:
        path = self.dir / f"{task_id}.json"
        if not path.exists():
            return None
        return TaskState.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list(self) -> List[TaskState]:
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                out.append(self.load(p.stem))
            except Exception:
                continue
        return out

    def delete(self, task_id: str) -> bool:
        path = self.dir / f"{task_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False
