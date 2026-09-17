import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "superbrain-2.0" / "python"))
from superbrain2.core.llm import LLMResponse, ToolCall

from agent_body.runtime import Body


class _Scripted:
    """离线脑替身：先写文件(hook 已见)，再回读观察到的事实。"""
    def chat(self, messages, tools=None, **kwargs):
        if messages[-1]["role"] == "tool":
            return LLMResponse(content="Observed: " + messages[-1]["content"])
        return LLMResponse(
            content="", finish_reason="tool_calls",
            tool_calls=[ToolCall("write-1", "write_file",
                                 json.dumps({"path": "result.txt",
                                             "content": "报告已就绪"}))])


class RuntimeWiringTest(unittest.TestCase):
    def _make_body(self) -> Body:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        return Body(root / "state", root / "work", llm=_Scripted())

    def test_chat_records_transcript_and_search_finds(self):
        body = self._make_body()
        try:
            body.chat("s2", "节点线路卡顿不稳定，帮我排查")
            hits = body.search_sessions("节点线路卡顿")
            self.assertTrue(hits, "chat 应把对话记入 transcript 供 /search 检索")
            self.assertEqual(hits[0]["session"], "s2")
        finally:
            body.close()

    def test_cron_run_due_with_fake_executor(self):
        body = self._make_body()
        try:
            # 用过去的 one-shot：立即到期
            body.cron_add("j1", "@2026-01-01T00:00:00",
                          payload={"prompt": "巡检"})
            saw = []
            body.cron_run_due(executor=lambda job: saw.append(job["id"]))
            self.assertEqual(saw, ["j1"])
        finally:
            body.close()

    def test_default_cron_executor_runs_prompt_via_chat(self):
        body = self._make_body()
        try:
            body.cron_add("j2", "@2026-01-01T00:00:00",
                          payload={"prompt": "生成巡检报告"})
            ran = body.cron_run_due()  # 用默认 executor -> self.chat
            self.assertEqual(ran, ["j2"])
            self.assertIn("j2", body.cron_output)
            self.assertIn("Observed", body.cron_output["j2"])
        finally:
            body.close()

    def test_search_sessions_degrades_gracefully(self):
        body = self._make_body()
        try:
            body.transcript = None
            self.assertIn("error", body.search_sessions("q")[0])
        finally:
            body.close()

    def test_chat_image_builds_multimodal_and_replies(self):
        import io
        from PIL import Image
        body = self._make_body()
        try:
            buf = io.BytesIO()
            Image.new("RGB", (32, 32), (200, 30, 30)).save(buf, "PNG")
            img_bytes = buf.getvalue()
            r = body.chat_image("s9", img_bytes, message="看看这张图")
            self.assertIn("reply", r)
            self.assertTrue(r["reply"])
        finally:
            body.close()

    def test_exec_fires_pre_post_hooks(self):
        root = Path(tempfile.mkdtemp())
        body = Body(root / "state", root / "work", mode="unrestricted",
                    llm=_Scripted())
        try:
            order = []
            body.hooks.register("pre_tool", lambda **c: order.append("pre"))
            body.hooks.register("post_tool", lambda **c: order.append("post"))
            r = body._exec("echo hi")
            self.assertEqual(r["exit_code"], 0)
            self.assertEqual(order, ["pre", "post"])
            self.assertEqual(body.hooks.fired("pre_tool"), 1)
            self.assertIn("hi", r["output"])
        finally:
            body.close()

    def test_plan_wiring(self):
        body = self._make_body()
        try:
            rep = body.plan("先A。再B", runner=lambda desc, i: f"做了{i}")
            self.assertEqual(rep["done"], 2)
            self.assertEqual(rep["progress"], "2/2")
            self.assertEqual(len(rep["results"]), 2)
        finally:
            body.close()

    def test_close_releases_resources(self):
        body = self._make_body()
        body.close()
        # transcript 连接已关闭并置空（防文件句柄泄漏）
        self.assertIsNone(body.transcript)


if __name__ == "__main__":
    unittest.main()