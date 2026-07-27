"""Idempotency helpers for authorize requests."""

from __future__ import annotations

import hashlib
import json

from .models import AuthorizeRequest, AuthorizeResponse


def request_fingerprint(request: AuthorizeRequest) -> str:
    """Stable hash of the action payload — same key must map to the same intent."""
    payload = {
        "agent_id": request.agent_id,
        "action": request.action,
        "resource": request.resource,
        "amount_cents": request.amount_cents,
        "counterparty": request.counterparty,
        "approval_id": request.approval_id,
        "context": request.context.model_dump(),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def serialize_response(response: AuthorizeResponse) -> str:
    return response.model_dump_json()


def deserialize_response(raw: str) -> AuthorizeResponse:
    return AuthorizeResponse.model_validate_json(raw)
