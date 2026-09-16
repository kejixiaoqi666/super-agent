"""Metadata-only Auth Plane. Secret values stay in an external Vault/Broker."""
from dataclasses import dataclass, asdict
from pathlib import Path
import json, os, tempfile


@dataclass(frozen=True)
class AuthProfile:
    id: str
    provider: str
    auth_type: str
    account_id: str
    scopes: tuple[str, ...] = ()
    token_ref: str = ""
    source: str = "official"
    base_url: str | None = None
    expires_at: float | None = None
    status: str = "unknown"


class AuthStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.items = {}
        if self.path.exists():
            for item in json.loads(self.path.read_text(encoding="utf-8")):
                self.items[item["id"]] = AuthProfile(**item)

    def save(self, profile):
        if not profile.token_ref.startswith(("vault://", "env://", "keychain://")):
            raise ValueError("token_ref must reference a Vault, environment or keychain entry")
        self.items[profile.id] = profile
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix="auth.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump([asdict(x) for x in self.items.values()], f, ensure_ascii=False, indent=2); f.flush(); os.fsync(f.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name): os.unlink(name)
        return profile

    def get(self, profile_id):
        if profile_id not in self.items: raise KeyError(profile_id)
        return self.items[profile_id]

    def list(self): return list(self.items.values())


class AuthBroker:
    def __init__(self, store, resolver=None):
        self.store, self.resolver = store, resolver

    def lease(self, profile_id, purpose, ttl=300):
        profile = self.store.get(profile_id)
        if self.resolver is None:
            raise RuntimeError("no Vault resolver configured; metadata-only mode")
        value = self.resolver(profile.token_ref)
        if not value: raise RuntimeError("credential unavailable")
        # Deliberately return a scoped opaque handle, not the secret to a model.
        return {"profile_id": profile.id, "provider": profile.provider, "purpose": purpose,
                "lease_ttl": ttl, "secret_handle": id(value)}
