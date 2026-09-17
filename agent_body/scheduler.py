"""定时任务 CronScheduler —— cron 表达式 / @every 间隔 / 一次性，持久化。

补齐成熟 agent（Hermes cron）的自主定时任务能力。设计：
  - 调度说明支持三种：
      cron    `m h dom mon dow`      五段 cron（分钟 小时 日 月 周）
      every   `@every 30s|5m|2h|1d`  间隔
      one-shot ISO 时间戳            到期跑一次后停
  - CronScheduler 持有 jobs（JSON 持久化到 data_dir/scheduler.json），
    提供 run_due(now) 供上层调用（也可 loop() 常驻轮询），next_run 自动推进。
  - 时钟可注入（clock 参数），便于测试时间推进。

不依赖第三方库；cron 字段支持 `*`、`*/N`、`N-M`、`N,M` 与单个数字。
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

_EVERY = re.compile(r"^@every\s+(\d+)\s*(s|m|h|d)?$")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
JobRunner = Callable[[dict], None]  # (job dict) -> None；含 payload，可在一性次移除后读取


def _to_epoch(dt: datetime) -> float:
    return dt.timestamp()


def _cron_matches(spec: dict, t: datetime) -> bool:
    """五段 cron 是否命中该时刻。dow 用 datetime.weekday()(0=周一)。"""
    return (_field_ok(spec.get("minute"), t.minute)
            and _field_ok(spec.get("hour"), t.hour)
            and _field_ok(spec.get("dom"), t.day)
            and _field_ok(spec.get("mon"), t.month)
            and _field_ok(spec.get("dow"), t.weekday()))


def _field_ok(spec, value) -> bool:
    """spec: None(任意) 或 {values:set, step:n} 或数字范围。"""
    if spec is None:
        return True
    if isinstance(spec, set):
        return value in spec
    if isinstance(spec, dict):  # {min,max,step}
        lo, hi, st = spec["min"], spec["max"], spec.get("step", 1)
        if value < lo or value > hi:
            return False
        return (value - lo) % st == 0
    return value == spec


def _parse_cron_field(s, lo, hi):
    s = s.strip()
    if s == "*":
        return None
    if "/" in s:
        base, step = s.split("/")
        if base in ("*", ""):
            return {"min": lo, "max": hi, "step": int(step)}
        if "-" in base:
            a, b = base.split("-")
            return {"min": int(a), "max": int(b), "step": int(step)}
    if "-" in s:
        a, b = s.split("-")
        return {v for v in range(int(a), int(b) + 1)}
    if "," in s:
        return {int(x) for x in s.split(",")}
    v = int(s)
    if not (lo <= v <= hi):
        raise ValueError(f"cron 字段越界: {v} 不在 [{lo},{hi}]")
    return v


def parse_schedule(spec: str) -> Dict:
    """解析调度说明，返回规范化 dict，供 CronScheduler 计算 next_run。"""
    spec = spec.strip()
    if spec.startswith("@every"):
        m = _EVERY.match(spec)
        if not m:
            raise ValueError(f"无法解析 @every: {spec}")
        n = int(m.group(1))
        unit = (m.group(2) or "m")
        return {"type": "every", "interval": n * _UNITS[unit]}
    if spec.startswith("@"):  # 一次性 ISO
        dt = datetime.fromisoformat(spec[1:])
        return {"type": "one-shot", "at": dt.timestamp()}
    parts = spec.split()
    if len(parts) != 5:
        raise ValueError(f"cron 需 5 段(分 时 日 月 周): {spec}")
    minute, hour, dom, mon, dow = parts
    return {
        "type": "cron",
        "minute": _parse_cron_field(minute, 0, 59),
        "hour": _parse_cron_field(hour, 0, 23),
        "dom": _parse_cron_field(dom, 1, 31),
        "mon": _parse_cron_field(mon, 1, 12),
        "dow": _parse_cron_field(dow, 0, 6),  # 0=周日
    }


def next_run(spec: Dict, now: float) -> Optional[float]:
    if spec["type"] == "every":
        return now + spec["interval"]
    if spec["type"] == "one-shot":
        return spec["at"]
    # cron：从当前时刻起按分钟遍历到命中
    base = datetime.fromtimestamp(now).replace(second=0, microsecond=0)
    from datetime import timedelta
    for step in range(5 * 24 * 60):  # 至多推进 5 天，防死循环
        t = base + timedelta(minutes=step)
        if _cron_matches(spec, t):
            return _to_epoch(t)
    return None


class CronScheduler:
    """持久化定时任务。clock 可注入用于测试。"""

    def __init__(self, data_dir: str | Path, clock: Optional[Callable[[], float]] = None):
        self.path = Path(data_dir) / "scheduler.json"
        self.clock = clock or time.time
        self.jobs: Dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self.jobs = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.jobs = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.jobs, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self.path)

    def add(self, job_id: str, spec: str, runner: Optional[JobRunner] = None,
            payload: Optional[dict] = None) -> dict:
        parsed = parse_schedule(spec)
        now = self.clock()
        job = {"id": job_id, "spec": spec, "parsed": parsed,
               "next_run": next_run(parsed, now),
               "last_run": None, "count": 0, "payload": payload or {}}
        self.jobs[job_id] = job
        self._save()
        return job

    def remove(self, job_id: str) -> bool:
        if job_id in self.jobs:
            del self.jobs[job_id]
            self._save()
            return True
        return False

    def due(self, now: Optional[float] = None) -> List[dict]:
        now = now if now is not None else self.clock()
        return [j for j in self.jobs.values()
                if j["next_run"] is not None and j["next_run"] <= now]

    def run_due(self, now: Optional[float] = None, runner: Optional[JobRunner] = None):
        """执行所有到期任务；runner 收到完整 job dict（含 payload，可在一次性任务
        移除后仍读到其内容）。默认以 payload.prompt 交给上层执行。"""
        now = now if now is not None else self.clock()
        ran = []
        for job in self.due(now):
            spec = job["parsed"]
            if spec["type"] == "one-shot":
                del self.jobs[job["id"]]  # 一次性：跑完移除
            else:
                job["next_run"] = next_run(spec, now)
                if job["next_run"] is not None and job["next_run"] <= now:
                    job["next_run"] = now + 60  # 兜底推进，防紧循环
            job["last_run"] = now
            job["count"] += 1
            ran.append(job["id"])
            if runner:
                try:
                    runner(job)
                    job["last_error"] = None
                except Exception as e:
                    # 单个任务失败不影响其余任务/常驻循环
                    job["last_error"] = f"{type(e).__name__}: {e}"
        if ran:
            self._save()
        return ran

    def loop(self, poll_sec: float = 5.0, runner: Optional[JobRunner] = None,
             stop: Optional[threading.Event] = None):
        """常驻轮询（供后台线程使用）。"""
        while stop is None or not stop.is_set():
            self.run_due(runner=runner)
            if stop is not None:
                stop.wait(poll_sec)
            else:
                time.sleep(poll_sec)
