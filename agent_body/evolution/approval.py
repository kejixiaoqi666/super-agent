"""进化批准闸门（阶段③ 核心约束：批准才执行）。

用户定调：AI 只能观察/思考/提案准备；**执行优化必须用户批准**。
ApprovalGate 强制该不变量：
  - pending_approval → approved（用户批准）
  - **approved → applied（唯一能执行改动的路径）**
  - 未被批准的提案 apply() 一律拒绝（硬断言，不静默）。
apply 通过 injection(executor) 落地（例如 Soviete 的 self_mod / 插件注册中心）。
"""

from __future__ import annotations

from typing import Callable, Optional

from .ledger import EvolutionLedger


class ApprovalGate:
    def __init__(self, data_dir, ledger: Optional[EvolutionLedger] = None):
        self.ledger = ledger or EvolutionLedger(data_dir)

    def approve(self, pid: str, by: str = "user") -> dict:
        """用户批准：pending_approval → approved。"""
        return self.ledger.transition(pid, "approved", by=by)

    def reject(self, pid: str, by: str = "user", note: str = "") -> dict:
        """驳回：pending_approval → rejected。"""
        return self.ledger.transition(pid, "rejected", by=by, note=note)

    def archive(self, pid: str, by: str = "user") -> dict:
        return self.ledger.transition(pid, "archived", by=by)

    def apply(self, pid: str, executor: Optional[Callable[[dict], None]] = None,
              by: str = "system") -> dict:
        """执行提案。分层（用户定调 2026-09）：
          - upper(上层模块/插件)：自主进化，无需批准，直接落地；
          - base(底层/内核)：必须 approved 才可 apply，否则硬拒绝。
        executor(proposal) 负责真实落地（如 self_mod 改插件/写配置）。
        """
        p = self.ledger.get_proposal(pid)
        if p is None:
            return {"ok": False, "error": f"提案不存在: {pid}"}
        level = p.get("level", "upper")
        if level == "base" and p["state"] != "approved":
            return {"ok": False,
                    "error": f"底层进化需批准：{pid} 当前 {p['state']}（上层可自主）"}
        if executor is not None:
            executor(p)                     # 真实落地（可抛错）
        return self.ledger.transition(pid, "applied", by=by)