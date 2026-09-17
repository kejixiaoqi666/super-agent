"""生命周期钩子 HookRegistry —— PreToolUse/PostToolUse/Stop/Error 事件。

对标 Claude Code 的 hooks：在工具执行前后、会话停止、出错等关键点挂自定义回调。
设计：
  - 事件类型固定：pre_tool / post_tool / on_stop / on_error / on_message / subagent_stop
  - register(event, handler)：挂回调（handler(**ctx)）
  - dispatch(event, **ctx)：按注册顺序调用；单个 hook 抛错只记不打断主流程
  - 结果收集返回（供上层观测）
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

EVENTS = frozenset({
    "pre_tool", "post_tool", "on_stop", "on_error", "on_message",
    "subagent_stop",
})

HookHandler = Callable[..., Optional[dict]]


class HookError(ValueError):
    """非法事件/handler。"""


class HookRegistry:
    def __init__(self) -> None:
        self._handlers: Dict[str, List[HookHandler]] = {e: [] for e in EVENTS}
        self._fired: Dict[str, int] = {e: 0 for e in EVENTS}

    def register(self, event: str, handler: HookHandler) -> None:
        """注册事件回调。handler(**ctx) -> dict|None。"""
        if event not in EVENTS:
            raise HookError(f"未知事件 {event!r}，可选: {sorted(EVENTS)}")
        if not callable(handler):
            raise HookError("handler 必须是可调用对象")
        self._handlers[event].append(handler)

    def dispatch(self, event: str, **ctx) -> List[dict]:
        """触发事件，按序调用全部回调，收集非 None 结果。单 hook 异常不打断。"""
        if event not in EVENTS:
            raise HookError(f"未知事件 {event!r}")
        self._fired[event] += 1
        results: List[dict] = []
        for h in self._handlers[event]:
            try:
                r = h(**ctx)
                if r is not None:
                    results.append(r)
            except Exception:
                # hook 是外围观测，失败不打断主流程
                continue
        return results

    def handlers(self, event: str) -> int:
        return len(self._handlers.get(event, []))

    def fired(self, event: str) -> int:
        """某事件已触发次数（用于测试/观测）。"""
        return self._fired.get(event, 0)

    def clear(self, event: Optional[str] = None) -> None:
        if event:
            if event in self._handlers:
                self._handlers[event].clear()
        else:
            for e in self._handlers:
                self._handlers[e].clear()


def make_pre_tool_hook(fn) -> HookHandler:
    """便捷：把一个 (tool_name, args)->Any 函数包装成 pre_tool hook。"""
    def _wrap(**ctx):
        tool = ctx.get("tool")
        args = ctx.get("args") or {}
        if tool is None:
            return None
        out = fn(tool, args)
        return {"tool": tool, "pre": out}
    return _wrap
