"""命令沙箱 —— 安全可靠地执行系统命令。

解决你担心的三点：
  ① 执行授权烦 → 普通命令直接跑；危险命令走门禁仿示；长脚本一次交付
  ② 留垃圾   → 事务化执行：每次用隔离工作目录，结束自动回收临时文件
  ③ 脚本复用 → 支持脚本模板化（带参数校验 + 幂等 + 返回码检查）

设计：
  - sandbox 在独立工作目录（data_dir/sandbox/<task_id>/）跑命令
  - 结束自动清理（除非 keep=True 显式保留产物）
  - 内置超时 + 返回码检查 + 危险命令门禁
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

from ..safety import call_with_retry
from ..retry import RetryBudget, RetryBudgetExhausted

# 危险命令模式（触发需批准 / 拦截）
DANGEROUS_PATTERNS: List[str] = [
    "rm -rf /", "rm -fr /", "shutdown", "reboot", "mkfs", ":(){",
    "DROP TABLE", "DROP DATABASE", "git push --force", "git push -f",
    "curl.*|.*sh", "sudo ", "chmod -R 777 /", "> /dev/sda",
]


def _is_dangerous(cmd: str) -> Optional[str]:
    cmd_n = cmd.replace(" ", " ").strip()
    for pat in DANGEROUS_PATTERNS:
        if pat in cmd_n:
            return pat
    return None


class CommandError(RuntimeError):
    def __init__(self, cmd: str, code: int, output: str = ""):
        super().__init__(f"命令退出码 {code}: {cmd}")
        self.code = code
        self.cmd = cmd
        self.output = output


class Sandbox:
    def __init__(self, data_dir: str | Path, require_approval: bool = True):
        self.base = Path(data_dir) / "sandbox"
        self.require_approval = require_approval
        self._active: Dict[str, Path] = {}

    def _dir(self, task_id: str) -> Path:
        d = self.base / task_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def run(self, cmd: str, task_id: str = "adhoc", timeout: float = 60.0,
            keep: bool = False, retries: int = 0,
            retryable_errors: tuple = (),
            budget: Optional[RetryBudget] = None) -> dict:
        """在隔离沙箱目录执行命令。

        keep=True 时保留工作目录与产物（供取走），否则结束自动回收。
        retries/budget：统一重试；传 RetryBudget 时用预算上限（attempts+wait）
        返回 {ok, code, output, workdir, cleaned, elapsed, attempts}.
        """
        danger = _is_dangerous(cmd)
        if danger and self.require_approval:
            raise CommandError(
                cmd, -1, f"危险命令被拦截（命门 {danger}）。确认后才可执行。")
        workdir = self._dir(task_id)
        self._active[task_id] = workdir
        start = time.time()

        def go():
            r = subprocess.run(cmd, shell=True, cwd=str(workdir),
                               capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                raise CommandError(cmd, r.returncode, r.stderr or r.stdout)
            return r

        try:
            if budget is not None:
                # 统一重试预算：attempts+wait 双上限
                try:
                    r = budget.call(go)
                except RetryBudgetExhausted as exc:
                    raise CommandError(cmd, -2, f"重试预算耗尽: {exc}") from exc
            elif retries > 0:
                result, _ = call_with_retry(
                    go, max_retries=retries,
                    retryable=lambda e: isinstance(e, CommandError)
                    and e.code in retryable_errors)
                r = result
            else:
                r = go()
            cleaned_now = not keep
            return {
                "ok": True, "code": r.returncode,
                "output": (r.stdout or "")[:4000],
                "workdir": str(workdir), "cleaned": cleaned_now,
                "elapsed": round(time.time() - start, 3),
                "attempts": budget.attempts if budget is not None else 1,
            }
        finally:
            if not keep:
                shutil.rmtree(workdir, ignore_errors=True)
                self._active.pop(task_id, None)

    def get_workdir(self, task_id: str) -> Optional[Path]:
        return self._active.get(task_id)

    def cleanup(self, task_id: str) -> None:
        """显式回收某个任务的沙箱目录。"""
        d = self.base / task_id
        shutil.rmtree(d, ignore_errors=True)
        self._active.pop(task_id, None)

    def cleanup_all(self) -> int:
        """回收所有沙箱，返回清理目录数。"""
        n = 0
        for d in self.base.glob("*"):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                n += 1
        self._active.clear()
        return n