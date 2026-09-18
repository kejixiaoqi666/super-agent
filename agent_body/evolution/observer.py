"""进化观察者 —— 日常使用中自动记录：错误/痛点/优化点/迭代建议。"""

from __future__ import annotations

from .ledger import EvolutionLedger, KINDS


class Observer:
    """记录日常观察（错误/痛点/优化点/迭代），供 proposer 思考成提案。"""

    def __init__(self, data_dir, ledger: "EvolutionLedger | None" = None):
        self.ledger = ledger or EvolutionLedger(data_dir)

    def record(self, kind: str, detail: str, source: str = "", **meta) -> dict:
        """记一条观察。kind: error(执行错误) / pain(痛点/低效) / optimization(优化点) / iteration(迭代建议)。"""
        if kind not in KINDS:
            raise ValueError(f"未知观察类型 {kind!r}（可用: {KINDS}）")
        return self.ledger.add_observation(kind, detail, source=source, **meta)

    def record_error(self, detail: str, source: str = "", **meta) -> dict:
        return self.record("error", detail, source, **meta)

    def record_pain(self, detail: str, source: str = "", **meta) -> dict:
        return self.record("pain", detail, source, **meta)

    def record_optimization(self, detail: str, source: str = "", **meta) -> dict:
        return self.record("optimization", detail, source, **meta)

    def record_iteration(self, detail: str, source: str = "", **meta) -> dict:
        return self.record("iteration", detail, source, **meta)

    def observations(self, kind=None) -> list:
        return self.ledger.observations(kind)