"""Persistent task store for restart-safe Agentd execution."""
from __future__ import annotations
import json, sqlite3, time
from dataclasses import asdict
from pathlib import Path
from typing import Any
from .contracts import Task, TaskStatus, Principal, ToolCall

class TaskStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=15)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL, updated REAL NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS receipts (task_id TEXT NOT NULL, receipt_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(task_id, receipt_id))")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def put(self, task: Task) -> None:
        payload = asdict(task)
        payload["status"] = task.status.value
        self.db.execute("INSERT INTO tasks(id,payload,status,updated) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,status=excluded.status,updated=excluded.updated", (task.id, json.dumps(payload, ensure_ascii=False), task.status.value, time.time()))
        self.db.commit()

    def get(self, task_id: str) -> Task | None:
        row = self.db.execute("SELECT payload FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row: return None
        data = json.loads(row[0]); data["status"] = TaskStatus(data["status"])
        data["principal"] = Principal(**data["principal"])
        data["calls"] = [ToolCall(**c) for c in data.get("calls", [])]
        return Task(**data)

    def recoverable(self) -> list[Task]:
        rows = self.db.execute("SELECT payload FROM tasks WHERE status IN ('DRAFT','PLANNED','DISPATCHED','RUNNING','OBSERVING','PAUSED') ORDER BY updated").fetchall()
        out=[]
        for (raw,) in rows:
            data=json.loads(raw); data["status"] = TaskStatus(data["status"]); data["principal"] = Principal(**data["principal"]); data["calls"] = [ToolCall(**c) for c in data.get("calls", [])]; out.append(Task(**data))
        return out

    def save_receipt(self, task_id: str, receipt_id: str, payload: dict[str, Any]) -> bool:
        try:
            self.db.execute("INSERT INTO receipts(task_id,receipt_id,payload) VALUES(?,?,?)", (task_id, receipt_id, json.dumps(payload, ensure_ascii=False)))
            self.db.commit(); return True
        except sqlite3.IntegrityError:
            return False

    def get_receipt(self, task_id: str, receipt_id: str) -> dict[str, Any] | None:
        row=self.db.execute("SELECT payload FROM receipts WHERE task_id=? AND receipt_id=?", (task_id, receipt_id)).fetchone()
        return json.loads(row[0]) if row else None
