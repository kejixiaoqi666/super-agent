"""自进化思考 阶段③ 测试：观察 → 思考提案 → 批准 → 才执行。

核心硬约束（用户定调）：**未 approved 的提案不得 apply**。
"""

import tempfile
import unittest
from pathlib import Path

from agent_body.evolution import Evolution


class EvolutionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ev = Evolution(Path(self.tmp.name) / "data")

    def tearDown(self):
        self.tmp.cleanup()

    # ---- 观察 ----
    def test_observe_records(self):
        o1 = self.ev.observe("error", "执行超时", source="daemon")
        self.ev.observe("pain", "重复手动审批", source="cli")
        self.ev.observe("optimization", "脚本可并行")
        self.ev.observe("iteration", "Autopilot 可接安卓")
        self.assertEqual(len(self.ev.observations()), 4)
        self.assertEqual(self.ev.observations("error")[0]["oid"], o1["oid"])

    def test_observe_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            self.ev.observe("hack", "x")

    # ---- 提案 + 分层进化（核心不变量） ----
    def test_upper_self_evolves_without_approval(self):
        # 上层模块/插件：自主进化，无需批准
        p = self.ev.propose("执行", target="daemon", suggestion="加大超时")  # upper 默认
        res = self.ev.apply(p["pid"])
        self.assertTrue(res["ok"])
        self.assertEqual(res["state"], "applied")

    def test_base_requires_approval(self):
        # 底层/内核：必须批准才执行
        p = self.ev.propose_base("执行", target="daemon", suggestion="加大超时")
        res = self.ev.apply(p["pid"])
        self.assertFalse(res["ok"])
        self.assertIn("底层进化需批准", res["error"])
        # 批准后可执行
        self.ev.approve(p["pid"])
        res2 = self.ev.apply(p["pid"])
        self.assertTrue(res2["ok"])
        self.assertEqual(res2["state"], "applied")

    def test_approve_then_apply_runs_executor(self):
        p = self.ev.propose_base("执行", target="daemon", suggestion="加大超时")
        applied = []
        ap = self.ev.approve(p["pid"])
        self.assertTrue(ap["ok"])
        res = self.ev.apply(p["pid"], executor=lambda prop: applied.append(prop))
        self.assertTrue(res["ok"])
        self.assertEqual(res["state"], "applied")
        self.assertEqual(len(applied), 1)          # 真实落地被调用

    def test_illegal_transition_blocked(self):
        p = self.ev.propose_base("执行", target="x", suggestion="y")
        # 直接 rejected→applied 非法
        self.ev.reject(p["pid"])
        r = self.ev.apply(p["pid"])               # rejected 后 apply 底层
        self.assertFalse(r["ok"])
        self.assertIn("需批准", r["error"])

    def test_body_run_scripts_auto_observes_errors(self):
        """日常使用：执行失败自动记录为自进化观察(error)。"""
        from agent_body.runtime import Body
        with tempfile.TemporaryDirectory() as td:
            body = Body(Path(td) / "data", Path(td) / "work", mode="unrestricted")
            try:
                res = body.run_scripts(["print('hi')", "def f(:"], runtime="python")
                self.assertEqual(res[0]["status"], "done")
                self.assertNotEqual(res[1]["status"], "done")
                # 失败任务已入自进化观察
                errs = body.evolution().observations("error")
                self.assertTrue(any("SyntaxError" in (o["detail"] or "")
                                    for o in errs))
            finally:
                body.close()

    def test_ai_toolset_self_evolution(self):
        """层面一开放：AI 通过工具面自主进化上层 + 底层需批准。"""
        from agent_body.runtime import Body
        with tempfile.TemporaryDirectory() as td:
            body = Body(Path(td) / "data", Path(td) / "work", mode="unrestricted")
            try:
                t = body.ai_sovereign_toolset()
                # AI 记录观察
                t["observe"]("pain", "重复手动审批", source="ai")
                # AI 提上层提案 → 自主 apply 成功（无需批准）
                p = t["propose_upper"](center="界面", target="面板",
                                       suggestion="加批量按钮")
                r = t["apply_proposal"](p["pid"])
                self.assertTrue(r["ok"])
                self.assertEqual(r["state"], "applied")
                # AI 提底层提案 → apply 被拒（需批准）
                pb = t["propose_base"](center="内核", target="daemon",
                                       suggestion="改调度")
                rb = t["apply_proposal"](pb["pid"])
                self.assertFalse(rb["ok"])
                self.assertIn("批准", rb["error"])
                # AI 装插件（上层自由区）
                t["install_plugin"]("快捷", {"main.py": "def main(): return 1"})
                self.assertIn("快捷", t["list_plugins"]())
            finally:
                body.close()

    def test_approve_only_from_pending(self):
        p = self.ev.propose("执行", target="x", suggestion="y")
        self.ev.reject(p["pid"])
        r = self.ev.approve(p["pid"])             # rejected→approved 非法
        self.assertFalse(r["ok"])

    # ---- 从观察聚合提案 ----
    def test_propose_from_observations_priority_and_refs(self):
        self.ev.observe("error", "执行超时", source="daemon")
        self.ev.observe("pain", "执行低效")
        p = self.ev.propose_from_observations(
            "执行", target="daemon", suggestion="优化")
        self.assertEqual(p["priority"], "high")    # 含 error → 高优先级
        self.assertTrue(p["refs"])                 # 自动回填观察 oid
        self.assertEqual(p["state"], "pending_approval")


class SelfOpsTest(unittest.TestCase):
    """AI 自运维 + 自测 bug：selftest 找缺陷入自进化, ops_health/diagnose 汇总。"""

    def _body(self):
        from agent_body.runtime import Body
        td = tempfile.TemporaryDirectory()
        body = Body(Path(td.name) / "data", Path(td.name) / "work",
                    mode="unrestricted")
        return td, body

    def _project(self, td):
        """建一个带 1 通过 + 1 失败的测试项目。"""
        proj = Path(td.name) / "proj"
        (proj / "tests").mkdir(parents=True)
        (proj / "tests" / "test_demo.py").write_text(
            "def test_ok(): assert 1 == 1\n"
            "def test_bad(): assert 1 == 2\n", encoding="utf-8")
        return proj

    def test_selftest_finds_bug_and_observes(self):
        td, body = self._body()
        try:
            proj = self._project(td)
            res = body.selftest(workdir=proj)
            self.assertFalse(res["ok"])              # 找到缺陷
            self.assertGreaterEqual(res["failed"], 1)
            self.assertTrue(res["observed_error"])
            # 失败已入自进化观察
            errs = body.evolution().observations("error")
            self.assertTrue(any("selftest" in (o.get("source") or "")
                                for o in errs))
        finally:
            body.close(); td.cleanup()

    def test_ops_health_and_diagnose(self):
        td, body = self._body()
        try:
            proj = self._project(td)
            body.selftest(workdir=proj)              # 制造一个错误观察
            h = body.ops_health()
            for k in ("executor", "queue_pending", "error_observations",
                      "pending_proposals", "pending_upgrades", "trust_mode"):
                self.assertIn(k, h)
            self.assertGreaterEqual(h["error_observations"], 1)
            d = body.ops_diagnose()
            self.assertTrue(d["diagnosed"])
            self.assertTrue(any(i["kind"] == "selftest/exec_error"
                                for i in d["issues"]))
        finally:
            body.close(); td.cleanup()

    def test_ai_toolset_has_selfops(self):
        td, body = self._body()
        try:
            t = body.ai_sovereign_toolset()
            for k in ("selftest", "ops_health", "ops_diagnose"):
                self.assertIn(k, t)
        finally:
            body.close(); td.cleanup()


if __name__ == "__main__":
    unittest.main()