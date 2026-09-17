"""Phase 1 稳定性专项测试：熔断器 / 自查 / 连贯队列 / 进度条 / 命令沙箱。"""
import tempfile
import time
import unittest
from pathlib import Path

from agent_body.safety import (CircuitBreaker, call_with_retry,
                               with_backoff, SelfCheck)
from agent_body.loop import (TaskQueue, TaskStore, TaskState, TaskStatus,
                             render_progress)
from agent_body.exec import Sandbox, CommandError


# ---------- 熔断器 ----------
class CircuitBreakerTest(unittest.TestCase):
    def test_closed_allows(self):
        cb = CircuitBreaker()
        self.assertTrue(cb.allow())
        self.assertEqual(cb.state, "closed")

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        self.assertEqual(cb.state, "open")
        self.assertFalse(cb.allow())

    def test_resets_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=2, reset_timeout=0.01)
        cb.record_failure(); cb.record_failure()
        self.assertEqual(cb.state, "open")
        time.sleep(0.05)
        # half_open：只放行一次试探
        self.assertTrue(cb.allow())
        self.assertFalse(cb.allow())
        # 成功 → closed
        cb.record_success()
        self.assertEqual(cb.state, "closed")
        self.assertTrue(cb.allow())

    def test_success_resets(self):
        cb = CircuitBreaker(failure_threshold=2)
        cb.record_failure(); cb.record_failure()
        cb.record_success()
        self.assertEqual(cb.failures, 0)
        self.assertEqual(cb.state, "closed")

    def test_backoff(self):
        self.assertEqual(with_backoff(1), 1.0)
        self.assertEqual(with_backoff(2), 2.0)
        self.assertEqual(with_backoff(3), 4.0)
        self.assertEqual(with_backoff(10, cap=8), 8.0)  # 封顶


# ---------- 重试 ----------
class RetryTest(unittest.TestCase):
    def test_retries_until_success(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("boom")
            return "ok"

        result, attempts = call_with_retry(flaky, max_retries=3, base_backoff=0)
        self.assertEqual(result, "ok")
        self.assertEqual(attempts, 3)

    def test_gives_up_after_max(self):
        def always():
            raise ConnectionError("down")
        with self.assertRaises(ConnectionError):
            call_with_retry(always, max_retries=2, base_backoff=0)

    def test_timeout_raises(self):
        def slow():
            time.sleep(5)
            return "x"
        with self.assertRaises(TimeoutError):
            call_with_retry(slow, max_retries=0, timeout=0.05)


# ---------- 自查模块 ----------
class SelfCheckTest(unittest.TestCase):
    def test_heal_and_recheck(self):
        state = {"broken": True}
        sc = SelfCheck(
            diagnostics={"environment": lambda t: "发现: 服务未启动"},
            healers={"environment": lambda t: (state.__setitem__("broken", False), True)[1]},
        )
        rep = sc.run("svc", category="environment",
                     recheck=lambda t: not state["broken"])
        self.assertTrue(rep.ok)
        self.assertTrue(rep.healed)
        self.assertTrue(any("复验" in f for f in rep.findings))

    def test_no_healer_no_recheck(self):
        sc = SelfCheck()
        rep = sc.run("x", category="unknown")
        self.assertFalse(rep.ok)  # 无自愈无复验 → 不自称成功
        self.assertFalse(rep.healed)


# ---------- 连贯任务队列 ----------
class TaskQueueTest(unittest.TestCase):
    def _mk(self, tmp):
        store = TaskStore(Path(tmp) / "data")
        return TaskQueue(Path(tmp) / "data", store), store

    def test_serial_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, store = self._mk(tmp)
            q.enqueue("c1", ["任务1", "任务2", "任务3"])
            self.assertEqual(q.pending_count(), 3)
            first = q.next()
            self.assertIsNotNone(first)
            self.assertEqual(first.goal, "任务1")
            # 完成第一个 → 推进到第二个
            t = store.load(first.task_id)
            self.assertIsNotNone(t)
            # 走合法迁移：EXECUTING → DONE（模拟真实完成任务）
            t.transition(TaskStatus.EXECUTING)
            t.transition(TaskStatus.DONE)
            q.on_task_done(first.task_id)
            nxt = q.next()
            self.assertIsNotNone(nxt)
            self.assertEqual(nxt.goal, "任务2")

    def test_failure_puts_rest_in_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, store = self._mk(tmp)
            q.enqueue("c1", ["任务1", "任务2"])
            first = q.next()
            self.assertIsNotNone(first)
            affected = q.on_task_failed(first.task_id)
            self.assertEqual(len(affected), 2)  # 该环及后续同链都进未完成
            self.assertEqual(q.pending_count(), 0)

    def test_run_chain_integration(self):
        """回归：TaskQueue 已接入 Body.run_chain（原为孤立死代码），串行执行。"""
        from agent_body.runtime import Body

        class FakePort:
            def __init__(self, fail_on=None):
                self.fail_on = fail_on
                self.said = []
            def chat(self, msg, person_id=None):
                self.said.append(msg)
                return "ok"
            def recall(self, q, k=10): return []
            def remember(self, c, scope="", tier="", **kw): return "id"
            def save(self): pass
            def close(self): pass

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            work = Path(tmp) / "work"
            body = Body(data, work, mode="unrestricted")
            try:
                body.brains["task"] = FakePort()
                # 串行执行 2 个任务（无 plan → 单步=整个目标）
                res = body.run_chain(["任务一", "任务二"], chain_id="c9")
                self.assertEqual(len(res), 2)
                self.assertEqual([r["status"] for r in res], ["done", "done"])
                self.assertEqual(body.queue.pending_count(), 0)  # 全部出队
            finally:
                body.close()


# ---------- 进度条 ----------
class ProgressTest(unittest.TestCase):
    def test_render(self):
        t = TaskState(goal="大任务", plan=["a", "b", "c"])
        t.record_step(0, "a", "done", ok=True)
        line = render_progress(t)
        self.assertIn("1/3", line)
        self.assertIn("▓", line)

    def test_done(self):
        t = TaskState(goal="x", plan=["a"])
        t.record_step(0, "a", "ok", ok=True)
        self.assertIn("1/1", render_progress(t))


# ---------- 命令沙箱 ----------
class SandboxTest(unittest.TestCase):
    def test_run_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            sb = Sandbox(tmp)
            r = sb.run("echo hi", task_id="t1")
            self.assertTrue(r["ok"])
            self.assertIn("hi", r["output"])
            # 默认回收：工作目录被清理
            self.assertFalse((Path(tmp) / "sandbox" / "t1").exists())

    def test_keep_preserves(self):
        with tempfile.TemporaryDirectory() as tmp:
            sb = Sandbox(tmp)
            result = sb.run("touch out.txt", task_id="t1", keep=True)
            self.assertTrue(result["ok"])
            self.assertTrue((Path(tmp) / "sandbox" / "t1" / "out.txt").exists())
            sb.cleanup("t1")
            self.assertFalse((Path(tmp) / "sandbox" / "t1").exists())

    def test_dangerous_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            sb = Sandbox(tmp)
            with self.assertRaises(CommandError):
                sb.run("rm -rf /", task_id="t2")

    def test_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            sb = Sandbox(tmp)
            with self.assertRaises(CommandError):
                sb.run("exit 3", task_id="t3")


if __name__ == "__main__":
    unittest.main()
