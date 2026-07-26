"""Control for the stub agent fleet, so the console has live traffic to govern."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from .deps import get_control

router = APIRouter(prefix="/v1/simulator", tags=["simulator"])


@router.get("")
async def status(control=Depends(get_control)) -> dict:
    sim = control.simulator
    return {"running": sim.running, "rate_per_sec": sim.rate_per_sec, "decisions": sim.decisions}


@router.post("/start")
async def start(payload: dict = Body(default={}), control=Depends(get_control)) -> dict:
    control.simulator.start(float(payload.get("rate_per_sec") or control.simulator.rate_per_sec))
    return {"running": True, "rate_per_sec": control.simulator.rate_per_sec}


@router.post("/stop")
async def stop(control=Depends(get_control)) -> dict:
    await control.simulator.stop()
    return {"running": False}


@router.post("/burst")
async def burst(payload: dict = Body(default={}), control=Depends(get_control)) -> dict:
    count = await control.simulator.burst(int(payload.get("count") or 40))
    return {"fired": count}


@router.post("/reset-counters")
async def reset_counters(control=Depends(get_control)) -> dict:
    """Clear today's spend and rate counters so the demo can be run again.

    Kill state, revocations and the audit chain are deliberately untouched —
    the log is append-only and stays that way.
    """
    await control.state.reset_counters()
    return await control.stats()
