"""Plan 模式 —— 先分解步骤 → 逐步执行+验证，长任务不丢主线。

对标 Claude /plan：把大目标拆成有序步骤，逐步执行、可中断、可续、可看进度。
设计：
  - Plan：goal + 有序 steps，带 per-step 状态(done/failed/skipped)
  - make_plan(goal, decompose)：decompose 把 goal 拆成步骤列表（可注入 LLM 或启发式）
  - execute_plan(plan, step_runner, stop_on_error)：逐步执行；step_runner(step,i)->str|dict
  - 全程确定性、可测（decompose/runner 均可注入），不绑定具体执行引擎
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass
class Step:
    index: int
    desc: str
    status: str = "pending"      # pending/done/failed/skipped
    result: str = ""


@dataclass
class Plan:
    goal: str
    steps: List[Step] = field(default_factory=list)

    @property
    def done(self) -> int:
        return sum(1 for s in self.steps if s.status == "done")

    @property
    def total(self) -> int:
        return len(self.steps)

    def progress(self) -> str:
        if not self.steps:
            return "0/0"
        return f"{self.done}/{self.total}"


# decompose: (goal) -> list[str]（拆成步骤描述）
DecomposeFn = Callable[[str], List[str]]
# step_runner: (step_desc, step_index) -> str（执行单步，返回结果）
StepRunner = Callable[[str, int], str]


def make_plan(goal: str, decompose: Optional[DecomposeFn] = None) -> Plan:
    """按 decompose 把 goal 拆成步骤，生成 Plan。缺省按中文句号/分号切分。"""
    if decompose is None:
        decompose = _default_decompose
    parts = decompose(goal)
    if not parts:
        parts = [goal]  # 兜底：拆不出来就当成单步
    return Plan(goal=goal, steps=[Step(i, d) for i, d in enumerate(parts)])


def _default_decompose(goal: str) -> List[str]:
    """启发式切分：按 。；！？\n 拆句，去空。不用 LLM，确定性。"""
    import re
    parts = re.split(r"[。；！？\n]", goal)
    return [p.strip() for p in parts if p.strip()]


def execute_plan(plan: Plan, runner: StepRunner,
                 stop_on_error: bool = True) -> Dict:
    """逐步执行。返回 {goal, done, failed, results, progress}。"""
    results: List[Dict] = []
    failed = 0
    for step in plan.steps:
        if step.status == "done":
            continue
        try:
            step.result = runner(step.desc, step.index)
            step.status = "done"
        except Exception as e:
            step.status = "failed"
            step.result = f"<失败: {e}>"
            failed += 1
            if stop_on_error:
                break
        results.append({"index": step.index, "desc": step.desc,
                        "status": step.status, "result": step.result})
    return {"goal": plan.goal, "done": plan.done, "failed": failed,
            "results": results, "progress": plan.progress()}
