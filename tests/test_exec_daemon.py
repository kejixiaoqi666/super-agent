"""执行 daemon 测试：多运行时 / 高并发批量 / 错误分类 / 内存上限 / 清洁性。

验证（设计第一原则：高并发·省资源·清洁）：
  1. shell/python/node 多运行时执行成功
  2. run_many 数千任务并发、保持输入顺序、全部完成
  3. 错误分类：timeout/syntax/deps/permission
  4. mem_limit 强制内存上限 (setrlimit)
  5. 清洁：非 keep 任务跑完无残留工作目录
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from agent_body.exec.daemon import ExecutorDaemon, Task


class ExecutorDaemonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.pool = 4

    def tearDown(self):
        self.tmp.cleanup()

    def _d(self, pool=None):
        return ExecutorDaemon(self.data, pool_size=pool or self.pool)

    # ---- 多运行时 ----
    def test_shell_runtime(self):
        with self._d() as d:
            r = d.execute(Task(runtime="shell", command="echo hi"))
            self.assertTrue(r.ok)
            self.assertEqual(r.code, 0)
            self.assertIn("hi", r.output)

    def test_python_runtime(self):
        with self._d() as d:
            r = d.execute(Task(runtime="python",
                               command="print(sum(range(100)))"))
            self.assertTrue(r.ok)
            self.assertIn("4950", r.output)

    def test_node_runtime(self):
        if not shutil.which("node"):
            self.skipTest("node 不可用")
        with self._d() as d:
            r = d.execute(Task(runtime="node", command="console.log(2+3)"))
            self.assertTrue(r.ok)
            self.assertIn("5", r.output)

    def test_unknown_runtime_rejected(self):
        with self.assertRaises(ValueError):
            Task(runtime="ruby", command="x")

    # ---- 批量高并发 + 保序 ----
    def test_run_many_keeps_order_and_all_done(self):
        n = 50
        with self._d() as d:
            tasks = [Task(runtime="python",
                          command=f"print({i}*{i})") for i in range(n)]
            results = d.run_many(tasks)
            self.assertEqual(len(results), n)
            self.assertTrue(all(r.ok for r in results), "全部成功")
            # 保序：第 i 个输出含 i*i
            for i, r in enumerate(results):
                self.assertIn(str(i * i), r.output)
            self.assertEqual(d.stats()["submitted"], n)
            self.assertEqual(d.stats()["done"], n)

    def test_run_many_thousands_light(self):
        # 数千个轻量任务并发，验证高吞吐不炸
        n = 1000
        with self._d() as d:
            results = d.run_many([Task(runtime="shell", command=":") for _ in range(n)])
            self.assertTrue(all(r.ok for r in results))
            self.assertEqual(len(results), n)

    # ---- 错误分类 ----
    def test_timeout_classified(self):
        with self._d() as d:
            r = d.execute(Task(runtime="shell", command="sleep 5",
                               timeout_s=0.3))
            self.assertEqual(r.status, "timeout")
            self.assertEqual(r.error_class, "timeout")

    def test_syntax_error_classified(self):
        with self._d() as d:
            r = d.execute(Task(runtime="python", command="def f(:"))
            self.assertFalse(r.ok)
            self.assertEqual(r.error_class, "syntax")

    def test_missing_module_classified_deps(self):
        with self._d() as d:
            r = d.execute(Task(runtime="python",
                               command="import nonexistent_module_xyz_123"))
            self.assertFalse(r.ok)
            self.assertEqual(r.error_class, "deps")

    def test_dangerous_command_permission(self):
        with self._d() as d:
            r = d.execute(Task(runtime="shell", command="rm -rf /etc"))
            self.assertFalse(r.ok)
            self.assertEqual(r.error_class, "permission")
            self.assertEqual(r.code, -1)

    # ---- 内存上限 ----
    def test_mem_limit_restrains(self):
        with self._d() as d:
            # 强制内存上限，让其分配大数组撑爆 → 归 error（resource/runtime 均可）
            r = d.execute(Task(runtime="python", mem_limit_mb=48,
                               command="data = [0]*50_000_000"))   # ~400MB, 超 48MB
            self.assertFalse(r.ok)
            self.assertIn(r.error_class, ("resource", "runtime", "unknown"))

    # ---- 清洁性 ----
    def test_no_residue_after_run(self):
        self._clean_tasks = []
        with self._d() as d:
            d.execute(Task(runtime="shell", command="touch a.txt"))
            d.execute(Task(runtime="python", command="print(1)"))
        # 非 keep：sandbox 根下应无残留任务目录
        sandbox_root = self.data / "sandbox"
        leftover = [p.name for p in sandbox_root.iterdir()] if sandbox_root.exists() else []
        self.assertEqual(leftover, [], f"残留目录: {leftover}")


class BodyRunScriptsTest(unittest.TestCase):
    """Body.run_scripts 批量并发执行 + 保持顺序 + 关闭回收 executor。"""

    def test_body_run_scripts(self):
        from agent_body.runtime import Body
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            body = Body(data, Path(td) / "work", mode="unrestricted")
            try:
                res = body.run_scripts(
                    [f"print({i}*{i})" for i in range(20)],
                    runtime="python")
                self.assertEqual(len(res), 20)
                self.assertTrue(all(r["status"] == "done" for r in res))
                # 保序
                outs = [r["output"] for r in res]
                for i in range(20):
                    self.assertIn(str(i * i), outs[i])
                self.assertIsNotNone(body._executor)
            finally:
                body.close()
                self.assertIsNone(body._executor)   # executor 随 Body 回收


if __name__ == "__main__":
    unittest.main()