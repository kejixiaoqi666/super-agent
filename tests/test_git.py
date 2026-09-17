import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_body import git


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


class GitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp) / "repo"
        _init_repo(self.root)
        (self.root / "a.txt").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.root, check=True)

    def test_is_repo(self):
        self.assertTrue(git.is_repo(self.root))
        # 兄弟目录(不在仓库内)应为非仓库
        other = Path(self.tmp) / "other"
        other.mkdir(exist_ok=True)
        self.assertFalse(git.is_repo(other))

    def test_status_shows_modified(self):
        (self.root / "a.txt").write_text("hello world\n", encoding="utf-8")
        st = git.status(self.root)
        self.assertTrue(any("a.txt" in ln for ln in st))

    def test_diff_contains_change(self):
        (self.root / "a.txt").write_text("hello world\n", encoding="utf-8")
        d = git.diff(self.root)
        self.assertIn("hello world", d)

    def test_diff_stat_parses(self):
        (self.root / "a.txt").write_text("hello\nworld\nline3\n", encoding="utf-8")
        s = git.diff_stat(self.root)
        self.assertEqual(s["files"], 1)
        self.assertGreaterEqual(s["insertions"], 1)

    def test_commit_and_log(self):
        (self.root / "b.txt").write_text("new\n", encoding="utf-8")
        git.commit(self.root, "add b")
        entries = git.log(self.root, limit=3)
        self.assertEqual(entries[0][1], "add b")
        self.assertEqual(entries[1][1], "init")

    def test_commit_empty_message_raises(self):
        with self.assertRaises(git.GitError):
            git.commit(self.root, "   ")

    def test_current_branch(self):
        self.assertEqual(git.current_branch(self.root), "master")

    def test_non_repo_raises(self):
        non = Path(self.tmp) / "norepo"
        non.mkdir(exist_ok=True)
        with self.assertRaises(git.GitError):
            git.status(non)
        with self.assertRaises(git.GitError):
            git.commit(non, "x")


if __name__ == "__main__":
    unittest.main()
