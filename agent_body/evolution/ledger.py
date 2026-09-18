"""进化提案库 + 观察记录（阶段② 自进化思考的持久化基础）。

观察(observation) 与 提案(proposal) 统一存 data_dir/evolution/ledger.json。
提案状态机：recorded → thinking → pending_approval → approved → applied / rejected / archived
硬约束（用户定调）：未 approved 不得 apply（AI 只能观察/思考/提案，执行必须批准）。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import List, Optional

KINDS = ("error", "pain", "optimization", "iteration")
STATUSES = ("recorded", "thinking", "pending_approval",
            "approved", "applied", "rejected", "archived")


def _now() -> float:
    return time.time()


def _nid(prefix: str) -> str:
    return f"{prefix}_{int(_now() * 1000)}_{uuid.uuid4().hex[:6]}"


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {"observations": [], "proposals": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"observations": [], "proposals": []}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class EvolutionLedger:
    """观察 + 提案持久化库，带状态机。不依赖模型，纯确定性可测。"""

    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "evolution" / "ledger.json"
        self.data = _read_json(self.path)

    def _save(self) -> None:
        _write_json(self.path, self.data)

    # ---- 观察 ----
    def add_observation(self, kind: str, detail: str, source: str = "",
                        **meta) -> dict:
        if kind not in KINDS:
            raise ValueError(f"未知观察类型 {kind!r}（可用: {KINDS}）")
        obs = {"oid": _nid("obs"), "kind": kind, "detail": detail,
               "source": source, "ts": _now(),
               "meta": meta or {}}
        self.data["observations"].append(obs)
        self._save()
        return obs

    def observations(self, kind: Optional[str] = None) -> List[dict]:
        obs = self.data["observations"]
        if kind:
            obs = [o for o in obs if o["kind"] == kind]
        return list(reversed(obs))   # 新的在前

    # ---- 提案 ----
    def add_proposal(self, center: str, target: str, suggestion: str,
                     reasoning: str = "", priority: str = "medium",
                     refs: Optional[List[str]] = None,
                     state: str = "pending_approval") -> dict:
        """AI 思考后成形的提案。center=进化方向，target=改哪里，suggestion=怎么改。"""
        if state not in STATUSES:
            raise ValueError(f"未知状态 {state!r}")
        prop = {"pid": _nid("prop"), "center": center, "target": target,
                "suggestion": suggestion, "reasoning": reasoning,
                "priority": priority, "refs": list(refs or []),
                "state": state, "ts": _now(),
                "history": [{"state": state, "ts": _now()}]}
        self.data["proposals"].append(prop)
        self._save()
        return prop

    def get_proposal(self, pid: str) -> Optional[dict]:
        for p in self.data["proposals"]:
            if p["pid"] == pid:
                return p
        return None

    def proposals(self, state: Optional[str] = None) -> List[dict]:
        props = self.data["proposals"]
        if state:
            props = [p for p in props if p["state"] == state]
        return list(reversed(props))

    def transition(self, pid: str, to: str, by: str = "system", note: str = "") -> dict:
        """状态迁移校验：approve 只能来自 pending_approval；apply 只能来自 approved。"""
        if to not in STATUSES:
            raise ValueError(f"未知状态 {to!r}")
        p = self.get_proposal(pid)
        if p is None:
            return {"ok": False, "error": f"提案不存在: {pid}"}
        cur = p["state"]
        # 严格迁移规则（硬约束）
        legal = {
            "pending_approval": {"approved", "rejected", "archived"},
            "approved": {"applied", "archived"},
            "applied": {"archived"},
            "rejected": {"archived"},
            "recorded": {"thinking", "pending_approval", "archived"},
            "thinking": {"pending_approval", "archived"},
            "archived": set(),
        }
        if to not in legal.get(cur, set()):
            return {"ok": False,
                    "error": f"非法迁移: {cur} → {to}（{pid} 必须经 approved 才可 apply）"}
        p["state"] = to
        p["history"].append({"state": to, "ts": _now(), "by": by, "note": note})
        self._save()
        return {"ok": True, "pid": pid, "state": to}