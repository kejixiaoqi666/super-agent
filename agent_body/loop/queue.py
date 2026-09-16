"""连贯任务队列 —— 依赖有序串行 + 未完成清单。

任务 1→2→3 有前后依赖时：任务1 未自查通过 + 未真正成功，绝不进入任务2。
任一环失败 → 该环及后续全部进"未完成清单"，可 /resume 续跑。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .task import TaskState, TaskStore


@dataclass
class QueueItem:
    """队列里的一项：指向一个任务。"""
    chain_id: str
    task_id: str
    goal: str

    def to_dict(self) -> dict:
        return {"chain_id": self.chain_id, "task_id": self.task_id,
                "goal": self.goal}

    @classmethod
    def from_dict(cls, d: dict) -> "QueueItem":
        return cls(d["chain_id"], d["task_id"], d["goal"])


class TaskQueue:
    """连贯任务队列（串行 + 依赖）。持久化到 data_dir/queue/卷（0600）。"""

    def __init__(self, data_dir: str | Path, store: TaskStore):
        self.store = store
        self.dir = Path(data_dir) / "queue"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._queue: List[QueueItem] = self._load()
        self._chains: Dict[str, str] = self._load_chains()  # chain_id -> task_id 当前游标

    # ---- 队列数据（持久化）----
    def _load(self) -> List[QueueItem]:
        p = self.dir / "pending.json"
        if not p.exists():
            return []
        try:
            import json
            return [QueueItem.from_dict(x) for x in
                    json.loads(p.read_text(encoding="utf-8")).get("items", [])]
        except Exception:
            return []

    def _load_chains(self) -> Dict[str, str]:
        p = self.dir / "chains.json"
        if not p.exists():
            return {}
        try:
            import json
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self) -> None:
        import json
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / "pending.tmp"
        tmp.write_text(json.dumps(
            {"items": [i.to_dict() for i in self._queue], "at": time.time()},
            ensure_ascii=False), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.dir / "pending.json")
        ctmp = self.dir / "chains.tmp"
        ctmp.write_text(json.dumps(self._chains, ensure_ascii=False),
                        encoding="utf-8")
        ctmp.chmod(0o600)
        ctmp.replace(self.dir / "chains.json")

    # ---- 队列操作 ----
    def enqueue(self, chain_id: str, tasks: List[str]) -> None:
        """把一串连续任务加入队列（tasks 是 goals 列表，按序串行）。"""
        for goal in tasks:
            t = TaskState(goal=goal)  # 先建任务
            self.store.save(t)
            self._queue.append(QueueItem(chain_id, t.task_id, goal))
            if chain_id not in self._chains:
                self._chains[chain_id] = t.task_id
        self._save()

    def next(self) -> Optional[QueueItem]:
        """取下一个该跑的任务（串行：链内当前游标的第一个未完成项）。"""
        if not self._queue:
            return None
        return self._queue[0]

    def current_task(self, chain_id: str) -> Optional[str]:
        """当前链正在执行/应执行的任务 id。"""
        return self._chains.get(chain_id)

    def _dequeue_done(self, task_id: str) -> None:
        """从队列移除已完成的项。"""
        self._queue = [i for i in self._queue if i.task_id != task_id]
        # 若该链还有后续项，推进游标
        for chain_id in list(self._chains.keys()):
            remaining = [i for i in self._queue if i.chain_id == chain_id]
            if not remaining:
                del self._chains[chain_id]
            else:
                self._chains[chain_id] = remaining[0].task_id
        self._save()

    def on_task_done(self, task_id: str) -> None:
        """任务 DONE 后：出队，推进下一环。"""
        self._dequeue_done(task_id)

    def on_task_failed(self, task_id: str) -> List[QueueItem]:
        """任务 FAILED 后：该环及其后同链任务全部进"未完成清单"。"""
        failed = [i for i in self._queue if i.task_id == task_id]
        doomed = failed
        if failed:
            chain = failed[0].chain_id
            doomed = [i for i in self._queue
                      if i.chain_id == chain]
        self._queue = [i for i in self._queue
                       if i.task_id not in {d.task_id for d in doomed}]
        for cd in {d.chain_id for d in doomed}:
            self._chains.pop(cd, None)
        self._save()
        return doomed  # 返回受影响的任务（供上报"未完成清单"）

    def pending_count(self) -> int:
        return len(self._queue)

    def list_pending(self) -> List[QueueItem]:
        return list(self._queue)