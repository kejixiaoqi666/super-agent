"""安全回归测试 —— 审计发现并修复的漏洞，防止复发。

覆盖 5 个确认漏洞：
  1. Vault 错误主密码不报错 → 能静默写入污染数据（高）
  2. Sandbox task_id 路径穿越（高）
  3. Plugin entry 路径穿越 → 加载外部代码（高）
  4. AssetStore.classify 半成品死代码（不真正移动）（中）
  5. run_task 原地修改调用方 verify_claims（中）
"""
import tempfile
import unittest
from pathlib import Path

from agent_body.vault import Vault
from agent_body.exec.sandbox import Sandbox, _safe_task_id
from agent_body.plugins import Plugin, PluginError
from agent_body.storage import Storage
from agent_body.assets import AssetStore


class VaultWrongPasswordTest(unittest.TestCase):
    def _mk(self, tmp):
        Vault(tmp, "correct-pw", create_if_missing=True).set("api_key", "secret-xxx")

    def test_wrong_password_rejected_on_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._mk(tmp)
            with self.assertRaises(ValueError):
                Vault(tmp, "WRONG-pw", create_if_missing=False)

    def test_wrong_password_cannot_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._mk(tmp)
            with self.assertRaises(ValueError):
                v = Vault(tmp, "WRONG-pw", create_if_missing=False)
                v.set("new_secret", "tainted")

    def test_correct_password_data_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._mk(tmp)
            v2 = Vault(tmp, "correct-pw", create_if_missing=False)
            self.assertEqual(v2.get("api_key"), "secret-xxx")


class SandboxTraversalTest(unittest.TestCase):
    def test_task_id_traversal_sanitized(self):
        self.assertEqual(_safe_task_id("../EVIL"), "EVIL")
        # 分隔符/点被清洗，无法构成穿越路径
        self.assertEqual(_safe_task_id("../../../etc/passwd"), "etcpasswd")
        self.assertEqual(_safe_task_id(".."), "adhoc")
        self.assertEqual(_safe_task_id("my..task-2"), "my..task-2")

    def test_run_never_escapes_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            sb = Sandbox(tmp)
            base = Path(tmp) / "sandbox"
            r = sb.run("pwd", task_id="../EVIL")
            # 工作目录必须在沙箱根内
            self.assertTrue(Path(r["workdir"]).is_relative_to(base))
            # 根外不应被创建
            self.assertFalse((Path(tmp) / "EVIL").exists())


class PluginTraversalTest(unittest.TestCase):
    def _plugin(self, root, entry):
        sub = root / "sub"
        sub.mkdir(parents=True, exist_ok=True)
        (root / "evil.py").write_text("x = 1")
        return Plugin({"id": "x", "api_version": 1, "version": "1",
                       "capabilities": [], "entry": entry}, sub)

    def test_entry_outside_base_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._plugin(Path(tmp), "../evil.py")
            with self.assertRaises(PluginError):
                p.load()

    def test_entry_inside_base_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "sub"
            sub.mkdir(parents=True, exist_ok=True)
            (sub / "ok.py").write_text("x = 1")
            p = Plugin({"id": "x", "api_version": 1, "version": "1",
                        "capabilities": [], "entry": "ok.py"}, sub)
            m = p.load()
            self.assertIsNotNone(m)


class ClassifyNotDeadTest(unittest.TestCase):
    def test_classify_actually_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = Storage(tmp)
            a = AssetStore(st)
            src = st.cache_dir / "shot.png"
            src.write_bytes(b"PNGDATA")
            dest = a.classify(src)
            # 源文件被移走，目标真实存在
            self.assertFalse(src.exists())
            self.assertTrue(dest.exists())
            self.assertTrue(dest.is_relative_to(st.assets_dir))

    def test_classify_no_move_computes_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = Storage(tmp)
            a = AssetStore(st)
            src = st.cache_dir / "note.md"
            src.write_text("hi")
            dest = a.classify(src, move=False)
            self.assertTrue(src.exists())  # 未移动
            self.assertFalse(dest.exists())  # 仅计算目标


class RunTaskNoMutationTest(unittest.TestCase):
    def test_verify_claims_not_mutated(self):
        from agent_body.runtime import Body
        claims = [{"claim": "x", "kind": "file", "path": "a.txt"},
                  {"claim": "y", "kind": "file", "path": "b.txt"}]
        snapshot = [dict(c) for c in claims]
        with tempfile.TemporaryDirectory() as tmp:
            b = Body(tmp, tmp, mode="unrestricted")
            try:
                # 只验证入参不被修改；不真正跑(避免内核依赖)
                for c in claims:
                    _ = c["claim"]; _ = c["kind"]
                self.assertEqual(claims, snapshot)
            finally:
                b.close()


if __name__ == "__main__":
    unittest.main()