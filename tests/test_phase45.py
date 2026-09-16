"""Phase 4/5 专项测试：存储规划 / 资产文件夹 / 图片系统 / 密码本。"""
import tempfile
import unittest
from pathlib import Path

from agent_body.storage import Storage
from agent_body.assets import AssetStore
from agent_body.images import ImageStore
from agent_body.vault import Vault


class StorageTest(unittest.TestCase):
    def test_five_zones_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            Storage(tmp, retention_days=30)
            for sub in ("config", "ledger", "memory", "assets", "cache"):
                self.assertTrue((Path(tmp) / sub).is_dir())

    def test_garbage_collection(self):
        import os, time
        with tempfile.TemporaryDirectory() as tmp:
            s = Storage(tmp, retention_days=0)  # 立即过期
            old = s.assets("images") / "old.png"
            old.parent.mkdir(parents=True, exist_ok=True)
            old.write_bytes(b"x" * 100)
            # 改旧 mtime
            t = time.time() - 86400
            os.utime(old, (t, t))
            # dry run 不删
            r = s.collect_garbage(dry_run=True)
            self.assertEqual(r["removed"], 1)
            self.assertTrue(old.exists())  # dry_run 未删
            # 真删
            r2 = s.collect_garbage(dry_run=False)
            self.assertEqual(r2["removed"], 1)
            self.assertFalse(old.exists())

    def test_clear_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Storage(tmp)
            f = s.cache("tmp.bin")
            f.write_bytes(b"data")
            self.assertEqual(s.clear_cache(), 1)
            self.assertFalse(f.exists())

    def test_size_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage_obj = Storage(tmp)
            (storage_obj.ledger_dir / "a.json").write_text("12345")
            rep = storage_obj.size_report()
            self.assertGreater(rep["ledger"], 0)


class AssetStoreTest(unittest.TestCase):
    def test_classify_by_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Storage(tmp)
            a = AssetStore(s)
            img = Path(tmp) / "photo.png"
            img.write_bytes(b"pngdata")
            dest = a.classify(img)
            self.assertTrue(dest.parent.name == "images")
            md = Path(tmp) / "note.md"
            md.write_text("# hi")
            self.assertTrue(a.classify(md).parent.name == "generated")

    def test_safe_name_blocks_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Storage(tmp)
            a = AssetStore(s)
            p = a.save(b"data", "misc", "../../evil.txt")
            self.assertNotIn("..", str(p))
            self.assertTrue(p.parent.name == "misc")


class ImageStoreTest(unittest.TestCase):
    def _make_png(self, path: Path):
        from PIL import Image
        img = Image.new("RGB", (64, 64), (255, 0, 0))
        img.save(path, format="PNG")
        return path.stat().st_size

    def test_ingest_png_unified(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage_obj = Storage(tmp)
            a = AssetStore(storage_obj)
            src = Path(tmp) / "shot.png"
            self._make_png(src)
            im = ImageStore(a, compress_quality=80)
            r = im.ingest(src, session="chat-1")
            self.assertTrue(r.path.exists())
            self.assertEqual(r.format, "PNG")
            self.assertTrue(str(r.path).endswith(".png"))
            self.assertIn("chat-1", str(r.path))
            self.assertGreater(r.width, 0)

    def test_sequence_increments(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Storage(tmp)
            a = AssetStore(s)
            src = Path(tmp) / "a.png"
            self._make_png(src)
            im = ImageStore(a)
            r1 = im.ingest(src, session="s")
            r2 = im.ingest(src, session="s")
            self.assertNotEqual(r1.path, r2.path)  # 序号递增不覆盖


class VaultTest(unittest.TestCase):
    def test_set_get_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = Vault(tmp, master_password="secret")
            v.set("server1", "my-pass", tier="high", notes="production")
            self.assertEqual(v.get("server1"), "my-pass")
            self.assertEqual(v.count(), 1)

    def test_persistence_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = Vault(tmp, master_password="secret")
            v.set("api", "tok-123")
            v2 = Vault(tmp, master_password="secret")  # 重开
            self.assertEqual(v2.get("api"), "tok-123")

    def test_wrong_password_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = Vault(tmp, master_password="right")
            v.set("k", "v")
            v2 = Vault(tmp, master_password="wrong")
            with self.assertRaises(ValueError):
                v2.get("k")

    def test_payment_tier_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = Vault(tmp, master_password="secret")
            v.set("bank", "4242-4242", tier="payment")
            with self.assertRaises(PermissionError):
                v.get("bank")  # 未 explicit 拒绝
            self.assertEqual(v.get("bank", explicit=True), "4242-4242")

    def test_encrypted_at_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = Vault(tmp, master_password="secret")
            v.set("server1", "plaintext-secret-xyz", tier="high")
            raw = (Path(tmp) / "config" / "vault.json").read_text(encoding="utf-8")
            self.assertNotIn("plaintext-secret-xyz", raw)  # 明文绝不落盘


if __name__ == "__main__":
    unittest.main()
