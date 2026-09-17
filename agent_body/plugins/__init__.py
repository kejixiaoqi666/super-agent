"""插件系统 Plugins —— 发现 / 校验 / 加载 / 生命周期。

成熟 agent（Hermes/Claude Code）都有插件体系。本模块实现：
  - Plugin: 一个插件 = plugin.json 清单 + 可选 Python 实现(plugin.py)
  - PluginRegistry: 从若干根目录发现插件，校验清单，安全加载
  - 生命周期: load(导入模块) → enable(启用回调) → disable(停用)
  - 能力声明: capabilities 列表(工具/钩子/数据源)

清单 plugin.json:
  {"id":"my-plugin","version":"1.0.0","api_version":1,
   "capabilities":["tool:echo","hook:after_chat"],
   "entry":"plugin.py"}     # entry 为可选，缺省无代码(仅清单/数据源插件)

安全：插件在独立命名空间加载，显式 enable 才执行副作用；manifest-only
（无 entry）插件只做声明，不加载任何代码。
"""
from __future__ import annotations

import importlib.util
import json
import hashlib
from pathlib import Path
from typing import Any, Dict, List

# api_version 契约号：与 agent 要求的插件 API 版本匹配才接受
PLUGIN_API_VERSION = 1

_REQUIRED_MANIFEST = {"id", "version", "api_version", "capabilities"}


class PluginError(ValueError):
    pass


class Plugin:
    def __init__(self, manifest: Dict, base: Path):
        self.manifest = manifest
        self.base = base
        self.id = manifest["id"]
        self.version = manifest.get("version", "")
        self.api_version = manifest.get("api_version")
        self.capabilities = list(manifest.get("capabilities", []) or [])
        self.entry = manifest.get("entry")
        self.enabled = False
        self.module = None
        self.hash = ""

    def compute_hash(self) -> str:
        """对清单 + 入口代码做 sha256，用于溯源/变更检测。"""
        h = hashlib.sha256()
        h.update(Path(self.manifest["_manifest_path"]).read_bytes() if "_manifest_path" in self.manifest else b"")
        if self.entry:
            ep = self.base / self.entry
            if ep.exists():
                h.update(ep.read_bytes())
        self.hash = h.hexdigest()[:16]
        return self.hash

    def load(self) -> Any:
        """加载入口代码模块（无 entry 则返回 None）。显式 enable 才启副作用。"""
        if not self.entry:
            return None
        base = self.base.resolve()
        entry_path = (base / self.entry).resolve()
        # 防路径穿越：entry 必须落在插件目录内，禁止 ../ 逃逸加载外部代码
        if not entry_path.is_relative_to(base):
            raise PluginError(f"插件 {self.id}: 入口越界(路径穿越) {self.entry}")
        if not entry_path.exists():
            raise PluginError(f"插件 {self.id}: 入口 {self.entry} 不存在")
        spec = importlib.util.spec_from_file_location(
            f"agent_plugin_{self.id}", entry_path)
        if spec is None or spec.loader is None:
            raise PluginError(f"插件 {self.id}: 无法加载入口 {self.entry}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.module = module
        return module

    def enable(self) -> None:
        """启用插件：加载代码并调用 setup(agent)（若有）。"""
        if self.enabled:
            return
        module = self.load()
        if module is not None and hasattr(module, "setup"):
            module.setup()
        self.enabled = True

    def disable(self) -> None:
        """停用插件：调用 teardown()（若有），卸载模块。"""
        if not self.enabled:
            return
        if self.module is not None and hasattr(self.module, "teardown"):
            try:
                self.module.teardown()
            except Exception:
                pass
        self.enabled = False
        self.module = None

    def has_capability(self, cap: str) -> bool:
        return cap in self.capabilities

    def summary(self) -> dict:
        return {"id": self.id, "version": self.version,
                "api_version": self.api_version,
                "capabilities": self.capabilities,
                "enabled": self.enabled, "entry": self.entry,
                "hash": self.hash}


class PluginRegistry:
    def __init__(self, roots: List[str | Path]):
        self.roots = [Path(r) for r in roots]
        self._plugins: Dict[str, Plugin] = {}

    def scan(self) -> List[Plugin]:
        """从所有根目录发现插件，返回清单。重复/非法清单抛错。"""
        found: Dict[str, Plugin] = {}
        for root in self.roots:
            if not root.exists():
                continue
            for path in sorted(root.rglob("plugin.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception as exc:
                    raise PluginError(f"非法清单 {path}: {exc}")
                missing = _REQUIRED_MANIFEST - set(data)
                if missing:
                    raise PluginError(f"清单 {path} 缺字段: {sorted(missing)}")
                if data["id"] in found:
                    raise PluginError(f"插件 id 重复: {data['id']}")
                if not isinstance(data["capabilities"], list):
                    raise PluginError(f"清单 {path} capabilities 必须是数组")
                data["_manifest_path"] = str(path)
                p = Plugin(data, path.parent)
                p.compute_hash()
                found[data["id"]] = p
        self._plugins = found
        return list(found.values())

    def get(self, plugin_id: str) -> Plugin:
        return self._plugins[plugin_id]

    def names(self) -> List[str]:
        return sorted(self._plugins)

    def enable(self, plugin_id: str) -> Plugin:
        p = self.get(plugin_id)
        p.enable()
        return p

    def enable_capability(self, cap: str) -> List[Plugin]:
        """启用所有声明了某能力的插件，返回启用的列表。"""
        enabled = []
        for p in self._plugins.values():
            if p.has_capability(cap):
                p.enable()
                enabled.append(p)
        return enabled

    def all_summaries(self) -> List[dict]:
        return [p.summary() for p in self._plugins.values()]
