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
        """执行已批准的提案。【硬约束】仅 approved 可 apply，否则拒绝。
        executor(proposal) 负责真实落地（如 self_mod 改插件/写配置）。
        """
        p = self.ledger.get_proposal(pid)
        if p is None:
            return {"ok": False, "error": f"提案不存在: {pid}"}
        if p["state"] != "approved":
            return {"ok": False, "error": f"未批准不得执行：{pid} 当前 {p['state']}"}
        if executor is not None:
            executor(p)                     # 真实落地（可抛错）
        return self.ledger.transition(pid, "applied", by=by)