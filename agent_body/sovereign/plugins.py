"""开放式插件注册中心 —— AI 自由区的能力单元（阶段① 主权开放架构）。

随用随装、即装即卸、隔离：插件代码独立目录，卸载即净；
坏插件只影响自身调用，不波及 registry / 内核。
AI 在插件层可自由创建/修改/卸载；内核区写操作由 KernelReadOnlyGate 拒绝。
"""

from __future__ import annotations

import importlib.util
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


def _safe_name(name: str) -> str:
    """插件名只保留安全字符，杜绝路径穿越。"""
    safe = "".join(c for c in name if c.isalnum() or c in "-_")
    return safe or "plugin"


@dataclass
class Plugin:
    """一个能力插件。source 是代码文件(相对路径→内容)。"""
    name: str
    files: Dict[str, str] = field(default_factory=dict)
    entry: str = "main.py"
    category: str = "tool"        # tool | skill | autopilot_backend | widget
    version: str = "0.1.0"
    description: str = ""
    enabled: bool = True
    installed_at: float = field(default_factory=time.time)

    def manifest(self) -> dict:
        return {"name": self.name, "version": self.version,
                "category": self.category, "description": self.description,
                "entry": self.entry, "enabled": self.enabled,
                "installed_at": self.installed_at}


class PluginRegistry:
    """管理插件目录：安装/卸载/列出/动态加载执行。即装即卸即净。"""

    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir) / "plugins"
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, name: str) -> Path:
        return self.root / _safe_name(name)

    # ---- 安装 / 卸载 ----
    def install(self, plugin: Plugin) -> Path:
        """写入插件代码目录并注册。同名覆盖（即改即生效）。"""
        d = self._dir(plugin.name)
        if d.exists():
            # 覆盖安装：先清旧再写，防残留旧文件
            import shutil
            shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        for rel, content in plugin.files.items():
            # 防路径穿越：相对路径只允许安全子路径
            target = (d / rel).resolve()
            if not str(target).startswith(str(d.resolve())):
                raise ValueError(f"插件文件路径越界: {rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        (d / "manifest.json").write_text(
            __import__("json").dumps(plugin.manifest(), ensure_ascii=False, indent=2),
            encoding="utf-8")
        return d

    def uninstall(self, name: str) -> bool:
        """删除插件目录（即卸即净）。返回是否真的删了。"""
        d = self._dir(name)
        if d.exists():
            import shutil
            shutil.rmtree(d, ignore_errors=True)
            return True
        return False

    # ---- 查询 ----
    def list(self) -> List[str]:
        return [p.name for p in self.root.iterdir() if p.is_dir()]

    def get(self, name: str) -> Optional[dict]:
        d = self._dir(name)
        mf = d / "manifest.json"
        if not mf.exists():
            return None
        return __import__("json").loads(mf.read_text(encoding="utf-8"))

    # ---- 执行（隔离：动态加载，坏插件只影响自身） ----
    def run(self, name: str, fn: str = "main", *args, **kwargs) -> dict:
        """动态加载插件 entry 模块并调用 fn。每次重新加载（即改即生效）。
        异常被捕获返回 {ok:False, error}，不污染 registry / 内核。
        """
        d = self._dir(name)
        mf = d / "manifest.json"
        if not d.exists():
            return {"ok": False, "error": f"插件 {name} 未安装"}
        try:
            import json as _json
            entry_name = "main.py"
            if mf.exists():
                entry_name = _json.loads(
                    mf.read_text(encoding="utf-8")).get("entry", "main.py")
            entry_path = d / entry_name
            spec = importlib.util.spec_from_file_location(
                f"plugin_{_safe_name(name)}", entry_path)
            if spec is None or spec.loader is None:
                return {"ok": False, "error": f"插件 {name} 加载失败"}
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            call = getattr(mod, fn, None)
            if not callable(call):
                return {"ok": False, "error": f"插件 {name} 无函数 {fn}"}
            return {"ok": True, "result": call(*args, **kwargs)}
        except Exception as e:
            return {"ok": False,
                    "error": f"{type(e).__name__}: {e}"}
