"""agent_body.kernel —— 大脑孔位层。

agent 与大脑(superbrain-2.0)之间通过稳定契约 BrainPort 交互，
大脑是独立更新的内核/插件；agent 主体依赖契约而非内核内部。

用法：
    from agent_body.runtime import Body
    body = Body(...)          # 构造时已把 superbrain 内核注册进默认注册表
    port = default_registry().build("superbrain", data_dir=..., session=...)
"""
from .port import BrainPort, BrainTool, BRAIN_PORT_VERSION
from .registry import BrainRegistry, default_registry
from .superbrain_adapter import SuperBrainAdapter

__all__ = [
    "BrainPort", "BrainTool", "BRAIN_PORT_VERSION",
    "BrainRegistry", "default_registry",
    "SuperBrainAdapter",
]