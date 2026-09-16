"""Agent Loop —— 自主执行循环（身体层的成熟运行逻辑）。

把「单轮对话」升级为「任务驱动、自主推进到完成」：
  用户/入口 ──▶ AgentLoop ——▶ brain(思考/规划) ──▶ 身体工具执行
                                        │
                    验证门禁 ◀── 结果回喂(继续 or 完成)
                                        │
                  交付回复 + 记忆记录 + 断点续跑

机制：任务状态机 · 验证门禁 · 失败分类 · 记忆记录 · 断点续跑 · 权限分层 · 可观测。

定位：大脑负责思考/人格/单轮工具循环（内核已内置）；本模块在身体层补上
「跨任务生命周期」——状态、验证、续跑、记录。符合「大脑=核心，身体=执行+逻辑」。
"""
from .task import TaskState, TaskStatus, TaskStore
from .loop import AgentLoop
from .verify import VerificationGate, Evidence
from .failure import FailureClassifier

__all__ = [
    "TaskState", "TaskStatus", "TaskStore",
    "AgentLoop",
    "VerificationGate", "Evidence",
    "FailureClassifier",
]