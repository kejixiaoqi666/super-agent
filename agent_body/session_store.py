"""会话全文检索 SessionStore —— FTS5(trigram) 索引对话，支持中文子串检索。

补齐成熟 agent（Hermes session_search）的能力：可搜历史会话，越用越不笨。
用 FTS5 trigram tokenizer：无需中文分词器即可对 CJK 做子串匹配（查询≥3字）。
每条消息同时写入 `transcript`（保留原文、按会话回看）和 FTS5 虚表（全文检索）。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcript(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session TEXT NOT NULL,
    ts REAL NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts
    USING fts5(content, session, ts UNINDEXED, tokenize='trigram');
"""


def _build_match_query(q: str) -> str:
    """把用户输入转成 FTS5 trigram 匹配串：按空白切段，每段作 quoted phrase 用 AND 连接，
    避免空格/标点破坏 MATCH 语法。"""
    tokens = [t for t in q.strip().split() if t]
    if not tokens:
        return ""
    # trigram 需 ≥3 字；单 token 直接 quote
    return " AND ".join('"' + t + '"' for t in tokens)


class SessionStore:
    """SQLite + FTS5 的会话记录与全文检索。"""

    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(_SCHEMA)
        except sqlite3.OperationalError as e:
            raise RuntimeError("当前 SQLite 缺 FTS5 支持，无法建立会话检索: "
                               + str(e)) from None

    def record(self, session: str, content: str, role: str = "user",
               ts: Optional[float] = None) -> int:
        """记一条消息并加入 FTS5 索引（两 insert 在同一事务，保证一致）。"""
        ts = ts if ts is not None else time.time()
        self.conn.execute("BEGIN")
        try:
            cur = self.conn.execute(
                "INSERT INTO transcript(session, ts, role, content) VALUES(?,?,?,?)",
                (session, ts, role, content))
            self.conn.execute(
                "INSERT INTO transcript_fts(content, session, ts) VALUES(?,?,?)",
                (content, session, ts))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return cur.lastrowid

    def search(self, query: str, k: int = 10) -> List[Dict]:
        """按相关度返回命中消息，带 snippet。失败/空安全返回 []。"""
        mq = _build_match_query(query)
        if not mq:
            return []
        try:
            rows = self.conn.execute(
                "SELECT session, ts, "
                "snippet(transcript_fts, 0, '[', ']', '…', 40) AS snip, rank "
                "FROM transcript_fts WHERE transcript_fts MATCH ? "
                "ORDER BY rank LIMIT ?", (mq, k)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(r) for r in rows]

    def recent(self, session: str, k: int = 10) -> List[Dict]:
        """某个会话最近 k 条原文（无需检索）。"""
        rows = self.conn.execute(
            "SELECT session, role, ts, content FROM transcript "
            "WHERE session=? ORDER BY id DESC LIMIT ?",
            (session, k)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self.conn.close()