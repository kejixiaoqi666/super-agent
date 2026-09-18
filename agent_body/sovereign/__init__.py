"""主权开放架构 Sovereign —— 稳定内核 + 开放插件 + 升级治理。

阶段① 交付：开放插件注册中心(随装随卸即净) + 内核只读门(内核不可撼动, 写被拒走升级队列)。

用法：
  s = Sovereign(data_dir)
  s.install_plugin(Plugin(name="点赞助手", files={"main.py": "def main(): return 'ok'"}))
  s.list_plugins()
  s.check_write("agent_body/budget.py")   # -> {allowed:False, zone:"kernel", ...}
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from .gate import KernelReadOnlyGate
from .plugins import Plugin, PluginRegistry
from .self_mod import SelfMod


class Sovereign:
    """主权开放统一入口：插件自由区 + 内核只读门 + 自我修改。"""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.plugins = PluginRegistry(self.data_dir)
        self.gate = KernelReadOnlyGate(self.data_dir)
        self.self_mod = SelfMod(self.data_dir, gate=self.gate)
        # 自进化思考（观察/提案/批准/执行）
        from ..evolution import Evolution as _Evo
        self.evolution = _Evo(self.data_dir)

    # ---- 插件自由区 ----
    def install_plugin(self, plugin: Plugin) -> dict:
        self.plugins.install(plugin)
        return {"ok": True, "installed": plugin.name}

    def uninstall_plugin(self, name: str) -> dict:
        removed = self.plugins.uninstall(name)
        return {"ok": removed, "name": name}

    def list_plugins(self) -> List[str]:
        return self.plugins.list()

    def run_plugin(self, name: str, fn: str = "main", *a, **kw) -> dict:
        return self.plugins.run(name, fn, *a, **kw)

    # ---- 内核只读门 ----
    def check_write(self, path: str) -> dict:
        return self.gate.check_write(path)

    def zone_of(self, path: str) -> str:
        return self.gate.zone_of(path)

    def kernel_readonly_areas(self) -> List[str]:
        return self.gate.kernel_readonly_areas()
