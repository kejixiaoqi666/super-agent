"""Stable contracts shared by channels, routers, plugins and agentd."""
from dataclasses import dataclass, field
from enum import Enum
import time, uuid


class TaskStatus(str, Enum):
    DRAFT="DRAFT"; PLANNED="PLANNED"; DISPATCHED="DISPATCHED"; RUNNING="RUNNING"
    OBSERVING="OBSERVING"; VERIFIED="VERIFIED"; COMMITTED="COMMITTED"
    PAUSED="PAUSED"; FAILED="FAILED"; ROLLING_BACK="ROLLING_BACK"; ROLLED_BACK="ROLLED_BACK"


@dataclass(frozen=True)
class Principal:
    id: str
    roles: tuple[str, ...] = ("operator",)
    source: str = "local"


@dataclass(frozen=True)
class CapabilityLease:
    id: str
    principal_id: str
    tools: tuple[str, ...]
    targets: tuple[str, ...] = ()
    secret_scopes: tuple[str, ...] = ()
    expires_at: float = 0
    max_uses: int = 1
    uses: int = 0

    @classmethod
    def issue(cls, principal_id, tools, targets=(), secret_scopes=(), ttl=300, max_uses=1):
        if ttl <= 0 or max_uses < 1: raise ValueError("invalid lease")
        return cls(uuid.uuid4().hex, principal_id, tuple(tools), tuple(targets), tuple(secret_scopes), time.time()+ttl, max_uses)

    def consume(self, tool, target=None):
        if time.time() >= self.expires_at or self.uses >= self.max_uses or tool not in self.tools:
            return False
        if target and self.targets and target not in self.targets: return False
        return True


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict
    lease_id: str
    idempotency_key: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class Task:
    id: str
    principal: Principal
    goal: str
    status: TaskStatus = TaskStatus.DRAFT
    calls: list[ToolCall] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)

