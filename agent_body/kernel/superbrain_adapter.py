"""superbrain2 内核的 BrainPort 适配器。

将 superbrain-2.0 门面映射到稳定契约 BrainPort。agent 依赖契约而非本实现；
本适配器是「大脑 → 孔位」的桥，大脑升级仅需保证此桥可映射，agent 主体零改动。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List, Optional

from .port import BrainPort, BrainTool

_KERNEL_IMPORT_ERROR = None
try:
    from superbrain2 import SuperBrain
    from superbrain2.core.agent import AgentConfig
    from superbrain2.core.memory.store import MemoryStore
    from superbrain2.core.tools import Tool, ToolRegistry
except Exception as _exc:  # 内核缺装时保留错误，health() 如实报告
    _KERNEL_IMPORT_ERROR = _exc


class SuperBrainAdapter(BrainPort):
    """把 superbrain2 门面适配成 BrainPort 契约。"""

    def __init__(self, data_dir, session, llm=None,
                 kernel_path: Optional[Path] = None,
                 enable_learning: bool = False):
        self._errors = []
        self._closed = False
        self._session = session
        self._data_dir = Path(data_dir).resolve()
        self._data_dir.mkdir(parents=True, exist_ok=True)
        if _KERNEL_IMPORT_ERROR is not None:
            self._errors.append(_KERNEL_IMPORT_ERROR)
            self._brain = None
            return
        self._brain = self._build(llm, kernel_path, enable_learning)

    # ---- 装配 ----
    def _build(self, llm, kernel_path, enable_learning):
        if kernel_path is not None:
            import sys
            sys.path.insert(0, str(Path(kernel_path).resolve()))
        key = hashlib.sha256(self._session.encode()).hexdigest()
        store = MemoryStore(str(self._data_dir / (key + ".db")))
        try:
            brain = SuperBrain(
                llm=llm,
                store=store,
                config=AgentConfig(enable_learning=enable_learning))
            brain.load()
            return brain
        except Exception:
            store.close()
            raise

    # ---- BrainPort 实现：委托给 superbrain2 门面 ----
    def _require_brain(self):
        if self._brain is None:
            raise RuntimeError(
                "superbrain kernel not available: " +
                ("; ".join(str(e) for e in self._errors) or "kernel import failed"))
        return self._brain

    def _guard_optional(self, val, default=None):
        if self._brain is None:
            return default
        return val()

    def chat(self, message, person_id=None):
        return self._require_brain().chat(message, person_id=person_id or self._session)

    def remember(self, content, scope="user", tier="recall", **kw):
        return self._brain.remember(content, scope=scope, tier=tier, **kw)

    def recall(self, query, k=5):
        return self._brain.recall(query, k=k)

    def save(self):
        return self._brain.save()

    def load(self):
        return self._brain.load()

    def close(self):
        if self._closed:
            return
        try:
            if self._brain is not None:
                self._brain.save()
                self._brain.close()
        finally:
            self._closed = True

    def state(self):
        return self._require_brain().state()

    def personality(self):
        return self._require_brain().personality()

    def set_personality(self, dimension, value):
        return self._require_brain().set_personality(dimension, value)

    def personality_mode(self):
        return self._require_brain().personality_mode()

    def set_personality_mode(self, mode):
        return self._require_brain().set_personality_mode(mode)

    def apply_style(self, style):
        return self._require_brain().apply_style(style)

    def style_text(self):
        return self._require_brain().style_text()

    def humanize(self, text, person_id=None):
        return self._require_brain().humanize(text, person_id=person_id or self._session)

    def set_user_style(self, person_id, style):
        return self._require_brain().set_user_style(person_id, style)

    def user_style(self, person_id):
        return self._require_brain().user_style(person_id)

    def tick(self):
        return self._require_brain().tick()

    def generate_thoughts(self):
        return self._require_brain().generate_thoughts()

    def drain_thoughts(self):
        return self._require_brain().drain_thoughts()

    def generate_goals(self):
        return self._require_brain().generate_goals()

    def adopt_goals(self):
        return self._require_brain().adopt_goals()

    def autonomous_goals(self):
        return self._require_brain().autonomous_goals()

    def index_concepts(self, limit=20):
        return self._require_brain().index_concepts(limit)

    def deduplicate(self, threshold=0.85):
        return self._require_brain().deduplicate(threshold)

    def orientations(self):
        return self._require_brain().orientations()

    def user_profile(self, person_id):
        return self._require_brain().user_profile(person_id)

    # ---- 记忆维护 ----
    def memory_count(self):
        return self._require_brain().agent.store.count_nodes()

    # ---- 孔位注入 ----
    def attach_tools(self, tools: List[BrainTool]):
        brain = self._require_brain()
        registry = ToolRegistry()
        for t in tools:
            registry.register(Tool(
                t.name, t.description,
                {"type": "object", "properties": t.properties,
                 "required": list(t.properties)},
                t.handler, side_effects=t.side_effects))
        brain.agent.tools = registry

    def set_permissions(self, policy):
        self._require_brain().agent.permissions = policy

    # ---- 版本 / 健康 ----
    def ready(self) -> bool:
        return self._brain is not None

    def version(self):
        try:
            import superbrain2 as _sb
            return getattr(_sb, "__version__", "unknown")
        except Exception:
            return "unknown"

    def health(self):
        return {
            "kernel": "superbrain-2.0",
            "port": "BrainPort",
            "brain_loaded": self._brain is not None,
            "session": self._session,
            "errors": [str(e) for e in self._errors],
        }

    @property
    def brain(self):
        """底层内核实例（高级用途，默认不鼓励直接用）。"""
        return self._brain