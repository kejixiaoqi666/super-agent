import tempfile
import unittest
from pathlib import Path

from agent_body.handover import build_briefing, write_handover


class _FakeBrain:
    def __init__(self):
        self.remembered = []
        self.results = []

    def remember(self, content, tags=None, importance=0.5, tier="recall"):
        self.remembered.append({"content": content, "tags": tags,
                                "importance": importance, "tier": tier})

    def recall(self, query, k=5):
        return list(self.results)


def _mk_store():
    from agent_body.skills import SkillStore
    s = SkillStore(Path(tempfile.mkdtemp()) / "skills")
    return s


class HandoverTest(unittest.TestCase):
    def test_write_handover_uses_handover_tag_and_high_importance(self):
        brain = _FakeBrain()
        write_handover(brain, "正在跑面板巡检, 下一步部署")
        self.assertEqual(len(brain.remembered), 1)
        r = brain.remembered[0]
        self.assertIn("handover", r["tags"])
        self.assertEqual(r["importance"], 1.0)
        self.assertEqual(r["tier"], "recall")

    def test_write_empty_skipped(self):
        brain = _FakeBrain()
        write_handover(brain, "")
        write_handover(brain, "   ")
        self.assertEqual(brain.remembered, [])

    def test_briefing_includes_project_and_memory(self):
        brain = _FakeBrain()
        brain.results = [{"content": "上次交接: 在部署节点", "score": 0.8, "why": "x"},
                         {"content": "待办: 重启服务", "score": 0.6, "why": "x"}]
        b = build_briefing(brain, "当前项目: 机场运维")
        self.assertIn("当前项目: 机场运维", b)
        self.assertIn("上次交接", b)
        self.assertIn("待办: 重启服务", b)

    def test_briefing_includes_skills(self):
        brain = _FakeBrain()
        store = _mk_store()
        store.upsert("ops-a", "desc", "body")
        store.upsert("ops-b", "desc", "body")
        b = build_briefing(brain, "", skills_store=store)
        self.assertIn("已有技能", b)
        self.assertIn("ops-a", b)
        self.assertIn("ops-b", b)

    def test_briefing_empty_no_memory(self):
        brain = _FakeBrain()  # recall 空
        b = build_briefing(brain, "")
        self.assertEqual(b, "")  # 无项目/记忆/技能

    def test_briefing_dedup_memory(self):
        brain = _FakeBrain()
        brain.results = [{"content": "同一段", "score": 1},
                         {"content": "同一段", "score": 0.9}]
        b = build_briefing(brain, "")
        self.assertEqual(b.count("同一段"), 1)


if __name__ == "__main__":
    unittest.main()