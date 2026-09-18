"""Autopilot 自动操作测试：Driver 契约 + 调度 + 文本定位。

单元层（无浏览器）：用 stub Driver 验证统一接口、懒加载、click_text 定位逻辑。
真实浏览器 E2E（挖 DOM 布局/点击）单独跑 test_autopilot_e2e.py（需 playwright）。
"""

import unittest

from agent_body.autopilot import Autopilot, Driver, Element


class _StubDriver(Driver):
    """可编程 stub：describe 返回预设元素，act 记录动作。"""
    def __init__(self, elements):
        self.elements = elements
        self.acts = []

    def describe(self, text=""):
        return [e for e in self.elements
                if not text or text.lower() in (e.text or "").lower()]

    def act(self, action):
        self.acts.append(action)
        return {"op": action.get("op"), "ok": True}


class _StubAutopilot(Autopilot):
    def __init__(self, elements):
        super().__init__()
        self._elements = elements

    def driver(self, target="browser"):
        # 覆盖：返回 stub 而非真实浏览器后端（保持缓存语义一致）
        if target not in self._drivers:
            self._drivers[target] = _StubDriver(self._elements)
        self._last = target
        return self._drivers[target]


class DriverContractTest(unittest.TestCase):
    def test_find_by_text(self):
        d = _StubDriver([Element(0, tag="button", text="点赞", x=1, y=2)])
        el = d.find("点赞")
        assert el is not None
        self.assertEqual(el.tag, "button")
        self.assertIsNone(d.find("不存在"))   # 找不到不猜

    def test_describe_filter(self):
        d = _StubDriver([Element(0, text="A"), Element(1, text="B")])
        got = d.describe("b")
        self.assertEqual([e.text for e in got], ["B"])


class AutopilotDispatchTest(unittest.TestCase):
    def test_click_text_locates_and_acts(self):
        ap = _StubAutopilot([Element(0, tag="button", text="点赞", x=10, y=20)])
        res = ap.click_text("browser", "点赞")
        self.assertTrue(res["ok"])
        d = ap.driver("browser")
        assert isinstance(d, _StubDriver)
        self.assertEqual(d.acts[0]["op"], "click")
        self.assertEqual(d.acts[0]["index"], 0)

    def test_click_text_missing_returns_error_not_guess(self):
        ap = _StubAutopilot([])
        res = ap.click_text("browser", "不存在")
        self.assertFalse(res["ok"])
        self.assertIn("未找到", res["error"])

    def test_describe_delegates(self):
        ap = _StubAutopilot([Element(0, text="hi")])
        rows = ap.describe("browser", text="hi")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "hi")

    def test_unknown_backend_rejected(self):
        ap = Autopilot()
        with self.assertRaises(ValueError):
            ap.driver("nope")


if __name__ == "__main__":
    unittest.main()