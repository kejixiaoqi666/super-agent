"""升级治理队列 UpgradeQueue（阶段④ 主权开放架构）——底层/内核进化的正规通道。

用户定调：底层升级要「堆积成文档或存档（含代码文档）」→ 排队 → 门禁 → 测试 → 并入。
AI/自进化不能直接改内核；底层进化必须先提交需求文档存档，经门禁批准、测试通过才并入。

升级 状态机：
  submitted(文档存档待门禁) → approved(门禁过) → tested(测试过) → merged(已并入内核)
                              ↘ rejected(驳回) → archived
硬约束：并入(merge) 必须已完成 门禁批准+测试通过，不可跳过（不静默）。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Callable, List, Optional

STATES = ("submitted", "approved", "tested", "merged", "rejected", "archived")


def _now() -> float:
    return time.time()


def _uid() -> str:
    return f"up_{int(_now() * 1000)}_{uuid.uuid4().hex[:6]}"


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {"upgrades": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"upgrades": []}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class UpgradeQueue:
    """升级治理队列：文档存档→排队→门禁→测试→并入。不改内核则无需用。"""

    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "sovereign" / "upgrades.json"
        self.data = _read_json(self.path)

    def _save(self) -> None:
        _write_json(self.path, self.data)

    # ---- 提交（文档存档入队）----
    def submit(self, title: str, rationale: str, code_doc: str,
               files_impacted: Optional[List[str]] = None,
               risk: str = "low") -> dict:
        """底层升级请求存档入队。rationale=需求文档，code_doc=代码文档(必填，存底)。"""
        if not code_doc.strip():
            raise ValueError("底层升级必须附代码文档(code_doc)，不能空改")
        up = {"uid": _uid(), "title": title, "rationale": rationale,
              "code_doc": code_doc, "files_impacted": list(files_impacted or []),
              "risk": risk, "state": "submitted", "ts": _now(),
              "history": [{"state": "submitted", "ts": _now()}]}
        self.data["upgrades"].append(up)
        self._save()
        return up

    def get(self, uid: str) -> Optional[dict]:
        for u in self.data["upgrades"]:
            if u["uid"] == uid:
                return u
        return None

    def pending(self) -> List[dict]:
        """待门禁的升级文档。"""
        return [u for u in self.data["upgrades"] if u["state"] == "submitted"]

    # ---- 门禁 / 测试 / 并入 ----
    def _transition(self, uid: str, to: str, by: str, note: str = "") -> dict:
        if to not in STATES:
            raise ValueError(f"未知状态 {to!r}")
        u = self.get(uid)
        if u is None:
            return {"ok": False, "error": f"升级不存在: {uid}"}
        cur = u["state"]
        legal = {
            "submitted": {"approved", "rejected", "archived"},
            "approved": {"tested", "archived"},
            "tested": {"merged", "archived"},
            "rejected": {"archived"},
            "merged": {"archived"},
            "archived": set(),
        }
        if to not in legal.get(cur, set()):
            return {"ok": False,
                    "error": f"非法迁移: {cur} → {to}（{uid} 门禁/测试未过不可并入内核）"}
        u["state"] = to
        u["history"].append({"state": to, "ts": _now(), "by": by, "note": note})
        self._save()
        return {"ok": True, "uid": uid, "state": to}

    def approve(self, uid: str, by: str = "reviewer", note: str = "") -> dict:
        """门禁批准：submitted → approved。"""
        return self._transition(uid, "approved", by=by, note=note)

    def reject(self, uid: str, by: str = "reviewer", note: str = "") -> dict:
        return self._transition(uid, "rejected", by=by, note=note)

    def mark_tested(self, uid: str, by: str = "qa", note: str = "") -> dict:
        """测试通过：approved → tested。"""
        return self._transition(uid, "tested", by=by, note=note)

    def merge(self, uid: str, apply_fn: Callable[[dict], None],
              by: str = "system") -> dict:
        """并入内核。【硬约束】须已完成 门禁批准+测试通过，否则拒绝；不静默。
        apply_fn(upgrade) 负责真实改动（由授权方提供，这里保证流程）。
        """
        u = self.get(uid)
        if u is None:
            return {"ok": False, "error": f"升级不存在: {uid}"}
        if u["state"] != "tested":
            return {"ok": False,
                    "error": f"底层升级须门禁批准+测试通过才可并入：{uid} 当前 {u['state']}"}
        apply_fn(u)                      # 真实并入（可抛错）
        return self._transition(uid, "merged", by=by)

    def history(self, uid: str) -> List[dict]:
        u = self.get(uid)
        return list(u["history"]) if u else []