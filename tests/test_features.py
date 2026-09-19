"""功能开关层测试：能力保留·默认收敛。"""

import tempfile
import unittest
from pathlib import Path

from agent_body import config


class FeatureFlagsTest(unittest.TestCase):
    def test_all_default_off(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td) / ".env"
            env.write_text("", encoding="utf-8")
            f = config.features(env)
            self.assertTrue(all(v is False for v in f.values()))
            for name in config.FEATURE_DEFAULTS:
                self.assertFalse(config.feature_enabled(name, env))

    def test_enable_via_env(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td) / ".env"
            env.write_text("FEATURE_SELF_OPS=1\nFEATURE_ROUTER=on\n", encoding="utf-8")
            f = config.features(env)
            self.assertTrue(f["self_ops"])
            self.assertTrue(f["router"])
            self.assertFalse(f["sovereign"])
            self.assertFalse(f["self_evolution"])


if __name__ == "__main__":
    unittest.main()
