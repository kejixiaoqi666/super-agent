"""Git 集成 —— status/diff/commit/push，供编码类 agent 使用。

对标 Codex/Claude 的 git 能力：查看改动、提交、推送、历史。全部包裹 git CLI，
在指定工作目录执行；非 git 仓库返回清晰错误，绝不静默。
"""
from __future__ import annotations

import subprocess
from typing import Dict, List, Optional


class GitError(RuntimeError):
    """git 操作失败（非 git 仓库/冲突/提交失败等）。"""


def _git(cwd, *args, check: bool = True) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError) as e:
        raise GitError(f"git 执行失败: {e}") from None
    if check and r.returncode != 0:
        raise GitError((r.stderr or r.stdout or "").strip() or
                       f"git {' '.join(args)} 失败")
    return r.stdout


def is_repo(cwd) -> bool:
    try:
        out = _git(cwd, "rev-parse", "--is-inside-work-tree",
                   check=False).strip().lower()
        return out == "true"
    except GitError:
        return False


def status(cwd) -> List[str]:
    """porcelain 改动清单（每行 = 状态+文件）。"""
    out = _git(cwd, "status", "--porcelain")
    return [ln for ln in out.splitlines() if ln.strip()]


def diff(cwd, staged: bool = False) -> str:
    """unstaged diff（默认）；staged=True 看已暂存。"""
    args = ["diff", "--cached"] if staged else ["diff"]
    return _git(cwd, *args) or ""


def diff_stat(cwd) -> Dict:
    """改动统计 {files, insertions, deletions, shortstat}。"""
    short = _git(cwd, "diff", "--shortstat") or ""
    parts = short.strip().split(", ")
    files = ins = dele = 0
    for p in parts:
        if "file" in p or "files" in p:
            files = int(p.split()[0])
        elif "insertion" in p:
            ins = int(p.split()[0])
        elif "deletion" in p:
            dele = int(p.split()[0])
    return {"files": files, "insertions": ins, "deletions": dele,
            "shortstat": short.strip()}


def commit(cwd, message: str, all: bool = True) -> str:
    """提交。all=True 先 git add -A；返回 commit 摘要。"""
    if not message.strip():
        raise GitError("提交信息不能为空")
    if all:
        _git(cwd, "add", "-A")
    out = _git(cwd, "commit", "-m", message.strip())
    return (out or "").strip()


def push(cwd, remote: Optional[str] = None, branch: Optional[str] = None) -> str:
    """推送当前分支。remote/branch 缺省用远端跟踪。"""
    args = ["push"]
    if remote:
        args.append(remote)
        if branch:
            args.append(branch)
    return _git(cwd, *args).strip()


def log(cwd, limit: int = 10) -> List[List[str]]:
    """最近提交 [hash, 主题]。"""
    out = _git(cwd, "log", f"-{limit}", "--oneline",
               "--pretty=%h %s")
    return [ln.split(" ", 1) for ln in out.splitlines() if ln.strip()]


def current_branch(cwd) -> str:
    return _git(cwd, "branch", "--show-current").strip()