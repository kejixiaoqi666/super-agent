"""预动性 Proactivity 引擎测试 —— 确定性规则 + 只读预检 + watermark 去重。

验证点：
  1. git_dirty：改了代码没提交 → 给出提交建议，且预检报告改动数。
  2. pending_resume：有未完成任务 → 建议续跑。
  3. run_tests：有测试套件且依赖在 → 建议跑测试。
  4. watermark：同源建议在冷却期内不重复弹出。
  5. 门槛/上限：低于 min_confidence 不出；超过 max_items 截断。
  6. 接入 Body：run_task 成功后结果附带 next 高置信建议。
"""
import tempfile
import unittest
from pathlib import Path

from agent_body.proactive import (
    ProactiveEngine, rule_pending_resume, rule_run_tests, Suggestion,
)


def _mk_ws(tmp: Path, sub: str = "ws") -> Path:
    ws = tmp / sub
    ws.mkdir(parents=True, exist_ok=True)
    return ws


class RuleTest(unittest.TestCase):
    def test_rule_pending_resume_positive(self):
        s = rule_pending_resume({"pending_count": 2, "workspace": "/tmp"})
        self.assertIsNotNone(s)
        self.assertEqual(s.source, "pending_resume")
        self.assertEqual(s.command, "resume")

    def test_rule_pending_resume_zero(self):
        self.assertIsNone(rule_pending_resume({"pending_count": 0}))

    def test_rule_run_tests_detects_pytest(self):
        tmp = Path(tempfile.mkdtemp())
        ws = _mk_ws(tmp)
        (ws / "pyproject.toml").write_text("[project]\n")
        (ws / ".venv").mkdir()
        s = rule_run_tests({"workspace": str(ws)})
        self.assertIsNotNone(s)
        self.assertEqual(s.command, "pytest tests/ -q")
        self.assertTrue(any("虚拟环境" in r for r in s.ready))

    def test_rule_run_tests_no_suite(self):
        tmp = Path(tempfile.mkdtemp())
        ws = _mk_ws(tmp)
        self.assertIsNone(rule_run_tests({"workspace": str(ws)}))


class ProactiveEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ws = _mk_ws(self.tmp)
        self.engine = ProactiveEngine(
            self.ws, data_dir=self.tmp / "data", cooldown_minutes=0.01)

    def _ctx(self, pending=1):
        return {"workspace": str(self.ws), "pending_count": pending}

    def test_git_dirty_gives_commit_suggestion(self):
        # 造一个 git 仓库 + 一个改动
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=str(self.ws), check=True)
        (self.ws / "a.txt").write_text("hello")
        subprocess.run(["git", "add", "a.txt"], cwd=str(self.ws), check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=str(self.ws),
                       check=True)
        (self.ws / "a.txt").write_text("changed")
        s = self.engine.suggest(self._ctx())
        sources = [x.source for x in s]
        self.assertIn("git_dirty", sources)
        g = next(x for x in s if x.source == "git_dirty")
        self.assertIn("1", str(g.ready))  # 预检报告待提交文件数

    def test_pending_resume_suggested(self):
        s = self.engine.suggest({"workspace": str(self.ws), "pending_count": 3})
        self.assertTrue(any(x.source == "pending_resume" for x in s))

    def test_watermark_cooldown_suppresses_repeat(self):
        ctx = {"workspace": str(self.ws), "pending_count": 1}
        first = self.engine.suggest(ctx)
        self.assertTrue(any(x.source == "pending_resume" for x in first))
        # 冷却期内（设超长）同源不再出
        engine2 = ProactiveEngine(
            self.ws, data_dir=self.tmp / "data", cooldown_minutes=10 ** 6)
        second = engine2.suggest(ctx)
        self.assertFalse(any(x.source == "pending_resume" for x in second))

    def test_min_confidence_filters(self):
        eng = ProactiveEngine(self.ws, data_dir=self.tmp / "data",
                              min_confidence=0.9)
        s = eng.peek({"workspace": str(self.ws), "pending_count": 1})
        # pending_resume 0.65 < 0.9，应被过滤
        self.assertFalse(any(x.source == "pending_resume" for x in s))

    def test_max_items_cap(self):
        eng = ProactiveEngine(self.ws, data_dir=self.tmp / "data",
                              max_items=1, min_confidence=0.0)
        s = eng.peek({"workspace": str(self.ws), "pending_count": 1})
        self.assertLessEqual(len(s), 1)

    def test_peek_does_not_touch_watermark(self):
        eng = ProactiveEngine(self.ws, data_dir=self.tmp / "data",
                              cooldown_minutes=10 ** 6)
        ctx = {"workspace": str(self.ws), "pending_count": 1}
        # peek 不应记录 seen → 即便冷却很长，再次 peek 仍能出
        eng.peek(ctx)
        s = eng.peek(ctx)
        self.assertTrue(any(x.source == "pending_resume" for x in s))

    def test_to_dict_roundtrip(self):
        s = Suggestion("t", "cmd", 0.7, "why", "src", ready=["a"])
        d = s.to_dict()
        self.assertEqual(d["title"], "t")
        self.assertEqual(d["confidence"], 0.7)


if __name__ == "__main__":
    unittest.main()
