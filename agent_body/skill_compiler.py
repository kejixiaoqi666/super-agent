"""经验自动固化 SkillCompiler —— "用了N次成功 → 自动变成常驻技能"。

分工：大脑存知识记忆(distill.py)，身体管执行形态(skills.py)。
闭环：
  1. 任务成功 → 身体把经验喂给大脑 record_experience（大脑记）
  2. 同类任务重复成功达阈值 → 大脑 distill_skill（知识）+ 身体写成可执行 SKILL.md
  3. 下次同类任务 → curate 自动注入该技能（已接线）
全程确定性、可注入(brain/type_of/steps)，不绑定具体执行引擎。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

TypeOf = Callable[[str], str]
Describe = Callable[[str], str]


def default_type_of(task: str) -> str:
    """把任务归一化成稳定技能名：删除所有非中英文字符（空格/标点不影响 → 稳定 id）。"""
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "", task.strip())
    return s[:40] or "task"


def default_description_of(task: str) -> str:
    return f"自动固化技能：{task.strip()[:40]}"


class SkillCompiler:
    def __init__(self, skills_store, threshold: int = 3,
                 persist_path: Optional[Path] = None,
                 type_of: Optional[TypeOf] = None,
                 description_of: Optional[Describe] = None) -> None:
        self.store = skills_store
        self.threshold = max(1, threshold)
        self.persist_path = Path(persist_path) if persist_path else None
        self.type_of = type_of or default_type_of
        self.description_of = description_of or default_description_of
        self._counts: Dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        if self.persist_path and self.persist_path.exists():
            try:
                self._counts = json.loads(self.persist_path.read_text("utf-8"))
            except Exception:
                self._counts = {}

    def _save(self) -> None:
        if self.persist_path:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.persist_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._counts, ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(self.persist_path)

    def _format_procedure(self, steps: Optional[List[str]]) -> str:
        if not steps:
            return ""
        return "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps))

    def on_task_success(self, brain, task: str, context: str = "",
                        outcome: str = "", steps: Optional[List[str]] = None) -> dict:
        """任务成功后调用：喂经验给大脑 + 重复达阈值则固化成技能。"""
        name = self.type_of(task)
        procedure = self._format_procedure(steps) or task
        # 1) 大脑记经验（知识层）
        try:
            brain.record_experience(task, context, outcome, procedure)
        except Exception:
            pass
        # 2) 重复计数
        self._counts[name] = self._counts.get(name, 0) + 1
        count = self._counts[name]
        solidified = count >= self.threshold
        # 3) 达阈值 → 大脑蒸馏知识 + 身体写成可执行 SKILL.md
        if solidified:
            try:
                brain.distill_skill(name, procedure, success=True)
            except Exception:
                pass
            self.store.upsert(name, self.description_of(task), procedure)
        self._save()
        return {"name": name, "count": count, "solidified": solidified}

    def counts(self) -> Dict[str, int]:
        return dict(self._counts)
