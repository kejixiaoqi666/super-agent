from dataclasses import dataclass
from pathlib import Path
import tomllib
import re


@dataclass(frozen=True)
class Config:
    data_root: Path
    owner: str = "local-owner"
    context_budget_bytes: int = 12000
    max_records_per_owner: int = 50000
    host: str = "127.0.0.1"
    port: int = 8765
    token_env: str = "AGENTWORKBENCH_TOKEN"
    max_workers: int = 8
    request_timeout: int = 10

    def __post_init__(self):
        if self.host != "127.0.0.1":
            raise ValueError("prototype supports loopback only")
        if not isinstance(self.owner, str) or not self.owner.strip() or len(self.owner)>120:
            raise ValueError("invalid owner")
        for name, low, high in (("port",1,65535),("context_budget_bytes",128,1000000),
                               ("max_records_per_owner",1,1000000),("max_workers",1,64),
                               ("request_timeout",1,60)):
            value = getattr(self,name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"invalid {name}")
        if not isinstance(self.token_env,str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", self.token_env):
            raise ValueError("invalid token_env")

    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
        mem, server = doc.get("memory", {}), doc.get("server", {})
        root = Path(mem.get("data_root", "data"))
        if not root.is_absolute():
            root = path.parent / root
        cfg = cls(root.resolve(), mem.get("owner", "local-owner"),
                  mem.get("context_budget_bytes", 12000),
                  mem.get("max_records_per_owner", 50000),
                  server.get("host", "127.0.0.1"), server.get("port", 8765),
                  server.get("token_env","AGENTWORKBENCH_TOKEN"),
                  server.get("max_workers",8), server.get("request_timeout",10))
        return cfg
