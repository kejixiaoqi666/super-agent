"""统一原子写 + 每文件进程内锁：避免 JSON 状态并发读-改-写丢更新/损坏(gpt-6 雷区)。

用法: json_atomic_write(path, obj)  —— 加锁+tmp+原子replace, 幂等安全。
任意多线程/多协程写同一文件互斥, 杜绝交错覆盖。
"""
from __future__ import annotations

import json
import threading
import tempfile
import os
from pathlib import Path

_LOCKS = {}
_LOCK_GUARD = threading.Lock()


def _file_lock(path: Path) -> threading.RLock:
    key = os.path.abspath(str(path))
    with _LOCK_GUARD:
        lk = _LOCKS.get(key)
        if lk is None:
            lk = _LOCKS[key] = threading.RLock()
        return lk


def json_atomic_write(path, obj, indent=2):
    """加每文件锁 + tmp + 原子 replace 写 JSON。任何异常不打乱原文件。"""
    p = Path(path)
    with _file_lock(p):
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=indent)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)          # 原子
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    return p


def text_atomic_write(path, text):
    """加锁 + tmp + 原子 replace 写文本。"""
    p = Path(path)
    with _file_lock(p):
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    return p