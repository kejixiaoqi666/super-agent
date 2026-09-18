import unittest
from pathlib import Path

from agent_body.delegation import (
    delegate_task, parallel_delegate,
)
from memory_plane.model_router import (
    EchoProvider, ModelRequest, ModelRouter, Provider,
)


class _Scripted(Provider):
    """按序返回预设响应(可含 tool_calls)，验证子代理循环/工具派发。"""
    name = "scripted"

    def __init__(self, sequence):
        self.seq = list(sequence)
        self.calls = []

    def complete(self, request: ModelRequest) -> dict:
        self.calls.append(request)
        return self.seq.pop(0) if self.seq else {"content": "(no more)", "tool_calls": []}


def _tool_call(name, args, tid="t1"):
    return [{"id": tid, "type": "function",
             "function": {"name": name, "arguments": args}}]


class SubAgentTest(unittest.TestCase):
    def test_no_tool_returns_immediately(self):
        r = _Scripted([{"content": "完成，结果X", "tool_calls": []}])
        out = delegate_task(r, "算一下")
        self.assertEqual(out["summary"], "完成，结果X")
        self.assertEqual(out["steps"], 1)
        self.assertFalse(out["truncated"])

    def test_executes_tool_calls_then_concludes(self):
        seen = {}

        def dispatch(name, args):
            seen[name] = args
            return "11"
        r = _Scripted([
            {"content": "", "tool_calls": _tool_call("calc", '{"a":1,"b":2}')},
            {"content": "结果是11", "tool_calls": []},
        ])
        # 需要 EchoProvider 兼容的 ModelRequest —— Scripted 不认识也没关系
        out = delegate_task(r, "求和", tool_dispatch=dispatch,
                            tools=({"name": "calc"},))
        self.assertEqual(seen, {"calc": {"a": 1, "b": 2}})
        self.assertEqual(out["summary"], "结果是11")
        self.assertEqual(out["steps"], 2)
        # 验证工具结果回灌到后续请求
        last = r.calls[-1]
        roles = [m["role"] for m in last.messages]
        self.assertIn("tool", roles)

    def test_requests_tool_without_dispatch_is_truncated(self):
        r = _Scripted([{"content": "", "tool_calls": _tool_call("calc", "{}")}])
        out = delegate_task(r, "x", tools=({"name": "calc"},))
        self.assertTrue(out["truncated"])
        self.assertIn("未启用工具执行", out["note"])

    def test_max_steps_exhausted(self):
        # 每次都返回带工具调用 → 一直循环到 max_steps 耗尽
        r = _Scripted([{"content": "", "tool_calls": _tool_call("x", "{}")}] * 3)

        def dispatch(name, args):
            return "ok"
        out = delegate_task(r, "y", tool_dispatch=dispatch, max_steps=3)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["steps"], 3)

    def test_echo_provider_returns_goal(self):
        # Echo 不感知系统提示，直接回最后 user 文本
        router = ModelRouter([EchoProvider()])
        out = delegate_task(router, "帮我查IP")
        self.assertIn("帮我查IP", out["summary"])


class ParallelTest(unittest.TestCase):
    def test_parallel_isolated_and_ordered(self):
        router = ModelRouter([EchoProvider()])
        tasks = [("任务A", ""), ("任务B", "")]
        outs = parallel_delegate(router, tasks, max_concurrent=2)
        self.assertEqual(len(outs), 2)
        self.assertIn("任务A", outs[0]["summary"])
        self.assertIn("任务B", outs[1]["summary"])

    def test_dict_tasks(self):
        router = ModelRouter([EchoProvider()])
        outs = parallel_delegate(router, [{"goal": "G1", "context": "ctx1"}])
        self.assertIn("G1", outs[0]["summary"])
        # context 拼进 user 消息，Echo 原样返回
        self.assertIn("ctx1", outs[0]["summary"])


class _Raising(Provider):
    """complete 恒抛错，测并行/工具派发的异常隔离。"""
    name = "raising"

    def complete(self, request):
        raise RuntimeError("provider boom")


class RobustnessTest(unittest.TestCase):
    def test_parallel_isolates_worker_exception(self):
        outs = parallel_delegate(_Raising(),
                                 [("任务A", ""), ("任务B", "")], max_concurrent=2)
        self.assertEqual(len(outs), 2)
        for o in outs:
            self.assertIn("子代理异常", o["summary"])

    def test_subagent_tool_dispatch_error_handled(self):
        def bad_dispatch(name, args):
            raise RuntimeError("tool boom")
        r = _Scripted([{"content": "", "tool_calls": _tool_call("ls", "{")},
                       {"content": "done", "tool_calls": []}])
        out = delegate_task(r, "x", tool_dispatch=bad_dispatch,
                            tools=({"name": "ls"},))
        self.assertEqual(out["summary"], "done")  # 工具错误回传，子代理继续
        # 验证工具错误进入了后续请求
        last = r.calls[-1]
        tool_msgs = [m for m in last.messages if m["role"] == "tool"]
        self.assertIn("tool boom", tool_msgs[0]["content"])


from agent_body.delegation import (
    DelegationGovernor, DelegationLimitError, make_session_scoped_dispatch,
)


class _Policy:
    """模拟 BodyPolicy：needs_approval(name, cat, side)。"""

    def __init__(self, deny_writes=True, deny_exec=True):
        self.deny_writes = deny_writes
        self.deny_exec = deny_exec

    def needs_approval(self, name, category, side_effects):
        if side_effects == "write":
            return self.deny_writes
        if side_effects == "exec":
            return self.deny_exec
        return False


class GovernorTest(unittest.TestCase):
    def test_caps_concurrency(self):
        g = DelegationGovernor(max_concurrent=4, total_step_budget=100)
        plan = g.govern(6, requested_concurrent=10)
        self.assertEqual(plan.concurrency, 4)   # 请求10被压到cap 4
        self.assertEqual(plan.total_steps, 6 * 6)

    def test_honors_lower_request(self):
        g = DelegationGovernor(max_concurrent=4)
        plan = g.govern(2, requested_concurrent=2)
        self.assertEqual(plan.concurrency, 2)

    def test_step_budget_scales_down(self):
        g = DelegationGovernor(total_step_budget=64, per_default_steps=6)
        plan = g.govern(10, requested_steps=10)   # 10*10=100 > 64
        self.assertEqual(plan.steps_each, 6)      # 64//10=6
        self.assertEqual(plan.total_steps, 60)
        self.assertTrue(plan.warnings)            # 有预算受限警告

    def test_batch_too_large_rejected(self):
        g = DelegationGovernor(max_batch=8)
        with self.assertRaises(DelegationLimitError):
            g.govern(9)

    def test_zero_tasks_rejected(self):
        with self.assertRaises(DelegationLimitError):
            DelegationGovernor().govern(0)

    def test_parallel_respects_governor_step_budget(self):
        # 大量任务×大步数被压低 → 每个子代理在压低后步数内截断
        g = DelegationGovernor(total_step_budget=8, per_default_steps=4)
        plan = g.govern(4, requested_steps=4)      # 4*4=16>8 → 每任务2步
        self.assertEqual(plan.steps_each, 2)

    def test_parallel_applies_governed_steps_even_with_explicit_max(self):
        # 回归：调用方显式传 max_steps 也不得绕过 governor 步预算（预算绕过 bug）
        import unittest.mock as mock
        from agent_body import delegation as dmod
        g = DelegationGovernor(total_step_budget=8, per_default_steps=4)
        captured = {}
        def fake_delegate(router, goal, context, **kw):
            captured["max_steps"] = kw.get("max_steps")
            return {"summary": goal, "steps": 0}
        router = mock.MagicMock()
        with mock.patch.object(dmod, "delegate_task", fake_delegate):
            dmod.parallel_delegate(
                router, [("g1", "c"), ("g2", "c"), ("g3", "c"), ("g4", "c")],
                governor=g, max_steps=4)   # 显式传4
        self.assertEqual(captured["max_steps"], 2)   # 受控步数仍被施加


class SessionScopedDispatchTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "main.txt").write_text("main-data", encoding="utf-8")

    def test_write_lands_in_session_sandbox(self):
        pol = _Policy(deny_writes=False, deny_exec=True)
        dispatch, sandbox = make_session_scoped_dispatch(
            pol, "sessA", self.tmp)
        out = dispatch("write_file", {"path": "out.txt", "content": "hi"})
        self.assertIn("已写入委派沙箱", out)
        self.assertTrue((sandbox / "out.txt").exists())
        # 主工作区根目录没有 out.txt（隔离，不污染）
        self.assertFalse((self.tmp / "out.txt").exists())
        # 不同会话沙箱不同
        _, sandboxB = make_session_scoped_dispatch(pol, "sessB", self.tmp)
        self.assertNotEqual(sandbox, sandboxB)

    def test_readonly_policy_denies_write_and_exec(self):
        pol = _Policy(deny_writes=True, deny_exec=True)
        dispatch, _ = make_session_scoped_dispatch(pol, "s", self.tmp)
        self.assertIn("权限拒绝", dispatch("write_file", {"path": "a", "content": "x"}))
        self.assertIn("权限拒绝", dispatch("exec", {"command": "rm -rf /"}))

    def test_path_escape_refused(self):
        pol = _Policy(deny_writes=False)
        dispatch, _ = make_session_scoped_dispatch(pol, "s", self.tmp)
        out = dispatch("write_file", {"path": "../escape.txt", "content": "x"})
        self.assertIn("逃出委派沙箱", out)
        self.assertFalse((self.tmp / "escape.txt").exists())

    def test_read_workspace_and_sandbox(self):
        pol = _Policy()
        dispatch, sandbox = make_session_scoped_dispatch(pol, "s", self.tmp)
        (sandbox / "note.txt").write_text("sandbox-data", encoding="utf-8")
        self.assertEqual(dispatch("read_file", {"path": "main.txt"}),
                         "main-data")
        self.assertEqual(dispatch("read_file", {"path": "note.txt"}),
                         "sandbox-data")

    def test_exec_is_isolated_not_run(self):
        pol = _Policy(deny_exec=False)   # 即使允许也不真执行
        dispatch, _ = make_session_scoped_dispatch(pol, "s", self.tmp)
        out = dispatch("exec", {"command": "whoami"})
        self.assertIn("已隔离", out)


if __name__ == "__main__":
    unittest.main()