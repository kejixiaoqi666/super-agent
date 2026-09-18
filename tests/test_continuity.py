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


if __name__ == "__main__":
    unittest.main()
