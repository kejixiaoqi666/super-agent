"""Phase 6 专项测试：结构化输出 / 流式输出。"""
import unittest

from agent_body.struct import validate, ensure, SchemaError, tool_schema
from agent_body.stream import chunk_text, stream_telegram, StreamBuffer


class StructTest(unittest.TestCase):
    def test_valid_object(self):
        ok, errs = validate({"path": "/x", "recursive": True},
                            {"type": "object",
                             "required": ["path"],
                             "properties": {"path": {"type": "string"},
                                            "recursive": {"type": "boolean"}}})
        self.assertTrue(ok)

    def test_missing_required(self):
        ok, errs = validate({"recursive": True},
                            {"type": "object", "required": ["path"],
                             "properties": {"path": {"type": "string"}}})
        self.assertFalse(ok)
        self.assertTrue(any("path" in e for e in errs))

    def test_wrong_type(self):
        ok, errs = validate("not-an-int", {"type": "integer"})
        self.assertFalse(ok)

    def test_enum(self):
        ok, errs = validate("red", {"type": "string",
                                    "enum": ["red", "blue", "green"]})
        self.assertTrue(ok)
        ok2, _ = validate("purple", {"type": "string",
                                     "enum": ["red", "blue", "green"]})
        self.assertFalse(ok2)

    def test_additional_properties_false(self):
        ok, errs = validate({"a": 1, "bogus": 2},
                            {"type": "object", "additionalProperties": False,
                             "properties": {"a": {"type": "integer"}}})
        self.assertFalse(ok)

    def test_array_items(self):
        ok, errs = validate([1, 2, 3],
                            {"type": "array", "items": {"type": "integer"}})
        self.assertTrue(ok)
        ok2, _ = validate([1, "two", 3],
                          {"type": "array", "items": {"type": "integer"}})
        self.assertFalse(ok2)

    def test_ensure_raises(self):
        with self.assertRaises(SchemaError):
            ensure({"x": 1}, {"type": "object", "required": ["y"]})

    def test_tool_schema_shape(self):
        s = tool_schema("read", "读文件", {"path": {"type": "string"}}, ["path"])
        self.assertEqual(s["name"], "read")
        self.assertIn("path", s["inputSchema"]["required"])


class StreamTest(unittest.TestCase):
    def test_chunk_text(self):
        chunks = list(chunk_text("一二三四五六七八九十", 4))
        self.assertEqual("".join(chunks), "一二三四五六七八九十")
        self.assertTrue(all(len(c) <= 4 for c in chunks))

    def test_stream_telegram(self):
        calls = []
        mid = stream_telegram(
            "你好世界这是一段比较长的回复用来验证流式分批",
            send=lambda t: (calls.append(("send", t)), 42)[1],
            edit=lambda m, t: calls.append(("edit", m, t)),
            chunk=6, min_delta=5)
        self.assertEqual(mid, 42)
        self.assertEqual(calls[0][0], "send")
        self.assertEqual(calls[-1][1], 42)
        # 最终 edit 应包含完整文本
        final_edit = [c for c in calls if c[0] == "edit"][-1]
        self.assertIn("你好世界", final_edit[2])

    def test_stream_buffer(self):
        b = StreamBuffer(max_len=20)
        self.assertEqual(b.append("ab"), "ab")
        self.assertEqual(b.append("cd"), "abcd")
        b.append("x" * 30)
        self.assertLessEqual(len(b.text), 20)


if __name__ == "__main__":
    unittest.main()
