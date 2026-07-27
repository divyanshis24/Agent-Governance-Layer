"""The two ways an agent integrates.

  * POST /v1/authorize — SDK mode. The agent asks first, then acts.
  * POST /v1/proxy     — proxy mode. The layer makes the downstream call, so
                         the agent never holds a path to the money. This is the
                         mode that turns "should not bypass" into "cannot".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..bank import CoreBanking
from ..enforce.guardrails import mask_text
from ..models import AuthorizeRequest, AuthorizeResponse, Decision
from .deps import get_control

router = APIRouter(prefix="/v1", tags=["gateway"])


@router.post("/authorize", response_model=AuthorizeResponse)
async def authorize(request: AuthorizeRequest, control=Depends(get_control)) -> AuthorizeResponse:
    """The mandatory checkpoint. Every consequential action passes through here."""
    return await control.gateway.authorize(request)


@router.post("/authorize/batch", response_model=list[AuthorizeResponse])
async def authorize_batch(requests: list[AuthorizeRequest], control=Depends(get_control)) -> list[AuthorizeResponse]:
    return [await control.gateway.authorize(r) for r in requests]


@router.post("/proxy")
async def proxy(request: AuthorizeRequest, control=Depends(get_control)) -> dict:
    """Authorize, then execute against the core banking systems on the agent's behalf."""
    decision: AuthorizeResponse = await control.gateway.authorize(request)
    if decision.decision != Decision.ALLOW:
        return {
            "executed": False,
            "authorization": decision.model_dump(mode="json"),
            "result": None,
        }

    result = await CoreBanking.execute(request)

    # Mask on the way *out*, not just on what the request declared. A response
    # can carry PAN/SSN the agent never asked for, and in proxy mode the gateway
    # is the last thing that touches the payload before the agent sees it.
    policy = control.gateway.policy(request.agent_id)
    if (policy and policy.guardrails.mask_pan_ssn) or decision.obligations.get("mask_fields"):
        result = _apply_masking(result)

    return {"executed": True, "authorization": decision.model_dump(mode="json"), "result": result}


def _apply_masking(value):
    """Recursively redact PAN/SSN anywhere in the downstream payload."""
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {k: _apply_masking(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_apply_masking(v) for v in value]
    return value


@router.get("/decision/{request_id}")
async def decision_detail(request_id: str, control=Depends(get_control)) -> dict:
    row = await control.repo.one("SELECT * FROM audit WHERE request_id = ? ORDER BY seq DESC LIMIT 1", (request_id,))
    if not row:
        raise HTTPException(404, "no decision recorded for that request id")
    return row
