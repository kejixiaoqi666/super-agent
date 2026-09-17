"""重试预算 RetryBudget —— 统一的重试调度 + 预算上限。

在 call_with_retry(指数退避) 之上加「预算闸门」：
  - attempt_budget: 最多尝试次数（含首次）
  - wait_budget: 累计退避等待的秒数上限（防止重试间隔无限拉长）
  - retryable: 决定哪些异常值得重试（网络类等），预算不足时快速失败
核心价值：重试是有预算上限的，不会无限消耗时间/算力。

典型接入：exec 沙箱 / 网络调用统一走 retry_budget() 或 RetryBudget 类。
"""
from __future__ import annotations

import time
from typing import Callable, Optional


class RetryBudgetExhausted(RuntimeError):
    def __init__(self, message: str, reason: str = "budget"):
        super().__init__(message)
        self.reason = reason


class RetryBudget:
    """可观测的重试预算：记录已用尝试/已等待，预算耗尽抛 RetryBudgetExhausted。"""

    def __init__(self, max_attempts: int = 4, max_wait: float = 12.0,
                 base_backoff: float = 1.0, backoff_cap: float = 8.0,
                 retryable: Optional[Callable[[Exception], bool]] = None):
        assert max_attempts >= 1 and max_wait >= 0
        self.max_attempts = max_attempts
        self.max_wait = max_wait
        self.base_backoff = base_backoff
        self.backoff_cap = backoff_cap
        self._retryable = retryable
        self.attempts = 0
        self.waited = 0.0
        self.last_error: Optional[str] = None

    @property
    def exhausted(self) -> bool:
        """预算是否已耗尽（尝试次数或等待时间超过上限）。"""
        return self.attempts >= self.max_attempts or self.waited >= self.max_wait

    def _should_retry(self, exc: Exception) -> bool:
        if self._retryable is not None:
            try:
                return bool(self._retryable(exc))
            except Exception:
                return False
        return True

    def backoff(self) -> float:
        """下一次退避时长（受剩佘等待预算约束）。"""
        raw = min(self.base_backoff * (2 ** (self.attempts - 1)), self.backoff_cap)
        return min(raw, max(self.max_wait - self.waited, 0.0))

    def call(self, fn: Callable):
        """执行 fn，按预算重试。预算耗尽抛 RetryBudgetExhausted。"""
        while True:
            self.attempts += 1
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                if not self._should_retry(exc):
                    raise RetryBudgetExhausted(
                        f"重试预算：非重试类错误 {exc}", reason="non_retryable") from exc
                if self.exhausted:
                    raise RetryBudgetExhausted(
                        f"重试预算耗尽（尝试 {self.attempts}/{self.max_attempts}, "
                        f"等待 {self.waited:.1f}s/{self.max_wait}s）: {exc}",
                        reason="budget") from exc
                delay = self.backoff()
                if delay <= 0:
                    raise RetryBudgetExhausted(
                        f"重试预算：等待预算耗尽 {exc}", reason="wait_budget") from exc
                time.sleep(delay)
                self.waited += delay


def retry_budget(fn: Callable, *, max_attempts: int = 4, max_wait: float = 12.0,
                 base_backoff: float = 1.0, backoff_cap: float = 8.0,
                 retryable: Optional[Callable[[Exception], bool]] = None):
    """便捷函数式重试预算入口：执行 fn，返回 (result, budget)。"""
    budget = RetryBudget(max_attempts, max_wait, base_backoff, backoff_cap,
                         retryable)
    result = budget.call(fn)
    return result, budget