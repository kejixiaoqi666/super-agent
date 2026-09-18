"""主权开放架构 阶段① 测试：插件自由区(随装随卸即净) + 内核只读门。

验证：
  1. 插件安装/列出/动态加载执行
  2. 坏插件只影响自身，不污染 registry
  3. 卸载即净（目录消失）
  4. 插件文件路径穿越被拦
  5. 内核只读门：内核区写被拒并提示走升级队列(非静默)，插件区放行
"""

import tempfile
import unittest
from pathlib import Path

from agent_body.sovereign import Plugin, Sovereign


class PluginRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Sovereign(Path(self.tmp.name) / "data")

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_list_run(self):
        p = Plugin(name="点赞助手", category="tool",
                   files={"main.py": "def main(): return '点赞成功'"},
                   description="自动点赞小工具")
        self.s.install_plugin(p)
        self.assertIn("点赞助手", self.s.list_plugins())
        r = self.s.run_plugin("点赞助手")
        self.assertTrue(r["ok"])
        self.assertEqual(r["result"], "点赞成功")

    def test_run_custom_fn_and_args(self):
        p = Plugin(name="calc", files={
            "main.py": "def add(a, b): return a + b"})
        self.s.install_plugin(p)
        r = self.s.run_plugin("calc", "add", 3, 4)
        self.assertTrue(r["ok"])
        self.assertEqual(r["result"], 7)

    def test_bad_plugin_isolated(self):
        # 坏插件（语法错）运行失败，但 registry 不受影响
        p = Plugin(name="bad", files={"main.py": "def main(: syntax error"})
        self.s.install_plugin(p)
        r = self.s.run_plugin("bad")
        self.assertFalse(r["ok"])
        self.assertIn("error", r)
        # registry 仍正常
        self.assertIn("bad", self.s.list_plugins())
        good = Plugin(name="good", files={"main.py": "def main(): return 1"})
        self.s.install_plugin(good)
        self.assertTrue(self.s.run_plugin("good")["ok"])

    def test_uninstall_cleans_dir(self):
        p = Plugin(name="tmp", files={"main.py": "def main(): return 1"})
        self.s.install_plugin(p)
        d = self.s.plugins._dir("tmp")
        self.assertTrue(d.exists())
        res = self.s.uninstall_plugin("tmp")
        self.assertTrue(res["ok"])
        self.assertFalse(d.exists())          # 即卸即净
        self.assertNotIn("tmp", self.s.list_plugins())

    def test_reinstall_overwrites_no_residue(self):
        p1 = Plugin(name="x", files={"main.py": "def main(): return 1",
                                     "old.py": "x"})
        self.s.install_plugin(p1)
        p2 = Plugin(name="x", files={"main.py": "def main(): return 2"})
        self.s.install_plugin(p2)             # 覆盖安装，旧文件清掉
        d = self.s.plugins._dir("x")
        self.assertFalse((d / "old.py").exists())
        self.assertEqual(self.s.run_plugin("x")["result"], 2)

    def test_plugin_path_traversal_blocked(self):
        p = Plugin(name="evil", files={"../escape.py": "x"})
        with self.assertRaises(ValueError):
            self.s.install_plugin(p)


class KernelGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.s = Sovereign(self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def test_kernel_write_denied_with_upgrade_hint(self):
        res = self.s.check_write(str(Path("agent_body/budget.py")))
        self.assertFalse(res["allowed"])
        self.assertEqual(res["zone"], "kernel")
        self.assertIn("升级", res["reason"])   # 提示走升级队列，非静默失败

    def test_plugin_zone_allowed(self):
        res = self.s.check_write(str(self.data / "plugins" / "new" / "main.py"))
        self.assertTrue(res["allowed"])
        self.assertEqual(res["zone"], "plugin")

    def test_data_core_readonly(self):
        # 数据目录下、非插件区 → 核心数据只读
        res = self.s.check_write(str(self.data / "budget.json"))
        self.assertFalse(res["allowed"])
        self.assertEqual(res["zone"], "kernel")

    def test_free_zone_allowed(self):
        res = self.s.check_write("/tmp/anything")
        self.assertTrue(res["allowed"])
        self.assertEqual(res["zone"], "free")


if __name__ == "__main__":
    unittest.main()
