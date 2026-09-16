"""存储规划 Storage —— 一切资源预算受限，按重要度分级、按活跃度调度、按生命周期淘汰。

ROADMAP §6 落地：把 agent 的各类数据分到五个存储区，各管各的生命周期。

| 存储区 | 内容 | 策略 |
|---|---|---|
| 配置区 config/ | .env / config | 极小，常驻 |
| 账本区 ledger/ | 任务/会话 | 按任务归档，超期清理 |
| 记忆区 memory/ | 大脑记忆 | L0-L4 分层，冷记忆归档 |
| 资产区 assets/ | skills/MCP/截图/生成文件 | 侧边挂载，分类管理，按保留时长清理 |
| 缓存区 cache/ | 临时/下载 | 自动回收 |

设计要点：
  - 路径统一由 Storage 提供（不散落硬编码），客户端 Win/Linux/macOS 同一套布局。
  - 保留时长可配（config），超期自动清理（cold archive 可先移冷区再删）。
  - 缓存区最激进，随时可清；配置区/账本区常驻不动。
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Optional


class Storage:
    """五区存储布局 + 保留时长管理。"""

    def __init__(self, root: str | Path, retention_days: int = 30):
        self.root = Path(root).expanduser().resolve()
        self.retention_days = retention_days
        # 五区目录（跨客户端一致）
        self.config_dir = self.root / "config"
        self.ledger_dir = self.root / "ledger"
        self.memory_dir = self.root / "memory"
        self.assets_dir = self.root / "assets"
        self.cache_dir = self.root / "cache"
        self._mkdirs()

    def _mkdirs(self) -> None:
        for d in (self.config_dir, self.ledger_dir, self.memory_dir,
                  self.assets_dir, self.cache_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ---- 各存储区路径 ----
    def config(self, name: str = "") -> Path:
        return self.config_dir / name if name else self.config_dir

    def ledger(self, name: str = "") -> Path:
        return self.ledger_dir / name if name else self.ledger_dir

    def memory(self, name: str = "") -> Path:
        return self.memory_dir / name if name else self.memory_dir

    def assets(self, *parts: str) -> Path:
        """资产区分类路径：assets/<type>/<...>。"""
        return self.assets_dir.joinpath(*parts) if parts else self.assets_dir

    def cache(self, name: str = "") -> Path:
        return self.cache_dir / name if name else self.cache_dir

    # ---- 生命周期 ----
    def collect_garbage(self, dry_run: bool = False) -> dict:
        """按保留时长回收资产区/缓存区过期文件。

        dry_run=True 只统计不删除（供预览/审计）。
        返回 {'scanned': n, 'removed': m, 'freed_bytes': b}。
        """
        now = time.time()
        cutoff = now - self.retention_days * 86400
        removed = 0
        freed = 0
        scanned = 0
        targets = [self.assets_dir, self.cache_dir]
        for base in targets:
            for p in base.rglob("*"):
                if not p.is_file():
                    continue
                scanned += 1
                try:
                    if p.stat().st_mtime < cutoff:
                        freed += p.stat().st_size
                        removed += 1
                        if not dry_run:
                            p.unlink()
                except FileNotFoundError:
                    pass  # 并发下已被清，忽略
        # 顺手清空已空目录（非 dry_run 时）
        if not dry_run:
            for base in targets:
                for p in sorted(base.rglob("*"), key=lambda x: len(x.parts), reverse=True):
                    if p.is_dir() and not any(p.iterdir()):
                        try:
                            p.rmdir()
                        except OSError:
                            pass
        return {"scanned": scanned, "removed": removed, "freed_bytes": freed}

    def clear_cache(self) -> int:
        """激进回收缓存区（随时可清）。返回删除文件数。"""
        n = 0
        for p in self.cache_dir.rglob("*"):
            if p.is_file():
                p.unlink()
                n += 1
        return n

    def size_report(self) -> dict:
        """各存储区体积统计（字节）。"""
        out = {}
        for name, d in (("config", self.config_dir), ("ledger", self.ledger_dir),
                        ("memory", self.memory_dir), ("assets", self.assets_dir),
                        ("cache", self.cache_dir)):
            total = 0
            if d.exists():
                for p in d.rglob("*"):
                    if p.is_file():
                        try:
                            total += p.stat().st_size
                        except FileNotFoundError:
                            pass
            out[name] = total
        return out
