"""语义保持回归：真实 superbrain 内核 + hashing 向量库，长文写入→recall 原样取回原文。

验证用户严格约束「不丢语义 / 不幻觉 / token 是压缩后的」：
  - 写入多块事实 → 用事实内独特关键词查询 → 断言返回【原文精确包含】目标句。
  - "不幻觉" = 召回的是存储原文（精确匹配），而非模型改写/脑补。
  - 跨措辞语义查询能命中相关事实（hashing+enhance 的字符语义近似）。
说明：真正跨语义间隔检索需要 bge ONNX 语义模型（本机未装，hashing 只到字符特征级）。
"""
import unittest

from pathlib import Path

# 定位 superbrain-2.0 内核的 python 门面（与 superbrain_adapter 同源逻辑）
_here = Path(__file__).resolve()
for _root in (_here.parents[1], _here.parents[2], _here.parents[3]):
    _sb = _root / "superbrain-2.0" / "python"
    if (_sb / "superbrain2").exists():
        import sys
        sys.path.insert(0, str(_sb))
        break

from superbrain2.core.agent import AgentConfig, SuperBrainAgent  # noqa: E402
from superbrain2.core.llm import LLMResponse  # noqa: E402


class _FakeLLM:
    def __init__(self, usage=None):
        self._u = usage or (1234, 56, 1290)
    def chat(self, msgs, tools=None):
        return LLMResponse(content="ok", prompt_tokens=self._u[0],
                           completion_tokens=self._u[1], total_tokens=self._u[2])


class UsageThreadingTest(unittest.TestCase):
    """真实内核：LLMResponse usage → agent.usage() → facade.usage() 贯通，每轮重置。"""

    def test_agent_usage_reflects_real_prompt_tokens(self):
        agent = SuperBrainAgent(_FakeLLM(), store=None)
        agent._run_tool_loop([{"role": "user", "content": "hi"}], max_steps=2)
        u = agent.usage()
        self.assertEqual(u["prompt_tokens"], 1234)
        self.assertEqual(u["completion_tokens"], 56)
        self.assertEqual(u["calls"], 1)

    def test_usage_resets_per_turn(self):
        agent = SuperBrainAgent(_FakeLLM(), store=None)
        agent._run_tool_loop([{"role": "user", "content": "a"}], max_steps=2)
        agent._run_tool_loop([{"role": "user", "content": "b"}], max_steps=2)
        self.assertEqual(agent.usage()["calls"], 1)   # 第二轮从零重计

    def test_facade_usage_through(self):
        from superbrain2.facade import SuperBrain
        sb = SuperBrain(llm=_FakeLLM(), store=None)
        sb.chat("测试")
        self.assertEqual(sb.usage()["prompt_tokens"], 1234)


FACTS = [
    "生产机订阅网关监听在 18100/18101/18103 三个端口，rules.json 支持热加载。",
    "awsddns.okjnd.com 的 GTM DDNS 由生产机 systemd timer 每 60 秒同步一次。",
    "Pandora 面板已迁移到本机，PG18 跑在 Docker 127.0.0.1:5433。",
    "超脑记忆的遗忘不等于删除，archival 层的记忆可以被重新激活。",
    "任何被压缩掉的细节都能通过向量检索精确召回原文，不需要模型脑补。",
]

QUERIES = {  # 独特关键词 → 期望原样召回该句
    "rules.json 热加载": FACTS[0],
    "GTM 每60秒同步": FACTS[1],
    "PG18 5433": FACTS[2],
    "遗忘不等于删除 archival": FACTS[3],
    "向量检索精确召回原文": FACTS[4],
}


class SemanticRecallTest(unittest.TestCase):
    def test_verbatim_recall_no_hallucination(self):
        cfg = AgentConfig(embedder="hashing", embed_enhance=True,
                          embed_strip_stop=True)
        agent = SuperBrainAgent(_FakeLLM(), store=None, config=cfg)
        for f in FACTS:
            agent.remember(f, scope="user", tier="recall")

        for q, expect in QUERIES.items():
            with self.subTest(query=q):
                hits = agent.recall(q, k=3)
                all_text = " ".join(h[0].content for h in hits)
                self.assertIn(expect, all_text,
                              msg=f"关键词 {q!r} 未原样召回目标句")


if __name__ == "__main__":
    unittest.main()
