"""Read-only compatibility surfaces modeled on the local Hermes layout.

The adapter intentionally never imports or executes Hermes code.  It discovers
SKILL.md files and memory markdown as untrusted input, which keeps an
AgentWorkbench process isolated from an installed Hermes runtime.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HermesPaths:
    repo_root: Path
    data_root: Path

    @property
    def skills_root(self) -> Path:
        return self.data_root / "skills"

    @property
    def memories_root(self) -> Path:
        return self.data_root / "memories"


@dataclass(frozen=True)
class HermesSkill:
    name: str
    path: str
    sha256: str
    description: str


class HermesCompat:
    """Discover Hermes-compatible artifacts without executing them."""

    def __init__(self, paths: HermesPaths):
        self.paths = paths

    def discover_skills(self) -> list[HermesSkill]:
        roots = [self.paths.repo_root / "skills", self.paths.skills_root]
        found: dict[str, HermesSkill] = {}
        for root in roots:
            if not root.exists():
                continue
            for md in root.rglob("SKILL.md"):
                try:
                    raw = md.read_bytes()
                    text = raw.decode("utf-8", errors="replace")
                except OSError:
                    continue
                name = md.parent.name
                desc = _frontmatter_description(text)
                found.setdefault(name, HermesSkill(name, str(md), hashlib.sha256(raw).hexdigest(), desc))
        return sorted(found.values(), key=lambda item: item.name)

    def read_skill(self, name: str, *, max_bytes: int = 64_000) -> str:
        for skill in self.discover_skills():
            if skill.name == name:
                data = Path(skill.path).read_bytes()[:max_bytes]
                return data.decode("utf-8", errors="replace")
        raise FileNotFoundError(name)

    def discover_memory_markdown(self) -> list[dict[str, Any]]:
        """Return metadata only; callers must explicitly import content."""
        roots = [self.paths.data_root, self.paths.memories_root]
        out = []
        seen: set[Path] = set()
        for root in roots:
            if not root.exists():
                continue
            for path in root.glob("*.md"):
                path = path.resolve()
                if path in seen or path.name.lower() not in {"memory.md", "user.md"}:
                    continue
                seen.add(path)
                out.append({"name": path.name, "path": str(path), "sha256": _sha256(path)})
        return sorted(out, key=lambda item: item["name"])


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _frontmatter_description(text: str) -> str:
    for line in text.splitlines()[:30]:
        match = re.match(r"\s*(?:description|摘要)\s*:\s*(.+)$", line, re.I)
        if match:
            return match.group(1).strip().strip("'\"")[:300]
    for line in text.splitlines():
        if line.strip() and not line.startswith("#"):
            return line.strip()[:300]
    return ""
