"""AgentLoop —— 自主执行引擎。

流程（任务驱动，自主推进到完成）：
  submit(goal) ─▶ TaskState(PENDING)
       │  resume/run: PLANNING → brain 规划步骤
       ▼
   EXECUTING：对每步 plan 步骤，调用 brain.chat(带工具) 执行
       │          每步后 check_in（验证门禁 + 失败分类）
       ▼
   VERIFYING → DONE（证据全过） / EXECUTING（缺失打回）
               / FAILED（分类失败）
       │
   交付：把任务结果写进大脑记忆（remember），供未来续跑/检索。

断点续跑：从 task_id 读回 TaskState，next_pending_step() 找到下一步继续，
不重头执行已完成步骤。
"""
from __future__ import annotations

from typing import Callable, Optional

from .task import TaskState, TaskStatus, TaskStore
from .verify import VerificationGate
from .failure import FailureClassifier
from ..safety import (CircuitBreaker, CircuitOpenError, SelfCheck,)


def _safe_recheck(brain, goal: str) -> bool:
    """复验：让大脑快速确认目标是否已达成（简单对话，不调工具）。"""
    try:
        r = brain.chat(f"快速检查：目标'{goal}'是否已完成？只回复 是/否")
        return "是" in r or "yes" in r.lower() or "完成" in r
    except Exception:
        return False


class AgentLoop:
    def __init__(self, data_dir, workspace, brain_factory: Callable,
                 llm=None, max_steps: int = 8,
                 breaker: Optional[CircuitBreaker] = None,
                 selfcheck: Optional[SelfCheck] = None):
        """brain_factory(session) -> BrainPort：从身体拿到当前会话的大脑。"""
        self.store = TaskStore(data_dir)
        self.workspace = workspace
        self.brain_factory = brain_factory
        self.llm = llm
        self.max_steps = max_steps
        self.classifier = FailureClassifier()
        # 稳定性组件：熔断器（防死循环）+ 自查模块（失败后诊断/自愈）
        self.breaker = breaker or CircuitBreaker()
        self.selfcheck = selfcheck or SelfCheck()

    # ---- 生命周期 ----
    def submit(self, goal: str, owner: str = "local",
               plan: Optional[list] = None) -> TaskState:
        t = TaskState(goal=goal, owner=owner, plan=plan)
        if plan:
            t.add_plan(plan)
        self.store.save(t)
        return t

    def get(self, task_id: str) -> Optional[TaskState]:
        return self.store.load(task_id)

    def status(self, task_id: str) -> Optional[dict]:
        t = self.store.load(task_id)
        return t.summary() if t else None

    def cancel(self, task_id: str) -> bool:
        t = self.store.load(task_id)
        if t and t.can(TaskStatus.CANCELED):
            t.transition(TaskStatus.CANCELED)
            self.store.save(t)
            return True
        return False

    def delete(self, task_id: str) -> bool:
        return self.store.delete(task_id)

    # ---- 自主推进 ----
    def run(self, task_id: str, brain, planner=None,
            verifier: Optional[VerificationGate] = None) -> dict:
        """推进一个任务直到 DONE/FAILED（断点续跑：从下一步继续）。

        planner(goal, tools)->[steps]：可选，提供分步。缺省单步=整个目标。
        verifier：可选，交付前验收；缺省跳过验证（直接按完成算）。
        """
        t = self.store.load(task_id)
        if t is None:
            raise KeyError(f"task {task_id} not found")
        if not t.plan:
            if planner:
                t.transition(TaskStatus.PLANNING)
                steps = planner(t.goal, brain)
                t.add_plan(steps or [t.goal])
                self.store.save(t)
            else:
                t.add_plan([t.goal])

        t.transition(TaskStatus.EXECUTING)
        self.store.save(t)

        # 断点续跑：找到下一个未完成的步骤
        idx = t.next_pending_step()
        retry_budget = self.max_steps  # 重试预算：整任务最多 max_steps 次重试，防死循环
        while idx is not None:
            if len(t.steps) >= self.max_steps:
                t.set_failure("not_feasible", f"超过最大步骤 {self.max_steps}")
                t.transition(TaskStatus.FAILED)
                self.store.save(t)
                return t.summary()
            step = t.plan[idx]
            try:
                # 熔断检查：目标若已熔断，直接快速失败，不再尝试
                if not self.breaker.allow():
                    raise CircuitOpenError(t.goal, self.breaker.state)
                # 大脑自主执行这一步（思考 + 调工具）
                result = brain.chat(
                    f"任务步骤（第{idx + 1}/{len(t.plan)}步）: {step}\n"
                    f"整体目标: {t.goal}\n请完成这一步。需要工具就调用工具。")
                self.breaker.record_success()
            except Exception as exc:
                # 失败分类
                fc = self.classifier.classify(str(exc))
                # 熔断记录：保留原始失败分类，仅注明熔断
                if isinstance(exc, CircuitOpenError):
                    t.record_step(idx, step, f"熔断快速失败: {exc}", ok=False)
                    t.set_failure(fc["category"], f"熔断快速失败({exc})")
                    t.transition(TaskStatus.FAILED)
                    self.store.save(t)
                    return t.summary()
                self.breaker.record_failure()
                t.record_step(idx, step, str(exc), ok=False)
                # 自查模块：诊断 → 自愈 → 复验（失败后的第一动作）
                report = self.selfcheck.run(
                    t.goal, category=fc["category"],
                    recheck=lambda goal: _safe_recheck(brain, goal))
                t.record_selfcheck(report.to_dict())
                # 自查成功（自愈 + 复验通过）→ 重试该步，消耗一次重试预算
                if report.ok and retry_budget > 0:
                    retry_budget -= 1
                    # 清掉刚才那条失败记录，重跑该步
                    t.steps = [s for s in t.steps if not (s.get("index") == idx and not s.get("ok"))]
                    self.store.save(t)
                    continue
                if report.ok and retry_budget <= 0:
                    t.set_failure(fc["category"], "重试预算耗尽，仍无法完成该步")
                    t.transition(TaskStatus.FAILED)
                    self.store.save(t)
                    return t.summary()
                t.set_failure(fc["category"], fc["reason"] + " | " + " | ".join(report.findings))
                t.transition(TaskStatus.FAILED)
                self.store.save(t)
                return {**t.summary(), "selfchecks": t.selfchecks}
            t.record_step(idx, step, result, ok=True)
            self.store.save(t)
            idx = t.next_pending_step()

        # 全部步骤完成 → 验证门禁
        if verifier is not None:
            t.transition(TaskStatus.VERIFYING)
            self.store.save(t)
            verdict = verifier.run()
            for e in verifier.evidence:
                t.add_evidence(e.claim, e.verified, e.detail)
            if verdict["passed"]:
                t.transition(TaskStatus.DONE)
                # 记忆记录：任务成果写进大脑，供未来检索/续跑
                self._record_memory(brain, t)
            else:
                t.set_failure("missing_info",
                              "验收未通过: " + "; ".join(
                                  f"{m['claim']}->{m['detail']}"
                                  for m in verdict["missing"]))
                t.transition(TaskStatus.FAILED)
        else:
            t.transition(TaskStatus.DONE)
            self._record_memory(brain, t)
        self.store.save(t)
        return t.summary()

    def _record_memory(self, brain, t: TaskState) -> None:
        """任务成果写进大脑记忆（目标/结果），供未来续跑与检索。"""
        try:
            done = "; ".join(
                f"{i + 1}.{s['result'][:200]}" for i, s in enumerate(t.steps))
            brain.remember(
                f"任务[{t.task_id}]完成: {t.goal}。执行结果: {done}",
                scope="task", tier="recall")
        except Exception:
            pass  # 记忆记录失败不阻塞交付
