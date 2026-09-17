"""Single-threaded body. Kernel instances and SQLite connections stay on their owner thread."""
import subprocess
import time
from pathlib import Path
from typing import Optional, List, Union

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
from .struct import ensure as _ensure_schema
from .mcp import MCPClient as _MCPClient
from .observe import Tracer, get_logger
from .plugins import PluginRegistry


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
        # TaskQueue：连贯任务队列（串行+依赖，接入 run_chain）
        from .loop import TaskQueue
        self.queue = TaskQueue(str(data_dir), self.loop.store)
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
        # 可观测性：结构化 trace
        self.tracer = Tracer(data_dir)
        self.logger = get_logger("super-agent", data_dir)
        # 插件系统：从 data_dir/plugins + 资产区 plugins 发现
        self.plugins = PluginRegistry([Path(data_dir) / "plugins",
                                       self.storage.assets_dir / "plugins"])
        self.tools = ["read_file", "write_file", "shell", "search_files",
                      "web_search", "web_extract", "memory"]
        # 技能系统：发现工作区/资产区/数据目录的 skills/**/SKILL.md
        from .skills import SkillStore
        self.skills = SkillStore(Path(workspace) / "skills",
                                 self.storage.assets_dir / "skills",
                                 Path(data_dir) / "skills")
        # 定时任务：@every / cron / 一次性，持久化到 scheduler.json
        from .scheduler import CronScheduler
        self.scheduler = CronScheduler(Path(data_dir))
        self.cron_output: dict = {}           # cron 最近执行结果(job_id -> text)
        # 会话全文检索：自动给每个对话记入 transcript.db，供 /search 检索
        self.transcript = None
        try:
            from .session_store import SessionStore
            self.transcript = SessionStore(Path(data_dir) / "transcript.db")
        except Exception:
            self.transcript = None           # 无 FTS5 时优雅降级

    # ---- 会话全文检索 ----
    def search_sessions(self, query: str, k: int = 10) -> list:
        if self.transcript is None:
            return [{"error": "会话检索不可用(缺 FTS5)"}]
        return self.transcript.search(query, k)

    # ---- 定时任务（cron）----
    def cron_add(self, job_id: str, spec: str, payload=None) -> dict:
        return self.scheduler.add(job_id, spec, payload=payload)

    def cron_list(self) -> list:
        return [{"id": j["id"], "spec": j["spec"],
                 "next_run": j["next_run"], "count": j["count"]}
                for j in self.scheduler.jobs.values()]

    def cron_rm(self, job_id: str) -> bool:
        return self.scheduler.remove(job_id)

    def cron_run_due(self, executor=None) -> list:
        """执行所有到期任务。executor(job_dict) 缺省用 _default_cron_executor
        （跑 payload.prompt 经大脑，结果存 self.cron_output）。"""
        executor = executor or self._default_cron_executor
        return self.scheduler.run_due(runner=executor)

    def _default_cron_executor(self, job: dict) -> None:
        job_id = job.get("id", "?")
        prompt = (job.get("payload") or {}).get("prompt", "")
        if not prompt:
            self.cron_output[job_id] = "(该任务无提示词)"
            return
        try:
            r = self.chat("cron:" + job_id, prompt)
            self.cron_output[job_id] = r["reply"]
        except Exception as e:
            self.cron_output[job_id] = f"<cron 执行失败: {e}>"

    # ---- 技能（skills/**/SKILL.md）----
    def scan_skills(self) -> list:
        """发现并列出技能元数据。"""
        return [{"name": s.name, "version": s.version,
                 "description": s.description, "path": str(s.path)}
                for s in self.skills.list()]

    def skill_instructions(self, *names: str, include_body: bool = True) -> str:
        """渲染选中的技能为可注入上下文的指令块；未知名字静默跳过。"""
        return self.skills.instructions(*names, include_body=include_body)

    # ---- 委派 / 子代理（delegation）----
    def build_router(self):
        """从环境构造模型路由：有 SA_PROVIDER_*（或 OPENAI_*）则用真实 provider，
        否则回退离线 Echo（测试/无钥匙环境可用）。"""
        import os
        base = os.environ.get("SA_PROVIDER_BASE", os.environ.get("OPENAI_BASE_URL"))
        key = os.environ.get("SA_PROVIDER_KEY", os.environ.get("OPENAI_API_KEY"))
        model = os.environ.get("SA_PROVIDER_MODEL") or "default"
        from memory_plane.model_router import (EchoProvider, FallbackRouter,
                                               ModelRouter, OpenAIProvider,
                                               ProviderConfig)
        if base and key:
            return FallbackRouter(
                [OpenAIProvider(ProviderConfig("primary", base, key, model))],
                routes={"default": "primary"})
        return ModelRouter([EchoProvider()])

    def delegate(self, goal: str, context: str = "", max_steps: int = 6,
                 tool_dispatch=None) -> dict:
        """委派一个子代理任务（用环境配置的 provider；无则 Echo 离线）。"""
        from .delegation import delegate_task
        return delegate_task(self.build_router(), goal, context,
                             max_steps=max_steps, tool_dispatch=tool_dispatch)

    # ---- Phase 4/5 便捷入口（供 CLI/TUI/Bot 用）----
    def open_vault(self, master_password: str) -> Vault:
        """打开（或首次创建）密码本。主密码不落盘，仅用于派生密钥。"""
        return Vault(self.data_dir, master_password)

    def gc(self, dry_run: bool = False) -> dict:
        """按保留时长回收过期资产/缓存。"""
        return self.storage.collect_garbage(dry_run=dry_run)

    def storage_report(self) -> dict:
        return self.storage.size_report()

    def mcp_client(self, name: str, command: str, args=None, cwd=None):
        """连接一个外部 MCP 服务器（stdio），返回已握手客户端。"""
        client = _MCPClient(command, args or [], cwd=cwd, name=name)
        client.connect()
        return client

    # ---- 断点续跑 / 插件 / 观测 ----
    def pending_tasks(self) -> list:
        """未完成清单（FAILED/CANCELED 可续跑任务）。"""
        from .resume import resume_list
        return resume_list(self.loop)

    def resume_tasks(self, task_id: Optional[str] = None,
                     session: str = "local") -> list:
        """续跑全部（或指定）未完成任务。返回各任务最终摘要。"""
        from .resume import resume_one, resume_all
        brain = self.brain(session)
        if task_id:
            return [resume_one(self.loop, task_id, brain)]
        return resume_all(self.loop, brain)

    def scan_plugins(self) -> list:
        """发现并返回插件清单（不启用）。"""
        return self.plugins.scan()

    def enable_capability(self, cap: str) -> list:
        """启用所有声明了某能力的插件，返回启用的清单。"""
        return [p.summary() for p in self.plugins.enable_capability(cap)]

    def _path(self, path):
        target = (self.workspace / path).resolve()
        if self.policy.mode != "unrestricted" and not target.is_relative_to(self.workspace):
            raise PermissionError("path is outside the configured workspace")
        return target

    _READ_SCHEMA = {"type": "object", "required": ["path"],
                    "properties": {"path": {"type": "string"}}, "additionalProperties": False}
    _WRITE_SCHEMA = {"type": "object", "required": ["path", "content"],
                     "properties": {"path": {"type": "string"},
                                    "content": {"type": "string"}},
                     "additionalProperties": False}
    _EXEC_SCHEMA = {"type": "object", "required": ["command"],
                    "properties": {"command": {"type": "string"}}, "additionalProperties": False}

    def _read(self, path):
        _ensure_schema({"path": path}, self._READ_SCHEMA, "read_file 入参")
        with self._path(path).open(encoding="utf-8") as stream:
            return stream.read(16000)

    def _write(self, path, content):
        _ensure_schema({"path": path, "content": content}, self._WRITE_SCHEMA,
                       "write_file 入参")
        if self.policy.mode == "read-only":
            raise PermissionError("read-only mode")
        target = self._path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"written": str(target), "bytes": len(content.encode("utf-8"))}

    def _exec(self, command):
        _ensure_schema({"command": command}, self._EXEC_SCHEMA, "exec 入参")
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
                BrainTool("web_search", "Search the web (DuckDuckGo, keyless)",
                          {"query": {"type": "string"},
                           "limit": {"type": "integer"}}, self._web_search, "read"),
                BrainTool("web_extract", "Fetch a URL and extract readable text",
                          {"url": {"type": "string"},
                           "max_chars": {"type": "integer"}},
                          self._web_extract, "read"),
                BrainTool("git", "Git ops: status/diff/commit/log/push/branch",
                          {"op": {"type": "string"},
                           "args": {"type": "array"}},
                          lambda a: self._git(a.get("op", ""), *a.get("args", [])),
                          "exec"),
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
        with self.tracer.span("curate", session=session):
            curated = self.curator.curate(brain, self.project, message,
                                          self.tools, skills_store=self.skills)
            prompt = curated.to_prompt()
        with self.tracer.span("chat", session=session):
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
        # 会话全文检索：把对话记入 transcript（无 FTS5 时 self.transcript 为 None）
        if self.transcript is not None:
            try:
                self.transcript.record(session, message, role="user")
                self.transcript.record(session, reply, role="assistant")
            except Exception:
                pass
        return {
            "reply": reply,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "dropped_memory": curated.dropped,
        }

    def chat_image(self, session: str, image: Union[str, Path, bytes],
                   message: str = "看看这张图", person_id=None) -> dict:
        """带图对话：读图 → base64 data URL → 交给大脑多模态识别（模型识图，非本地OCR）。"""
        import base64
        if isinstance(image, bytes):
            raw = image
        else:
            raw = Path(image).read_bytes()
        if not raw:
            raise ValueError("无法读取图片")
        mime = "image/png"
        if isinstance(image, (str, Path)):
            ext = str(image).lower()
            if ext.endswith((".jpg", ".jpeg")):
                mime = "image/jpeg"
            elif ext.endswith(".webp"):
                mime = "image/webp"
        data_url = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
        brain = self.brain(session)
        with self.tracer.span("chat_image", session=session):
            reply = brain.chat(message, person_id=person_id or session,
                               images=[data_url])
            brain.save()
        if self.transcript is not None:
            try:
                self.transcript.record(session, f"[图] {message}", role="user")
                self.transcript.record(session, reply, role="assistant")
            except Exception:
                pass
        return {"reply": reply}

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

    # ---- Web 工具（真实可用，供大脑调用）----
    def _git(self, op: str, *args) -> str:
        """git 操作封装（作用于工作区）。op: status/diff/diffstat/commit/log/push/branch/isrepo。"""
        import agent_body.git as G
        wd = self.workspace
        if op == "status":
            return "\n".join(G.status(wd)) or "(无改动)"
        if op == "diff":
            staged = bool(args and args[0] == "--cached")
            return G.diff(wd, staged=staged) or "(无差异)"
        if op == "diffstat":
            import json
            return json.dumps(G.diff_stat(wd), ensure_ascii=False)
        if op == "commit":
            if not args:
                return "commit 需要提交信息"
            return G.commit(wd, args[0])
        if op == "log":
            n = int(args[0]) if args and str(args[0]).isdigit() else 10
            return "\n".join(f"{h} {s}" for h, s in G.log(wd, n))
        if op == "push":
            return G.push(wd, args[0] if args else None) or "已推送"
        if op == "branch":
            return G.current_branch(wd)
        if op == "isrepo":
            return str(G.is_repo(wd)).lower()
        raise ValueError(f"未知 git 操作: {op}")

    def _web_search(self, args: dict) -> str:
        from .web import web_search, WebError
        q = str(args.get("query", "")).strip()
        if not q:
            return "web_search 需要 query 参数"
        try:
            res = web_search(q, limit=int(args.get("limit", 5)))
        except WebError as e:
            return f"web_search 失败: {e}"
        lines = [f"{i + 1}. {r['title']}\n   {r['url']}"
                 + (f"\n   {r['snippet'][:120]}" if r.get("snippet") else "")
                 for i, r in enumerate(res)]
        return "\n".join(lines)

    def _web_extract(self, args: dict) -> str:
        from .web import web_extract, WebError
        url = str(args.get("url", "")).strip()
        if not url:
            return "web_extract 需要 url 参数"
        try:
            return web_extract(url, max_chars=int(args.get("max_chars", 4000)))
        except WebError as e:
            return f"web_extract 失败: {e}"

    # ---- AgentLoop 接入：任务驱动自主执行 ----
    def run_task(self, goal, session="task", owner="local",
                 verify_claims=None):
        """提交并运行一个自主任务，返回任务摘要。

        verify_claims: [{claim, kind, path/content/...}, ...] 可选，交付前验收。
        注意：不修改调用方传入的 verify_claims（内部拷贝）。
        """
        brain = self.brain(session)
        task = self.loop.submit(goal, owner=owner)
        gate = None
        if verify_claims:
            gate = VerificationGate(self.workspace)
            for c in verify_claims:
                claim = c["claim"]; kind = c["kind"]
                # 拷贝剩余字段，避免 c.pop 破坏调用方字典
                extra = {k: v for k, v in c.items() if k not in ("claim", "kind")}
                gate.add(claim, kind, **extra)
        return self.loop.run(task.task_id, brain, verifier=gate)

    def run_chain(self, goals, chain_id="chain", session="task"):
        """串行执行一串有依赖的任务(连贯任务队列)。

        goals: [目标1, 目标2, ...]，按序执行，前一个 DONE 才进下一个；
        任一环失败 → 该环及后续全部进未完成清单，可 /resume 续跑。
        返回各任务的最终摘要列表。
        """
        self.queue.enqueue(chain_id, list(goals))
        summaries = []
        while True:
            item = self.queue.next()
            if item is None:
                break
            brain = self.brain(session)
            summary = self.loop.run(item.task_id, brain)
            status = summary.get("status")
            if status == "done":
                self.queue.on_task_done(item.task_id)
            else:
                # 失败/取消：该环及后续同链任务进未完成清单
                affected = self.queue.on_task_failed(item.task_id)
                summaries.append(summary)
                summaries.extend(
                    {"task_id": a.task_id, "goal": a.goal, "status": "pending"}
                    for a in affected)
                break
            summaries.append(summary)
        return summaries

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
