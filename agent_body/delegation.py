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
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

from memory_plane.model_router import ModelRequest

ToolDispatch = Callable[[str, dict], str]


class DelegationLimitError(ValueError):
    """委派超限（批次过大 / 并行 cap / 步预算不足）。"""


@dataclass
class GovernedPlan:
    """经资源治理后的执行方案。"""
    concurrency: int          # 实际并行数（受到 cap 约束）
    steps_each: int           # 每个子代理的步数上限（可能被预算压低）
    total_steps: int          # 总可能步数 n*steps_each
    warnings: List[str] = field(default_factory=list)


class DelegationGovernor:
    """并行/资源治理 —— 让多子代理调度"受控"，不爆成本。

    - max_concurrent ：全局并行硬上限，调用方想开更多也会被压到 cap
    - max_batch      ：单批任务数上限，超了直接拒绝（避免误传巨批）
    - total_step_budget：整批总步数预算。len×每任务步数超了 → 按比例压低
      每任务步数（地板1），宁卿一步也别烧爆 token/时间。
    纯逻辑、可注入，便于测试；parallel_delegate 用它，而非各自硬编码 4。
    """

    def __init__(self, max_concurrent: int = 4, max_batch: int = 32,
                 total_step_budget: int = 64, per_default_steps: int = 6) -> None:
        self.max_concurrent = max(1, max_concurrent)
        self.max_batch = max(1, max_batch)
        self.total_step_budget = max(1, total_step_budget)
        self.per_default_steps = max(1, per_default_steps)

    def govern(self, n_tasks: int, requested_concurrent: Optional[int] = None,
               requested_steps: Optional[int] = None) -> GovernedPlan:
        """给定任务数/请求并发/请求步数 → 输出受控执行方案。超限抛错。"""
        if n_tasks <= 0:
            raise DelegationLimitError("no tasks to delegate")
        if n_tasks > self.max_batch:
            raise DelegationLimitError(
                f"batch size {n_tasks} exceeds max_batch {self.max_batch}")
        # 并行 cap：取 min，绝不超过全局上限
        concurrency = self.max_concurrent
        if requested_concurrent is not None:
            concurrency = max(1, requested_concurrent)
        if concurrency > self.max_concurrent:
            concurrency = self.max_concurrent
        # 步预算：total_budget // n 得出每任务许可步数，按比例压低
        steps = requested_steps or self.per_default_steps
        steps = max(1, steps)
        warnings: List[str] = []
        if n_tasks * steps > self.total_step_budget:
            scaled = max(1, self.total_step_budget // n_tasks)
            if scaled < steps:
                warnings.append(
                    f"步预算受限: 每任务 {steps}→{scaled} 步 "
                    f"(总预算 {self.total_step_budget})")
            steps = scaled
        return GovernedPlan(concurrency=concurrency, steps_each=steps,
                            total_steps=n_tasks * steps, warnings=warnings)


# ---- 会话权限隔离 ----
# 工具名 → (类别, 副作用) 映射；未知工具按只读(read)处理，绝不默认放行写/执行。
_SIDE: Dict[str, Tuple[str, str]] = {
    "write_file": ("file", "write"),
    "exec": ("shell", "exec"),
    "shell": ("shell", "exec"),
    "git": ("git", "exec"),
}


def _safe_session(session: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", str(session)) or "sess"
    return s[:64]


def make_session_scoped_dispatch(policy, session: str, workspace_root,
                                 ops: Optional[Dict[str, Callable]] = None):
    """构造"会话隔离 + 权限受限"的委派工具派发。

    让被委派的子代理**默认只读**（即使主 body 是 unrestricted/workspace），
    且写/执行交互落到该 session 专属沙箱目录，从而：
      ① 委派子代理无法误写主工作区 / 执行危险命令
      ② 并行子代理各写各的沙箱，互不覆盖
      ③ 不同会话数据不互相污染（会话权限隔离）

    policy 需 duck-type：needs_approval(name, category, side_effects) -> bool。
    ops 可选扩展：{'read_file','write_file','exec'} 的自定义实现。
    返回 (dispatch, sandbox_path)。
    """
    root = Path(workspace_root)
    sandbox = root / ".delegation" / _safe_session(session)
    sandbox.mkdir(parents=True, exist_ok=True)

    def _scoped_write(name: str, args: dict) -> str:
        if not isinstance(args, dict) or not args.get("path"):
            return "<write_file 缺 path>"
        rel = Path(str(args["path"]))
        if rel.is_absolute():
            # 绝对路径强制折回沙箱相对根
            try:
                rel = rel.relative_to(root)
            except ValueError:
                rel = Path(rel.name)
        target = (sandbox / rel).resolve()
        if not target.is_relative_to(sandbox):   # 防 ../ 逃逸
            return "<权限拒绝: 写入目标逃出委派沙箱>"
        target.parent.mkdir(parents=True, exist_ok=True)
        content = str(args.get("content", ""))
        try:
            target.write_text(content, encoding="utf-8")
        except OSError as e:
            return f"<写入失败: {type(e).__name__}: {e}>"
        return f"已写入委派沙箱: {target}"

    def _scoped_read(name: str, args: dict) -> str:
        if not isinstance(args, dict) or not args.get("path"):
            return "<read_file 缺 path>"
        p = Path(str(args["path"]))
        # 先试沙箱内，再试工作区只读
        for base in (sandbox, root):
            cand = (base / p).resolve() if not p.is_absolute() else p
            if cand.exists() and cand.is_file():
                try:
                    return cand.read_text("utf-8")[:16000]
                except OSError as e:
                    return f"<读取失败: {type(e).__name__}: {e}>"
        return "<文件不存在>"

    def dispatch(name: str, args: dict) -> str:
        cat = _SIDE.get(name, ("other", "read"))
        if policy is not None and policy.needs_approval(name, *cat):
            return (f"<权限拒绝: 委派内 {name} 属 {cat[1]}, 当前策略不允许>")
        if name == "write_file":
            return _scoped_write(name, args)
        if name == "read_file":
            return _scoped_read(name, args)
        if name == "exec":
            cmd = str(args.get("command", "")) if isinstance(args, dict) else ""
            return (f"<委派 exec 已隔离: 只读委派不执行命令, 收到: {cmd}>")
        if ops and name in ops:
            try:
                return str(ops[name](args))
            except Exception as e:
                return f"<委派工具 {name} 异常: {type(e).__name__}: {e}>"
        return f"<未知委派工具: {name}>"

    return dispatch, sandbox


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
                      max_concurrent: int = 4,
                      governor: Optional[DelegationGovernor] = None, **kw) -> List[dict]:
    """并行跑多个子代理任务，按输入顺序返回结果。

    tasks: 每个为 dict {"goal","context"} 或 tuple (goal, context)。
    kw 透传给 delegate_task（tools/tool_dispatch/max_steps/profile）。
    资源治理：传入 governor 时，并行数被压到 cap、每任务步数按总预算压低，
    批次过大直接抛 DelegationLimitError；未传则保留朴素的 max_concurrent 兼容。
    """
    # ---- 资源治理 ----
    if governor is not None:
        plan = governor.govern(len(tasks), requested_concurrent=max_concurrent,
                               requested_steps=kw.get("max_steps"))
        # 预算修正：无论调用方是否显式传 max_steps，都必须施加受控步数
        # （否则显式传 max_steps 会绕过 governor 的步预算缩减——真实预算绕过 bug）
        kw["max_steps"] = plan.steps_each
        eff_concurrent = plan.concurrency
    else:
        eff_concurrent = max(1, max_concurrent)

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

    with ThreadPoolExecutor(max_workers=eff_concurrent) as ex:
        futs = [ex.submit(worker, i, t) for i, t in enumerate(tasks)]
        for f in as_completed(futs):
            i, res = f.result()
            results[i] = res
    return results
