"""内核默认注册。

把内置的 SuperBrainAdapter 注册进默认注册表；后续 agent 主体按名 build 即可。
不在此处实例化内核，仅注册类，避免包级副作用产生大脑实例。

用法：import agent_body 时自动调用 ensure_default_registered()。
"""
from .kernel.registry import default_registry
from .kernel.superbrain_adapter import SuperBrainAdapter


def ensure_default_registered() -> None:
    """确保默认内核已注册（幂等）。"""
    reg = default_registry()
    if not reg.has("superbrain"):
        reg.register("superbrain", SuperBrainAdapter)


ensure_default_registered()
