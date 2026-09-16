"""安全/稳定性 —— 熔断器 + 重试预算 + 指数退避 + 超时。

防死循环的核心：同一目标连续失败到阈值 → 熔断（open），一段时间内不再尝试，
让请求快速失败（fail-fast）而不是空转重试。这是"连不上就一直连"的解药。

三态：
  CLOSED   正常，放行
  OPEN     熔断，直接拒绝（快速失败），等待 timeout 后转 HALF_OPEN
  HALF_OPEN 试探一次，成功转 CLOSED，失败回 OPEN
"""
from __future__ import annotations

import time
from typing import Callable, Optional


class CircuitOpenError(RuntimeError):
    """熔断中：目标暂不可用，快速失败。"""

    def __init__(self, key: str, reason: str = "circuit open"):
        super().__init__(f"circuit open: {key} ({reason})")
        self.key = key
        self.reason = reason


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, reset_timeout: float = 30.0):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._failures = 0
        self._opened_at: Optional[float] = None
        self._half_open_trying = False

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if time.time() - self._opened_at >= self.reset_timeout:
            return "half_open"
        return "open"

    @property
    def failures(self) -> int:
        return self._failures

    def allow(self) -> bool:
        """能否发起一次尝试？熔断中返回 False（快速失败）。"""
        s = self.state
        if s == "closed":
            return True
        if s == "half_open":
            # 只放行一次试探
            if not self._half_open_trying:
                self._half_open_trying = True
                return True
            return False
        return False  # open

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._half_open_trying = False

    def record_failure(self) -> bool:
        """记录一次失败；达到阈值则熔断。返回是否已熔断。"""
        self._half_open_trying = False
        self._failures += 1
        if self.state == "half_open" or self._failures >= self.failure_threshold:
            self._opened_at = time.time()
            return True
        return False

    def reset(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._half_open_trying = False


def with_backoff(attempt: int, base: float = 1.0, cap: float = 8.0) -> float:
    """指数退避延迟（秒）：1s,2s,4s... 封顶。"""
    return min(base * (2 ** (attempt - 1)), cap)


def call_with_retry(fn: Callable, max_retries: int = 3, base_backoff: float = 1.0,
                    backoff_cap: float = 8.0, timeout: Optional[float] = None,
                    retryable: Optional[Callable[[Exception], bool]] = None) -> tuple:
    """带重试预算 + 指数退避 + 超时的调用。

    返回 (result, attempts)。所有重试耗尽仍失败 → 抛最后一次异常。
    retryable(err)：可选，判断某异常是否值得重试（如网络类）；缺省全重试。
    """
    import functools

    if timeout is not None:
        fn = functools.partial(_with_timeout, fn, timeout)
    attempts = 0
    last_exc: Optional[Exception] = None
    while attempts <= max_retries:
        try:
            return fn(), attempts + 1
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            attempts += 1
            if attempts > max_retries:
                break
            if retryable is not None and not retryable(exc):
                break
            time.sleep(with_backoff(attempts, base_backoff, backoff_cap))
    raise last_exc  # type: ignore[misc]


def _with_timeout(fn, timeout: float):
    """在独立线程执行 fn，超时抛 TimeoutError。"""
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        return fut.result(timeout=timeout)
