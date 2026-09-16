"""资产文件夹 Assets —— skills/MCP/截图/生成文件分类管理，侧边挂载。

ROADMAP §6/§7 落地：对话产生的一切资产统一落在资产区，按类型分类：
  assets/skills/      # 技能（SKILL.md）
  assets/mcp/         # MCP 服务器配置/清单
  assets/images/      # 对话截图/生成图片（见 images.py 统一存放+压缩）
  assets/generated/   # 生成的文档/代码/文件
  assets/downloads/   # 下载的内容
  assets/misc/        # 其它

跨客户端（Win/Linux/macOS）同一套布局，方便任何一端取用。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .storage import Storage

_IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".bmp", ".jxl"}


class AssetStore:
    """资产分类挂载 + 写入工具。"""

    def __init__(self, storage: Storage):
        self.storage = storage

    def _type_dir(self, asset_type: str) -> Path:
        p = self.storage.assets(asset_type)
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ---- 分类目录 ----
    @property
    def skills_dir(self) -> Path:
        return self._type_dir("skills")

    @property
    def mcp_dir(self) -> Path:
        return self._type_dir("mcp")

    @property
    def images_dir(self) -> Path:
        return self._type_dir("images")

    @property
    def generated_dir(self) -> Path:
        return self._type_dir("generated")

    @property
    def downloads_dir(self) -> Path:
        return self._type_dir("downloads")

    @property
    def misc_dir(self) -> Path:
        return self._type_dir("misc")

    # ---- 分类写入 ----
    def save(self, data: bytes, asset_type: str, name: str) -> Path:
        """把字节按类型存到资产区对应目录。返回落盘路径。"""
        d = self._type_dir(asset_type)
        path = d / _safe_name(name)
        path.write_bytes(data)
        return path

    def save_text(self, text: str, asset_type: str, name: str) -> Path:
        d = self._type_dir(asset_type)
        path = d / _safe_name(name)
        path.write_text(text, encoding="utf-8")
        return path

    def classify(self, path: str | Path) -> Path:
        """按扩展名自动分类并（可选）移入对应资产目录。返回目标路径。"""
        src = Path(path).resolve()
        if not src.exists():
            return src
        suffix = src.suffix.lower()
        if suffix in _IMAGE_TYPES:
            target_dir = self.images_dir
        elif suffix in {".md", ".txt", ".py", ".js", ".json", ".yaml", ".yml",
                        ".toml", ".go", ".rs", ".sh", ".ps1", ".html", ".css"}:
            target_dir = self.generated_dir
        elif suffix in {".zip", ".tar", ".gz", ".rar", ".7z", ".pdf", ".docx", ".xlsx"}:
            target_dir = self.downloads_dir
        else:
            target_dir = self.misc_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / src.name
        # 同名则追加序号，避免覆盖
        i = 1
        while dest.exists():
            dest = target_dir / f"{src.stem}_{i}{src.suffix}"
            i += 1
        return dest


def _safe_name(name: str) -> str:
    """清洗文件名，防止路径穿越/危险字符。"""
    name = Path(name).name  # 只取最后一段，杜绝 ../ 等
    # 去掉明显危险的字符
    cleaned = "".join(c for c in name if c not in '/\\:\x00\n\r\t')
    return cleaned or "unnamed"
