"""Headless 常驻守护 —— 完全体无 bot 也能后台自治运行（供 systemd 自启）。

循环：tick(认知推进) → 自运维(selftest 巡检/观察错误) → 处理待办任务 → 静默心跳。
不依赖 telegram/REPL，适合后台无人值守常驻。优雅退出(SIGINT/SIGTERM)。
"""

from __future__ import annotations

import logging
import signal
import threading

log = logging.getLogger(__name__)


def serve(body, session: str = "daemon", interval: float = 60.0,
          enabled: bool = True) -> None:
    """常驻 daemon 主循环。enabled=False 时只 tick 不自运维。"""
    stop = threading.Event()

    def _sig(signum, frame):
        stop.set()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    log.info("sa daemon 启动: session=%s interval=%.1fs", session, interval)
    body.brain(session)            # 预热内核
    while not stop.is_set():
        try:
            body.tick(session)     # 大脑认知推进(需求/情绪/自主想法)
        except Exception as e:
            log.warning("tick 异常: %s", e)
        if enabled:
            try:                  # AI 自运维：观察执行错误已由 run_scripts 自动做；这里汇总健康
                body.ops_health()
            except Exception as e:
                log.warning("ops_health 异常: %s", e)
        # 处理待办任务（续跑 pending）
        try:
            for p in body.pending_tasks():
                body.resume_tasks(p["task_id"], session)
        except Exception:
            pass
        stop.wait(interval)
    # 收尾
    try:
        body.close()
    except Exception:
        pass
    log.info("sa daemon 退出")