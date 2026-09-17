"""预动性 Proactivity —— 主动预测并预先做好用户下一步要的。

哲学：不做"猜测性破坏动作"，只做"只读预检 + 可一步执行的准备"。
任务完成/会话推进后，用确定性规则引擎预测用户最可能的下一步，
提前做安全预检（依赖在不在 / 改动脏不脏 / 测试套件在不在 / 产物在不在），
把"下一步该执行的命令"备好，用户一步确认即可执行（或跳过）。

防骚扰：同源建议带 watermark 去重，最近展示过/置信度过低就不重复弹出。

全程确定性、可注入(rules/probes)，不绑定特定执行引擎，便于测试与换内核。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .git import is_repo, status as git_status


@dataclass
class Suggestion:
    """一条预动性建议：下一步该做什么 + 已预检好的就绪项。"""
    title: str            # 人话标题
    command: str          # 下一步可一步执行的命令（CLI）
    confidence: float     # 0-1
    reason: str           # 为什么预测这个
    source: str           # 触发规则名，用于 watermark 去重
    ready: List[str] = field(default_factory=list)  # 已完成的预检/预准备

    def to_dict(self) -> dict:
        return {"title": self.title, "command": self.command,
                "confidence": round(self.confidence, 2), "reason": self.reason,
                "source": self.source, "ready": self.ready}


Rule = Callable[[dict], Optional[Suggestion]]


def _has_venv(ws: Path) -> bool:
    for v in (".venv", "venv", "env"):
        if (ws / v).exists():
            return True
    return False


def _test_command(ws: Path) -> Optional[str]:
    """探测最可能的测试命令；未识别返回 None。"""
    if (ws / "pyproject.toml").exists() or (ws / "pytest.ini").exists() \
            or (ws / "setup.py").exists() or (ws / "setup.cfg").exists():
        return "pytest tests/ -q"
    if (ws / "package.json").exists():
        return "npm test"
    if (ws / "Cargo.toml").exists():
        return "cargo test"
    return None


def rule_git_dirty(ctx: dict) -> Optional[Suggestion]:
    """改了代码没提交 → 建议提交（预检：仓库存在 + 有多少未提交改动）。"""
    ws = Path(ctx["workspace"])
    if not is_repo(ws):
        return None
    changes = git_status(ws)
    if not changes:
        return None
    return Suggestion(
        title=f"提交这次改动（{len(changes)} 个文件未提交）",
        command="git commit -m \"...\" && git push",
        confidence=0.72,
        reason="检测到工作区有未提交改动，下一步通常是提交并推送。",
        source="git_dirty",
        ready=["git 仓库存在", f"有 {len(changes)} 个文件待提交"],
    )


def rule_pending_resume(ctx: dict) -> Optional[Suggestion]:
    """有未完成任务 → 建议续跑。"""
    n = int(ctx.get("pending_count") or 0)
    if n <= 0:
        return None
    return Suggestion(
        title=f"续跑未完成的任务（{n} 个待处理）",
        command="resume",
        confidence=0.65,
        reason="队列里有未完成任务，最自然的下一步是接着做。",
        source="pending_resume",
        ready=[f"{n} 个任务在未完成清单，可断点续跑"],
    )


def rule_run_tests(ctx: dict) -> Optional[Suggestion]:
    """代码改过且有测试套件 → 建议跑测试（预检：依赖在不在 + 测试命令在不在）。"""
    ws = Path(ctx["workspace"])
    cmd = _test_command(ws)
    if not cmd:
        return None
    ready = [f"识别到测试命令: {cmd}"]
    if _has_venv(ws):
        ready.append("本地虚拟环境存在，依赖大概率已装")
    return Suggestion(
        title="跑一遍测试验证改动",
        command=cmd,
        confidence=0.58,
        reason="刚改完代码，按纪律下一步应先过测试再交付。",
        source="run_tests",
        ready=ready,
    )


def rule_inspect_artifact(ctx: dict) -> Optional[Suggestion]:
    """刚完成任务生成了产物 → 建议检查产物是否符合预期。"""
    lt = ctx.get("last_task") or {}
    goal = str(lt.get("goal") or "")
    produced = str(lt.get("produced") or "")
    if not produced or not any(k in goal for k in ("写", "生成", "创建", "导出", "报告", "文档")):
        return None
    return Suggestion(
        title=f"检查刚生成的产物: {produced}",
        command=f"cat {produced}",
        confidence=0.55,
        reason=f"任务「{goal[:30]}」刚完成并产出文件，下一步通常是核对产物。",
        source="inspect_artifact",
        ready=["产物文件已落盘", "可一步打开查看"],
    )


DEFAULT_RULES: List[Rule] = [rule_pending_resume, rule_git_dirty,
                             rule_run_tests, rule_inspect_artifact]


class ProactiveEngine:
    """预动性引擎：跑规则 + watermark 去重 + 置信度门槛 + 上限。

    persist_path 存"最近展示时间"，避免对同一来源反复打扰。
    """

    def __init__(self, workspace, data_dir: Optional[Path] = None,
                 rules: Optional[List[Rule]] = None,
                 min_confidence: float = 0.5,
                 cooldown_minutes: float = 30,
                 max_items: int = 3) -> None:
        self.workspace = Path(workspace)
        self.data_dir = Path(data_dir) if data_dir else None
        self.rules = rules or list(DEFAULT_RULES)
        self.min_confidence = min_confidence
        self.cooldown_sec = cooldown_minutes * 60
        self.max_items = max_items
        self._seen: Dict[str, float] = {}
        self._load()

    # ---- watermark（防骚扰）----
    def _seen_path(self) -> Optional[Path]:
        if not self.data_dir:
            return None
        p = self.data_dir / "proactive" / "seen.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _load(self) -> None:
        p = self._seen_path()
        if p and p.exists():
            try:
                self._seen = json.loads(p.read_text("utf-8"))
            except Exception:
                self._seen = {}

    def _save(self) -> None:
        p = self._seen_path()
        if p:
            p.write_text(json.dumps(self._seen, ensure_ascii=False), "utf-8")

    def _in_cooldown(self, source: str, now: float) -> bool:
        last = self._seen.get(source)
        return last is not None and (now - last) < self.cooldown_sec

    def _record_shown(self, source: str, now: float) -> None:
        self._seen[source] = now
        self._save()

    # ---- 主入口 ----
    def suggest(self, context: Optional[dict] = None) -> List[Suggestion]:
        """返回按置信度排序的预动性建议（已去重+门槛+上限）。"""
        ctx = dict(context or {})
        ctx.setdefault("workspace", str(self.workspace))
        now = time.time()
        out: List[Suggestion] = []
        for rule in self.rules:
            try:
                s = rule(ctx)
            except Exception:
                continue
            if s is None or s.confidence < self.min_confidence:
                continue
            if self._in_cooldown(s.source, now):
                continue
            out.append(s)
        out.sort(key=lambda s: s.confidence, reverse=True)
        top = out[: self.max_items]
        for s in top:
            self._record_shown(s.source, now)
        return top

    def peek(self, context: Optional[dict] = None) -> List[Suggestion]:
        """只预测不标记（不改 watermark），用于预览/测试。"""
        ctx = dict(context or {})
        ctx.setdefault("workspace", str(self.workspace))
        out = []
        for rule in self.rules:
            try:
                s = rule(ctx)
            except Exception:
                continue
            if s and s.confidence >= self.min_confidence:
                out.append(s)
        out.sort(key=lambda s: s.confidence, reverse=True)
        return out[: self.max_items]
