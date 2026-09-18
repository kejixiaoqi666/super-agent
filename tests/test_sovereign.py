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


class SelfModTest(unittest.TestCase):
    """阶段② 自我修改：限定插件层 + 留痕 + 可回滚 + 内核拒绝。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.s = Sovereign(self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_edit_remove_plugin_trailed(self):
        sm = self.s.self_mod
        r = sm.add_plugin("算数", {"main.py": "def main(): return 1"})
        self.assertTrue(r["ok"])
        self.assertEqual(self.s.run_plugin("算数")["result"], 1)
        # 编辑
        r = sm.edit_plugin("算数", {"main.py": "def main(): return 99"})
        self.assertTrue(r["ok"])
        self.assertEqual(self.s.run_plugin("算数")["result"], 99)
        self.assertEqual(len(sm.trail()), 2)

    def test_kernel_write_rejected_not_silent(self):
        sm = self.s.self_mod
        # 尝试写内核（本项目 agent_body/budget.py 相对 cwd）
        res = sm.write_managed("agent_body/budget.py", "HACK")
        self.assertFalse(res["allowed"])
        self.assertIn("升级", res["reason"])     # 提示走升级队列
        self.assertEqual(len(sm.trail()), 0)      # 被拒不留痕

    def test_write_managed_then_rollback(self):
        sm = self.s.self_mod
        target = Path(self.tmp.name) / "scratch" / "idea.txt"   # free 区可写
        sm.write_managed(str(target), "v1")
        self.assertEqual(target.read_text(), "v1")
        sm.write_managed(str(target), "v2")
        self.assertEqual(target.read_text(), "v2")
        res = sm.restore_last_good(1)
        self.assertEqual(res["undone"], 1)
        self.assertEqual(target.read_text(), "v1")   # 回滚到 v1

    def test_data_dir_core_write_rejected(self):
        # 数据目录下、非插件 → 核心数据只读，写被拒并提示升级
        sm = self.s.self_mod
        res = sm.write_managed(str(self.data / "notes" / "idea.txt"), "x")
        self.assertFalse(res["allowed"])
        self.assertEqual(res["zone"], "kernel")

    def test_rollback_plugin_edit(self):
        sm = self.s.self_mod
        sm.add_plugin("p", {"main.py": "def main(): return 'A'"})
        sm.edit_plugin("p", {"main.py": "def main(): return 'B'"})
        self.assertEqual(self.s.run_plugin("p")["result"], "B")
        sm.restore_last_good(1)   # 撤销 edit → 回 A
        self.assertEqual(self.s.run_plugin("p")["result"], "A")
        sm.restore_last_good(1)   # 撤销 add → 插件没了
        self.assertNotIn("p", self.s.list_plugins())

    def test_remove_plugin_rollback_restores(self):
        sm = self.s.self_mod
        sm.add_plugin("keep", {"main.py": "def main(): return 'keep'"})
        sm.remove_plugin("keep")
        self.assertNotIn("keep", self.s.list_plugins())
        sm.restore_last_good(1)
        self.assertIn("keep", self.s.list_plugins())
        self.assertEqual(self.s.run_plugin("keep")["result"], "keep")


class UpgradeQueueTest(unittest.TestCase):
    """阶段④ 升级治理：文档存档→门禁→测试→并入，不可跳过。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.uq = Sovereign(Path(self.tmp.name) / "data").upgrade_queue

    def tearDown(self):
        self.tmp.cleanup()

    def test_submit_persists_doc_and_code_doc(self):
        u = self.uq.submit(
            title="执行daemon提速", rationale="批量1000任务可更高效",
            code_doc="def _execute(): ...", files_impacted=["exec/daemon.py"],
            risk="medium")
        self.assertEqual(u["state"], "submitted")
        self.assertEqual(u["code_doc"], "def _execute(): ...")
        self.assertEqual(len(self.uq.pending()), 1)     # 排队待门禁

    def test_submit_requires_code_doc(self):
        with self.assertRaises(ValueError):
            self.uq.submit(title="x", rationale="y", code_doc="   ")

    def test_cannot_merge_without_gate(self):
        u = self.uq.submit(title="t", rationale="r", code_doc="cd")
        merged = []
        res = self.uq.merge(u["uid"], lambda up: merged.append(up))
        self.assertFalse(res["ok"])
        self.assertIn("门禁", res["error"])

    def test_merge_requires_tested(self):
        u = self.uq.submit(title="t", rationale="r", code_doc="cd")
        self.uq.approve(u["uid"])
        # approved 直接 merge（没测试）→ 拒绝
        res = self.uq.merge(u["uid"], lambda up: None)
        self.assertFalse(res["ok"])
        self.assertIn("测试", res["error"])

    def test_full_flow_submit_approve_test_merge(self):
        u = self.uq.submit(title="t", rationale="r", code_doc="cd")
        merged = []
        self.assertTrue(self.uq.approve(u["uid"])["ok"])
        self.assertTrue(self.uq.mark_tested(u["uid"])["ok"])
        res = self.uq.merge(u["uid"], lambda up: merged.append(up))
        self.assertTrue(res["ok"])
        self.assertEqual(res["state"], "merged")
        self.assertEqual(len(merged), 1)     # 真实并入被调用
        # 已并入不可再 merge
        res2 = self.uq.merge(u["uid"], lambda up: None)
        self.assertFalse(res2["ok"])

    def test_reject_then_no_merge(self):
        u = self.uq.submit(title="t", rationale="r", code_doc="cd")
        self.uq.reject(u["uid"])
        res = self.uq.merge(u["uid"], lambda up: None)
        self.assertFalse(res["ok"])


class TrustModeTest(unittest.TestCase):
    """阶段⑥ 信任模式：guided/sovereign 切换 + 持久化 + 硬约束不因 mode 改变。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.s = Sovereign(self.data)
        self.t = self.s.trust

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_guided(self):
        self.assertEqual(self.t.get(), "guided")
        self.assertTrue(self.t.is_guided())
        self.assertFalse(self.t.is_sovereign())

    def test_switch_and_persist(self):
        self.assertTrue(self.t.set("sovereign")["ok"])
        self.assertEqual(self.t.get(), "sovereign")
        self.assertTrue(self.t.is_sovereign())
        # 新建实例读回持久化
        t2 = Sovereign(self.data).trust
        self.assertEqual(t2.get(), "sovereign")

    def test_unknown_mode_rejected(self):
        res = self.t.set("anarchy")
        self.assertFalse(res["ok"])

    def test_hard_constraints_not_mode_dependent(self):
        # 无论 guided 还是 sovereign，内核只读 + 底层批准都不变
        for mode in ("guided", "sovereign"):
            self.t.set(mode)
            au = self.t.autonomy()
            self.assertTrue(au["kernel_readonly"])
            self.assertTrue(au["base_requires_approval"])
            self.assertTrue(au["upper_autonomous"])
            # 内核写仍被 gate 拒
            res = self.s.check_write(str(Path("agent_body/budget.py")))
            self.assertFalse(res["allowed"])
            self.assertEqual(res["zone"], "kernel")


if __name__ == "__main__":
    unittest.main()
