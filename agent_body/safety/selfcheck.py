"""自查模块 SelfCheck —— 失败后的第一动作（你要的特重要模块）。

哲学：失败三次不是"停止"，而是"进入自查"。
流程：诊断 → 根因自查 → 自愈尝试 → 复验，真成功才进入下一步。
不再盲目重试，也不再直接放弃。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .circuit import call_with_retry


@dataclass
class CheckReport:
    """一次自查的结果。"""
    ok: bool
    phase: str                 # diagnostic | root_cause | self_heal | recheck
    findings: List[str] = field(default_factory=list)
    healed: bool = False       # 是否自愈成功
    duration: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "phase": self.phase, "findings": self.findings,
            "healed": self.healed, "duration": round(self.duration, 3),
            "error": self.error,
        }


class SelfCheck:
    """自查器：组合诊断器/自愈器/复验器，跑一轮自查。"""

    def __init__(self, diagnostics: Optional[Dict[str, Callable]] = None,
                 healers: Optional[Dict[str, Callable]] = None):
        """
        diagnostics: {类别名: callable(target)->str 找到根因的描述或""}
        healers:     {类别名: callable(target)->bool 尝试修复}
        类别名与 FailureClassifier 的 category 对应。
        """
        self.diagnostics = diagnostics or {}
        self.healers = healers or {}

    def run(self, target: str, category: str = "unknown",
            max_retries: int = 2, timeout: float = 15.0,
            recheck: Optional[Callable[[str], bool]] = None) -> CheckReport:
        """对目标 target 跑一轮自查。

        1. diagnostic   诊断，找根因（用诊断器，不猜）
        2. self_heal    若该类别有自愈器，尝试修复
        3. recheck      若提供复验函数，复验真成功
        返回 CheckReport。
        """
        start = time.time()
        report = CheckReport(ok=False, phase="diagnostic")

        # 1) 诊断：找根因
        diag = self.diagnostics.get(category)
        if diag:
            try:
                finding, _ = call_with_retry(
                    lambda: diag(target), max_retries=1, timeout=timeout)
                if finding:
                    report.findings.append(f"[诊断] {finding}")
            except Exception as exc:
                report.findings.append(f"[诊断失败] {exc}")

        # 2) 自愈：若该类别有修复器，尝试
        healer = self.healers.get(category)
        if healer:
            report.phase = "self_heal"
            try:
                healed, _ = call_with_retry(
                    lambda: healer(target), max_retries=max_retries,
                    timeout=timeout)
                report.healed = bool(healed)
                report.findings.append(f"[自愈] {'成功' if healed else '未修复'}")
            except Exception as exc:
                report.findings.append(f"[自愈失败] {exc}")

        # 3) 复验：真成功才算
        if recheck is not None:
            report.phase = "recheck"
            try:
                ok, _ = call_with_retry(
                    lambda: recheck(target), max_retries=1, timeout=timeout)
                report.ok = bool(ok)
                report.findings.append(f"[复验] {'通过' if ok else '仍未通过'}")
            except Exception as exc:
                report.error = str(exc)
                report.findings.append(f"[复验异常] {exc}")
        else:
            report.ok = report.healed  # 无复验器，以自愈是否成功为准

        report.duration = time.time() - start
        return report
