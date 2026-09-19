"""功能开关层测试：默认 auto(可用·路由按需启用), on/off 强制覆盖。"""

import tempfile
import unittest
from pathlib import Path

from agent_body import config


class FeatureFlagsTest(unittest.TestCase):
    def test_all_default_auto(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td) / ".env"
            env.write_text("", encoding="utf-8")
            f = config.features(env)
            # 默认 auto：功能可用、不废掉，由路由按需启用
            self.assertTrue(all(v == "auto" for v in f.values()))
            for name in config.FEATURE_DEFAULTS:
                self.assertEqual(config.feature_state(name, env), "auto")
                # auto ≠ off → 视为可用
                self.assertTrue(config.feature_enabled(name, env))

    def test_force_off_is_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td) / ".env"
            env.write_text("FEATURE_SELF_EVOLUTION=0\n", encoding="utf-8")
            self.assertEqual(config.feature_state("self_evolution", env), "off")
            self.assertFalse(config.feature_enabled("self_evolution", env))
            # 其它仍 auto 可用
            self.assertTrue(config.feature_enabled("sovereign", env))

    def test_force_on_and_off(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td) / ".env"
            env.write_text("FEATURE_SELF_OPS=1\nFEATURE_ROUTER=0\n", encoding="utf-8")
            self.assertEqual(config.feature_state("self_ops", env), "on")
            self.assertEqual(config.feature_state("router", env), "off")
            self.assertEqual(config.feature_state("streaming", env), "auto")


if __name__ == "__main__":
    unittest.main()