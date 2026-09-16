"""Provider-neutral model routing with local deterministic provider for tests."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[dict, ...]
    tools: tuple[dict, ...] = ()
    profile: str = "default"


class Provider:
    name = "abstract"
    def complete(self, request): raise NotImplementedError


class EchoProvider(Provider):
    name = "echo"
    def complete(self, request):
        text = request.messages[-1].get("content", "") if request.messages else ""
        return {"provider": self.name, "content": text, "tool_calls": []}


class ModelRouter:
    def __init__(self, providers, routes=None):
        self.providers = {p.name:p for p in providers}; self.routes = routes or {"default": next(iter(self.providers), "")}
    def complete(self, request):
        name = self.routes.get(request.profile, self.routes.get("default"))
        if name not in self.providers: raise RuntimeError(f"no model provider for profile {request.profile}")
        return self.providers[name].complete(request)
