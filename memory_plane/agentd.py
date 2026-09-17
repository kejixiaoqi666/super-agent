"""In-process control-plane kernel; executors are injected and never model-owned."""
from .contracts import TaskStatus, Task
import time, uuid


ALLOWED = {
    TaskStatus.DRAFT: {TaskStatus.PLANNED}, TaskStatus.PLANNED: {TaskStatus.DISPATCHED},
    TaskStatus.DISPATCHED: {TaskStatus.RUNNING}, TaskStatus.RUNNING: {TaskStatus.OBSERVING, TaskStatus.FAILED},
    TaskStatus.OBSERVING: {TaskStatus.VERIFIED, TaskStatus.ROLLING_BACK, TaskStatus.FAILED},
    TaskStatus.VERIFIED: {TaskStatus.COMMITTED, TaskStatus.ROLLING_BACK},
    TaskStatus.ROLLING_BACK: {TaskStatus.ROLLED_BACK, TaskStatus.FAILED},
}


class Agentd:
    def __init__(self, executors=None): self.tasks = {}; self.executors = executors or {}; self.receipts = {}; self.lease_usage = {}
    def create_task(self, principal, goal):
        task = Task(uuid.uuid4().hex, principal, goal); self.tasks[task.id] = task; return task
    def transition(self, task_id, status):
        task = self.tasks[task_id]
        if status not in ALLOWED.get(task.status, set()): raise ValueError(f"invalid transition {task.status}->{status}")
        task.status = status; task.events.append({"type":"status","status":status.value,"at":time.time()}); return task
    def dispatch(self, task_id, call, lease):
        task = self.tasks[task_id]
        if call.idempotency_key in self.receipts: return self.receipts[call.idempotency_key]
        target = call.args.get("target")
        uses = self.lease_usage.get(lease.id, 0)
        if uses >= lease.max_uses or not lease.consume(call.name, target):
            raise PermissionError("capability lease denied or expired")
        self.lease_usage[lease.id] = uses + 1
        if task.status == TaskStatus.DRAFT: self.transition(task_id, TaskStatus.PLANNED)
        if task.status == TaskStatus.PLANNED: self.transition(task_id, TaskStatus.DISPATCHED)
        if task.status == TaskStatus.DISPATCHED: self.transition(task_id, TaskStatus.RUNNING)
        task.calls.append(call)
        executor = self.executors.get(call.name)
        if executor is None: raise RuntimeError(f"no executor for {call.name}")
        result = executor(call.args); receipt = {"task_id":task_id,"idempotency_key":call.idempotency_key,"result":result}
        self.receipts[call.idempotency_key] = receipt; return receipt
