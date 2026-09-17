import tempfile
import unittest
from pathlib import Path

from agent_body.skill_compiler import SkillCompiler, default_type_of
from agent_body.skills import SkillStore


class _FakeBrain:
    def __init__(self):
        self.experiences = []
        self.distilled = []

    def record_experience(self, task, context="", outcome="", lesson=""):
        self.experiences.append({"task": task, "lesson": lesson})

    def distill_skill(self, name, procedure, success=True):
        self.distilled.append({"name": name, "procedure": procedure})


class SkillCompilerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = SkillStore(self.tmp / "skills")
        self.brain = _FakeBrain()
        self.compiler = SkillCompiler(self.store, threshold=2,
                                      persist_path=self.tmp / "counts.json")

    def test_records_experience_to_brain(self):
        self.compiler.on_task_success(self.brain, "重启节点服务",
                                      outcome="ok", steps=["查状态", "重启"])
        self.assertEqual(len(self.brain.experiences), 1)
        self.assertIn("1. 查状态", self.brain.experiences[0]["lesson"])

    def test_solidifies_after_threshold(self):
        r1 = self.compiler.on_task_success(self.brain, "重启节点服务",
                                           steps=["查状态", "重启"])
        r2 = self.compiler.on_task_success(self.brain, "重启节点服务",
                                           steps=["查状态", "重启"])
        self.assertFalse(r1["solidified"])
        self.assertTrue(r2["solidified"])
        self.assertEqual(r2["count"], 2)
        # 大脑蒸馏
        self.assertEqual(self.brain.distilled[0]["name"],
                         default_type_of("重启节点服务"))
        # 身体写出可执行 SKILL.md
        sk = self.store.get(default_type_of("重启节点服务"))
        self.assertIsNotNone(sk)
        self.assertIn("查状态", sk.body)

    def test_counts_persisted(self):
        self.compiler.on_task_success(self.brain, "查面板")
        c2 = SkillCompiler(self.store, threshold=2,
                           persist_path=self.tmp / "counts.json")
        self.assertEqual(c2.counts(), {"查面板": 1})

    def test_type_of_normalizes(self):
        self.assertEqual(default_type_of("  重启 节点  服务!  "),
                         default_type_of("重启节点服务"))
        self.assertNotEqual(default_type_of("查面板"), default_type_of("部署"))

    def test_custom_type_of(self):
        comp = SkillCompiler(self.store, threshold=1, type_of=lambda t: "always")
        comp.on_task_success(self.brain, "随便啥")
        self.assertEqual(comp.counts(), {"always": 1})


if __name__ == "__main__":
    unittest.main()
