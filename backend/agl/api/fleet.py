"""Fleet operations: roster, revocation, and the emergency stop."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from ..models import Agent, AgentPolicy, AgentView, FleetState
from .deps import get_control, operator

router = APIRouter(prefix="/v1", tags=["fleet"])


@router.get("/agents", response_model=list[AgentView])
async def list_agents(control=Depends(get_control)) -> list[AgentView]:
    return await control.fleet_view()


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, control=Depends(get_control)) -> dict:
    agent = control.gateway.agent(agent_id)
    if agent is None:
        raise HTTPException(404, f"unknown agent '{agent_id}'")
    policy = control.gateway.policy(agent_id)
    counters = await control.state.counters(agent_id)
    views = {v.id: v for v in await control.fleet_view()}
    return {
        "agent": views[agent_id].model_dump(),
        "policy": policy.model_dump() if policy else None,
        "counters": counters.__dict__,
        "policy_history": await control.repo.policy_history(agent_id),
    }


@router.post("/agents", status_code=201)
async def register_agent(
    agent: Agent = Body(...), policy: AgentPolicy = Body(...), control=Depends(get_control), actor: str = Depends(operator)
) -> dict:
    """Onboard a new agent. Deny-by-default means a new agent can do nothing
    until its permission set says otherwise."""
    policy.agent_id = agent.id
    policy.updated_by = actor
    saved_agent, saved_policy = await control.register_agent(agent, policy)
    return {"agent": saved_agent.model_dump(), "policy": saved_policy.model_dump()}


@router.post("/agents/{agent_id}/revoke")
async def revoke_agent(
    agent_id: str,
    payload: dict = Body(default={}),
    control=Depends(get_control),
    actor: str = Depends(operator),
) -> dict:
    """Real-time revocation — the agent's next action fails immediately."""
    try:
        agent = await control.revoke_agent(agent_id, actor, payload.get("reason", "revoked by operator"))
    except KeyError:
        raise HTTPException(404, f"unknown agent '{agent_id}'")
    return agent.model_dump()


@router.post("/agents/{agent_id}/reinstate")
async def reinstate_agent(agent_id: str, control=Depends(get_control), actor: str = Depends(operator)) -> dict:
    try:
        agent = await control.reinstate_agent(agent_id, actor)
    except KeyError:
        raise HTTPException(404, f"unknown agent '{agent_id}'")
    return agent.model_dump()


# --- fleet-wide emergency stop --------------------------------------------
@router.get("/fleet", response_model=FleetState)
async def fleet_state(control=Depends(get_control)) -> FleetState:
    return await control.fleet_state()


@router.post("/fleet/halt", response_model=FleetState)
async def halt_fleet(
    payload: dict = Body(default={}), control=Depends(get_control), actor: str = Depends(operator)
) -> FleetState:
    """Freeze every agent. One flag, checked first on every decision."""
    return await control.halt_fleet(actor, payload.get("reason", "operator emergency stop"))


@router.post("/fleet/resume", response_model=FleetState)
async def resume_fleet(control=Depends(get_control), actor: str = Depends(operator)) -> FleetState:
    return await control.resume_fleet(actor)


@router.put("/fleet/cap", response_model=FleetState)
async def set_fleet_cap(
    payload: dict = Body(...), control=Depends(get_control), actor: str = Depends(operator)
) -> FleetState:
    return await control.set_fleet_cap(int(payload["daily_cap_cents"]), actor)


@router.get("/stats")
async def stats(control=Depends(get_control)) -> dict:
    return await control.stats()


@router.get("/operator-events")
async def operator_events(limit: int = 50, control=Depends(get_control)) -> list[dict]:
    return await control.repo.list_operator_events(limit)
