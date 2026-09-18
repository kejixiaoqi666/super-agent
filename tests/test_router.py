"""路由测试：判断是否需要过超脑(direct/brain)。"""

import unittest

from agent_body.router import Router


class _FakeLLM:
    def __init__(self, content):
        self._c = content

    def chat(self, messages, max_tokens=160):
        from superbrain2.core.llm import LLMResponse
        return LLMResponse(self._c, 0, 0)


class RouterTest(unittest.TestCase):
    def _r(self, content):
        return Router(_FakeLLM(content))

    def test_direct_when_not_need_brain(self):
        r = self._r('{"route":"direct","answer":"因为太阳落山了。"}')
        route, ans = r.classify("为什么天黑了")
        self.assertEqual(route, "direct")
        self.assertIn("太阳", ans)

    def test_brain_when_need_brain(self):
        r = self._r('{"route":"brain"}')
        route, ans = r.classify("查一下这个IP 1.2.3.4")
        self.assertEqual(route, "brain")

    def test_bad_json_defaults_brain_safe(self):
        r = self._r("随便说点什么，不是 JSON")
        route, ans = r.classify("abc")
        self.assertEqual(route, "brain")

    def test_direct_without_answer_defaults_brain(self):
        r = self._r('{"route":"direct"}')     # 有 route 但无 answer → 安全走 brain
        route, _ = r.classify("x")
        self.assertEqual(route, "brain")

    def test_escaped_answer_unquotes(self):
        r = self._r('{"route":"direct","answer":"他说：\\"你好\\"没问题"}')
        route, ans = r.classify("x")
        self.assertEqual(route, "direct")
        self.assertIn('"你好"', ans)


if __name__ == "__main__":
    unittest.main()
