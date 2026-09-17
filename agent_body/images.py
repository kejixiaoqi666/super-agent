"""图片系统 Images —— 统一存放 + 压缩入库 + 保留时长。

ROADMAP §7 落地（Python 身体层实现；Rust 客户端后续可内嵌 Squoosh 编码器做本地压缩）：
  - 统一存放：assets/images/<session>/<yyyy-mm-dd>_<seq>.<ext>
  - 压缩入库：默认转 PNG 基准入库，可配质量（默认约 80% 体积，保可读）
  - 保留时长：超期由 Storage.collect_garbage 统一回收
  - 可转换：传入其它格式 → 自动压缩成 PNG 入库

注：ROADMAP 提到搬 Squoosh(Rust/WASM) 编码器由 Rust 客户端承担；此处 Python 侧用
Pillow 提供等价能力（OxiPNG≈Pillow 的 PNG 优化，MozJPEG/WebP/AVIF Pillow 也支持），
保证身体层立即可用、跨平台一致。
"""
from __future__ import annotations

import io
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from .assets import AssetStore

try:
    from PIL import Image, ImageOps
    _HAS_PIL = True
except Exception:  # Pillow 缺装时降级为原样拷贝（不压缩，仍统一命名/分类）
    _HAS_PIL = False


@dataclass
class ImageResult:
    """图片入库结果。"""
    path: Path            # 落盘路径
    src_bytes: int        # 原体积
    out_bytes: int        # 入库后体积
    ratio: float          # out/src（<1 表示变小）
    format: str           # 入库格式（通常 PNG）
    width: int = 0
    height: int = 0

    def summary(self) -> str:
        return (f"{self.path.name}: {self.src_bytes}→{self.out_bytes} bytes "
                f"({self.ratio * 100:.0f}%) [{self.format}]")


class ImageTooLargeError(ValueError):
    """图片像素超上限，拒绝解压/入库（防解压炸弹吃内存）。"""


class ImageStore:
    """图片统一存放 + 压缩入库。"""

    def __init__(self, assets: AssetStore, compress_quality: int = 80,
                 max_pixels: int = 40_000_000):
        self.assets = assets
        self.compress_quality = compress_quality  # 0-100，默认 80（ROADMAP 参数）
        self.max_pixels = max_pixels  # 尺寸上限（像素），防解压炸弹 DoS

    def ingest(self, src: Union[str, Path, bytes], session: str = "default",
               target_format: str = "PNG") -> ImageResult:
        """把图片压缩入库，返回结果。

        src: 源图片路径或原始字节。
        session: 会话标识（目录）。
        target_format: 入库格式，默认 PNG（最稳，ROADMAP §7.2）。
        """
        raw = Path(src).read_bytes() if isinstance(src, (str, Path)) else bytes(src)
        orig_size = len(raw)
        ext_of_src = Path(src).suffix.lower() if isinstance(src, (str, Path)) else ".bin"

        out_dir = self.assets.images_dir / _safe_session(session)
        out_dir.mkdir(parents=True, exist_ok=True)
        base = f"{time.strftime('%Y-%m-%d')}_{self._next_seq(out_dir)}"

        if not _HAS_PIL:
            # 无 Pillow：原样拷入，统一命名/分类
            target = out_dir / f"{base}{ext_of_src}"
            target.write_bytes(raw)
            return ImageResult(path=target, src_bytes=orig_size,
                               out_bytes=orig_size, ratio=1.0,
                               format=ext_of_src.lstrip("."))

        try:
            img = Image.open(path_or_bytes(src, raw))
            img = ImageOps.exif_transpose(img)  # 尊重 EXIF 方向
            # 解压前显式检查尺寸：超上限立即拒绝，防解压炸弹吃内存
            # （不依赖 Pillow 宽松默认 + 仅警告不抛错的地带）
            if img.width * img.height > self.max_pixels:
                raise ImageTooLargeError(
                    f"图片 {img.width}x{img.height}={img.width * img.height} "
                    f"像素，超过上限 {self.max_pixels}")
            img.load()
        except ImageTooLargeError:
            raise
        except Exception:
            # 打不开（非图）则原样落盘，不误伤
            target = out_dir / f"{base}{ext_of_src}"
            target.write_bytes(raw)
            return ImageResult(path=target, src_bytes=orig_size,
                               out_bytes=orig_size, ratio=1.0,
                               format=ext_of_src.lstrip(".") or "bin")

        fmt = target_format.upper()
        if fmt != "PNG":
            # 指定非 PNG：用质量参数压缩输出
            buf = io.BytesIO()
            img.save(buf, format=fmt, quality=self.compress_quality)
            out_bytes = len(buf.getvalue())
            target = out_dir / f"{base}.{fmt.lower()}"
            target.write_bytes(buf.getvalue())
            return ImageResult(path=target, src_bytes=orig_size, out_bytes=out_bytes,
                               ratio=(out_bytes / orig_size) if orig_size else 1.0,
                               format=fmt, width=img.width, height=img.height)

        # 默认：统一转 PNG 入库（ROADMAP §7.2 存储基准），optimize≈OxiPNG
        mode = "RGBA" if _has_alpha(img) else "RGB"
        rgb = img.convert(mode)
        buf = io.BytesIO()
        rgb.save(buf, format="PNG", optimize=True)
        out_bytes = len(buf.getvalue())
        target = out_dir / f"{base}.png"
        target.write_bytes(buf.getvalue())
        return ImageResult(path=target, src_bytes=orig_size, out_bytes=out_bytes,
                           ratio=(out_bytes / orig_size) if orig_size else 1.0,
                           format="PNG", width=img.width, height=img.height)

    def _next_seq(self, out_dir: Path) -> int:
        """当天序号：取目录里同名日期前缀的最大 seq + 1。"""
        date_part = time.strftime("%Y-%m-%d")
        n = 0
        for p in out_dir.glob(f"{date_part}_*"):
            try:
                n = max(n, int(p.stem.split("_")[-1]))
            except ValueError:
                continue
        return n + 1


def path_or_bytes(src, raw: bytes):
    """返回 Pillow 可接受的输入：路径或 BytesIO。"""
    return Path(src) if isinstance(src, (str, Path)) else io.BytesIO(raw)


def _safe_session(s: str) -> str:
    import re
    s = re.sub(r"[^A-Za-z0-9._-]", "_", s).strip("._")
    return s or "default"


def _has_alpha(img) -> bool:
    return img.mode in ("RGBA", "LA", "P") or \
        (img.mode == "RGB" and "transparency" in img.info)