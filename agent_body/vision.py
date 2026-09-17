"""模型无关的图像分析 vision —— 纯算法(Pillow)，无 LLM、无 OCR、无外部模型。

对标"识别视觉但不用模型"的诉求，提供最快、零成本、确定性的图像分析：
  - dhash：感知哈希(64-bit) → 去重 / 变化检测 / 近似匹配
  - similarity(a,b)：两图 0~1 相似度
  - dominant_colors：主色统计
  - status_color：面板状态色启发式(红/绿/黄/中性) —— 适合机场/监控面板状态灯
  - meta：宽高/格式/大小

全是确定性算法，无网络、无 API、毫秒级。真正"读懂图里文字"需本地 OCR
(可选装 tesseract/paddleocr)，本模块不依赖。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

from PIL import Image

RGB = Tuple[int, int, int]


def _open(src) -> Image.Image:
    if isinstance(src, (str, Path)):
        return Image.open(src).convert("RGB")
    if isinstance(src, Image.Image):
        return src.convert("RGB")
    raise TypeError("src 需为路径或 PIL.Image")


def dhash(src, hash_size: int = 8) -> int:
    """dHash 感知哈希：缩放为 (hash_size+1, hash_size) 灰度，比较相邻像素左右明暗。
    返回 hash_size^2 位整数。相似图哈希接近（汉明距离小）。
    注意：纯色无梯度图会哈希成全 1（无法区分），只对有结构的图有意义。"""
    img = _open(src).convert("L").resize((hash_size + 1, hash_size),
                                         Image.Resampling.LANCZOS)
    data = img.tobytes()  # 灰度字节，长度 (hash_size+1)*hash_size
    w = hash_size + 1
    bits = 0
    for row in range(hash_size):
        for col in range(hash_size):
            left = data[row * w + col]
            right = data[row * w + col + 1]
            bits = (bits << 1) | (1 if left >= right else 0)
    return bits


def hamming(a: int, b: int, bits: int = 64) -> int:
    return bin(a ^ b).count("1")


def similarity(src_a, src_b, hash_size: int = 8) -> float:
    """0~1 相似度（1 - 汉明距离/位数）。1=完全相同。"""
    total = hash_size * hash_size
    return 1.0 - hamming(dhash(src_a, hash_size), dhash(src_b, hash_size)) / total


def dominant_colors(src, n: int = 3) -> List[RGB]:
    """主色（RGB 量化到 32 级后计数，去重）。适合取面板主色调。"""
    img = _open(src).convert("RGB").resize((64, 64))
    raw = img.tobytes()
    counts: Dict[RGB, int] = {}
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        key = (r >> 5 << 5, g >> 5 << 5, b >> 5 << 5)  # 量化到 32 级
        counts[key] = counts.get(key, 0) + 1
    return [c for c, _ in sorted(counts.items(), key=lambda t: -t[1])[:n]]


def _hsl_sat(rgb: RGB) -> float:
    r, g, b = (x / 255.0 for x in rgb)
    mx, mn = max(r, g, b), min(r, g, b)
    return 0.0 if mx == mn else (mx - mn) / mx


def status_color(src, saturated_threshold: float = 0.4,
                 share: float = 0.08) -> str:
    """面板状态色启发式：统计饱和色占比，判断 red/green/yellow/neutral。
    适合"节点状态灯"型面板（红=异常、绿=正常、黄=警告）。"""
    img = _open(src).convert("RGB").resize((48, 48))
    raw = img.tobytes()
    n = 0
    r_, g_, b_ = 0, 0, 0
    for i in range(0, len(raw), 3):
        px = (raw[i], raw[i + 1], raw[i + 2])
        if _hsl_sat(px) >= saturated_threshold:
            n += 1
            r_, g_, b_ = r_ + px[0], g_ + px[1], b_ + px[2]
    if n == 0 or n / (48 * 48) < share:
        return "neutral"
    r_, g_, b_ = r_ / n, g_ / n, b_ / n
    if r_ > g_ and r_ > b_:
        return "red"
    if g_ > r_ and g_ > b_:
        return "green"
    if r_ > b_ and g_ > b_:
        return "yellow"
    return "neutral"


def meta(src) -> Dict:
    """图片基本信息：宽高/格式/模式/文件大小。"""
    img = _open(src)
    out = {"width": img.width, "height": img.height,
           "mode": img.mode, "format": img.format}
    if isinstance(src, (str, Path)):
        try:
            out["bytes"] = Path(src).stat().st_size
        except OSError:
            pass
    return out
