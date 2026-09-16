"""Versioned memory, attributable feedback and bounded, explainable retrieval.

Owner is a trusted caller identity, never a field taken from a chat message.
SQLite is authoritative. FTS5 is a rebuildable Chinese-bigram/word index.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import math
import re
import sqlite3
import time
import uuid


class MemoryError(ValueError):
    pass


class Conflict(MemoryError):
    pass


class NotFound(MemoryError):
    pass


class BudgetExceeded(MemoryError):
    pass


def uid():
    return uuid.uuid4().hex


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def tokens(value):
    # Unicode61 alone treats a whole Chinese sentence as a token. Pre-segment it.
    found = re.findall(r"[a-z0-9_./:@-]+|[\u3400-\u9fff]+", value.lower())
    output = []
    for word in found:
        if re.fullmatch(r"[\u3400-\u9fff]+", word):
            output.extend(word[i:i+2] for i in range(max(1, len(word)-1)))
        else:
            output.append(word)
    return list(dict.fromkeys(output))


def secret_check(value):
    # Heuristic only: unknown secret formats still require caller-side filtering.
    text = encoded(value)
    patterns = [r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY",
                r"\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{16,}",
                r"\bBearer\s+[A-Za-z0-9._-]{12,}",
                r"\b\d{6,12}:[A-Za-z0-9_-]{30,}",
                r"(?:password|passwd|api_key|refresh_token|密码)\s*[=:]\s*[^\s,;\"}]{4,}"]
    if any(re.search(p, text, re.I) for p in patterns):
        raise MemoryError("possible credential: store a vault reference instead")


def string(value, name, limit=1000, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise MemoryError(f"invalid {name}")
    return value.strip()


class Store:
    def __init__(self, data_root, clock=time.time, max_records=50000, initialize=True):
        self.root = Path(data_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.max_records = max_records
        self.db = sqlite3.connect(self.root / "memory.sqlite3", timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=15000")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1) or (not initialize and version != 1):
            self.db.close()
            raise MemoryError("unsupported schema version")
        if not initialize:
            return
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS memories (
          id TEXT PRIMARY KEY, owner TEXT NOT NULL, project TEXT,
          fact_key TEXT, current_revision INTEGER NOT NULL DEFAULT 1,
          status TEXT NOT NULL CHECK(status IN ('candidate','active','superseded','forgotten')),
          created REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS memory_scope ON memories(owner,project,status);
        CREATE TABLE IF NOT EXISTS versions (
          memory_id TEXT NOT NULL REFERENCES memories(id), revision INTEGER NOT NULL,
          title TEXT NOT NULL, content TEXT NOT NULL, priority TEXT NOT NULL,
          kind TEXT NOT NULL, importance REAL NOT NULL, confidence REAL NOT NULL,
          entities TEXT NOT NULL, source_ref TEXT NOT NULL, valid_until REAL,
          content_hash TEXT NOT NULL, created REAL NOT NULL,
          PRIMARY KEY(memory_id,revision)
        );
        CREATE TABLE IF NOT EXISTS events (
          id TEXT PRIMARY KEY, memory_id TEXT NOT NULL REFERENCES memories(id),
          revision INTEGER NOT NULL, kind TEXT NOT NULL, created REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS feedback (
          memory_id TEXT NOT NULL, revision INTEGER NOT NULL, task_id TEXT NOT NULL,
          outcome TEXT NOT NULL CHECK(outcome IN ('success','failure')),
          evidence_ref TEXT NOT NULL, created REAL NOT NULL,
          PRIMARY KEY(memory_id,revision,task_id),
          FOREIGN KEY(memory_id,revision) REFERENCES versions(memory_id,revision)
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(memory_id UNINDEXED, terms);
        CREATE TABLE IF NOT EXISTS manifests (
          id TEXT PRIMARY KEY, owner TEXT NOT NULL, project TEXT, payload TEXT NOT NULL,
          created REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS checkpoints (
          owner TEXT NOT NULL, session TEXT NOT NULL, revision INTEGER NOT NULL,
          payload TEXT NOT NULL, payload_hash TEXT NOT NULL, created REAL NOT NULL,
          PRIMARY KEY(owner,session,revision)
        );
        PRAGMA user_version=1;
        ''')

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self, write=True):
        self.db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def _row(self, owner, memory_id):
        row = self.db.execute('''SELECT m.*,v.title,v.content,v.priority,v.kind,
            v.importance,v.confidence,v.entities,v.source_ref,v.valid_until,v.content_hash
            FROM memories m JOIN versions v ON v.memory_id=m.id
            AND v.revision=m.current_revision WHERE m.owner=? AND m.id=?''',
            (owner, memory_id)).fetchone()
        if row is None:
            raise NotFound("memory not found")
        return dict(row)

    def get(self, owner, memory_id):
        row = self._row(owner, memory_id)
        row["entities"] = json.loads(row["entities"])
        return row

    def list(self, owner, project=None):
        with self.transaction(write=False):
            if project is None:
                ids = self.db.execute('''SELECT id FROM memories WHERE owner=?
                  ORDER BY created DESC,id LIMIT 500''', (owner,)).fetchall()
            else:
                ids = self.db.execute('''SELECT id FROM memories WHERE owner=?
                  AND (project IS NULL OR project=?) ORDER BY created DESC,id LIMIT 500''',
                  (owner, project)).fetchall()
            return [self.get(owner, r[0]) for r in ids]

    def _validate(self, data):
        if not isinstance(data, dict):
            raise MemoryError("memory must be an object")
        allowed = {"title", "content", "priority", "kind", "importance", "confidence",
                   "entities", "source_ref", "valid_until"}
        if set(data) - allowed:
            raise MemoryError("unknown memory fields")
        d = dict(data)
        for key, length in (("title", 300), ("content", 24000), ("source_ref", 1500)):
            d[key] = string(d.get(key), key, length)
        d.setdefault("priority", "P2")
        d.setdefault("kind", "semantic")
        d.setdefault("importance", 0.5)
        d.setdefault("confidence", 0.5)
        d.setdefault("entities", [])
        d.setdefault("valid_until", None)
        if d["priority"] not in ("P0","P1","P2","P3","P4"):
            raise MemoryError("invalid priority")
        if d["kind"] not in ("semantic","episodic","procedural"):
            raise MemoryError("invalid kind")
        for k in ("importance", "confidence"):
            if type(d[k]) not in (int, float) or not math.isfinite(d[k]) or not 0 <= d[k] <= 1:
                raise MemoryError(f"invalid {k}")
        if not isinstance(d["entities"], list) or len(d["entities"]) > 30:
            raise MemoryError("invalid entities")
        d["entities"] = sorted(set(string(e, "entity", 150).lower() for e in d["entities"]))
        if d["valid_until"] is not None:
            if type(d["valid_until"]) not in (int, float) or not math.isfinite(d["valid_until"]):
                raise MemoryError("valid_until must be a finite UTC Unix timestamp")
        secret_check(d)
        return d

    def _version(self, memory_id, revision, d):
        self.db.execute('''INSERT INTO versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (memory_id, revision, d["title"], d["content"], d["priority"], d["kind"],
             d["importance"], d["confidence"], encoded(d["entities"]), d["source_ref"],
             d["valid_until"], digest(d), self.clock()))

    def _event(self, memory_id, revision, kind):
        self.db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                        (uid(), memory_id, revision, kind, self.clock()))

    def _index(self, owner, memory_id):
        self.db.execute("DELETE FROM memory_fts WHERE memory_id=?", (memory_id,))
        row = self._row(owner, memory_id)
        if row["status"] == "active":
            terms = tokens(row["title"] + " " + row["content"] + " " + " ".join(json.loads(row["entities"])))
            self.db.execute("INSERT INTO memory_fts VALUES (?,?)", (memory_id, " ".join(terms)))

    def create(self, owner, data, project=None, fact_key=None):
        string(owner, "owner", 120)
        project = string(project, "project", 120, True)
        fact_key = string(fact_key, "fact_key", 250, True)
        d = self._validate(data)
        secret_check([project, fact_key])
        memory_id = uid()
        with self.transaction():
            n = self.db.execute("SELECT count(*) FROM memories WHERE owner=?", (owner,)).fetchone()[0]
            if n >= self.max_records:
                raise MemoryError("owner record limit reached")
            self.db.execute("INSERT INTO memories VALUES (?,?,?,?,1,'candidate',?)",
                            (memory_id, owner, project, fact_key, self.clock()))
            self._version(memory_id, 1, d)
            self._event(memory_id, 1, "created")
            return self.get(owner, memory_id)

    def _check(self, row, revision, statuses):
        if type(revision) is not int or row["current_revision"] != revision or row["status"] not in statuses:
            raise Conflict("revision or lifecycle changed; reload before retry")

    def accept(self, owner, memory_id, revision, replaces=None):
        with self.transaction():
            row = self._row(owner, memory_id)
            self._check(row, revision, ("candidate",))
            if replaces:
                old = self._row(owner, replaces)
                if (not row["fact_key"] or old["fact_key"] != row["fact_key"]
                        or old["project"] != row["project"] or old["status"] != "active"):
                    raise Conflict("replacement must target an active fact in the same scope")
                self.db.execute("UPDATE memories SET status='superseded' WHERE id=?", (replaces,))
                self._index(owner, replaces)
                self._event(replaces, old["current_revision"], "superseded")
            if row["fact_key"]:
                conflict = self.db.execute('''SELECT id FROM memories WHERE owner=? AND project IS ?
                  AND fact_key=? AND status='active' AND id!=?''',
                  (owner, row["project"], row["fact_key"], memory_id)).fetchone()
                if conflict:
                    raise Conflict("active fact exists; select an explicit replacement")
            self.db.execute("UPDATE memories SET status='active' WHERE id=?", (memory_id,))
            self._event(memory_id, revision, "accepted")
            self._index(owner, memory_id)
            return self.get(owner, memory_id)

    def revise(self, owner, memory_id, revision, data):
        d = self._validate(data)
        with self.transaction():
            row = self._row(owner, memory_id)
            self._check(row, revision, ("candidate", "active"))
            self._version(memory_id, revision+1, d)
            self.db.execute("UPDATE memories SET current_revision=?,status='candidate' WHERE id=?",
                            (revision+1, memory_id))
            self._event(memory_id, revision+1, "revised")
            self._index(owner, memory_id)
            return self.get(owner, memory_id)

    def forget(self, owner, memory_id, revision):
        with self.transaction():
            row = self._row(owner, memory_id)
            self._check(row, revision, ("candidate","active","superseded"))
            # Logical erasure; forensic disk/backups are outside this prototype.
            self.db.execute("UPDATE memories SET status='forgotten',fact_key=NULL WHERE id=?", (memory_id,))
            self.db.execute('''UPDATE versions SET title='',content='',entities='[]',source_ref='',
                content_hash='' WHERE memory_id=?''', (memory_id,))
            self.db.execute("DELETE FROM feedback WHERE memory_id=?", (memory_id,))
            self._event(memory_id, revision, "forgotten")
            self._index(owner, memory_id)
        return {"id": memory_id, "status": "forgotten", "erasure": "logical-only"}

    def feedback(self, owner, memory_id, revision, task_id, outcome, evidence_ref):
        task_id = string(task_id, "task_id", 150)
        evidence_ref = string(evidence_ref, "evidence_ref", 1500)
        secret_check([task_id, evidence_ref])
        if outcome not in ("success", "failure"):
            raise MemoryError("outcome must be success or failure")
        with self.transaction():
            row = self._row(owner, memory_id)
            self._check(row, revision, ("active",))
            old = self.db.execute('''SELECT outcome,evidence_ref FROM feedback
              WHERE memory_id=? AND revision=? AND task_id=?''', (memory_id,revision,task_id)).fetchone()
            if old:
                if tuple(old) != (outcome, evidence_ref):
                    raise Conflict("conflicting feedback for the same task")
                return {"recorded": False, "reason": "duplicate-task"}
            self.db.execute("INSERT INTO feedback VALUES (?,?,?,?,?,?)",
              (memory_id,revision,task_id,outcome,evidence_ref,self.clock()))
        return {"recorded": True}

    def history(self, owner, memory_id):
        with self.transaction(write=False):
            self._row(owner, memory_id)
            return [dict(r) for r in self.db.execute("SELECT * FROM versions WHERE memory_id=? ORDER BY revision", (memory_id,))]

    def read_manifest(self, owner, manifest_id):
        row = self.db.execute("SELECT payload FROM manifests WHERE owner=? AND id=?", (owner,manifest_id)).fetchone()
        if row is None:
            raise NotFound("manifest not found")
        return json.loads(row[0])

    def rebuild_index(self, owner):
        with self.transaction():
            ids = self.db.execute("SELECT id FROM memories WHERE owner=?", (owner,)).fetchall()
            for row in ids:
                self._index(owner, row[0])
        return {"indexed": len(ids)}

    def retrieve(self, owner, query, project=None, budget=12000, task_context="", entities=None):
        # One snapshot for FTS, versions, feedback and the stored receipt.
        # Writes remain serialized by SQLite; independent reads use WAL snapshots.
        with self.transaction():
            return self._retrieve(owner, query, project, budget, task_context, entities)

    def _retrieve(self, owner, query, project, budget, task_context, entities):
        if not isinstance(query, str) or len(query) > 4000 or not isinstance(task_context, str) or len(task_context) > 24000:
            raise MemoryError("invalid query/context")
        if type(budget) is not int or not 128 <= budget <= 1000000:
            raise MemoryError("budget must be 128..1000000 UTF-8 bytes")
        project = string(project, "project", 120, True)
        entities = [] if entities is None else entities
        if not isinstance(entities, list) or len(entities) > 30:
            raise MemoryError("invalid query entities")
        entity_set = {string(e, "entity", 150).lower() for e in entities}
        qtokens = set(tokens(query))
        if len(qtokens) > 100:
            raise MemoryError("query has too many terms")
        # Scope filters execute before ranking and before any content leaves SQLite.
        fts_ids = set()
        if qtokens:
            match = " OR ".join('"' + t.replace('"','""') + '"' for t in sorted(qtokens))
            fts_ids = {r[0] for r in self.db.execute('''SELECT f.memory_id FROM memory_fts f
              JOIN memories m ON m.id=f.memory_id WHERE memory_fts MATCH ?
              AND m.owner=? AND (m.project IS NULL OR m.project=?) AND m.status='active' ''',
              (match,owner,project))}
        ids = self.db.execute('''SELECT id FROM memories WHERE owner=? AND status='active'
          AND (project IS NULL OR project=?)''', (owner,project)).fetchall()
        rows = [self._row(owner, r[0]) for r in ids]
        now = self.clock()
        fresh = [r for r in rows if r["valid_until"] is None or r["valid_until"] > now]
        overrides = {r["fact_key"] for r in fresh if r["project"] is not None and r["fact_key"]}
        selected, excluded = [], []
        for r in rows:
            reason = None
            if r["valid_until"] is not None and r["valid_until"] <= now:
                reason = "expired"
            elif r["project"] is None and r["fact_key"] in overrides:
                reason = "project-override"
            if reason:
                excluded.append({"id": r["id"], "reason": reason})
                continue
            words = set(tokens(r["title"] + " " + r["content"]))
            lexical = len(qtokens & words) / max(1, len(qtokens))
            exact = bool(entity_set & set(json.loads(r["entities"])))
            mandatory = r["priority"] == "P0"
            if not mandatory and not (r["id"] in fts_ids or exact):
                continue
            feedback = self.db.execute('''SELECT outcome,created FROM feedback
              WHERE memory_id=? AND revision=?''', (r["id"],r["current_revision"])).fetchall()
            good = [f for f in feedback if f["outcome"] == "success"]
            bad = len(feedback)-len(good)
            reliability = (len(good)+1)/(len(feedback)+2)
            activation = min(1.0, sum(math.exp(-max(0,now-f["created"])/(30*86400)) for f in good)/5)
            # Explicit, bounded initial heuristic. No unbounded frequency multiplier.
            score = (0.45*lexical + 0.2*int(exact) + 0.1*r["importance"]
                     + 0.1*r["confidence"] + 0.1*reliability + 0.05*activation)
            score *= 1/(1+0.2*bad)
            r["rank"] = {"score": round(score,6), "lexical": lexical, "entity_match": exact,
                         "successes": len(good), "failures": bad, "reliability": reliability,
                         "activation": activation, "mandatory": mandatory}
            selected.append(r)
        selected.sort(key=lambda r: (not r["rank"]["mandatory"], -r["rank"]["score"], r["id"]))
        blocks = ["TASK\n" + task_context]
        used = len(blocks[0].encode("utf-8"))
        if used > budget:
            raise BudgetExceeded("task context exceeds budget; no truncated task emitted")
        packed = []
        for r in selected:
            block = f'\n\n[{r["id"]}@{r["current_revision"]} {r["priority"]}] {r["title"]}\n{r["content"]}\nSource: {r["source_ref"]}'
            cost = len(block.encode("utf-8"))
            if used+cost > budget:
                if r["rank"]["mandatory"]:
                    raise BudgetExceeded("P0 constraints exceed budget; expand budget explicitly")
                excluded.append({"id": r["id"], "reason": "budget"})
                continue
            blocks.append(block)
            used += cost
            packed.append({"id":r["id"],"revision":r["current_revision"],"hash":r["content_hash"],
                           "bytes":cost,"rank":r["rank"]})
        manifest = {"id":uid(),"algorithm":"bounded-feedback-v1","budget_unit":"utf8_bytes",
                    "budget":budget,"used":used,"query_hash":digest([query,task_context]),
                    "selected":packed,"excluded":excluded}
        # Manifest contains references, never a second copy of memory content.
        self.db.execute("INSERT INTO manifests VALUES (?,?,?,?,?)",
                        (manifest["id"],owner,project,encoded(manifest),now))
        return {"context":"".join(blocks),"manifest":manifest}

    def checkpoint(self, owner, session, state, expected_revision=0):
        string(session,"session",150)
        required = {"goal","constraints","decisions","completed","pending","evidence_refs","next_action"}
        if not isinstance(state,dict) or set(state) != required:
            raise MemoryError("checkpoint requires goal, constraints, decisions, completed, pending, evidence_refs, next_action")
        for k in ("goal","next_action"):
            string(state[k],k,10000)
        for k in required - {"goal","next_action"}:
            if not isinstance(state[k],list) or len(state[k])>100:
                raise MemoryError("checkpoint collections must be bounded lists")
            for item in state[k]:
                string(item,k,10000)
        secret_check(state)
        payload = encoded(state)
        if len(payload.encode())>128000:
            raise MemoryError("checkpoint too large")
        with self.transaction():
            current = self.db.execute("SELECT COALESCE(MAX(revision),0) FROM checkpoints WHERE owner=? AND session=?", (owner,session)).fetchone()[0]
            if type(expected_revision) is not int or current != expected_revision:
                raise Conflict("checkpoint revision changed")
            self.db.execute("INSERT INTO checkpoints VALUES (?,?,?,?,?,?)",
              (owner,session,current+1,payload,digest(state),self.clock()))
        return {"session":session,"revision":current+1,"hash":digest(state),"bytes":len(payload.encode())}

    def read_checkpoint(self, owner, session, revision=None):
        sql = "SELECT * FROM checkpoints WHERE owner=? AND session=?"
        args = [owner,session]
        if revision is not None:
            sql += " AND revision=?"
            args.append(revision)
        row = self.db.execute(sql+" ORDER BY revision DESC LIMIT 1",args).fetchone()
        if row is None:
            raise NotFound("checkpoint not found")
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def health(self, owner):
        counts = {r[0]:r[1] for r in self.db.execute("SELECT status,count(*) FROM memories WHERE owner=? GROUP BY status",(owner,))}
        return {"schema":1,"counts":counts,"ranking":"bounded-feedback-v1",
                "budget_unit":"utf8_bytes","semantic_search":False,"automatic_learning":False}
