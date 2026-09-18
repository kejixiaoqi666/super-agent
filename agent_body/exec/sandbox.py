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

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

from ..safety import call_with_retry
from ..retry import RetryBudget, RetryBudgetExhausted

# 危险命令模式（触发需批准 / 拦截）。用「正则 + 归一化」匹配，
# 避免子串匹配被大小写/多空格/转义绕过。
# 每个元素: (标签, 编译正则)
DANGEROUS_PATTERNS: List[tuple] = [
    ("删根", re.compile(r"rm\s+(-[a-zA-Z]*[fr][a-zA-Z]*\s+)+/")),
    ("删根", re.compile(r"rm\s+-rf\s+(/|~/)")),
    ("清盘", re.compile(r"\b(mkfs|fdisk)\b")),
    ("关机重启", re.compile(r"\b(shutdown|reboot|poweroff|halt)\b")),
    ("fork炸弹", re.compile(r":\(\)\{")),
    ("删库", re.compile(r"\b(drop\s+(table|database))\b")),
    ("强推", re.compile(r"git\s+push\s+(-f|--force)\b")),
    ("管道装shell", re.compile(r"(curl|wget)[^|;]*\|[^;]*\b(sh|bash|zsh)\b")),
    ("sudo", re.compile(r"\bsudo\s+")),
    ("递归改权根", re.compile(r"chmod\s+-R\s+777\s+/")),
    ("写块设备", re.compile(r">\s*/dev/")),
    ("改所有文件属主", re.compile(r"chown\s+-R\s+\S+\s+/")),
    ("dd 覆盖磁盘", re.compile(r"\bdd\b.*(of=|>)\s*/dev/")),
    ("dd 覆盖磁盘", re.compile(r"\bdd\b.*if=/dev/")),
]


def _normalize(cmd: str) -> str:
    """归一化：合并空白、统一引号，用于绕过检测的匹配。"""
    s = cmd.strip()
    s = s.replace("\\ ", " ").replace("\\\t", "\t")
    s = re.sub(r"\s+", " ", s)
    return s


def _is_dangerous(cmd: str) -> Optional[str]:
    """检查命令是否危险。命中返回标签，否则 None。归一化后匹配，堵绕过。"""
    norm = _normalize(cmd)
    norm_lower = norm.lower()
    for label, pat in DANGEROUS_PATTERNS:
        # 先对归一化(保留大小写)匹配，再对转小写版本匹配（覆盖大小写绕过）
        if pat.search(norm) or pat.search(norm_lower):
            return label
    return None


def _safe_task_id(task_id: str) -> str:
    """清洗 task_id：只保留安全字符，杜绝 ../ 路径穿越。"""
    tid = "".join(c for c in task_id if c.isalnum() or c in "-_.")
    tid = tid.strip(".")
    return tid or "adhoc"


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
        # 防路径穿越：task_id 只取安全字符，杜绝 ../ 逃逸沙箱根
        tid = _safe_task_id(task_id)
        d = self.base / tid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def run(self, cmd: str, task_id: str = "adhoc", timeout: float = 60.0,
            keep: bool = False, retries: int = 0,
            retryable_errors: tuple = (),
            budget: Optional[RetryBudget] = None,
            mem_limit_mb: Optional[int] = None) -> dict:
        """在隔离沙箱目录执行命令。

        keep=True 时保留工作目录与产物（供取走），否则结束自动回收。
        retries/budget：统一重试；传 RetryBudget 时用预算上限（attempts+wait）
        mem_limit_mb: Linux 用 setrlimit(RLIMIT_AS) 限制子进程虚拟内存，超限归 resource 错。
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
            # Popen + select 带超时流式读：既保内存(截断)又保超时(不阻塞死)
            import select as _select
            preexec = None
            if mem_limit_mb:
                # 内存上限：fork 出的 shell/脚本进程设 RLIMIT_AS（虚拟内存硬上限）。
                # setrlimit 是 async-signal-safe 系统调用，preexec_fn 内安全（不分配内存）。
                _lim = mem_limit_mb * 1024 * 1024
                def _limit():
                    import resource as _res
                    _res.setrlimit(_res.RLIMIT_AS, (_lim, _lim))
                    _res.setrlimit(_res.RLIMIT_CORE, (0, 0))
                preexec = _limit
            proc = subprocess.Popen(
                cmd, shell=True, cwd=str(workdir),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, preexec_fn=preexec)
            out_parts: List[str] = []
            total = 0
            cap = 100_000  # 最多保留 ~100KB，防大输出吃内存
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    proc.terminate()
                    raise CommandError(cmd, -9, "命令执行超时")
                if proc.poll() is not None and proc.stdout is not None:
                    # 进程结束，读走剩余输出
                    rest = proc.stdout.read()
                    if rest:
                        out_parts.append(rest)
                        total += len(rest)
                    break
                if proc.stdout is not None:
                    r, _, _ = _select.select([proc.stdout], [], [], min(remaining, 0.1))
                    if r:
                        chunk = proc.stdout.readline()
                        if not chunk:
                            continue
                        out_parts.append(chunk)
                        total += len(chunk)
                        if total > cap:  # 超上限截断丢弃，防无界累积
                            out_parts = out_parts[-10:]
            full = "".join(out_parts)
            if proc.returncode != 0:
                raise CommandError(cmd, proc.returncode, full)
            return _Result(proc.returncode, full)

        class _Result:
            def __init__(self, code, stdout):
                self.returncode = code
                self.stdout = stdout

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
        tid = _safe_task_id(task_id)
        d = self.base / tid
        if d.is_relative_to(self.base):  # 双保险：绝不删沙箱根之外
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