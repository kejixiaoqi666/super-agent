"""config + stats 单元测试：.env 读写/权限/打码、token 计费统计。"""
import os
import tempfile
import unittest
from pathlib import Path

from agent_body import config as cfg
from agent_body.stats import TokenStats


class ConfigTest(unittest.TestCase):
    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            cfg.save({"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_ALLOWED_USERS": "111"},
                     env_path=env)
            loaded = cfg.load(env_path=env)
            self.assertEqual(loaded["TELEGRAM_BOT_TOKEN"], "123:abc")
            self.assertEqual(loaded["TELEGRAM_ALLOWED_USERS"], "111")

    def test_env_file_permission_0600(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            cfg.save({"TELEGRAM_BOT_TOKEN": "secret"}, env_path=env)
            self.assertEqual(env.stat().st_mode & 0o777, 0o600)

    def test_mask_hides_value(self):
        m = cfg.mask("1234567890ABCDEF")
        self.assertNotIn("1234", m[4:])  # 中间被掩盖
        self.assertEqual(len(m), len("1234567890ABCDEF"))

    def test_env_override_envvar(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            cfg.save({"TELEGRAM_ALLOWED_USERS": "999"}, env_path=env)
            os.environ["TELEGRAM_ALLOWED_USERS"] = "777"
            try:
                loaded = cfg.load(env_path=env)
                self.assertEqual(loaded["TELEGRAM_ALLOWED_USERS"], "777")
            finally:
                del os.environ["TELEGRAM_ALLOWED_USERS"]

    def test_status_report_no_secret_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            cfg.save({"TELEGRAM_BOT_TOKEN": "12345678ABCDEFGH"}, env_path=env)
            report = cfg.status_report()
            self.assertNotIn("ABCDEFGH", report["TELEGRAM_BOT_TOKEN"]["value"])


class StatsTest(unittest.TestCase):
    def test_record_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = TokenStats(tmp)
            s.record("a", "你好世界", "回复", model="deepseek-v4-flash")
            s.record("a", "第二句", "回复内容", model="deepseek-v4-flash")
            sm = s.summary()
            self.assertEqual(sm["conversations"], 2)
            self.assertGreater(sm["total_tokens"], 0)
            self.assertIn("by_day", sm)

    def test_persistence_and_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = TokenStats(tmp)
            s.record("a", "hi", "reply", model="deepseek-v4-flash")
            path = Path(tmp) / "token_stats.json"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            # 重新加载
            s2 = TokenStats(tmp)
            self.assertEqual(len(s2._rows), 1)

    def test_cost_calculated(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = TokenStats(tmp)
            r = s.record("a", "x" * 4000, "y" * 1000, model="deepseek-v4-flash")
            self.assertGreater(r["prompt_tokens"], 0)
            self.assertGreater(r["total_tokens"], 0)


if __name__ == "__main__":
    unittest.main()