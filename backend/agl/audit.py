"""The tamper-evident audit log.

Every decision — allow, deny, block, quarantine, escalate — becomes exactly one
entry. Each entry's hash covers its own canonical content *and* the previous
entry's hash, so the log is a chain: altering or deleting any historical row
invalidates every hash after it, and verification catches it.

Writes are handed to a single background writer so the decision path never waits
on disk. The single writer is also what makes the chain well-ordered: seq and
prev_hash are assigned in one place, so there is no race over the chain head.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

from .models import AuditEntry, ChainVerification, Decision, ReasonCode

GENESIS_HASH = "0" * 64


def compute_hash(
    *,
    seq: int,
    prev_hash: str,
    ts: float,
    agent_id: str,
    action: str,
    resource: str | None,
    amount_cents: int,
    decision: str,
    reason_code: str,
    request_id: str,
) -> str:
    """SHA-256 over a canonical JSON encoding — stable across drivers and hosts."""
    payload = json.dumps(
        {
            "seq": seq,
            "prev": prev_hash,
            "ts": round(ts, 6),
            "agent": agent_id,
            "action": action,
            "resource": resource,
            "amount": amount_cents,
            "decision": decision,
            "reason_code": reason_code,
            "request_id": request_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def entry_hash(entry: AuditEntry, prev_hash: str | None = None) -> str:
    return compute_hash(
        seq=entry.seq,
        prev_hash=prev_hash if prev_hash is not None else entry.prev_hash,
        ts=entry.ts,
        agent_id=entry.agent_id,
        action=entry.action,
        resource=entry.resource,
        amount_cents=entry.amount_cents,
        decision=entry.decision.value,
        reason_code=entry.reason_code.value,
        request_id=entry.request_id,
    )


class AuditChain:
    """Append-only, hash-chained decision log with an async single writer."""

    def __init__(self, repo, on_append=None, async_writes: bool = True) -> None:
        self.repo = repo
        self.on_append = on_append
        self.async_writes = async_writes
        self._queue: asyncio.Queue[AuditEntry] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._seq = 0
        self._head = GENESIS_HASH
        self._lock = asyncio.Lock()
        self._drained = asyncio.Event()
        self._drained.set()

    async def start(self) -> None:
        head = await self.repo.audit_head()
        if head:
            self._seq = int(head["seq"])
            self._head = head["hash"]
        if self.async_writes:
            self._task = asyncio.create_task(self._writer(), name="agl-audit-writer")

    async def stop(self) -> None:
        await self.drain()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    @property
    def head_hash(self) -> str:
        return self._head

    @property
    def height(self) -> int:
        return self._seq

    async def append(
        self,
        *,
        agent_id: str,
        agent_name: str,
        action: str,
        resource: str | None,
        amount_cents: int,
        decision: Decision,
        reason_code: ReasonCode,
        reason: str,
        request_id: str,
        actor: str = "agent",
        latency_ms: float = 0.0,
        ts: float | None = None,
    ) -> AuditEntry:
        """Seal an entry into the chain and hand it to the writer.

        Sequencing and hashing happen inline (microseconds, in memory) so the
        caller gets the hash back immediately; only the durable write is
        deferred.
        """
        async with self._lock:
            self._seq += 1
            seq, prev = self._seq, self._head
            ts = ts or time.time()
            digest = compute_hash(
                seq=seq,
                prev_hash=prev,
                ts=ts,
                agent_id=agent_id,
                action=action,
                resource=resource,
                amount_cents=amount_cents,
                decision=decision.value,
                reason_code=reason_code.value,
                request_id=request_id,
            )
            self._head = digest
            entry = AuditEntry(
                seq=seq,
                ts=ts,
                agent_id=agent_id,
                agent_name=agent_name,
                action=action,
                resource=resource,
                amount_cents=amount_cents,
                decision=decision,
                reason_code=reason_code,
                reason=reason,
                request_id=request_id,
                actor=actor,
                latency_ms=latency_ms,
                prev_hash=prev,
                hash=digest,
            )

        if self.async_writes:
            self._drained.clear()
            self._queue.put_nowait(entry)
        else:
            await self.repo.append_audit(entry)
        if self.on_append:
            await self.on_append(entry)
        return entry

    async def _writer(self) -> None:
        while True:
            entry = await self._queue.get()
            try:
                await self.repo.append_audit(entry)
            except Exception as exc:  # pragma: no cover - operational path
                print(f"[agl] audit write failed for seq={entry.seq}: {exc}")
            finally:
                self._queue.task_done()
                if self._queue.empty():
                    self._drained.set()

    async def drain(self) -> None:
        """Wait for durable writes to land (used by tests and verification)."""
        if self.async_writes and not self._queue.empty():
            await self._queue.join()
        self._drained.set()

    # --- verification ------------------------------------------------------
    async def verify(self) -> ChainVerification:
        """Recompute the whole chain and report the first break, if any."""
        started = time.perf_counter()
        await self.drain()
        entries = await self.repo.audit_all_ascending()
        prev = GENESIS_HASH
        expected_seq = 1
        for entry in entries:
            if entry.seq != expected_seq:
                return ChainVerification(
                    ok=False,
                    entries_checked=expected_seq - 1,
                    broken_at=entry.seq,
                    detail=f"sequence gap: expected {expected_seq}, found {entry.seq} — an entry was deleted",
                    head_hash=prev,
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
            if entry.prev_hash != prev:
                return ChainVerification(
                    ok=False,
                    entries_checked=entry.seq - 1,
                    broken_at=entry.seq,
                    detail=f"chain break at #{entry.seq}: prev_hash does not match the previous entry",
                    head_hash=prev,
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
            recomputed = entry_hash(entry)
            if recomputed != entry.hash:
                return ChainVerification(
                    ok=False,
                    entries_checked=entry.seq - 1,
                    broken_at=entry.seq,
                    detail=f"entry #{entry.seq} was modified after it was written",
                    head_hash=prev,
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
            prev = entry.hash
            expected_seq += 1
        return ChainVerification(
            ok=True,
            entries_checked=len(entries),
            broken_at=None,
            detail=f"{len(entries)} entries verified — chain intact",
            head_hash=prev,
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    async def proof(self, seq: int) -> dict[str, Any]:
        """Inclusion evidence for one entry: itself plus its neighbours' links."""
        await self.drain()
        rows = await self.repo.query(
            "SELECT seq, prev_hash, hash, agent_id, action, decision, reason FROM audit WHERE seq BETWEEN ? AND ? ORDER BY seq",
            (max(seq - 1, 1), seq + 1),
        )
        target = next((r for r in rows if r["seq"] == seq), None)
        if not target:
            return {"found": False, "seq": seq}
        return {
            "found": True,
            "seq": seq,
            "entry": target,
            "links": rows,
            "head_hash": self._head,
            "height": self._seq,
        }
