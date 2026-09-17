import tempfile
import unittest
from pathlib import Path

from PIL import Image

from agent_body.vision import (dhash, dominant_colors, meta, similarity,
                               status_color)


def _solid(color, size=(64, 64)):
    return Image.new("RGB", size, color)


def _gradient(direction="h", offset=0, size=(64, 64)):
    """水平/垂直渐变图（有结构，dHash 可区分）。offset 引入细微差异。"""
    img = Image.new("L", size)
    px = img.load()
    for y in range(size[1]):
        for x in range(size[0]):
            v = (x + offset) * 255 // (size[0] - 1) if direction == "h" \
                else (y + offset) * 255 // (size[1] - 1)
            px[x, y] = min(255, v)
    return img.convert("RGB")


class DhashTest(unittest.TestCase):
    def test_same_image_identical_hash(self):
        a = _gradient()
        self.assertEqual(dhash(a), dhash(a.copy()))

    def test_different_gradients_differ(self):
        self.assertNotEqual(dhash(_gradient("h")), dhash(_gradient("v")))
        self.assertNotEqual(dhash(_gradient(offset=0)), dhash(_gradient(offset=40)))

    def test_perceptual_hash_stable_across_small_change(self):
        # 轻微平移/亮度 → 哈希接近（汉明距离小）
        import agent_body.vision as v
        a = _gradient(offset=0)
        b = _gradient(offset=1)
        self.assertLessEqual(v.hamming(dhash(a), dhash(b)), 16)


class SimilarityTest(unittest.TestCase):
    def test_identical_is_1(self):
        a = _gradient()
        self.assertEqual(similarity(a, a.copy()), 1.0)

    def test_similar_higher_than_different(self):
        base = _gradient(offset=0)
        near = _gradient(offset=1)
        far = _gradient("v")  # 完全不同方向
        self.assertGreater(similarity(base, near), similarity(base, far))

    def test_path_and_image_both_work(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.png"
            _gradient().save(p)
            self.assertGreater(similarity(p, _gradient(offset=1)), 0.6)


class DominantColorTest(unittest.TestCase):
    def test_solid_color_dominant(self):
        colors = dominant_colors(_solid((200, 0, 0)), n=1)
        self.assertEqual(len(colors), 1)
        self.assertEqual(colors[0][0], 192)  # 200 量化到 32 级 = 192

    def test_returns_n_colors(self):
        colors = dominant_colors(_gradient(), n=3)
        self.assertLessEqual(len(colors), 3)


class StatusColorTest(unittest.TestCase):
    def test_red(self):
        self.assertEqual(status_color(_solid((230, 30, 30))), "red")

    def test_green(self):
        self.assertEqual(status_color(_solid((30, 220, 40))), "green")

    def test_neutral(self):
        self.assertEqual(status_color(_solid((120, 120, 120))), "neutral")


class MetaTest(unittest.TestCase):
    def test_meta_reports_dims(self):
        m = meta(_gradient(size=(80, 40)))
        self.assertEqual(m["width"], 80)
        self.assertEqual(m["height"], 40)


if __name__ == "__main__":
    unittest.main()
