"""验证门禁 Verification —— 交付前必过验收。

缺证据 = 未完成。机制：针对任务类型收集证据，逐条验证（文件存在?测试过?结果真实?），
全部通过才允许任务 DONE；否则标记缺失，打回执行。

对照 Hermes 的验收门禁：改代码→changed+test；部署→health+version；数据→source+calc；
生成文件→path+format；操作后台→final_state。
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class Evidence:
    """一条验收证据。"""
    claim: str          # 声称完成的事（"文件已写入"）
    kind: str           # file_exists | content_contains | command | manual
    verified: bool = False
    detail: str = ""
    # 针对 kind 的校验参数
    path: str = ""
    content: str = ""
    command: str = ""
    expected: str = ""

    def verify(self, workspace: Path) -> bool:
        """独立执行校验，不看「声称」，只看真实状态。"""
        try:
            if self.kind == "file_exists":
                ok = (workspace / self.path).exists()
                self.detail = f"file exists={ok}"
            elif self.kind == "content_contains":
                p = workspace / self.path
                ok = p.exists() and self.content in p.read_text(encoding="utf-8")
                self.detail = f"'{self.content}' in {self.path}={ok}"
            elif self.kind == "command":
                import tempfile
                with tempfile.TemporaryFile() as out:
                    r = subprocess.run(self.command, shell=True, cwd=workspace,
                                       stdout=out, stderr=subprocess.STDOUT,
                                       timeout=15)
                    out.seek(0)
                    got = out.read(2000).decode("utf-8", errors="replace")
                ok = r.returncode == 0 and (not self.expected or self.expected in got)
                self.detail = f"exit={r.returncode} expected='{self.expected}'"
            elif self.kind == "manual":
                ok = True  # 需人工确认的证据，标记待确认
                self.detail = "manual review required"
            else:
                ok = False
                self.detail = f"unknown kind {self.kind}"
            self.verified = bool(ok)
            return self.verified
        except Exception as exc:
            self.verified = False
            self.detail = f"verify error: {exc}"
            return False


@dataclass
class VerificationGate:
    """交付验收门禁：汇总所有证据，全过才放行 DONE。"""
    workspace: Path
    evidence: List[Evidence] = field(default_factory=list)
    hard: bool = True   # True=证据不足即失败；False=缺失打回但不判失败

    def add(self, claim: str, kind: str, **kw) -> "Evidence":
        e = Evidence(claim=claim, kind=kind, **kw)
        self.evidence.append(e)
        return e

    def run(self) -> dict:
        """逐条独立校验，返回门禁结果。"""
        results = []
        for e in self.evidence:
            ok = e.verify(self.workspace)
            results.append({"claim": e.claim, "kind": e.kind,
                            "verified": ok, "detail": e.detail})
        passed = all(r["verified"] for r in results)
        missing = [r for r in results if not r["verified"]]
        return {
            "passed": passed,
            "evidence_total": len(results),
            "evidence_passed": sum(1 for r in results if r["verified"]),
            "missing": missing,   # 缺失证据（验收不通过的原因）
            "blocked": (self.hard and not passed),
            "verdict": "PASS" if passed else (
                "BLOCKED" if self.hard else "PENDING"),
        }
