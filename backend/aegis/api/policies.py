"""Policy editing. Every save is a new immutable version."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from ..models import AgentPolicy
from .deps import get_control, operator

router = APIRouter(prefix="/v1/policies", tags=["policies"])


@router.get("")
async def list_policies(control=Depends(get_control)) -> dict[str, dict]:
    return {aid: p.model_dump() for aid, p in control.gateway.policies.items()}


@router.get("/{agent_id}", response_model=AgentPolicy)
async def get_policy(agent_id: str, control=Depends(get_control)) -> AgentPolicy:
    policy = control.gateway.policy(agent_id)
    if policy is None:
        raise HTTPException(404, f"no policy bound to '{agent_id}'")
    return policy


@router.put("/{agent_id}", response_model=AgentPolicy)
async def update_policy(
    agent_id: str, policy: AgentPolicy, control=Depends(get_control), actor: str = Depends(operator)
) -> AgentPolicy:
    """Takes effect on the very next decision — no restart, no redeploy."""
    if control.gateway.agent(agent_id) is None:
        raise HTTPException(404, f"unknown agent '{agent_id}'")
    return await control.update_policy(agent_id, policy, actor)


@router.patch("/{agent_id}", response_model=AgentPolicy)
async def patch_policy(
    agent_id: str,
    patch: dict = Body(...),
    control=Depends(get_control),
    actor: str = Depends(operator),
) -> AgentPolicy:
    """Partial edit — what the console's toggles and cap fields send."""
    current = control.gateway.policy(agent_id)
    if current is None:
        raise HTTPException(404, f"no policy bound to '{agent_id}'")
    merged = current.model_dump()
    for key, value in patch.items():
        if key in {"spend", "guardrails", "hitl"} and isinstance(value, dict):
            merged[key].update(value)
        elif key in merged:
            merged[key] = value
    return await control.update_policy(agent_id, AgentPolicy(**merged), actor)


@router.get("/{agent_id}/history")
async def policy_history(agent_id: str, control=Depends(get_control)) -> list[dict]:
    return await control.repo.policy_history(agent_id)
