import tempfile
from pathlib import Path
import unittest

from agent_body.skills import SkillStore, _parse_frontmatter


def _write(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


class ParseFrontmatterTest(unittest.TestCase):
    def test_no_frontmatter(self):
        fm, body = _parse_frontmatter("hello world")
        self.assertEqual(fm, {})
        self.assertEqual(body, "hello world")

    def test_standard(self):
        text = "---\nname: foo\ndescription: 做某某\nversion: 1.0.0\n---\n# 正文\n步骤"
        fm, body = _parse_frontmatter(text)
        self.assertEqual(fm["name"], "foo")
        self.assertEqual(fm["description"], "做某某")
        self.assertEqual(fm["version"], "1.0.0")
        self.assertIn("步骤", body)

    def test_missing_fields_ok(self):
        fm, body = _parse_frontmatter("---\nname: bar\n---\nbody")
        self.assertEqual(fm, {"name": "bar"})
        self.assertEqual(body, "body")


class SkillStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_discovers_nested(self):
        _write(self.root, "a/SKILL.md", "---\nname: alpha\ndescription: 甲\n---\n内容")
        _write(self.root, "x/y/b/SKILL.md", "---\nname: beta\n---\n乙")
        s = SkillStore(self.root)
        self.assertEqual(set(s.names()), {"alpha", "beta"})
        self.assertEqual(s.get("alpha").description, "甲")
        self.assertEqual(s.get("beta").name, "beta")

    def test_dedup_first_root_wins(self):
        r1 = self.root / "r1"; r2 = self.root / "r2"
        _write(r1, "s/SKILL.md", "---\nname: same\ndescription: 一\n---\n")
        _write(r2, "s/SKILL.md", "---\nname: same\ndescription: 二\n---\n")
        s = SkillStore(r1, r2)
        self.assertEqual(len(s.list()), 1)
        self.assertEqual(s.get("same").description, "一")  # r1 优先

    def test_fallback_name_to_dir(self):
        _write(self.root, "my-skill/SKILL.md", "---\ndescription: 无name\n---\nbody")
        s = SkillStore(self.root)
        self.assertIn("my-skill", s.names())

    def test_skips_malformed_missing(self):
        _write(self.root, "ok/SKILL.md", "---\nname: ok\n---\n")
        _write(self.root, "empty/SKILL.md", "")          # 空 → name 回退目录
        _write(self.root, "notskill.txt", "---\nname: nope\n---\n")
        s = SkillStore(self.root)
        self.assertIn("ok", s.names())

    def test_load_and_render(self):
        _write(self.root, "s/SKILL.md",
               "---\nname: s1\ndescription: 描述\nversion: 2.0\n---\n# Step\n1. 做")
        s = SkillStore(self.root)
        txt = s.instructions("s1", "missing_skill")
        self.assertIn("Skill: s1", txt)
        self.assertIn("描述", txt)
        self.assertIn("版本: 2.0", txt)
        self.assertIn("1. 做", txt)
        # 未知名字被静默跳过
        self.assertNotIn("missing_skill", txt)

    def test_match_keywords(self):
        _write(self.root, "a/SKILL.md", "---\nname: a\ndescription: 机场运维\n---\n")
        _write(self.root, "b/SKILL.md", "---\nname: b\ndescription: 无限\n---\n")
        s = SkillStore(self.root)
        self.assertEqual([x.name for x in s.match(["机场"])], ["a"])
        self.assertEqual([x.name for x in s.match(["nope"])], [])

    def test_missing_root_tolerant(self):
        s = SkillStore(self.root / "does-not-exist")
        self.assertEqual(s.names(), [])

    def test_reload(self):
        s = SkillStore(self.root)
        self.assertEqual(s.names(), [])
        _write(self.root, "n/SKILL.md", "---\nname: n\n---\n")
        self.assertEqual(s.reload(), 1)
        self.assertIn("n", s.names())


class MatchTextTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        _write(self.root, "ops/SKILL.md",
               "---\nname: airport-ops\ndescription: 机场节点运维排障\n---\n"
               "排查节点线路卡顿不稳定、延迟高的步骤")
        _write(self.root, "unrelated/SKILL.md",
               "---\nname: cooking\ndescription: 菜谱\n---\n今天晚饭番茄炒蛋")

    def test_match_text_chinese_bigram(self):
        s = SkillStore(self.root)
        hits = s.match_text("节点卡顿延迟高怎么办")
        self.assertEqual([h.name for h in hits], ["airport-ops"])

    def test_match_text_picks_right_domain(self):
        s = SkillStore(self.root)
        hits = s.match_text("番茄炒蛋怎么做")
        self.assertEqual([h.name for h in hits], ["cooking"])
        # 且不会误匹配机场技能
        self.assertNotIn("airport-ops", [h.name for h in hits])

    def test_match_text_empty_and_min_overlap(self):
        s = SkillStore(self.root)
        self.assertEqual(s.match_text(""), [])
        self.assertEqual(s.match_text("xyz"), [])  # 与两技能都无重叠

    def test_bigrams(self):
        self.assertEqual(SkillStore.bigrams("ab"), {"ab"})
        self.assertIn("节点", SkillStore.bigrams("节点卡顿"))
        self.assertEqual(SkillStore.bigrams(""), set())


class CurateInjectionTest(unittest.TestCase):
    def test_curate_injects_matching_skill(self):
        from agent_body.context import ProjectContext
        from agent_body.curate import Curator
        root = Path(tempfile.mkdtemp())
        _write(root, "ops/SKILL.md",
               "---\nname: ops\ndescription: 节点运维\n---\n排查卡顿步骤: 重启")
        store = SkillStore(root)
        ctx = ProjectContext(str(root))
        curator = Curator()

        class _FakeBrain:
            def recall(self, *a, **k):
                return []

        out = curator.curate(_FakeBrain(), ctx, "节点卡顿怎么排查", skills_store=store)
        self.assertEqual(len(out.skills), 1)
        self.assertIn("重启", out.skills[0])
        prompt = out.to_prompt()
        self.assertIn("参考技能", prompt)
        self.assertIn("重启", prompt)


if __name__ == "__main__":
    unittest.main()