import unittest

from agent_body.hooks import (HookError, HookRegistry, make_pre_tool_hook)


class HookRegistryTest(unittest.TestCase):
    def setUp(self):
        self.reg = HookRegistry()

    def test_register_and_dispatch_collects(self):
        seen = {}
        self.reg.register("pre_tool", lambda **c: seen.update(c) or {"ok": 1})
        res = self.reg.dispatch("pre_tool", tool="ls", args={})
        self.assertEqual(seen["tool"], "ls")
        self.assertEqual(res, [{"ok": 1}])

    def test_unknown_event_register_raises(self):
        with self.assertRaises(HookError):
            self.reg.register("no_such", lambda: None)

    def test_unknown_event_dispatch_raises(self):
        with self.assertRaises(HookError):
            self.reg.dispatch("no_such")

    def test_non_callable_handler_raises(self):
        with self.assertRaises(HookError):
            self.reg.register("on_stop", 42)

    def test_hook_exception_does_not_break_flow(self):
        order = []
        self.reg.register("post_tool", lambda **c: (_ for _ in ()).throw(RuntimeError("boom")))
        self.reg.register("post_tool", lambda **c: order.append("ok") or {"done": 1})
        res = self.reg.dispatch("post_tool", tool="x", args={})
        self.assertEqual(order, ["ok"])
        self.assertEqual(res, [{"done": 1}])

    def test_fired_counter(self):
        self.assertEqual(self.reg.fired("on_stop"), 0)
        self.reg.dispatch("on_stop")
        self.assertEqual(self.reg.fired("on_stop"), 1)
        self.reg.dispatch("on_stop")
        self.assertEqual(self.reg.fired("on_stop"), 2)

    def test_handlers_count_and_clear(self):
        self.reg.register("on_message", lambda **c: None)
        self.reg.register("on_message", lambda **c: None)
        self.assertEqual(self.reg.handlers("on_message"), 2)
        self.reg.clear("on_message")
        self.assertEqual(self.reg.handlers("on_message"), 0)

    def test_all_valid_events(self):
        for e in ("pre_tool", "post_tool", "on_stop", "on_error",
                  "on_message", "subagent_stop"):
            self.reg.register(e, lambda **c: None)
            self.reg.dispatch(e)


class PreToolHookTest(unittest.TestCase):
    def test_make_pre_tool_hook(self):
        calls = []

        def pre(tool, args):
            calls.append((tool, args))
            return "blocked"
        hook = make_pre_tool_hook(pre)
        out = hook(tool="shell", args={"command": "rm -rf"})
        self.assertEqual(calls, [("shell", {"command": "rm -rf"})])
        self.assertEqual(out, {"tool": "shell", "pre": "blocked"})

    def test_hook_no_tool_returns_none(self):
        hook = make_pre_tool_hook(lambda t, a: "x")
        self.assertIsNone(hook())


if __name__ == "__main__":
    unittest.main()