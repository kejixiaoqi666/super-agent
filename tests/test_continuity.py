"""自动续接 Auto-Continuity + 每轮真实输入记账测试。

验证点：
  1. ContextBudget 跟踪每轮真实输入（last/max_input），区分累计成本。
  2. over_compressed 依据当轮输入占窗口比例（而非累计）触发——上下文长但输入聚焦不误触。
  3. ContinuityManager.should_continue / status / successor_id。
  4. make_anchors 只收真实字段、空 meta 不产生锚点（绝不编造）。
  5. execute 写精确锚点进向量记忆并返回后继会话。
"""
import tempfile
import unittest
from pathlib import Path

from agent_body.budget import ContextBudget
from agent_body.continuity import ContinuityManager, successor_id


class BudgetInputTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # 小窗口方便触发：256000 太大，用 1000
        self.b = ContextBudget(self.tmp, context_length=1000, continuity_at=0.5)

    def test_tracks_last_and_max_input(self):
        self.b.record("s", 100, 50, input_tokens=300)
        u = self.b.session_usage("s")
        self.assertEqual(u["last_input"], 300)
        self.assertEqual(u["max_input"], 300)
        # 更大的当轮输入 → max 更新，累计成本也累计
        self.b.record("s", 200, 50, input_tokens=500)
        u = self.b.session_usage("s")
        self.assertEqual(u["last_input"], 500)
        self.assertEqual(u["max_input"], 500)
        self.assertEqual(u["prompt"], 300)   # 累计 prompt = 100+200
        self.assertEqual(u["messages"], 2)

    def test_max_input_never_decreases(self):
        self.b.record("s", 100, 0, input_tokens=500)
        self.b.record("s", 100, 0, input_tokens=50)   # 这轮输入变小
        u = self.b.session_usage("s")
        self.assertEqual(u["max_input"], 500)          # 高水位保留
        self.assertEqual(u["last_input"], 50)

    def test_input_falls_back_to_prompt(self):
        self.b.record("s", 120, 30)                    # 未给 input_tokens
        u = self.b.session_usage("s")
        self.assertEqual(u["last_input"], 120)

    def test_over_compressed_by_input_not_cumulative(self):
        # 累计成本已经很大，但每轮输入小 → 不触发续接（上下文长但输入聚焦）
        self.b.record("s", 300, 300, input_tokens=300)  # input 300/1000=30% < 50%
        self.assertFalse(self.b.over_compressed("s"))
        # 当轮输入达到 50% 窗口 → 触发
        self.b.record("s", 10, 10, input_tokens=600)    # 600/1000=60%
        self.assertTrue(self.b.over_compressed("s"))

    def test_no_input_seen_not_triggered(self):
        self.assertFalse(self.b.over_compressed("s"))   # 无记录

    def test_graded_levels(self):
        # 窗口1000, 触发线500(0.5), 预警线400(0.5*0.8)
        self.b.record("s", 10, 10, input_tokens=300)   # 30% → ok
        self.assertEqual(self.b.continuity_status("s")["level"], "ok")
        self.b.record("s", 10, 10, input_tokens=430)   # 43% → warn(预动预警, 未到触发线)
        st = self.b.continuity_status("s")
        self.assertEqual(st["level"], "warn")
        self.assertTrue(st["warning"])
        self.assertFalse(st["needs_continuity"])
        self.b.record("s", 10, 10, input_tokens=600)   # 60% → critical
        st = self.b.continuity_status("s")
        self.assertEqual(st["level"], "critical")
        self.assertTrue(st["needs_continuity"])


class ContinuityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.budget = ContextBudget(self.tmp, context_length=1000, continuity_at=0.5)
        self.cm = ContinuityManager(self.budget, context_length=1000, continuity_at=0.5)

    def test_should_continue_reflects_budget(self):
        self.assertFalse(self.cm.should_continue("s"))
        self.budget.record("s", 10, 10, input_tokens=900)
        self.assertTrue(self.cm.should_continue("s"))

    def test_successor_id_increments(self):
        self.assertEqual(successor_id("main"), "main#2")
        self.assertEqual(successor_id("main#2"), "main#3")
        self.assertEqual(successor_id("x#10"), "x#11")

    def test_make_anchors_only_real_fields(self):
        anchors = self.cm.make_anchors(
            {"session": "s", "goal": "部署节点", "project": "机场面板",
             "decisions": ["用PG18"], "blockers": []})
        self.assertTrue(any("部署节点" in a for a in anchors))
        self.assertTrue(any("PG18" in a for a in anchors))
        self.assertFalse(any("阻断项" in a for a in anchors))  # 空阻断项不写

    def test_empty_meta_no_anchors(self):
        # 无任何真实事实 → 不产生锚点（绝不编造内容）
        self.assertEqual(self.cm.make_anchors({}), [])
        self.assertEqual(self.cm.make_anchors(None), [])

    def test_execute_writes_to_brain_and_returns_successor(self):
        written = []

        class _FakeBrain:
            def remember(self, content, tags=None, importance=0.0, tier=""):
                written.append(content)
                return "node-abc"

        self.budget.record("s", 10, 10, input_tokens=900)
        res = self.cm.execute(
            "s", {"goal": "部署节点", "project": "机场面板"},
            brain=_FakeBrain())
        self.assertEqual(res["successor_session"], "s#2")
        self.assertTrue(written)
        self.assertIn("部署节点", written[0])
        self.assertTrue(res["note"])

    def test_execute_without_brain_no_write(self):
        # 无 brain → 不写、不编造
        res = self.cm.execute("s", {"goal": "x"})
        self.assertEqual(res["note"], "")
        self.assertEqual(res["successor_session"], "s#2")

    def test_cooldown_prevents_duplicate_fire(self):
        # 写入成功触发后，冷却期内不重复触发（防刷屏写重复锚点）
        class _B:
            def remember(self, content, tags=None, importance=0.0, tier=""):
                return "node"
        self.budget.record("s", 10, 10, input_tokens=900)
        self.assertTrue(self.cm.should_continue("s"))
        self.cm.execute("s", {"goal": "部署"}, brain=_B())   # 写入成功→记录触发
        self.assertFalse(self.cm.should_continue("s"))       # 冷却期内静默
        # 手动清冷却 → 可再次触发
        self.cm._last_fired["s"] = 0.0
        self.assertTrue(self.cm.should_continue("s"))

    def test_failed_write_does_not_set_cooldown(self):
        # 写入失败（如无 brain）不进入冷却，不压制后续重试
        self.budget.record("s", 10, 10, input_tokens=900)
        res = self.cm.execute("s", {"goal": "部署"})
        self.assertFalse(res["anchors_written"])
        self.assertTrue(self.cm.should_continue("s"))   # 未进冷却 → 仍可触发
        self.assertNotIn("s", self.cm._last_fired)


class BodyUsageWiringTest(unittest.TestCase):
    """Body.chat 用内核真实 usage.prompt_tokens 记账（覆盖本地代理估算）。"""

    def test_chat_uses_real_usage_over_proxy(self):
        from agent_body.runtime import Body

        class FakePort:
            def __init__(self):
                self.said = []
            def chat(self, msg, person_id=None):
                self.said.append(msg)
                return "ok"
            def usage(self):
                # 内核真实输入：远大于身体构造的精简 prompt
                return {"prompt_tokens": 900, "completion_tokens": 50,
                        "total_tokens": 950, "calls": 1}
            def recall(self, q, k=10): return []
            def remember(self, c, scope="", tier="", **kw): return "id"
            def save(self): pass
            def close(self): pass

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            work = Path(tmp) / "work"
            body = Body(data, work, mode="unrestricted")
            try:
                body.brains["s"] = FakePort()
                body.chat("s", "你好")
                # 输入记账应取内核真实值 900，而非身体精简 prompt 的估算
                last_input = body.budget.session_usage("s")["last_input"]
                self.assertGreaterEqual(last_input, 900)
                # 触发线 = 900/1000 >= 0.5? 默认窗口256000不触发；用 status 断言已记账
                st = body.continuity_status("s")
                self.assertGreater(st["last_input"], 0)
            finally:
                body.close()

    def test_chat_falls_back_to_proxy_when_no_usage(self):
        from agent_body.runtime import Body

        class FakePort:
            def chat(self, msg, person_id=None): return "ok"
            def recall(self, q, k=10): return []
            def remember(self, c, scope="", tier="", **kw): return "id"
            def save(self): pass
            def close(self): pass

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            work = Path(tmp) / "work"
            body = Body(data, work, mode="unrestricted")
            try:
                body.brains["s"] = FakePort()
                body.chat("s", "你好")
                last_input = body.budget.session_usage("s")["last_input"]
                self.assertGreater(last_input, 0)   # 用身体构造的精简 prompt 兜底
            finally:
                body.close()


class ContinuityAdviceTest(unittest.TestCase):
    """continuity_advice：critical 才落盘写锚点；warn/ok 轻量咨询不建 brain。"""

    def _body(self, input_tokens):
        from agent_body.runtime import Body

        class FakePort:
            def __init__(self):
                self.written = []
            def chat(self, msg, person_id=None): return "ok"
            def usage(self):
                return {"prompt_tokens": input_tokens, "completion_tokens": 0,
                        "total_tokens": input_tokens, "calls": 1}
            def recall(self, q, k=10): return []
            def remember(self, c, scope="", tier="", **kw):
                self.written.append(c)
                return "id"
            def save(self): pass
            def close(self): pass

        tmp = tempfile.TemporaryDirectory()
        per = Path(tmp.name) / "data"
        body = Body(per, Path(tmp.name) / "work", mode="unrestricted")
        body.brains["s"] = FakePort()
        body.project.set_project("机场面板", "部署节点")   # 提供真实事实，锚点非空
        return tmp, body

    def test_critical_writes_anchors(self):
        # 200000/256000≈78% > 触发线50% → critical，写锚点+给后继会话
        tmp, body = self._body(200000)
        try:
            body.chat("s", "你好")          # 记账真实输入
            adv = body.continuity_advice("s")
            self.assertEqual(adv["level"], "critical")
            self.assertTrue(adv["anchors_written"])
            self.assertIn("s#2", adv["successor_session"])
            self.assertTrue(body.brains["s"].written)   # 锚点确实写入内核
        finally:
            body.close(); tmp.cleanup()

    def test_warn_does_not_write(self):
        # 110000/256000≈43% > 预警线40% 但 < 触发线50% → warn, 不写锚点
        tmp, body = self._body(110000)
        try:
            body.chat("s", "你好")
            adv = body.continuity_advice("s")
            self.assertEqual(adv["level"], "warn")
            self.assertFalse(adv.get("anchors_written", False))
            self.assertEqual(body.brains["s"].written, [])   # 未落盘
            self.assertIn("预警线", str(adv.get("action", "")) or "")
        finally:
            body.close(); tmp.cleanup()

    def test_ok_is_healthy(self):
        tmp, body = self._body(1000)   # ≈0.4% 窗口
        try:
            body.chat("s", "你好")
            adv = body.continuity_advice("s")
            self.assertEqual(adv["level"], "ok")
            self.assertFalse(adv["warning"])
        finally:
            body.close(); tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
