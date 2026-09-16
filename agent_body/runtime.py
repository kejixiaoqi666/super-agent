"""Single-threaded body. Kernel instances and SQLite connections stay on their owner thread."""
import subprocess
import time
from pathlib import Path

from .kernel import BrainTool, default_registry


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
        reply = brain.chat(message, person_id=person_id or session)
        brain.save()
        return {"reply": reply, "elapsed_seconds": round(time.monotonic() - started, 3)}

    def tick(self, session):
        brain = self.brain(session)
        result = brain.tick()
        brain.save()
        return result

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
