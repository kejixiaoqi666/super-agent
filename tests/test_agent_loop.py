"""AgentLoop 自主执行循环专项测试：状态机/验证门禁/失败分类/断点续跑/记忆记录。"""
import tempfile
import unittest
from pathlib import Path

from agent_body.loop import (AgentLoop, TaskState, TaskStatus,
                             VerificationGate, FailureClassifier)
from agent_body.loop.task import TaskStore
from agent_body.safety.selfcheck import SelfCheck, CheckReport


class FakeBrain:
    """假大脑：无模型依赖，可脚本化控制回复/抛错，模拟真实大脑调用。"""
    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.calls = []
        self.remembered = []

    def chat(self, message, person_id=None):
        self.calls.append(message)
        if self.replies:
            return self.replies.pop(0)
        return f"[fake] done: {message[-40:]}"

    def remember(self, content, scope="user", tier="recall", **kw):
        self.remembered.append(content)
        return "id"


def _make_loop(tmp):
    data = Path(tmp) / "data"
    work = Path(tmp) / "work"
    work.mkdir(parents=True, exist_ok=True)
    return AgentLoop(data, work, lambda s: FakeBrain())


class TaskStateMachineTest(unittest.TestCase):
    def test_legal_transitions(self):
        t = TaskState("目标")
        t.transition(TaskStatus.PLANNING)
        t.transition(TaskStatus.EXECUTING)
        t.transition(TaskStatus.VERIFYING)
        t.transition(TaskStatus.DONE)
        self.assertEqual(t.status, TaskStatus.DONE)

    def test_illegal_transition_raises(self):
        t = TaskState("目标")
        with self.assertRaises(ValueError):
            t.transition(TaskStatus.DONE)  # PENDING 不能直接 DONE

    def test_terminal_states_lock(self):
        t = TaskState("目标")
        t.transition(TaskStatus.FAILED)
        # FAILED 现在允许断点续跑：可拉回 EXECUTING（不再视为锁死终态）
        self.assertTrue(t.can(TaskStatus.EXECUTING))
        t2 = TaskState("目标")
        t2.transition(TaskStatus.EXECUTING)
        t2.transition(TaskStatus.DONE)
        with self.assertRaises(ValueError):
            t2.transition(TaskStatus.EXECUTING)  # DONE 仍是真正终态

    def test_resume_cursor_skips_done(self):
        t = TaskState("目标", plan=["a", "b", "c"])
        t.record_step(0, "a", "ok", True)
        self.assertEqual(t.next_pending_step(), 1)  # 从 b 继续
        t.record_step(1, "b", "ok", True)
        self.assertEqual(t.next_pending_step(), 2)

    def test_resume_skips_failed_step_no_infinite_loop(self):
        """回归：失败步骤也算已消费，续跑不卡死同一步骤（死循环防护）。"""
        t = TaskState("目标", plan=["a", "b", "c"])
        t.record_step(0, "a", "失败", False)  # 失败步骤
        self.assertEqual(t.next_pending_step(), 1)  # 跳过失败的a，从b继续
        t.record_step(1, "b", "ok", True)
        self.assertEqual(t.next_pending_step(), 2)

    def test_selfcheck_retry_budget_prevents_infinite_loop(self):
        """回归：selfcheck 永远 ok + brain 永远失败 → 重试预算耗尽 FAILED，非无限循环。"""
        class AlwaysOk(SelfCheck):
            def run(self, target, category="unknown", max_retries=2,
                    timeout=15.0, recheck=None):
                return CheckReport(ok=True, phase="recheck",
                                   findings=["[复验] 通过"])

        class Boom(FakeBrain):
            def chat(self, message, person_id=None):
                raise RuntimeError("始终失败")

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            work = Path(tmp) / "work"
            work.mkdir(parents=True, exist_ok=True)
            # 熔断阈值调大，让 selfcheck 重试预算先耗尽（验证该防护独立生效）
            from agent_body.safety import CircuitBreaker
            loop = AgentLoop(data, work, lambda s: Boom(),
                             max_steps=10, selfcheck=AlwaysOk(),
                             breaker=CircuitBreaker(failure_threshold=999))
            t = loop.submit("目标")
            t.add_plan(["步骤1"])  # 单步，会一直触发重试
            s = loop.run(t.task_id, Boom())
            # 预算耗尽后失败，不无限循环
            self.assertEqual(s["status"], "failed")
            self.assertIn("重试预算耗尽", s["failure"]["reason"])


class TaskStoreTest(unittest.TestCase):
    def test_roundtrip_and_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp)
            t = TaskState("目标", plan=["x"])
            t.add_plan(["x", "y"])
            store.save(t)
            loaded = store.load(t.task_id)
            self.assertEqual(loaded.goal, "目标")
            self.assertEqual(loaded.status, TaskStatus.PENDING)
            self.assertEqual(loaded.plan, ["x", "y"])

    def test_list_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp)
            a = TaskState("A"); store.save(a)
            b = TaskState("B"); store.save(b)
            self.assertEqual(len(store.list()), 2)
            self.assertTrue(store.delete(a.task_id))
            self.assertEqual(len(store.list()), 1)


class VerificationGateTest(unittest.TestCase):
    def test_pass_when_all_evidence_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"; work.mkdir()
            (work / "out.txt").write_text("hello world")
            gate = VerificationGate(work)
            gate.add("文件已写入", "file_exists", path="out.txt")
            gate.add("内容正确", "content_contains", path="out.txt", content="hello")
            v = gate.run()
            self.assertTrue(v["passed"])
            self.assertEqual(v["verdict"], "PASS")

    def test_block_when_evidence_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"; work.mkdir()
            gate = VerificationGate(work)
            gate.add("文件已写入", "file_exists", path="ghost.txt")
            v = gate.run()
            self.assertFalse(v["passed"])
            self.assertEqual(v["verdict"], "BLOCKED")
            self.assertGreaterEqual(len(v["missing"]), 1)


class FailureClassifierTest(unittest.TestCase):
    def setUp(self):
        self.cls = FailureClassifier()

    def test_permission(self):
        self.assertEqual(self.cls.classify("Permission denied: /etc")["category"],
                         "permission")

    def test_environment(self):
        self.assertEqual(self.cls.classify("Connection refused: 443")["category"],
                         "environment")

    def test_missing_info(self):
        self.assertEqual(self.cls.classify("No such file: x.db")["category"],
                         "missing_info")

    def test_param(self):
        self.assertEqual(self.cls.classify("TypeError: bad arg")["category"],
                         "param_error")

    def test_unknown(self):
        self.assertEqual(self.cls.classify("weird custom glitch")["category"],
                         "unknown")


class AgentLoopTest(unittest.TestCase):
    def test_done_with_default_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop = _make_loop(tmp)
            t = loop.submit("做个事")
            s = loop.run(t.task_id, FakeBrain())
            self.assertEqual(s["status"], "done")
            self.assertEqual(s["steps_done"], 1)

    def test_failed_when_brain_raises(self):
        class Boom(FakeBrain):
            def chat(self, message, person_id=None):
                raise RuntimeError("Permission denied: rm")
        with tempfile.TemporaryDirectory() as tmp:
            loop = _make_loop(tmp)
            t = loop.submit("危险任务")
            s = loop.run(t.task_id, Boom())
            self.assertEqual(s["status"], "failed")
            self.assertEqual(s["failure"]["category"], "permission")

    def test_verification_gating(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"; work.mkdir()
            data = Path(tmp) / "data"
            loop = AgentLoop(data, work, lambda s: FakeBrain())
            # 验证门禁要求文件存在——但大脑没写文件 → BLOCKED → failed
            t = loop.submit("生成 config.txt")
            gate = VerificationGate(work)
            gate.add("config.txt 已生成", "file_exists", path="config.txt")
            s = loop.run(t.task_id, FakeBrain(["[fake] 完成任务"]), verifier=gate)
            self.assertEqual(s["status"], "failed")
            self.assertEqual(s["failure"]["category"], "missing_info")

    def test_verification_passes_when_file_created(self):
        # 先由外部把文件写进工作目录（模拟大脑工具执行的结果），验证门禁再通过
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"; work.mkdir()
            data = Path(tmp) / "data"
            (work / "config.txt").write_text("done")
            loop = AgentLoop(data, work, lambda s: FakeBrain())
            t = loop.submit("生成 config.txt")
            gate = VerificationGate(work)
            gate.add("config.txt 已生成", "file_exists", path="config.txt")
            s = loop.run(t.task_id, FakeBrain(["[fake] ok"]), verifier=gate)
            self.assertEqual(s["status"], "done")

    def test_memory_recorded_on_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop = _make_loop(tmp)
            brain = FakeBrain()
            t = loop.submit("记录任务")
            loop.run(t.task_id, brain)
            self.assertTrue(any("任务" in r for r in brain.remembered))

    def test_cancel_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop = _make_loop(tmp)
            t = loop.submit("可取消")
            self.assertTrue(loop.cancel(t.task_id))
            self.assertEqual(loop.status(t.task_id)["status"], "canceled")
            self.assertTrue(loop.delete(t.task_id))
            self.assertIsNone(loop.status(t.task_id))


if __name__ == "__main__":
    unittest.main()