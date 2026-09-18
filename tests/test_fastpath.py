"""轻量快通道测试：多信号启发式 —— 简单秒回(不经过大脑), 复杂走超脑。"""

import unittest

from agent_body.fastpath import fast_reply


class FastPathTest(unittest.TestCase):
    def test_greeting_instant_no_llm(self):
        for g in ("你好", "hi", "Hello", "在吗", "早上好"):
            r = fast_reply(g)
            assert r is not None
            self.assertIn("超脑", r)

    def test_thanks_ack(self):
        for s in ("谢谢", "好的", "ok", "明白了"):
            self.assertIsNotNone(fast_reply(s))

    def test_arithmetic_computed_locally(self):
        self.assertEqual(fast_reply("1+1="), "= 2")
        self.assertEqual(fast_reply("10 / 4 ="), "= 2.5")

    def test_time_local(self):
        r = fast_reply("现在几点")
        self.assertIsNotNone(r)

    def test_short_noise(self):
        self.assertIsNotNone(fast_reply("哦"))
        self.assertIsNotNone(fast_reply("..."))

    def test_real_request_goes_to_brain(self):
        # 复杂/真实/开放请求 → None, 交给超脑（不硬答）
        self.assertIsNone(fast_reply("帮我分析一下这几个IP的风险"))
        self.assertIsNone(fast_reply("部署一个nginx并配置https"))
        self.assertIsNone(fast_reply("写一首关于春天的诗"))
        self.assertIsNone(fast_reply("为什么我的机场订阅连不上"))
        self.assertIsNone(fast_reply("对比一下TikTok和抖音的差异"))

    def test_empty(self):
        self.assertIsNone(fast_reply(""))


if __name__ == "__main__":
    unittest.main()