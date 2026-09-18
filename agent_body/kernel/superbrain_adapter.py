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

# 内核符号延迟绑定：模块导入时内核可能未装/未进 sys.path，故不在此硬 import。
# 探测到内核后由 _ensure_kernel_symbols() 动态绑定到模块命名空间，成功前一切为 None。
SuperBrain = None
AgentConfig = None
MemoryStore = None
Tool = None
ToolRegistry = None


def _ensure_kernel_symbols():
    """导入 superbrain2 并把需要的符号绑定到模块命名空间。

    返回 None 表示成功；返回异常表示内核不可用（health() 如实报告）。
    """
    global SuperBrain, AgentConfig, MemoryStore, Tool, ToolRegistry, _KERNEL_IMPORT_ERROR
    if SuperBrain is not None:
        return None  # 已绑定
    try:
        from superbrain2 import SuperBrain
        from superbrain2.core.agent import AgentConfig
        from superbrain2.core.memory.store import MemoryStore
        from superbrain2.core.tools import Tool, ToolRegistry
        _KERNEL_IMPORT_ERROR = None
        return None
    except Exception as exc:
        _KERNEL_IMPORT_ERROR = exc
        return exc


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
        # 未显式指定内核路径时自动探测并插入 sys.path，再尝试加载内核。
        # （模块顶部 import 可能因内核缺装已失败；此处探测后重试，避免"测试能过/真跑崩"）。
        kp = Path(kernel_path).resolve() if kernel_path is not None \
            else self._find_kernel()
        if kp is not None:
            import sys
            if str(kp) not in sys.path:
                sys.path.insert(0, str(kp))
        err = self._ensure_kernel_symbols()
        if err is not None:
            self._errors.append(err)
            self._brain = None
            return
        self._brain = self._build(llm, enable_learning)

    @staticmethod
    def _ensure_kernel_symbols():
        """确保内核符号已绑定；成功返回 None，失败返回异常。"""
        return _ensure_kernel_symbols()

    # ---- 装配 ----
    def _build(self, llm, enable_learning):
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

    @staticmethod
    def _find_kernel():
        """自动探测 superbrain-2.0 内核 Python 目录（未显式指定 kernel_path 时兜底）。

        常见布局：<repo>/superbrain-2.0/python 或环境变量 SUPERBRAIN_KERNEL_PATH。
        找不到返回 None（health() 会如实报告，不硬崩）。
        """
        candidates = []
        env = __import__("os").environ.get("SUPERBRAIN_KERNEL_PATH")
        if env:
            candidates.append(Path(env))
        # 相对本文件向上找：agent_body/kernel/ -> 项目根/superbrain-2.0/python
        here = Path(__file__).resolve()
        candidates.append(here.parents[2] / "superbrain-2.0" / "python")
        candidates.append(here.parents[3] / "superbrain-2.0" / "python")
        for c in candidates:
            p = Path(c).resolve()
            if (p / "superbrain2").exists():
                return p
        return None

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

    def chat(self, message, person_id=None, images=None):
        return self._require_brain().chat(
            message, person_id=person_id or self._session, images=images)

    def usage(self) -> dict:
        """当轮真实 token 用量（内核向量压缩后喂给模型的输入）——供自动续接判断。"""
        try:
            return self._require_brain().usage()
        except Exception:
            return {"prompt_tokens": 0, "completion_tokens": 0,
                    "total_tokens": 0, "calls": 0}

    def remember(self, content, scope="user", tier="recall", **kw):
        return self._brain.remember(content, scope=scope, tier=tier, **kw)

    def recall(self, query, k=5):
        return self._brain.recall(query, k=k)

    def record_experience(self, task, context="", outcome="", lesson=""):
        return self._brain.record_experience(task, context=context,
                                             outcome=outcome, lesson=lesson)

    def distill_skill(self, name, procedure, success=True):
        return self._brain.distill_skill(name, procedure, success=success)

    def best_skills(self, k=5):
        return self._brain.best_skills(k=k)

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