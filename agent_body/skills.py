"""技能系统 Skills —— 发现 / 加载 / 注入 SKILL.md 到上下文。

对齐成熟 agent（Hermes/Codex）的约定：`skills/**/SKILL.md`，frontmatter 含
`name` / `description` / `version`。本模块负责：
  - 多目录递归发现 SKILL.md（资产区、工作区、数据目录）
  - 解析 frontmatter + 正文，按 name 去重索引
  - 按名加载、按关键词匹配、渲染成可注入上下文的指令块

只读技能文件、不执行其内容、不 import 第三方代码——技能是给模型看的指令。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# frontmatter：文件开头的 ---\n ... \n--- 块
_FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S | re.M)


@dataclass
class Skill:
    """一份已发现并解析的技能。"""
    name: str
    path: Path
    description: str = ""
    version: str = ""
    body: str = ""
    frontmatter: dict = field(default_factory=dict)

    def render_instructions(self, include_body: bool = True) -> str:
        """渲染成注入上下文的指令块。"""
        parts = [f"# Skill: {self.name}"]
        if self.description:
            parts.append(f"描述: {self.description}")
        if self.version:
            parts.append(f"版本: {self.version}")
        if include_body and self.body:
            parts.append("```markdown\n" + self.body.rstrip() + "\n```")
        return "\n".join(parts)


def _parse_frontmatter(text: str):
    """返回 (frontmatter_dict, body)。frontmatter 缺失时 dict 为空、body 为全文。"""
    m = _FM.match(text)
    if not m:
        return {}, text.strip()
    fm: dict = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip().lower()] = v.strip()
    return fm, text[m.end():].strip()


class SkillStore:
    """多根目录技能索引。同 name 取先发现的根（前面根优先）。"""

    def __init__(self, *roots):
        self.roots: List[Path] = [Path(r) for r in roots if r]
        self._by_name: dict = {}
        self._scan()

    def _scan(self) -> int:
        n = 0
        for root in self.roots:
            if not root.exists():
                continue
            for p in root.rglob("SKILL.md"):
                if self._load_file(p):
                    n += 1
        return n

    def _load_file(self, path: Path) -> bool:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        fm, body = _parse_frontmatter(text)
        name = (fm.get("name") or path.parent.name).strip()
        if not name or name in self._by_name:   # 先发现优先，去重
            return False
        self._by_name[name] = Skill(
            name=name, path=path,
            description=fm.get("description", ""),
            version=fm.get("version", ""),
            body=body, frontmatter=fm,
        )
        return True

    def reload(self) -> int:
        self._by_name.clear()
        return self._scan()

    # ---- 查询 ----
    def names(self) -> List[str]:
        return sorted(self._by_name)

    def list(self) -> List[Skill]:
        return sorted(self._by_name.values(), key=lambda s: s.name)

    def get(self, name: str) -> Optional[Skill]:
        return self._by_name.get(name)

    def load(self, *names: str) -> List[Skill]:
        out = []
        for n in names:
            s = self.get(n)
            if s:
                out.append(s)
        return out

    def match(self, keywords: List[str]) -> List[Skill]:
        """按描述/正文关键词匹配（小写包含）。"""
        kw = [k.lower() for k in keywords if k]
        if not kw:
            return []
        return [s for s in self.list()
                if any(k in (s.description + s.body).lower() for k in kw)]

    def instructions(self, *names: str, include_body: bool = True) -> str:
        """把若干技能渲染成一块注入上下文的指令。未知名字静默跳过。"""
        return "\n\n".join(
            s.render_instructions(include_body) for s in self.load(*names))
