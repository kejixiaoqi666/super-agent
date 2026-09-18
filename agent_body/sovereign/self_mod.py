"""自我修改 Self-Modification —— AI 支配智能体（阶段② 主权开放架构）。

AI 能改：插件层（增/改/卸插件、受管文件）——随改随生效。
AI 不能改：稳定内核（gate 拦截并提示走升级队列）。
每次改动留痕(trail) + 记录改前快照，可 restore_last_good 回滚 → "掌控一切但不乱改"。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from .gate import KernelReadOnlyGate
from .plugins import Plugin, PluginRegistry


class SelfMod:
    """插件层自我修改 + 留痕 + 可回滚。所有写经 gate 分区，内核只读。"""

    def __init__(self, data_dir: str | Path, gate: Optional[KernelReadOnlyGate] = None):
        self.data_dir = Path(data_dir)
        self.registry = PluginRegistry(self.data_dir)
        self.gate = gate or KernelReadOnlyGate(self.data_dir)
        self._trail_path = self.data_dir / "sovereign" / "trail.jsonl"
        self._trail_path.parent.mkdir(parents=True, exist_ok=True)
        self._trail: List[dict] = self._load_trail()

    # ---- 留痕 ----
    def _load_trail(self) -> List[dict]:
        if not self._trail_path.exists():
            return []
        out = []
        for line in self._trail_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
        return out

    def _append_trail(self, rec: dict) -> None:
        self._trail.append(rec)
        with self._trail_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _guard(self, path: str):
        """写守卫：内核区拒绝(带升级提示)，返回 None 表示放行。"""
        ok, res = self.gate.guard_write(path)
        return None if ok else res

    # ---- 插件读写快照 ----
    def _snapshot_plugin(self, name: str) -> Optional[dict]:
        d = self.registry._dir(name)
        if not d.exists():
            return None
        files = {}
        for p in d.iterdir():
            if p.is_file() and p.name != "manifest.json":
                files[p.name] = p.read_text(encoding="utf-8")
        return files or None

    # ---- 操作 ----
    def add_plugin(self, name: str, files: Dict[str, str], **kw) -> dict:
        d = self.registry._dir(name)
        g = self._guard(str(d))
        if g:
            return g
        self.registry.install(Plugin(name=name, files=files, **kw))
        self._append_trail({"ts": time.time(), "op": "add_plugin",
                            "name": name, "before": None})
        return {"ok": True, "op": "add_plugin", "name": name}

    def edit_plugin(self, name: str, files: Dict[str, str], **kw) -> dict:
        d = self.registry._dir(name)
        g = self._guard(str(d))
        if g:
            return g
        before = self._snapshot_plugin(name)
        self.registry.install(Plugin(name=name, files=files, **kw))
        self._append_trail({"ts": time.time(), "op": "edit_plugin",
                            "name": name, "before": before})
        return {"ok": True, "op": "edit_plugin", "name": name}

    def remove_plugin(self, name: str) -> dict:
        d = self.registry._dir(name)
        g = self._guard(str(d))
        if g:
            return g
        before = self._snapshot_plugin(name)
        self.registry.uninstall(name)
        self._append_trail({"ts": time.time(), "op": "remove_plugin",
                            "name": name, "before": before})
        return {"ok": True, "op": "remove_plugin", "name": name}

    def write_managed(self, path: str | Path, content: str) -> dict:
        """写一个受管文件（gate 分区后；内核区拒绝）。快照改前内容用于回滚。"""
        target = Path(path)
        g = self._guard(str(target))
        if g:
            return g
        before = target.read_text(encoding="utf-8") if target.exists() else None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._append_trail({"ts": time.time(), "op": "write_managed",
                            "path": str(target), "before": before,
                            "existed": target.exists()})
        return {"ok": True, "op": "write_managed", "path": str(target)}

    # ---- 回滚 ----
    def restore_last_good(self, n: int = 1) -> dict:
        """撤销最近 n 次改动（用改前快照还原）。返回撤销数。"""
        undone = 0
        for _ in range(n):
            if not self._trail:
                break
            rec = self._trail.pop()
            ok = self._restore_one(rec)
            if ok:
                undone += 1
        self._rewrite_trail()
        return {"ok": True, "undone": undone}

    def _restore_one(self, rec: dict) -> bool:
        op = rec.get("op")
        try:
            if op == "add_plugin":
                self.registry.uninstall(rec["name"]); return True
            if op == "remove_plugin":
                if rec.get("before"):
                    self.registry.install(Plugin(
                        name=rec["name"], files=rec["before"]))
                return True
            if op == "edit_plugin":
                before = rec.get("before")
                if before:
                    self.registry.install(Plugin(name=rec["name"], files=before))
                else:
                    self.registry.uninstall(rec["name"])
                return True
            if op == "write_managed":
                p = Path(rec["path"])
                if rec.get("before") is not None:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(rec["before"], encoding="utf-8")
                elif p.exists():
                    p.unlink()
                return True
        except Exception:
            return False
        return False

    def _rewrite_trail(self) -> None:
        with self._trail_path.open("w", encoding="utf-8") as f:
            for rec in self._trail:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def trail(self) -> List[dict]:
        return list(self._trail)
