"""Durable records: agents, policies, spend ledger, approvals, audit chain.

One implementation, two drivers. PostgreSQL is used when DATABASE_URL points at
it; otherwise SQLite (WAL) gives the demo the same API with no daemon to run.
Statements are written with `?` placeholders and translated for psycopg. DDL is
deliberately portable, and the audit `seq` is assigned by the single-writer
audit task rather than a sequence, so both drivers see identical rows.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from ..models import Agent, AgentPolicy, Approval, AuditEntry, Decision, ReasonCode

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS agents (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        owner TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active',
        created_at DOUBLE PRECISION NOT NULL,
        revoked_at DOUBLE PRECISION,
        revoked_by TEXT,
        revoke_reason TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS policies (
        agent_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        body TEXT NOT NULL,
        updated_at DOUBLE PRECISION NOT NULL,
        updated_by TEXT NOT NULL,
        PRIMARY KEY (agent_id, version)
    )""",
    """CREATE TABLE IF NOT EXISTS audit (
        seq INTEGER PRIMARY KEY,
        ts DOUBLE PRECISION NOT NULL,
        agent_id TEXT NOT NULL,
        agent_name TEXT DEFAULT '',
        action TEXT NOT NULL,
        resource TEXT,
        amount_cents INTEGER DEFAULT 0,
        decision TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        reason TEXT NOT NULL,
        request_id TEXT NOT NULL,
        actor TEXT DEFAULT 'agent',
        latency_ms DOUBLE PRECISION DEFAULT 0,
        prev_hash TEXT NOT NULL,
        hash TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS ledger (
        id TEXT PRIMARY KEY,
        ts DOUBLE PRECISION NOT NULL,
        agent_id TEXT NOT NULL,
        action TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        counterparty TEXT,
        request_id TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS approvals (
        id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL,
        agent_name TEXT DEFAULT '',
        action TEXT NOT NULL,
        resource TEXT,
        amount_cents INTEGER DEFAULT 0,
        reason TEXT DEFAULT '',
        status TEXT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        decided_at DOUBLE PRECISION,
        decided_by TEXT,
        note TEXT,
        request_id TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS operator_events (
        id TEXT PRIMARY KEY,
        ts DOUBLE PRECISION NOT NULL,
        kind TEXT NOT NULL,
        actor TEXT NOT NULL,
        target TEXT,
        detail TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS kv (
        k TEXT PRIMARY KEY,
        v TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS idempotency (
        agent_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        response_json TEXT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (agent_id, idempotency_key)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_audit_agent ON audit (agent_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit (ts)",
    "CREATE INDEX IF NOT EXISTS idx_ledger_agent ON ledger (agent_id, ts)",
]


class Repository:
    """Async wrapper over a synchronous DBAPI connection.

    Every call hops to a worker thread, so the event loop is never blocked by
    disk I/O on the decision path.
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self.is_postgres = url.startswith(("postgres://", "postgresql://"))
        self._conn: Any = None
        self._lock = asyncio.Lock()

    # --- connection --------------------------------------------------------
    async def connect(self) -> "Repository":
        if self.is_postgres:
            import psycopg

            self._conn = await asyncio.to_thread(psycopg.connect, self.url, autocommit=True)
        else:
            path = Path(self.url.replace("sqlite:///", ""))
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.isolation_level = None
        await self.migrate()
        return self

    async def close(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.is_postgres else sql

    def _exec_sync(self, sql: str, params: tuple = ()) -> None:
        cur = self._conn.cursor()
        try:
            cur.execute(self._sql(sql), params)
        finally:
            cur.close()

    def _query_sync(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = self._conn.cursor()
        try:
            cur.execute(self._sql(sql), params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            cur.close()

    async def execute(self, sql: str, params: tuple = ()) -> None:
        async with self._lock:
            await asyncio.to_thread(self._exec_sync, sql, params)

    async def query(self, sql: str, params: tuple = ()) -> list[dict]:
        async with self._lock:
            return await asyncio.to_thread(self._query_sync, sql, params)

    async def one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = await self.query(sql, params)
        return rows[0] if rows else None

    async def migrate(self) -> None:
        for stmt in SCHEMA:
            await self.execute(stmt)

    async def reset(self) -> None:
        for table in (
            "audit",
            "ledger",
            "approvals",
            "policies",
            "agents",
            "operator_events",
            "kv",
            "idempotency",
        ):
            await self.execute(f"DELETE FROM {table}")

    # --- small config values ----------------------------------------------
    async def get_kv(self, key: str, default: Any = None) -> Any:
        row = await self.one("SELECT v FROM kv WHERE k = ?", (key,))
        return json.loads(row["v"]) if row else default

    async def set_kv(self, key: str, value: Any) -> None:
        await self.execute(
            "INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v",
            (key, json.dumps(value)),
        )

    # --- agents ------------------------------------------------------------
    async def upsert_agent(self, agent: Agent) -> None:
        await self.execute(
            """INSERT INTO agents (id, name, description, owner, status, created_at, revoked_at, revoked_by, revoke_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (id) DO UPDATE SET
                 name = EXCLUDED.name, description = EXCLUDED.description, owner = EXCLUDED.owner,
                 status = EXCLUDED.status, revoked_at = EXCLUDED.revoked_at,
                 revoked_by = EXCLUDED.revoked_by, revoke_reason = EXCLUDED.revoke_reason""",
            (
                agent.id,
                agent.name,
                agent.description,
                agent.owner,
                agent.status,
                agent.created_at,
                agent.revoked_at,
                agent.revoked_by,
                agent.revoke_reason,
            ),
        )

    async def list_agents(self) -> list[Agent]:
        rows = await self.query("SELECT * FROM agents ORDER BY created_at")
        return [Agent(**r) for r in rows]

    async def get_agent(self, agent_id: str) -> Agent | None:
        row = await self.one("SELECT * FROM agents WHERE id = ?", (agent_id,))
        return Agent(**row) if row else None

    # --- policies ----------------------------------------------------------
    async def save_policy(self, policy: AgentPolicy) -> AgentPolicy:
        """Policies are versioned: every edit writes a new immutable row."""
        row = await self.one("SELECT MAX(version) AS v FROM policies WHERE agent_id = ?", (policy.agent_id,))
        policy.version = int((row or {}).get("v") or 0) + 1
        policy.updated_at = time.time()
        await self.execute(
            "INSERT INTO policies (agent_id, version, body, updated_at, updated_by) VALUES (?, ?, ?, ?, ?)",
            (policy.agent_id, policy.version, policy.model_dump_json(), policy.updated_at, policy.updated_by),
        )
        return policy

    async def get_policy(self, agent_id: str) -> AgentPolicy | None:
        row = await self.one(
            "SELECT body FROM policies WHERE agent_id = ? ORDER BY version DESC LIMIT 1", (agent_id,)
        )
        return AgentPolicy(**json.loads(row["body"])) if row else None

    async def all_policies(self) -> dict[str, AgentPolicy]:
        rows = await self.query("SELECT agent_id, body, version FROM policies ORDER BY agent_id, version")
        out: dict[str, AgentPolicy] = {}
        for r in rows:  # later versions overwrite earlier ones
            out[r["agent_id"]] = AgentPolicy(**json.loads(r["body"]))
        return out

    async def policy_history(self, agent_id: str) -> list[dict]:
        return await self.query(
            "SELECT version, updated_at, updated_by FROM policies WHERE agent_id = ? ORDER BY version DESC",
            (agent_id,),
        )

    # --- audit -------------------------------------------------------------
    async def audit_head(self) -> dict | None:
        return await self.one("SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1")

    async def append_audit(self, entry: AuditEntry) -> None:
        await self.execute(
            """INSERT INTO audit (seq, ts, agent_id, agent_name, action, resource, amount_cents,
                                  decision, reason_code, reason, request_id, actor, latency_ms, prev_hash, hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.seq,
                entry.ts,
                entry.agent_id,
                entry.agent_name,
                entry.action,
                entry.resource,
                entry.amount_cents,
                entry.decision.value,
                entry.reason_code.value,
                entry.reason,
                entry.request_id,
                entry.actor,
                entry.latency_ms,
                entry.prev_hash,
                entry.hash,
            ),
        )

    async def append_audit_batch(self, entries: Iterable[AuditEntry]) -> None:
        for entry in entries:
            await self.append_audit(entry)

    async def list_audit(
        self,
        limit: int = 100,
        before_seq: int | None = None,
        agent_id: str | None = None,
        decision: str | None = None,
    ) -> list[AuditEntry]:
        sql = "SELECT * FROM audit WHERE 1=1"
        params: list[Any] = []
        if before_seq:
            sql += " AND seq < ?"
            params.append(before_seq)
        if agent_id:
            sql += " AND agent_id = ?"
            params.append(agent_id)
        if decision:
            sql += " AND decision = ?"
            params.append(decision)
        sql += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        rows = await self.query(sql, tuple(params))
        return [_audit_from_row(r) for r in rows]

    async def audit_all_ascending(self) -> list[AuditEntry]:
        rows = await self.query("SELECT * FROM audit ORDER BY seq ASC")
        return [_audit_from_row(r) for r in rows]

    async def audit_count(self) -> int:
        row = await self.one("SELECT COUNT(*) AS c FROM audit")
        return int((row or {}).get("c") or 0)

    async def audit_stats_today(self, since: float) -> dict:
        rows = await self.query(
            "SELECT decision, COUNT(*) AS c FROM audit WHERE ts >= ? GROUP BY decision", (since,)
        )
        return {r["decision"]: int(r["c"]) for r in rows}

    # --- ledger ------------------------------------------------------------
    async def add_ledger(self, entry_id: str, agent_id: str, action: str, amount: int, counterparty: str | None, request_id: str) -> None:
        await self.execute(
            "INSERT INTO ledger (id, ts, agent_id, action, amount_cents, counterparty, request_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (entry_id, time.time(), agent_id, action, amount, counterparty, request_id),
        )

    async def spend_by_agent(self, since: float) -> dict[str, int]:
        rows = await self.query(
            "SELECT agent_id, SUM(amount_cents) AS total FROM ledger WHERE ts >= ? GROUP BY agent_id", (since,)
        )
        return {r["agent_id"]: int(r["total"] or 0) for r in rows}

    # --- approvals ---------------------------------------------------------
    async def save_approval(self, approval: Approval) -> None:
        await self.execute(
            """INSERT INTO approvals (id, agent_id, agent_name, action, resource, amount_cents, reason,
                                      status, created_at, decided_at, decided_by, note, request_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (id) DO UPDATE SET
                 status = EXCLUDED.status, decided_at = EXCLUDED.decided_at,
                 decided_by = EXCLUDED.decided_by, note = EXCLUDED.note""",
            (
                approval.id,
                approval.agent_id,
                approval.agent_name,
                approval.action,
                approval.resource,
                approval.amount_cents,
                approval.reason,
                approval.status,
                approval.created_at,
                approval.decided_at,
                approval.decided_by,
                approval.note,
                approval.request_id,
            ),
        )

    async def get_approval(self, approval_id: str) -> Approval | None:
        row = await self.one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        return Approval(**row) if row else None

    async def list_approvals(self, status: str | None = None, limit: int = 50) -> list[Approval]:
        if status:
            rows = await self.query(
                "SELECT * FROM approvals WHERE status = ? ORDER BY created_at DESC LIMIT ?", (status, limit)
            )
        else:
            rows = await self.query("SELECT * FROM approvals ORDER BY created_at DESC LIMIT ?", (limit,))
        return [Approval(**r) for r in rows]

    # --- operator events ---------------------------------------------------
    async def add_operator_event(self, event_id: str, kind: str, actor: str, target: str | None, detail: str) -> None:
        await self.execute(
            "INSERT INTO operator_events (id, ts, kind, actor, target, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, time.time(), kind, actor, target, detail),
        )

    async def list_operator_events(self, limit: int = 50) -> list[dict]:
        return await self.query("SELECT * FROM operator_events ORDER BY ts DESC LIMIT ?", (limit,))

    # --- idempotency -------------------------------------------------------
    async def get_idempotency(self, agent_id: str, idempotency_key: str) -> dict | None:
        return await self.one(
            "SELECT request_hash, response_json FROM idempotency WHERE agent_id = ? AND idempotency_key = ?",
            (agent_id, idempotency_key),
        )

    async def save_idempotency(
        self, agent_id: str, idempotency_key: str, request_hash: str, response_json: str
    ) -> None:
        await self.execute(
            """INSERT INTO idempotency (agent_id, idempotency_key, request_hash, response_json, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (agent_id, idempotency_key) DO NOTHING""",
            (agent_id, idempotency_key, request_hash, response_json, time.time()),
        )


def _audit_from_row(r: dict) -> AuditEntry:
    return AuditEntry(
        seq=r["seq"],
        ts=r["ts"],
        agent_id=r["agent_id"],
        agent_name=r.get("agent_name") or "",
        action=r["action"],
        resource=r.get("resource"),
        amount_cents=r.get("amount_cents") or 0,
        decision=Decision(r["decision"]),
        reason_code=ReasonCode(r["reason_code"]),
        reason=r["reason"],
        request_id=r["request_id"],
        actor=r.get("actor") or "agent",
        latency_ms=r.get("latency_ms") or 0.0,
        prev_hash=r["prev_hash"],
        hash=r["hash"],
    )


async def open_repository(url: str, *, fail_closed: bool = False) -> Repository:
    """Prefer the configured database. Fail closed if required but unreachable."""
    try:
        return await Repository(url).connect()
    except Exception as exc:
        if url.startswith("sqlite"):
            raise
        if fail_closed:
            raise RuntimeError(f"Database required but unavailable: {exc}") from exc
        print(f"[aegis] {url.split('://')[0]} unavailable ({exc}); falling back to SQLite")
        from ..config import BASE_DIR

        return await Repository(f"sqlite:///{BASE_DIR / 'data' / 'aegis.db'}").connect()
