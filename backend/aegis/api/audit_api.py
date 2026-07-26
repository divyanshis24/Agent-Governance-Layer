"""The audit log: read, verify, prove — and, in demo mode, break on purpose.

The tamper endpoint exists so the claim can be tested rather than asserted:
edit a historical row behind the log's back, re-verify, and watch the chain
report exactly which entry was altered.
"""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..config import settings
from ..models import AuditEntry, ChainVerification
from .deps import get_control

router = APIRouter(prefix="/v1/audit", tags=["audit"])


@router.get("", response_model=list[AuditEntry])
async def list_audit(
    limit: int = 100,
    before_seq: int | None = None,
    agent_id: str | None = None,
    decision: str | None = None,
    control=Depends(get_control),
) -> list[AuditEntry]:
    await control.chain.drain()
    return await control.repo.list_audit(limit=limit, before_seq=before_seq, agent_id=agent_id, decision=decision)


@router.get("/verify", response_model=ChainVerification)
async def verify(control=Depends(get_control)) -> ChainVerification:
    """Recompute every hash from genesis and report the first break."""
    return await control.verify_chain()


@router.get("/head")
async def head(control=Depends(get_control)) -> dict:
    return {"height": control.chain.height, "head_hash": control.chain.head_hash}


@router.get("/proof/{seq}")
async def proof(seq: int, control=Depends(get_control)) -> dict:
    result = await control.chain.proof(seq)
    if not result.get("found"):
        raise HTTPException(404, f"no audit entry #{seq}")
    return result


@router.get("/export")
async def export(control=Depends(get_control)) -> StreamingResponse:
    """CSV for the auditor, hashes included so the chain travels with the data."""
    await control.chain.drain()
    entries = await control.repo.audit_all_ascending()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["seq", "ts", "agent_id", "agent_name", "action", "resource", "amount_cents",
         "decision", "reason_code", "reason", "request_id", "actor", "latency_ms", "prev_hash", "hash"]
    )
    for e in entries:
        writer.writerow(
            [e.seq, e.ts, e.agent_id, e.agent_name, e.action, e.resource or "", e.amount_cents,
             e.decision.value, e.reason_code.value, e.reason, e.request_id, e.actor, round(e.latency_ms, 3),
             e.prev_hash, e.hash]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.read()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=aegis-audit-log.csv"},
    )


@router.post("/_tamper")
async def tamper(payload: dict = Body(default={}), control=Depends(get_control)) -> dict:
    """DEMO ONLY — mutate a historical row directly in the database.

    This is the adversary's move: reach past the append-only API and edit the
    record. It proves the log is tamper-*evident*, not tamper-proof: the write
    succeeds, and verification catches it immediately.
    """
    if not settings.demo_mode:
        raise HTTPException(403, "tamper endpoint is disabled outside demo mode")
    await control.chain.drain()
    seq = int(payload.get("seq") or 0)
    if not seq:
        row = await control.repo.one(
            "SELECT seq FROM audit WHERE decision IN ('block','deny','quarantine') ORDER BY seq DESC LIMIT 1"
        )
        if not row:
            raise HTTPException(400, "no entry available to tamper with")
        seq = int(row["seq"])
    new_reason = payload.get("reason", "within policy")
    new_decision = payload.get("decision", "allow")
    await control.repo.execute(
        "UPDATE audit SET decision = ?, reason = ? WHERE seq = ?", (new_decision, new_reason, seq)
    )
    verification = await control.verify_chain()
    await control.bus.publish("audit.tampered", {"seq": seq, "verification": verification.model_dump()})
    return {"tampered_seq": seq, "verification": verification.model_dump()}


@router.post("/_restore")
async def restore(control=Depends(get_control)) -> dict:
    """DEMO ONLY — rebuild the chain from the surviving entries after a tamper."""
    if not settings.demo_mode:
        raise HTTPException(403, "restore endpoint is disabled outside demo mode")
    await control.chain.drain()
    entries = await control.repo.audit_all_ascending()
    from ..audit import GENESIS_HASH, entry_hash

    prev = GENESIS_HASH
    for entry in entries:
        entry.prev_hash = prev
        digest = entry_hash(entry)
        await control.repo.execute(
            "UPDATE audit SET prev_hash = ?, hash = ? WHERE seq = ?", (prev, digest, entry.seq)
        )
        prev = digest
    control.chain._head = prev
    control.chain._seq = entries[-1].seq if entries else 0
    verification = await control.verify_chain()
    await control.bus.publish("audit.restored", {"verification": verification.model_dump()})
    return {"rebuilt": len(entries), "verification": verification.model_dump()}
