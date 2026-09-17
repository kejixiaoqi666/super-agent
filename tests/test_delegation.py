import unittest

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
        r = _Scripted([{"content": "", "tool_calls": _tool_call("ls", "{}")},
                       {"content": "done", "tool_calls": []}])
        out = delegate_task(r, "x", tool_dispatch=bad_dispatch,
                            tools=({"name": "ls"},))
        self.assertEqual(out["summary"], "done")  # 工具错误回传，子代理继续
        # 验证工具错误进入了后续请求
        last = r.calls[-1]
        tool_msgs = [m for m in last.messages if m["role"] == "tool"]
        self.assertIn("tool boom", tool_msgs[0]["content"])


if __name__ == "__main__":
    unittest.main()