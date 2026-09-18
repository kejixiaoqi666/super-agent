import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import io

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "superbrain-2.0" / "python"))
from superbrain2.core.llm import LLMResponse, ToolCall
from agent_body.runtime import Body


class ScriptedModel:
    """Protocol fixture; real kernel performs all tool dispatch and persistence."""
    def chat(self, messages, tools=None, **kwargs):
        if messages[-1]["role"] == "tool":
            return LLMResponse(content="Observed: " + messages[-1]["content"])
        return LLMResponse(content="", finish_reason="tool_calls", tool_calls=[
            ToolCall("write-1", "write_file", json.dumps({"path": "result.txt", "content": "body works"}))])


class BodyTests(unittest.TestCase):
    def test_telegram_allowlist_and_checkpoint(self):
        from agent_body.telegram import serve
        calls = []
        polls = []
        class FakeBrain:
            def chat_stream(self, text):
                calls.append(("stream", text))
                yield ("text", "ok")
        class FakeBody:
            def brain(self, session):
                calls.append(("brain", session))
                return FakeBrain()
        def response(request, timeout):
            payload = json.loads(request.data)
            method = request.full_url.rsplit("/", 1)[-1]
            if method == "getUpdates":
                polls.append(payload["offset"])
                if len(polls) > 1:
                    raise KeyboardInterrupt()
                result = [
                    {"update_id": 1, "message": {"from": {"id": 9}, "chat": {"id": 9, "type": "private"}, "text": "denied"}},
                    {"update_id": 2, "message": {"from": {"id": 7}, "chat": {"id": -1, "type": "group"}, "text": "denied"}},
                    {"update_id": 3, "message": {"from": {"id": 7}, "chat": {"id": 7, "type": "private"}, "text": "allowed"}},
                ]
            elif method == "sendMessage":
                result = {"message_id": 1}   # 需返回 message_id 供流式 edit
            else:
                result = {}
            return io.BytesIO(json.dumps({"ok": True, "result": result}).encode())
        with tempfile.TemporaryDirectory() as temp:
            body = FakeBody()
            body.data_dir = Path(temp)
            with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake", "TELEGRAM_ALLOWED_USERS": "7"}), patch("urllib.request.urlopen", side_effect=response):
                with self.assertRaises(KeyboardInterrupt):
                    serve(body)
            # 只处理了白名单7的私聊"allowed"，且走流式(brain.chat_stream)
            self.assertIn(("brain", "telegram:7"), calls)
            self.assertIn(("stream", "allowed"), calls)
            # 拒绝的(id9, group)不触发 brain
            self.assertFalse(any(c[0] == "stream" and c[1] != "allowed" for c in calls))
            self.assertEqual(polls, [0, 4])
            self.assertEqual(json.loads((Path(temp) / "telegram-offset.json").read_text()), 4)

    def test_real_kernel_tool_roundtrip_restart_and_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            body = Body(root / "state", root / "work", llm=ScriptedModel())
            try:
                reply = body.chat("a", "write result.txt")
                self.assertIn("Observed", reply["reply"])
                self.assertEqual((root / "work" / "result.txt").read_text(), "body works")
                body.brain("a").remember("session alpha private fact")
                self.assertEqual(body.brain("b").memory_count(), 0)
                body.brain("a").set_personality_mode("guided")
            finally:
                body.close()
            reopened = Body(root / "state", root / "work", llm=ScriptedModel())
            try:
                self.assertGreater(reopened.brain("a").memory_count(), 0)
                self.assertEqual(reopened.brain("a").personality_mode(), "guided")
                self.assertIn("thoughts", reopened.tick("a"))
            finally:
                reopened.close()

    def test_read_only_and_workspace_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            body = Body(root / "state", root / "work", mode="read-only", llm=ScriptedModel())
            try:
                body.chat("a", "write a file")
                self.assertFalse((root / "work" / "result.txt").exists())
                with self.assertRaises(PermissionError):
                    body._read("../outside.txt")
                with self.assertRaises(PermissionError):
                    body._exec("echo blocked")
            finally:
                body.close()

    def test_owner_mode_exec(self):
        with tempfile.TemporaryDirectory() as temp:
            body = Body(Path(temp) / "state", Path(temp) / "work", mode="unrestricted", llm=ScriptedModel())
            try:
                result = body._exec('echo body-execution-ok')
                self.assertEqual(result["exit_code"], 0)
                self.assertIn("body-execution-ok", result["output"])
            finally:
                body.close()
