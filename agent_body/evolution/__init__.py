"""自进化思考 Self-Evolution Thinking（阶段③ 主权开放架构）。

循环：观察 → 思考 → 提案 → 批准 → 执行 → 复盘。
AI 只做观察/思考/提案准备；执行优化必须用户批准（ApprovalGate 硬约束）。

用法：
  ev = Evolution(data_dir)
  ev.observer.record_error("执行超时", source="daemon")
  prop = ev.propose("执行", target="daemon", suggestion="加大超时默认值")
  ev.approve(prop["pid"])                    # 用户批准
  ev.apply(prop["pid"], executor=...)        # 批准后才落地
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional

from .approval import ApprovalGate
from .ledger import EvolutionLedger
from .observer import Observer
from .proposer import Proposer


class Evolution:
    """自进化思考统一入口。"""

    def __init__(self, data_dir: str | Path):
        self.base = Path(data_dir) / "evolution"
        self.ledger = EvolutionLedger(data_dir)
        self.observer = Observer(data_dir, ledger=self.ledger)
        self.proposer = Proposer(data_dir, ledger=self.ledger)
        self.approval = ApprovalGate(data_dir, ledger=self.ledger)

    # ---- 观察（AI 日常记录） ----
    def observe(self, kind: str, detail: str, source: str = "", **meta) -> dict:
        return self.observer.record(kind, detail, source, **meta)

    # ---- 思考成提案（落 pending_approval） ----
    def propose(self, center: str, target: str, suggestion: str,
                reasoning: str = "", priority: Optional[str] = None,
                refs: Optional[List[str]] = None,
                level: str = "upper") -> dict:
        """成提案。level: upper(上层模块/插件,自主进化) / base(底层内核,需批准)。"""
        return self.proposer.draft(center, target, suggestion,
                                   reasoning=reasoning, priority=priority,
                                   refs=refs, level=level)

    def propose_base(self, center: str, target: str, suggestion: str,
                     reasoning: str = "", priority: Optional[str] = None,
                     refs: Optional[List[str]] = None) -> dict:
        """底层/内核进化提案（必须用户批准才执行）。"""
        return self.proposer.draft(center, target, suggestion,
                                   reasoning=reasoning, priority=priority,
                                   refs=refs, level="base")

    def propose_from_observations(self, center: str, target: str,
                                  suggestion: str,
                                  kinds: Optional[List[str]] = None) -> dict:
        return self.proposer.draft_from_observations(
            center, target, suggestion, kinds=kinds)

    # ---- 批准（用户掌舵） ----
    def approve(self, pid: str, by: str = "user") -> dict:
        return self.approval.approve(pid, by=by)

    def reject(self, pid: str, by: str = "user", note: str = "") -> dict:
        return self.approval.reject(pid, by=by, note=note)

    # ---- 执行（仅已批准可落地） ----
    def apply(self, pid: str, executor: Optional[Callable[[dict], None]] = None,
              by: str = "system") -> dict:
        return self.approval.apply(pid, executor=executor, by=by)

    def pending(self) -> List[dict]:
        """待用户批准的提案。"""
        return self.proposer.pending()

    def observations(self, kind=None) -> list:
        return self.observer.observations(kind)