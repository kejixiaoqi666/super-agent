"""Local Skill Registry with progressive-disclosure metadata indexing."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SkillRecord:
    skill_id: str
    name: str
    path: str
    scope: str
    description: str
    triggers: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    index_bytes: int = 0
    full_bytes: int = 0
    sha256: str = ""


class SkillRegistry:
    def __init__(self, roots: list[Path]):
        self.roots = roots
        self.records: list[SkillRecord] = []

    def scan(self) -> list[SkillRecord]:
        records: list[SkillRecord] = []
        seen: set[Path] = set()
        for root in self.roots:
            if not root.exists():
                continue
            scope = "project" if root.name.lower() == "skills" and root.parent.name.lower() != "hermes" else "global"
            for path in root.rglob("SKILL.md"):
                path = path.resolve()
                if path in seen:
                    continue
                seen.add(path)
                try:
                    raw = path.read_bytes()
                except OSError:
                    continue
                text = raw.decode("utf-8", errors="replace")
                meta = _metadata(text)
                name = path.parent.name
                records.append(SkillRecord(
                    skill_id=meta.get("id", name), name=name, path=str(path), scope=meta.get("scope", scope),
                    description=meta.get("description", _first_body_line(text)),
                    triggers=tuple(meta.get("triggers", ())), permissions=tuple(meta.get("permissions", ())),
                    index_bytes=len((meta.get("description", "") + " " + " ".join(meta.get("triggers", ()))).encode("utf-8")),
                    full_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        self.records = sorted(records, key=lambda r: (r.scope, r.skill_id))
        return list(self.records)

    def match(self, query: str, *, max_results: int = 5) -> list[dict[str, Any]]:
        if not self.records:
            self.scan()
        q = set(_tokens(query))
        ranked = []
        for rec in self.records:
            words = set(_tokens(" ".join((rec.name, rec.description, *rec.triggers))))
            score = len(q & words) / max(1, len(q))
            if score:
                ranked.append((score, rec))
        ranked.sort(key=lambda item: (-item[0], item[1].skill_id))
        return [{"skill_id": rec.skill_id, "score": round(score, 4), "path": rec.path,
                 "index_bytes": rec.index_bytes, "full_bytes": rec.full_bytes, "permissions": rec.permissions}
                for score, rec in ranked[:max_results]]

    def diagnostics(self) -> list[dict[str, Any]]:
        if not self.records:
            self.scan()
        out = []
        for rec in self.records:
            broad = len(rec.description) > 240 or not rec.triggers
            out.append({"skill_id": rec.skill_id, "overbroad": broad, "missing_triggers": not bool(rec.triggers),
                        "progressive_disclosure": rec.full_bytes > max(4096, rec.index_bytes * 8)})
        for i, left in enumerate(self.records):
            for right in self.records[i + 1:]:
                overlap = set(_tokens(" ".join(left.triggers))) & set(_tokens(" ".join(right.triggers)))
                if overlap:
                    out.append({"kind": "trigger_overlap", "left": left.skill_id, "right": right.skill_id,
                                "tokens": sorted(overlap)})
        return out


def _tokens(text: str) -> list[str]:
    return [x.lower() for x in re.findall(r"[\w\u4e00-\u9fff]+", text) if len(x) > 1]


def _first_body_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip() and not line.startswith("#") and not line.startswith("---"):
            return line.strip()[:300]
    return ""


def _metadata(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {"triggers": [], "permissions": []}
    lines = text.splitlines()
    in_front = bool(lines and lines[0].strip() == "---")
    for line in lines[1:] if in_front else []:
        if line.strip() == "---":
            break
        m = re.match(r"\s*(id|scope|description)\s*:\s*(.+)$", line, re.I)
        if m:
            result[m.group(1).lower()] = m.group(2).strip().strip("'\"")
        m = re.match(r"\s*(triggers|permissions)\s*:\s*\[(.*)\]", line, re.I)
        if m:
            result[m.group(1).lower()] = [x.strip().strip("'\"") for x in m.group(2).split(",") if x.strip()]
    return result
