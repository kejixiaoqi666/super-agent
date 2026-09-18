"""Provider-neutral model routing with real OpenAI-compatible provider + fallback.

原实现只有离线 EchoProvider（测试替身）。本模块补上真实 HTTP 提供方与失败切换链：
  - OpenAIProvider：OpenAI 兼容 `{base_url}/chat/completions`，用 stdlib urllib
    （无 openai SDK 依赖），返回 content + tool_calls。
  - FallbackRouter：按 profile 先走主 provider，失败（HTTP 4xx/5xx/网络/解析）顺序
    切到其余 provider，全失败抛 ProviderError 并附每个 provider 的错误。
  - EchoProvider 保留作离线测试替身。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional


class ProviderError(RuntimeError):
    """provider 调用/解析失败（可被 FallbackRouter 捕获并切换）。"""


@dataclass(frozen=True)
class ModelRequest:
    messages: tuple = field(default_factory=tuple)
    tools: tuple = field(default_factory=tuple)
    profile: str = "default"


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "default"
    timeout: float = 60.0


class Provider:
    name = "abstract"

    def complete(self, request: ModelRequest) -> dict:
        raise NotImplementedError


class EchoProvider(Provider):
    """离线测试替身：返回最后一条消息的文本。"""
    name = "echo"

    def complete(self, request: ModelRequest) -> dict:
        text = request.messages[-1].get("content", "") if request.messages else ""
        return {"provider": self.name, "content": text, "tool_calls": []}


class OpenAIProvider(Provider):
    """OpenAI 兼容提供方（真实 HTTP，stdlib urllib，无额外依赖）。"""

    def __init__(self, config: ProviderConfig):
        self.config = config
        self.name = config.name

    def _endpoint(self) -> str:
        return self.config.base_url.rstrip("/") + "/chat/completions"

    def complete(self, request: ModelRequest) -> dict:
        payload: dict = {
            "model": self.config.model,
            "messages": [dict(m) for m in request.messages],
        }
        if request.tools:
            payload["tools"] = [dict(t) for t in request.tools]
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint(), data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.config.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            raise ProviderError(f"{self.name} HTTP {e.code}: {detail}") from None
        except OSError as e:
            raise ProviderError(f"{self.name} 网络错误: {e}") from None
        return self._parse(data)

    def _parse(self, data: dict) -> dict:
        try:
            choice = data["choices"][0]["message"]
            content = choice.get("content")
            tool_calls = choice.get("tool_calls") or []
            out = {"provider": self.name, "content": content,
                   "tool_calls": [dict(t) for t in tool_calls]}
            # 抓真实 token 用量（每轮真实输入大小，供预算/自动续接判断）
            usage = data.get("usage") or {}
            if isinstance(usage, dict) and usage:
                out["usage"] = {
                    "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                    "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                    "total_tokens": int(usage.get("total_tokens", 0) or 0),
                }
            return out
        except Exception as e:  # 缺字段/结构不对
            raise ProviderError(f"{self.name} 响应解析失败: {e}") from None


class ModelRouter:
    """按 profile 路由到 provider（无失败切换）。"""

    def __init__(self, providers: List[Provider],
                 routes: Optional[Dict[str, str]] = None):
        self.providers = {p.name: p for p in providers}
        self.routes = routes or {"default": next(iter(self.providers), "")}

    def complete(self, request: ModelRequest) -> dict:
        name = self.routes.get(request.profile, self.routes.get("default"))
        if name not in self.providers:
            raise RuntimeError(f"no model provider for profile {request.profile}")
        return self.providers[name].complete(request)


class FallbackRouter(ModelRouter):
    """带失败切换链：先走主 provider，失败顺序切其余，全失败抛 ProviderError。"""

    def _chain(self, profile: str) -> List[str]:
        primary = self.routes.get(profile, self.routes.get("default"))
        order = [primary] if primary in self.providers else []
        order += [n for n in self.providers if n not in order]
        return order

    def complete(self, request: ModelRequest) -> dict:
        errors: List[str] = []
        for name in self._chain(request.profile):
            try:
                return self.providers[name].complete(request)
            except ProviderError as e:
                errors.append(f"{name}: {e}")
        msg = "全部 provider 失败" + (f" -> {'; '.join(errors)}" if errors else "")
        raise ProviderError(msg)
