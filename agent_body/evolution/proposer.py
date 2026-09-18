"""进化思考者 —— 把观察沉淀成「进化提案」（AI 思考+记忆+准备，不执行）。

AI 的角色：观察(observer) → 思考(draft 提案, 含改哪里/怎么改/理由/优先级) → 存 pending_approval，
等待用户批准。proposer 本身不执行任何改动——执行权在 ApprovalGate + 用户批准。
"""

from __future__ import annotations

from typing import List, Optional

from .ledger import EvolutionLedger

# 观察类型 → 默认优先级（错误默认高，痛点中，优化/迭代低）
_PRIORITY_BY_KIND = {"error": "high", "pain": "medium",
                     "optimization": "low", "iteration": "low"}


class Proposer:
    """把观察聚合成进化提案。AI 的深思(reasoning)由外部(模型)提供，本类负责结构化 + 存库。"""

    def __init__(self, data_dir, ledger: Optional[EvolutionLedger] = None):
        self.ledger = ledger or EvolutionLedger(data_dir)

    def draft(self, center: str, target: str, suggestion: str,
              reasoning: str = "", priority: Optional[str] = None,
              refs: Optional[List[str]] = None) -> dict:
        """AI 思考后成提案，落 pending_approval（已准备，等用户批准）。"""
        return self.ledger.add_proposal(
            center=center, target=target, suggestion=suggestion,
            reasoning=reasoning,
            priority=priority or "medium",
            refs=list(refs or []) if refs else None,
            state="pending_approval")

    def draft_from_observations(self, center: str, target: str,
                                suggestion: str, refs: Optional[List[str]] = None,
                                kinds: Optional[List[str]] = None) -> dict:
        """基于一批观察成提案：优先级取引用观察中最高档，refs 自动填观察 oid。"""
        obs = self.ledger.observations()
        selected = [o for o in obs
                    if (not kinds or o["kind"] in kinds)
                    and center.lower() in (o["detail"] or "").lower()]
        selected = selected[:10]
        prio = "low"
        for o in selected:
            level = _PRIORITY_BY_KIND.get(o["kind"], "low")
            if level == "high":
                prio = "high"
                break
            if level == "medium" and prio != "high":
                prio = "medium"
        return self.ledger.add_proposal(
            center=center, target=target, suggestion=suggestion,
            reasoning=f"由 {len(selected)} 条相关观察聚合而成",
            priority=prio,
            refs=list(refs or []) if refs else [o["oid"] for o in selected],
            state="pending_approval")

    def pending(self) -> list:
        """待用户批准的提案。"""
        return self.ledger.proposals("pending_approval")