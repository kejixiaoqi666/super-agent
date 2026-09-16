"""Manifest-only plugin registry; loading code is intentionally out of process."""
import hashlib, json
from pathlib import Path


class PluginManifestError(ValueError): pass


class PluginRegistry:
    def __init__(self, roots): self.roots = [Path(x) for x in roots]; self.manifests = {}
    def scan(self):
        found = {}
        for root in self.roots:
            if not root.exists(): continue
            for path in root.rglob("plugin.json"):
                try: data = json.loads(path.read_text(encoding="utf-8"))
                except Exception as exc: raise PluginManifestError(f"invalid {path}: {exc}")
                required = {"id","version","api_version","kind","capabilities"}
                if not required <= set(data) or not isinstance(data["capabilities"], list):
                    raise PluginManifestError(f"manifest missing fields: {path}")
                if data["id"] in found: raise PluginManifestError(f"duplicate plugin: {data['id']}")
                data["path"] = str(path); data["hash"] = hashlib.sha256(path.read_bytes()).hexdigest()
                found[data["id"]] = data
        self.manifests = found; return list(found.values())
    def get(self, plugin_id): return self.manifests[plugin_id]
