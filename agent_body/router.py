"""消息路由器：输入先进来, 用大模型快速判断"简单/复杂", 决定走哪条处理路径。

设计（用户定调 2026-09）："输入先直接调用大模型简单判断即可, 多一步很快,
就知道需要如何处理了"——用轻量路由调用替代死规则命中, 覆盖所有情况。

  - 极简单规则(fastpath: 问候/算术/时间)先零网络兜底秒回
  - 其余 → 调大模型做**一次轻量判断**: 输出 {route: simple|complex, answer?}
      simple → 简短回答(不经过超脑重型认知)
      complex → 交给超脑完整管线(工具/记忆/深层推理)
  判不了/异常 → 默认 complex(进大脑, 安全不错答)。
"""

from __future__ import annotations

import json
import re
from typing import Tuple


_ROUTER_PROMPT = """你是消息路由器。只判断: 这条消息【需不需要过超脑】。
过超脑 = 要用超脑的记忆/长期上下文/工具(查IP/服务器/执行/分析/自主任务/部署/代码操作/记忆里的事)。
不过超脑 = 普通问答/闲聊/写作/解释/翻译/建议等, 直接用普通模型即可回答。
只输出 JSON:
- 不需要过超脑 → {"route":"direct","answer":"直接的回答"}
- 需要过超脑 → {"route":"brain"}
用户消息: {text}"""


class Router:
    """用远程大模型做轻量路由：判断是否需要过超脑(一次调用, 输出极短)。"""

    def __init__(self, llm):
        self._llm = llm          # superbrain LLMProvider(读 SUPERBRAIN_LLM_*)

    def classify(self, text: str, max_tokens: int = 160) -> Tuple[str, str]:
        """返回 (route, answer?) —— route: direct/brain; answer 仅 direct 时有效。"""
        msgs = [{"role": "system", "content": "你是高效的路由器，严格按要求输出 JSON。"},
                {"role": "user", "content": _ROUTER_PROMPT.replace("{text}", text[:500])}]
        try:
            r = self._llm.chat(msgs, max_tokens=max_tokens)
        except Exception:
            return "brain", ""          # 判断失败 → 过超脑, 安全
        content = (r.content or "").strip()
        # 防御式解析：只认 JSON 里的 route/answer, 绝不因坏 JSON 误判
        m = re.search(r'"route"\s*:\s*"([^"]+)"', content)
        route = (m.group(1) if m else "").lower()
        ans = ""
        a = re.search(r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)"', content)
        if a:
            ans = json.loads('"' + a.group(1) + '"')      # 还原转义
        if route == "direct" and ans.strip():
            return "direct", ans.strip()
        return "brain", ""          # 非 direct/无答案 → 过超脑, 安全
