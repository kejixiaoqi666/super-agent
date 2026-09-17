"""精挑输入模块 Curate —— "只挑有用的，不塞废话"。

调用大脑之前，用项目上下文对「记忆/历史」做精挑：
  1. 用项目相关查询 recall 记忆，只取高相关（score 阈值）
  2. 过滤掉噪音记忆（context_cleaner.is_noise）
  3. 工具按项目标签惰性注入（不全量载入）
  4. 拼成精挑上下文包给大脑

核心目标：输入前明确在做什么项目、当前目标，只喂相关，无用废话不输入。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .context import ProjectContext


@dataclass
class CuratedContext:
    """精挑后的上下文包。"""
    project_line: str = ""
    relevant_memory: List[str] = field(default_factory=list)
    tool_hints: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    message: str = ""
    dropped: int = 0          # 被过滤掉的相关记忆条数

    def to_prompt(self) -> str:
        """组装成喂给大脑的精挑上下文。"""
        parts = []
        if self.project_line:
            parts.append(self.project_line)
        if self.relevant_memory:
            parts.append("相关记忆:\n" + "\n".join(
                f"- {m[:120]}" for m in self.relevant_memory[:6]))
        if self.skills:
            parts.append("参考技能:\n" + "\n".join(self.skills[:3]))
        if self.tool_hints:
            parts.append("可用工具线索: " + " ".join(self.tool_hints[:8]))
        parts.append(self.message)
        return "\n\n".join(p for p in parts if p)


class Curator:
    """精挑器：把原始输入 → 精挑上下文包。"""

    def __init__(self, memory_threshold: float = 0.3,
                 max_memory: int = 6, is_noise=None):
        self.memory_threshold = memory_threshold
        self.max_memory = max_memory
        # 噪音过滤器：优先用内核 context_cleaner.is_noise
        self._is_noise = is_noise

    def _noise(self, text: str) -> bool:
        if self._is_noise:
            try:
                return bool(self._is_noise(text))
            except Exception:
                return False
        return False

    def curate(self, brain, ctx: ProjectContext, message: str,
               all_tools: Optional[List[str]] = None,
               skills_store=None) -> CuratedContext:
        """精挑：用项目上下文过滤记忆 + 工具 + 自动注入相关技能，拼上下文包。

        brain: BrainPort（须有 recall(query, k)）
        ctx:   当前项目上下文
        message: 本次输入
        all_tools: 全部可用工具名（可选，用于惰性注入）
        skills_store: 技能仓库（可选，按消息自动匹配相关技能注入）
        """
        out = CuratedContext(
            project_line=ctx.describe(),
            message=message)

        # 1) 精挑记忆：用项目查询检索，只取高相关且非噪音
        query = ctx.build_query() or message
        try:
            hits = brain.recall(query, k=10) or []
            for h in hits:
                content = (h.get("content") or "") if isinstance(h, dict) else str(h)
                score = h.get("score", 0) if isinstance(h, dict) else 0
                if score < self.memory_threshold:
                    out.dropped += 1
                    continue
                if self._noise(content):
                    out.dropped += 1
                    continue
                out.relevant_memory.append(content)
                if len(out.relevant_memory) >= self.max_memory:
                    break
        except Exception:
            pass

        # 2) 工具惰性注入：有项目标签时，只提相关工具名
        if all_tools:
            tags = ctx.data.get("tags", [])
            if tags:
                lowered = [t.lower() for t in tags]
                out.tool_hints = [
                    t for t in all_tools
                    if any(tag in t.lower() for tag in lowered)]
                if not out.tool_hints:
                    out.tool_hints = all_tools[:5]  # 兜底给少量
            else:
                out.tool_hints = all_tools[:5]  # 无标签给少量核心

        # 3) 技能自动注入：按消息 bigram 匹配相关技能，把指令拼进上下文
        if skills_store is not None:
            try:
                for skill in skills_store.match_text(message, k=3):
                    out.skills.append(skill.render_instructions())
            except Exception:
                pass
        return out
