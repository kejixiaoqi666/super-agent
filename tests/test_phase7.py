"""Phase 7 专项测试：插件系统 / 可观测性 / 断点续跑 / 重试预算。"""
import json
import tempfile
import unittest
from pathlib import Path
from typing import Optional

from agent_body.plugins import PluginRegistry, PluginError
from agent_body.observe import Tracer, get_logger
from agent_body.retry import RetryBudgetExhausted, retry_budget
from agent_body.loop.task import TaskState, TaskStatus
from agent_body.loop.loop import AgentLoop
from agent_body.resume import resume_list, resume_one


# ---------- 插件系统 ----------
class PluginTest(unittest.TestCase):
    def _make_plugin_dir(self, tmp, plugin_id="p1",
                         entry: Optional[str] = None) -> Path:
        d = Path(tmp) / plugin_id
        d.mkdir(parents=True)
        manifest = {"id": plugin_id, "version": "1.0.0",
                    "api_version": 1,
                    "capabilities": ["tool:echo", "hook:after_chat"]}
        if entry:
            manifest["entry"] = entry
        (d / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
        if entry:
            (d / entry).write_text(
                "SIDE_EFFECT = []\n"
                "def setup():\n"
                "    SIDE_EFFECT.append('enabled')\n"
                "def teardown():\n"
                "    SIDE_EFFECT.append('disabled')\n",
                encoding="utf-8")
        return d

    def test_scan_finds_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_plugin_dir(tmp)
            reg = PluginRegistry([tmp])
            plugins = reg.scan()
            self.assertEqual(len(plugins), 1)
            self.assertEqual(plugins[0].id, "p1")
            self.assertIn("tool:echo", plugins[0].capabilities)

    def test_scan_duplicate_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 两个不同根目录下同名插件 → 应报重复
            r1 = Path(tmp) / "r1"
            r2 = Path(tmp) / "r2"
            self._make_plugin_dir(r1, plugin_id="dup", entry=None)
            self._make_plugin_dir(r2, plugin_id="dup", entry=None)
            reg = PluginRegistry([r1, r2])
            with self.assertRaises(PluginError):
                reg.scan()

    def test_sca_missing_fields_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad"
            bad.mkdir()
            (bad / "plugin.json").write_text(
                json.dumps({"id": "x"}), encoding="utf-8")
            reg = PluginRegistry([tmp])
            with self.assertRaises(PluginError):
                reg.scan()

    def test_enable_with_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_plugin_dir(tmp, entry="plugin.py")
            reg = PluginRegistry([tmp])
            reg.scan()
            p = reg.enable("p1")
            self.assertTrue(p.enabled)
            mod_before = p.module
            self.assertEqual(mod_before.SIDE_EFFECT, ["enabled"])
            p.disable()
            self.assertFalse(p.enabled)
            self.assertIsNone(p.module)  # disable 卸载模块
            self.assertEqual(mod_before.SIDE_EFFECT, ["enabled", "disabled"])

    def test_manifest_only_no_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_plugin_dir(tmp, entry=None)
            reg = PluginRegistry([tmp])
            reg.scan()
            p = reg.enable("p1")
            self.assertTrue(p.enabled)  # manifest-only 插件 enable 不加载代码
            self.assertIsNone(p.module)

    def test_enable_capability(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_plugin_dir(tmp, entry=None)
            reg = PluginRegistry([tmp])
            reg.scan()
            enabled = reg.enable_capability("tool:echo")
            self.assertEqual([p.id for p in enabled], ["p1"])


# ---------- 可观测性 ----------
class ObserveTest(unittest.TestCase):
    def test_span_writes_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr = Tracer(tmp)
            with tr.span("chat", session="s1"):
                pass
            tid = tr.trace_id()
            self.assertTrue(tid)
            events = tr.read_trace(tid)
            self.assertEqual(len(events), 2)  # start + end
            self.assertEqual(events[0]["event"], "start")
            self.assertEqual(events[1]["event"], "end")
            self.assertIn("elapsed_ms", events[1])

    def test_span_captures_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr = Tracer(tmp)
            with self.assertRaises(ValueError):
                with tr.span("tool"):
                    raise ValueError("boom")
            events = tr.read_trace(tr.trace_id())
            self.assertEqual(events[-1]["event"], "error")
            self.assertIn("boom", events[-1]["error"])

    def test_log_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr = Tracer(tmp)
            tr.log("tool_call", tool="read_file", ok=True)
            events = tr.read_trace(tr.trace_id())
            self.assertEqual(events[0]["event"], "tool_call")
            self.assertEqual(events[0]["tool"], "read_file")

    def test_get_logger_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            lg = get_logger("test-observe", tmp)
            lg2 = get_logger("test-observe", tmp)
            self.assertIs(lg, lg2)  # 幂等


# ---------- 重试预算 ----------
class RetryTest(unittest.TestCase):
    def test_success_first_attempt(self):
        calls = []
        result, budget = retry_budget(
            lambda: (calls.append(1), "ok")[1], max_attempts=3)
        self.assertEqual(result, "ok")
        self.assertEqual(budget.attempts, 1)
        self.assertEqual(len(calls), 1)

    def test_retries_then_succeeds(self):
        calls = []
        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("transient")
            return "done"
        result, budget = retry_budget(flaky, max_attempts=5, base_backoff=0.01,
                                      backoff_cap=0.05)
        self.assertEqual(result, "done")
        self.assertEqual(budget.attempts, 3)

    def test_budget_exhausted(self):
        calls = []
        def always_fail():
            calls.append(1)
            raise RuntimeError("nope")
        with self.assertRaises(RetryBudgetExhausted):
            retry_budget(always_fail, max_attempts=3, max_wait=0.05,
                         base_backoff=0.01, backoff_cap=0.03)
        self.assertGreaterEqual(len(calls), 1)

    def test_non_retryable_stops(self):
        calls = []
        def boom():
            calls.append(1)
            raise ValueError("permanent")
        with self.assertRaises(RetryBudgetExhausted) as ctx:
            retry_budget(boom, max_attempts=5, max_wait=1.0,
                         retryable=lambda e: False)
        self.assertEqual(ctx.exception.reason, "non_retryable")
        self.assertEqual(len(calls), 1)  # 只试一次就停


# ---------- 断点续跑 ----------
class ResumeTest(unittest.TestCase):
    def _make_loop(self, tmp):
        class FakeBrain:
            def chat(self, msg, person_id=None): return "step done"
            def __call__(self, session): return self
        loop = AgentLoop(tmp, tmp, lambda s: FakeBrain(),
                         max_steps=8)
        return loop

    def test_resume_list_after_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop = self._make_loop(tmp)
            t = loop.submit("任务A")
            t.set_failure("network", "连不上")
            t.transition(TaskStatus.FAILED)
            loop.store.save(t)
            pending = resume_list(loop)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["status"], "failed")

    def test_failed_can_transition_to_executing(self):
        t = TaskState("x")
        t.set_failure("net", "e")
        t.transition(TaskStatus.FAILED)
        self.assertTrue(t.can(TaskStatus.EXECUTING))  # 续跑许可

    def test_canceled_can_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop = self._make_loop(tmp)
            t = loop.submit("任务B")
            t.transition(TaskStatus.CANCELED)
            loop.store.save(t)
            pending = resume_list(loop)
            self.assertEqual(pending[0]["status"], "canceled")
            # 续跑不应质疑状态机
            self.assertTrue(t.can(TaskStatus.EXECUTING))

    def test_resume_runs_to_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop = self._make_loop(tmp)
            t = loop.submit("任务C")
            t.set_failure("timeout", "超时")
            t.transition(TaskStatus.FAILED)
            loop.store.save(t)
            summary = resume_one(loop, t.task_id, loop.brain_factory("local"))
            self.assertEqual(summary["status"], "done")
            self.assertEqual(len(resume_list(loop)), 0)  # 完成后不在未完成清单


if __name__ == "__main__":
    unittest.main()