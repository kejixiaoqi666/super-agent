"""子代理/委派 SubAgent delegation —— 并行隔离任务。

补齐成熟 agent（Codex/Hermes）的多代理编排。设计：
  - SubAgent：一个**隔离的有界循环**。持有 goal + context + 工具列表，用
    router（如 FallbackRouter/OpenAIProvider）调模型，执行模型发出的
    tool_calls（通过注入的 tool_dispatch），直到给出最终回答或步数耗尽。
  - delegate_task：单个子代理任务 → 摘要。
  - parallel_delegate：多个子代理任务并行（ThreadPoolExecutor），
    按输入顺序返回结果，互不共享状态。

子代理与主 agent 隔离：各自独立 messages、独立步数预算，异常不污染主循环。
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Tuple, Union

from memory_plane.model_router import ModelRequest

ToolDispatch = Callable[[str, dict], str]

_SYSTEM = ("你是子代理。只完成分配给你的任务，必要时调用工具获取事实；"
           "完成时给出简明、可直接返回的结果，不啰嗦。")


class SubAgent:
    """一个隔离的有界子代理执行体。"""

    def __init__(self, goal: str, context: str = "",
                 tools: Tuple[dict, ...] = (), max_steps: int = 6,
                 tool_dispatch: Optional[ToolDispatch] = None,
                 profile: str = "default"):
        self.goal = goal
        self.context = context
        self.tools = tuple(tools)
        self.max_steps = max_steps
        self.tool_dispatch = tool_dispatch
        self.profile = profile

    def _initial_messages(self) -> List[dict]:
        user = (self.context + "\n" if self.context else "") + f"任务: {self.goal}"
        return [{"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user}]

    def run(self, router) -> dict:
        """执行子代理循环，返回 {summary, steps, truncated, ...}。"""
        messages = self._initial_messages()
        for step in range(1, self.max_steps + 1):
            tools = self.tools if self.tool_dispatch else ()
            out = router.complete(
                ModelRequest(messages=tuple(messages), tools=tools,
                             profile=self.profile))
            tool_calls = out.get("tool_calls") or []
            if not tool_calls:
                return {"summary": out.get("content") or "", "steps": step,
                        "truncated": False}
            if not self.tool_dispatch:
                return {"summary": out.get("content") or "", "steps": step,
                        "truncated": True,
                        "note": "模型请求了工具但未启用工具执行"}
            for tc in tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name", "")
                raw = fn.get("arguments") or "{}"
                if isinstance(raw, str):
                    try:
                        args = json.loads(raw) if raw.strip() else {}
                    except Exception:
                        args = {}
                else:
                    args = raw or {}
                try:
                    result = self.tool_dispatch(name, args)
                except Exception as e:
                    # 工具派发失败：把错误作为工具结果回传，不崩整个子代理
                    result = f"<工具 {name} 执行失败: {type(e).__name__}: {e}>"
                messages.append({"role": "assistant", "tool_calls": [tc]})
                messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                                 "name": name, "content": str(result)})
        return {"summary": "（步数耗尽，未得出最终结论）", "steps": self.max_steps,
                "truncated": True}


def delegate_task(router, goal: str, context: str = "",
                  tools: Tuple[dict, ...] = (),
                  tool_dispatch: Optional[ToolDispatch] = None,
                  max_steps: int = 6, profile: str = "default") -> dict:
    """单个子代理任务，返回摘要 dict。"""
    return SubAgent(goal, context, tools, max_steps, tool_dispatch,
                    profile).run(router)


def parallel_delegate(router, tasks: List[Union[Tuple[str, str], Dict]],
                      max_concurrent: int = 4, **kw) -> List[dict]:
    """并行跑多个子代理任务，按输入顺序返回结果。

    tasks: 每个为 dict {"goal","context"} 或 tuple (goal, context)。
    kw 透传给 delegate_task（tools/tool_dispatch/max_steps/profile）。
    """
    results: List[dict] = [{} for _ in tasks]

    def worker(i: int, task) -> Tuple[int, dict]:
        try:
            if isinstance(task, dict):
                g, c = task["goal"], task.get("context", "")
            else:
                g = task[0]
                c = task[1] if len(task) > 1 else ""
            return i, delegate_task(router, g, c, **kw)
        except Exception as e:
            return i, {"summary": f"<子代理异常: {type(e).__name__}: {e}>",
                       "steps": 0, "truncated": True, "error": str(e)}

    with ThreadPoolExecutor(max_workers=max_concurrent) as ex:
        futs = [ex.submit(worker, i, t) for i, t in enumerate(tasks)]
        for f in as_completed(futs):
            i, res = f.result()
            results[i] = res
    return results
