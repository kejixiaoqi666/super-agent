"""内核只读门 —— 稳定内核不可撼动（阶段① 主权开放架构）。

所有写操作按目标分区：
  - kernel 区（稳定内核）：拒绝写入，明确提示走升级治理（不静默失败）
  - plugin 区：放行（AI 自由区）
  - free 区（默认）：放行
AI 只能【读】内核、【写】插件区；要改内核，必须提交需求文档走升级队列。

分区判定基于路径前缀。前缀可用构造参数覆盖（可测 / 可部署调整）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Tuple


class KernelReadOnlyGate:
    """写操作分区门禁。check_write 返回 {allowed, zone, reason}，不抛错。"""

    def __init__(self, data_dir: str | Path,
                 kernel_prefixes: Iterable[str] = (),
                 plugin_root: str | Path | None = None):
        self.data_dir = str(Path(data_dir).resolve())
        self.plugin_root = str(Path(plugin_root).resolve()) \
            if plugin_root else str(Path(data_dir) / "plugins")
        # 默认内核只读区：本包核心模块 + 数据目录下非插件核心数据
        self._kernel = [
            "agent_body/sovereign",   # 治理自身
            "agent_body/exec",        # 执行 daemon（核心）
            "agent_body/kernel",      # BrainPort 契约
            "agent_body/budget.py",
            "agent_body/continuity.py",
            "agent_body/autopilot",
            "docs/SOVEREIGN.md",
        ]
        self._kernel.extend(kernel_prefixes or [])
        self._kernel = [p.rstrip("/") for p in self._kernel]
        # 匹配用：把相对前缀解析成绝对路径（相对 cwd/项目根），与 zone_of 里 resolve 后的路径对齐
        self._kernel_abs = [str(Path(p).resolve()) for p in self._kernel]

    # ---- 分区 ----
    def zone_of(self, path: str) -> str:
        """返回该路径所属分区: kernel | plugin | free。"""
        p = str(Path(path).resolve())
        if p.startswith(self.plugin_root + "/") or p == self.plugin_root:
            return "plugin"
        if p.startswith(self.data_dir + "/") \
                and not p.startswith(self.plugin_root + "/"):
            # 数据目录下、非插件 → 视为核心数据（只读）
            return "kernel"
        for prefix in self._kernel_abs:
            if p == prefix or p.startswith(prefix + "/"):
                return "kernel"
        return "free"

    def check_write(self, path: str) -> dict:
        """写目标分区检查。kernel 区拒绝并提示走升级队列（不是静默失败）。"""
        zone = self.zone_of(path)
        if zone == "kernel":
            return {
                "allowed": False, "zone": zone,
                "reason": ("稳定内核只读不可撼动。要改进请走升级治理："
                           "先提交需求文档(含代码文档)进 evolution/upgrade 队列，批准后并入。")}
        return {"allowed": True, "zone": zone, "reason": ""}

    def guard_write(self, path: str) -> Tuple[bool, dict]:
        """写守卫：返回 (放行?, 结果)。放行才允许写入。"""
        res = self.check_write(path)
        return res["allowed"], res

    def kernel_readonly_areas(self) -> List[str]:
        return list(self._kernel)
