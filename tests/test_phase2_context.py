"""Phase 2 上下文治理专项测试：项目上下文 / 精挑输入 / token预算。"""
import tempfile
import unittest

from agent_body.context import ProjectContext
from agent_body.curate import Curator, CuratedContext
from agent_body.budget import ContextBudget, estimate_tokens


# ---------- 项目上下文 ----------
class ProjectContextTest(unittest.TestCase):
    def test_set_and_describe(self):
        with tempfile.TemporaryDirectory() as tmp:
            pc = ProjectContext(tmp)
            pc.set_project("面板项目", "修网关转发bug", tags=["panel", "gateway"])
            d = pc.describe()
            self.assertIn("面板项目", d)
            self.assertIn("修网关转发bug", d)
            self.assertIn("gateway", d)

    def test_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            pc = ProjectContext(tmp)
            pc.set_project("A", goal="g", tags=["t"])
            pc2 = ProjectContext(tmp)  # 新实例读回
            self.assertEqual(pc2.project, "A")
            self.assertEqual(pc2.goal, "g")

    def test_history_on_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            pc = ProjectContext(tmp)
            pc.set_project("旧项目", goal="x")
            pc.set_project("新项目", goal="y")
            self.assertEqual(pc.project, "新项目")
            self.assertTrue(any(h["project"] == "旧项目" for h in pc.data["history"]))

    def test_build_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            pc = ProjectContext(tmp)
            pc.set_project("云南巡检", goal="查机器状态", tags=["vpn", "check"])
            q = pc.build_query()
            self.assertIn("云南巡检", q)
            self.assertIn("check", q)


# ---------- 精挑输入 ----------
class FakeRecallBrain:
    """模拟有记忆检索的大脑：返回带 score 的记忆。"""
    def __init__(self, hits):
        self._hits = hits
    def recall(self, query, k=5):
        return self._hits
    def chat(self, msg, person_id=None):
        return "假装回复"


class CuratorTest(unittest.TestCase):
    def _hits(self):
        return [
            {"content": "抱歉，这个距离比较远，我处理不了。", "score": 0.1},  # 低相关
            {"content": "关于网关18830端口的路由规则", "score": 0.9},   # 高相关
            {"content": "昨天天气不错", "score": 0.2},                       # 低相关/噪音
        ]

    def test_curator_filters_irrelevant(self):
        with tempfile.TemporaryDirectory() as tmp:
            pc = ProjectContext(tmp)
            pc.set_project("网关", goal="查路由", tags=["gateway"])
            curator = Curator(memory_threshold=0.3)
            out = curator.curate(FakeRecallBrain(self._hits()), pc, "查路由")
            self.assertIn("网关", out.project_line)      # 项目感知注入
            self.assertEqual(len(out.relevant_memory), 1)  # 只留1条高相关
            self.assertIn("18830", out.relevant_memory[0])
            self.assertEqual(out.dropped, 2)             # 无效的滤掉

    def test_to_prompt_assembles(self):
        c = CuratedContext(project_line="项目: P", message="帮我")
        c.relevant_memory.append("关键记忆")
        c.tool_hints.append("gateway")
        p = c.to_prompt()
        self.assertIn("项目: P", p)
        self.assertIn("关键记忆", p)
        self.assertIn("帮我", p)

    def test_no_project_uses_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            pc = ProjectContext(tmp)  # 未设项目
            out = Curator().curate(FakeRecallBrain([]), pc, "直接问")
            self.assertIn("直接问", out.message)


# ---------- 上下文预算 ----------
class BudgetTest(unittest.TestCase):
    def test_estimate_tokens(self):
        self.assertGreater(estimate_tokens("你好世界"), 0)  # 中文按字算
        self.assertEqual(estimate_tokens("abcd"), 1)        # 4个ASCII=1

    def test_record_and_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = ContextBudget(tmp, max_tokens=100, archive_at=0.5)
            self.assertFalse(b.over_budget("s1"))
            # 灌到超线
            for _ in range(10):
                b.record("s1", 20, 10)   # 30/次 → 300 > 50
            self.assertTrue(b.over_budget("s1"))

    def test_archive_resets(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = ContextBudget(tmp, max_tokens=100, archive_at=0.5)
            for _ in range(5):
                b.record("s1", 20, 10)
            self.assertTrue(b.over_budget("s1"))
            b.archive("s1")
            self.assertFalse(b.over_budget("s1"))  # 归档后腾出
            self.assertEqual(b.status()["archives"], 1)


if __name__ == "__main__":
    unittest.main()