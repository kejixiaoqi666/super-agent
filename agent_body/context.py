"""项目感知上下文 ProjectContext —— "输入前明确在做什么项目"。

你要的核心：每次输入前明确在做什么项目、当前目标是什么、哪些重要，
只喂相关的记忆/工具/历史，无用废话不输入。

ProjectContext 跟踪当前项目 + 目标 + 相关标签，持久化（0600），供精挑输入用。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Optional


class ProjectContext:
    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "context.json"
        self.data = {
            "project": "",          # 当前项目名
            "goal": "",             # 当前目标
            "tags": [],             # 相关标签（检索用）
            "focus": "",            # 精挑聚焦说明
            "history": [],          # 最近项目切换记录（供回退）
            "updated_at": 0.0,
        }
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                self.data.update(loaded)
            except Exception:
                pass

    def _save(self) -> None:
        self.data["updated_at"] = time.time()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False),
                       encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.path)

    # ---- 状态 ----
    @property
    def project(self) -> str:
        return self.data["project"]

    @property
    def goal(self) -> str:
        return self.data["goal"]

    def set_project(self, project: str, goal: str = "",
                    tags: Optional[List[str]] = None,
                    focus: str = "") -> None:
        """切换/设定当前项目。旧项目入历史。"""
        old = self.data["project"]
        if old and old != project:
            self.data["history"].append({
                "project": old, "goal": self.data["goal"],
                "at": time.time()})
            self.data["history"] = self.data["history"][-10:]  # 只留最近10个
        self.data["project"] = project
        self.data["goal"] = goal
        self.data["focus"] = focus
        if tags:
            self.data["tags"] = list(dict.fromkeys(tags))  # 去重保序
        self._save()

    def add_tag(self, tag: str) -> None:
        if tag and tag not in self.data["tags"]:
            self.data["tags"].append(tag)
            self._save()

    def clear(self) -> None:
        self.data["project"] = ""
        self.data["goal"] = ""
        self.data["tags"] = []
        self.data["focus"] = ""
        self._save()

    def describe(self) -> str:
        """给模型/检索用的上下文说明。"""
        parts = []
        if self.data["project"]:
            parts.append(f"当前项目: {self.data['project']}")
        if self.data["goal"]:
            parts.append(f"当前目标: {self.data['goal']}")
        if self.data["focus"]:
            parts.append(f"聚焦: {self.data['focus']}")
        if self.data["tags"]:
            parts.append(f"相关: {' '.join(self.data['tags'])}")
        return "；".join(parts) if parts else "(未设定项目上下文)"

    def build_query(self) -> str:
        """构建用于记忆检索的精挑查询（取重要部分，不含废话）。"""
        parts = []
        if self.data["project"]:
            parts.append(self.data["project"])
        if self.data["goal"]:
            parts.append(self.data["goal"])
        parts.extend(self.data["tags"][:5])
        return " ".join(parts) or ""
