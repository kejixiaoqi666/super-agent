"""Single-threaded body. Kernel instances and SQLite connections stay on their owner thread."""
import subprocess
import time
from pathlib import Path
from typing import Optional, List

from .kernel import BrainTool, default_registry
from .loop import AgentLoop, VerificationGate
from .stats import TokenStats
from . import config as cfg
from .context import ProjectContext
from .curate import Curator
from .budget import ContextBudget, estimate_tokens
from .storage import Storage
from .assets import AssetStore
from .images import ImageStore
from .vault import Vault


class BodyPolicy:
    def __init__(self, mode):
        if mode not in ("read-only", "workspace", "unrestricted"):
            raise ValueError("mode must be read-only, workspace or unrestricted")
        self.mode = mode

    def needs_approval(self, tool_name, category, side_effects):
        return (self.mode == "read-only" and side_effects in ("write", "exec")) or (
            self.mode == "workspace" and side_effects == "exec")


class Body:
    def __init__(self, data_dir, workspace, mode="workspace", llm=None):
        self.data_dir = Path(data_dir).resolve()
        self.workspace = Path(workspace).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.policy = BodyPolicy(mode)
        self.llm = llm
        self.brains = {}
        self.closed = False
        # AgentLoop：自主执行引擎（任务状态机/验证门禁/记忆记录/断点续跑）
        self.loop = AgentLoop(str(data_dir), workspace, self.brain)
        # TokenStats：token 计费 + 上下文统计
        self.stats = TokenStats(str(data_dir))
        # 当前对话模型（供计费换算）
        self.model = cfg.load().get("SUPERBRAIN_LLM_MODEL", "default")
        # Phase 2 上下文治理：项目感知精挑输入 + 上下文预算
        self.project = ProjectContext(str(data_dir))
        self.curator = Curator()
        self.budget = ContextBudget(str(data_dir))
        # Phase 4 存储/资产/图片 + Phase 5 密码本
        self.storage = Storage(data_dir)
        self.assets = AssetStore(self.storage)
        self.images = ImageStore(self.assets)
        self.vault_path = self.storage.config("vault.json")  # 密码本落点
        self.tools = ["read_file", "write_file", "shell", "search_files",
                      "web_search", "web_extract", "memory"]

    # ---- Phase 4/5 便捷入口（供 CLI/TUI/Bot 用）----
    def open_vault(self, master_password: str) -> Vault:
        """打开（或首次创建）密码本。主密码不落盘，仅用于派生密钥。"""
        return Vault(self.data_dir, master_password)

    def gc(self, dry_run: bool = False) -> dict:
        """按保留时长回收过期资产/缓存。"""
        return self.storage.collect_garbage(dry_run=dry_run)

    def storage_report(self) -> dict:
        return self.storage.size_report()

    def _path(self, path):
        target = (self.workspace / path).resolve()
        if self.policy.mode != "unrestricted" and not target.is_relative_to(self.workspace):
            raise PermissionError("path is outside the configured workspace")
        return target

    def _read(self, path):
        with self._path(path).open(encoding="utf-8") as stream:
            return stream.read(16000)

    def _write(self, path, content):
        if self.policy.mode == "read-only":
            raise PermissionError("read-only mode")
        target = self._path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"written": str(target), "bytes": len(content.encode("utf-8"))}

    def _exec(self, command):
        if self.policy.mode != "unrestricted":
            raise PermissionError("shell requires unrestricted mode")
        # Output goes to a file so a noisy child cannot exhaust host memory.
        import tempfile
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(command, shell=True, cwd=self.workspace,
                                    stdout=output, stderr=subprocess.STDOUT, timeout=30)
            output.seek(0)
            return {"exit_code": result.returncode,
                    "output": output.read(16000).decode("utf-8", errors="replace")}

    def brain(self, session):
        if self.closed:
            raise RuntimeError("body is closed")
        if not isinstance(session, str) or not session.strip():
            raise ValueError("session must be a nonempty string")
        if session not in self.brains:
            # 走孔位层实例化大脑；参数显式传入（不同 session 各自独立实例）。
            # 大脑是独立更新的内核，身体不直接 import 内核内部 → 内核升级/换内核不触碰身体。
            port = default_registry().build(
                "superbrain",
                data_dir=str(self.data_dir), session=session,
                llm=self.llm, enable_learning=self.llm is None)
            if not port.ready():
                port.close()
                raise RuntimeError("superbrain kernel not ready: " +
                                   str(port.health()))
            tools = [
                BrainTool("read_file", "Read a UTF-8 workspace file",
                          {"path": {"type": "string"}}, self._read, "read"),
                BrainTool("write_file", "Write a UTF-8 workspace file",
                          {"path": {"type": "string"}, "content": {"type": "string"}},
                          self._write, "write"),
                BrainTool("exec", "Run a shell command in workspace (30 second timeout)",
                          {"command": {"type": "string"}}, self._exec, "exec"),
            ]
            try:
                port.attach_tools(tools)
                port.set_permissions(self.policy)
            except Exception:
                port.close()
                raise
            self.brains[session] = port
        return self.brains[session]

    def chat(self, session, message, person_id=None):
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a nonempty string")
        brain = self.brain(session)
        started = time.monotonic()
        # 项目感知精挑：只喂相关记忆/工具线索，不塞废话
        curated = self.curator.curate(brain, self.project, message, self.tools)
        prompt = curated.to_prompt()
        reply = brain.chat(prompt, person_id=person_id or session)
        brain.save()
        # token 计费 + 上下文统计 + 预算记账
        try:
            pt = estimate_tokens(prompt)
            ct = estimate_tokens(reply)
            self.stats.record(session, message, reply, model=self.model)
            self.budget.record(session, pt, ct)
        except Exception:
            pass
        return {
            "reply": reply,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "dropped_memory": curated.dropped,
        }

    def set_project(self, project: str, goal: str = "",
                    tags: Optional[List[str]] = None, focus: str = "") -> dict:
        """设定当前项目上下文（供 CLI/TUI 调用）。"""
        self.project.set_project(project, goal, tags, focus)
        return {"set": project, "goal": goal, "tags": tags or []}

    def context_status(self) -> dict:
        return {
            "project": self.project.describe(),
            "budget": self.budget.status(),
            "tools": len(self.tools),
        }

    def tick(self, session):
        brain = self.brain(session)
        result = brain.tick()
        brain.save()
        return result

    # ---- AgentLoop 接入：任务驱动自主执行 ----
    def run_task(self, goal, session="task", owner="local",
                 verify_claims=None):
        """提交并运行一个自主任务，返回任务摘要。

        verify_claims: [{claim, kind, path/content/...}, ...] 可选，交付前验收。
        """
        brain = self.brain(session)
        task = self.loop.submit(goal, owner=owner)
        gate = None
        if verify_claims:
            gate = VerificationGate(self.workspace)
            for c in verify_claims:
                claim = c.pop("claim"); kind = c.pop("kind")
                gate.add(claim, kind, **c)
        return self.loop.run(task.task_id, brain, verifier=gate)

    def task_status(self, task_id=None):
        """查任务进度；task_id 缺省列出所有任务摘要。"""
        if task_id:
            return self.loop.status(task_id)
        return [t.summary() for t in self.loop.store.list()]

    def task_cancel(self, task_id):
        return self.loop.cancel(task_id)

    def task_delete(self, task_id):
        return self.loop.delete(task_id)

    def close(self):
        errors = []
        for brain in self.brains.values():
            try:
                brain.save()
            except Exception as exc:
                errors.append(exc)
            finally:
                brain.close()
        self.brains.clear()
        self.closed = True
        if errors:
            raise errors[0]
