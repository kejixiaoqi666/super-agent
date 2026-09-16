"""Brain Port —— agent 与大脑之间的稳定契约（孔位）。

设计定位（xray 内核 + 面板）：
  大脑(superbrain-2.0) = 内核/插件，独立仓库独立更新。
  agent_body = 身体/面板，通过本契约调用大脑，**不直接 import 内核内部**。

BrainPort 是 ABC 抽象接口：任何实现它的内核（SuperBrainAdapter 或第三方）都必须
实现全部契约方法，否则无法实例化 → registry 在注册/构建时可靠拦截不完整实现。

效果：
  - 大脑升级 / 换内核：只要实现本契约，agent 主体零改动。
  - 契约版本探测：大脑接口若变化，registry 能检测并提示，而非静默崩。
  - 不碰外部服务：大脑作为库被身体调用，不启动外部进程、不污染外部安装。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional


@dataclass
class BrainTool:
    """身体注入大脑的工具描述（注册表用）。"""
    name: str
    description: str
    properties: Dict
    handler: Callable
    side_effects: str = "read"  # none | read | write | exec


class BrainPort(ABC):
    """大脑能力面契约（纯接口，由具体 Adapter 实现）。

    agent 只依赖此契约；契约稳定则 agent 不因大脑内部演进而改动。
    """

    # ---- 会话与生命周期 ----
    @abstractmethod
    def chat(self, message: str, person_id: Optional[str] = None) -> str: ...
    @abstractmethod
    def remember(self, content: str, scope: str = "user", tier: str = "recall", **kw) -> str: ...
    @abstractmethod
    def recall(self, query: str, k: int = 5) -> List[Dict]: ...
    @abstractmethod
    def save(self) -> str: ...
    @abstractmethod
    def load(self) -> bool: ...
    @abstractmethod
    def close(self) -> None: ...

    # ---- 认知 / 人格 / 表达 ----
    @abstractmethod
    def state(self) -> dict: ...
    @abstractmethod
    def personality(self) -> dict: ...
    @abstractmethod
    def set_personality(self, dimension: str, value: float) -> bool: ...
    @abstractmethod
    def personality_mode(self) -> str: ...
    @abstractmethod
    def set_personality_mode(self, mode: str) -> bool: ...
    @abstractmethod
    def apply_style(self, style: str) -> List[str]: ...
    @abstractmethod
    def style_text(self) -> str: ...
    @abstractmethod
    def humanize(self, text: str, person_id: Optional[str] = None) -> str: ...
    @abstractmethod
    def set_user_style(self, person_id: str, style: str) -> bool: ...
    @abstractmethod
    def user_style(self, person_id: str) -> str: ...

    # ---- 自主推进 ----
    @abstractmethod
    def tick(self) -> dict: ...
    @abstractmethod
    def generate_thoughts(self) -> List[Dict]: ...
    @abstractmethod
    def drain_thoughts(self) -> List[Dict]: ...
    @abstractmethod
    def generate_goals(self) -> List[Dict]: ...
    @abstractmethod
    def adopt_goals(self) -> List[Dict]: ...
    @abstractmethod
    def autonomous_goals(self) -> List[Dict]: ...

    # ---- 记忆维护 ----
    @abstractmethod
    def memory_count(self) -> int: ...
    @abstractmethod
    def index_concepts(self, limit: int = 20) -> int: ...
    @abstractmethod
    def deduplicate(self, threshold: float = 0.85) -> int: ...
    @abstractmethod
    def orientations(self) -> dict: ...
    @abstractmethod
    def user_profile(self, person_id: str) -> Optional[Dict]: ...

    # ---- 孔位注入：身体把工具/权限挂进大脑 ----
    @abstractmethod
    def attach_tools(self, tools: List[BrainTool]) -> None: ...
    @abstractmethod
    def set_permissions(self, policy) -> None: ...

    # ---- 版本 / 健康 ----
    @abstractmethod
    def ready(self) -> bool: ...
    @abstractmethod
    def version(self) -> str: ...
    @abstractmethod
    def health(self) -> dict: ...


# 契约版本：大脑能力面接口的稳定标识。
# 大脑升级若变更契约 → 递增此号；registry 据此提示 agent 需要升级。
BRAIN_PORT_VERSION = 1
