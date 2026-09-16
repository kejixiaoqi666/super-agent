"""Brain Port 孔位层专项测试：契约版本保护 + 内核即插即用 + 内核缺失优雅降级。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "superbrain-2.0" / "python"))

from agent_body.kernel import (BRAIN_PORT_VERSION, BrainPort, BrainRegistry,
                               default_registry, SuperBrainAdapter)


class FabricatedKernel(BrainPort):
    """假内核：实现 BrainPort 契约的替身，验证「换内核 agent 主体零改动」。"""

    def __init__(self, data_dir, session="x", llm=None, tag="fake"):
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self.tag = tag
        self._saved = None

    # ---- 契约实现：最小可运行的假实现 ----
    def chat(self, message, person_id=None): return f"[{self.tag}] {message}"
    def remember(self, content, scope="user", tier="recall", **kw): return "fake-id"
    def recall(self, query, k=5): return []
    def save(self): self._saved = "saved"; return "ok"
    def load(self): return True
    def close(self): pass
    def state(self): return {"tag": self.tag}
    def personality(self): return {}
    def set_personality(self, dimension, value): return True
    def personality_mode(self): return "guided"
    def set_personality_mode(self, mode): return True
    def apply_style(self, style): return []
    def style_text(self): return ""
    def humanize(self, text, person_id=None): return text
    def set_user_style(self, person_id, style): return True
    def user_style(self, person_id): return ""
    def tick(self): return {"thoughts": []}
    def generate_thoughts(self): return []
    def drain_thoughts(self): return []
    def generate_goals(self): return []
    def adopt_goals(self): return []
    def autonomous_goals(self): return []
    def memory_count(self): return 0
    def index_concepts(self, limit=20): return 0
    def deduplicate(self, threshold=0.85): return 0
    def orientations(self): return {}
    def user_profile(self, person_id): return None
    def attach_tools(self, tools): pass
    def set_permissions(self, policy): pass
    def ready(self): return True
    def version(self): return "fake-1.0"
    def health(self): return {"ok": True, "kernel": "fake"}


class BrokenAdapter(BrainPort):
    """缺失 agent 属性的异常内核：注册应阻止（实现契约才收）。"""


class BrainPortTests(unittest.TestCase):
    def setUp(self):
        self._saved_classes = dict(default_registry()._classes)
        # 清空注册表，避免污染其它测试；再重新注册默认内核，保持测试自足
        default_registry()._classes.clear()
        from agent_body._bootstrap import ensure_default_registered
        ensure_default_registered()

    def tearDown(self):
        default_registry()._classes.clear()
        default_registry()._classes.update(self._saved_classes)

    def test_register_requires_brainport_subclass(self):
        reg = BrainRegistry()
        with self.assertRaises(TypeError):
            reg.register("broken", object)  # 非 BrainPort 拒收
        with self.assertRaises(TypeError):
            reg.register("broken2", BrokenAdapter)  # 不完整实现拒收

    def test_register_rejects_port_version_mismatch(self):
        reg = BrainRegistry()
        # 模拟大脑接口演进：契约版本升级了，旧内核未跟上 → 拒绝，而非静默崩
        with self.assertRaises(ValueError):
            reg.register("superbrain", SuperBrainAdapter,
                         port_version=BRAIN_PORT_VERSION + 1)

    def test_fabricated_kernel_plug_and_play(self):
        # 换内核 = 注册新适配器；agent 主体（通过契约调用）无需改动
        reg = BrainRegistry()
        reg.register("fake", FabricatedKernel)
        with tempfile.TemporaryDirectory() as temp:
            k = reg.build("fake", data_dir=temp)
            self.assertIsInstance(k, BrainPort)      # agent 只认契约
            self.assertTrue(k.ready())
            self.assertEqual(k.chat("hi"), "[fake] hi")  # 契约方法调用
            k.close()

    def test_default_registry_has_superbrain(self):
        # bootstrap 后默认注册表应含 superbrain 内核
        self.assertTrue(default_registry().has("superbrain"))

    def test_build_passes_params_per_instance(self):
        # 不同 session 各自独立实例（隔离不被闭包破坏）
        reg = BrainRegistry()
        reg.register("fake", FabricatedKernel)
        with tempfile.TemporaryDirectory() as temp:
            a = reg.build("fake", data_dir=temp, session="a")
            b = reg.build("fake", data_dir=temp, session="b")
            self.assertIsNot(a, b)

    def test_build_unknown_kernel_raises(self):
        reg = BrainRegistry()
        with self.assertRaises(KeyError):
            reg.build("ghost")


if __name__ == "__main__":
    unittest.main()