import tempfile
import unittest
from pathlib import Path

from agent_body.session_store import SessionStore, _build_match_query


class SessionStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "sess.db"
        self.s = SessionStore(self.db)

    def tearDown(self):
        self.s.close()

    def _seed(self):
        self.s.record("s1", "节点线路卡顿不稳定，延迟很高", role="user", ts=1)
        self.s.record("s1", "已帮你重启服务器恢复", role="assistant", ts=2)
        self.s.record("s2", "开通启用新的线路转发入口", role="user", ts=3)

    def test_record_and_search_cjk_substring(self):
        self._seed()
        hits = self.s.search("节点线路卡顿")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["session"], "s1")

    def test_snippet_wraps_match(self):
        self._seed()
        hits = self.s.search("重启服务器")
        self.assertEqual(len(hits), 1)
        self.assertIn("[", hits[0]["snip"])
        self.assertIn("重启服务器", hits[0]["snip"])

    def test_session_scoped(self):
        self._seed()
        hits = self.s.search("线路转发入口")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["session"], "s2")

    def test_no_match_returns_empty(self):
        self._seed()
        self.assertEqual(self.s.search("根本不存在的词xyzabc"), [])

    def test_empty_query_returns_empty(self):
        self._seed()
        self.assertEqual(self.s.search("   "), [])

    def test_recent_returns_last_k(self):
        for i in range(5):
            self.s.record("sA", f"msg{i}", role="user", ts=i)
        recent = self.s.recent("sA", k=3)
        self.assertEqual(len(recent), 3)
        self.assertEqual(recent[0]["content"], "msg4")  # 最新在前

    def test_persistence_across_reopen(self):
        self._seed()
        self.s.close()
        s2 = SessionStore(self.db)
        hits = s2.search("线路转发入口")
        self.assertEqual(len(hits), 1)
        s2.close()

    def test_build_match_query_and_joins_quoted(self):
        self.assertEqual(_build_match_query("重启 服务器"),
                         '"重启" AND "服务器"')
        self.assertEqual(_build_match_query("   "), "")

    def test_bad_path_raises(self):
        # 路径不可写/不存在：connect 或建表阶段都该清晰抛错，绝不静默
        with self.assertRaises(Exception):
            SessionStore("/nonexistent/dir/sess.db")


if __name__ == "__main__":
    unittest.main()