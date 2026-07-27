"""Human-in-the-loop: the queue an escalated action waits in."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from ..models import Approval
from .deps import get_control, operator

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])


@router.get("", response_model=list[Approval])
async def list_approvals(status: str | None = None, limit: int = 50, control=Depends(get_control)) -> list[Approval]:
    return await control.repo.list_approvals(status=status, limit=limit)


@router.get("/{approval_id}", response_model=Approval)
async def get_approval(approval_id: str, control=Depends(get_control)) -> Approval:
    approval = await control.repo.get_approval(approval_id)
    if approval is None:
        raise HTTPException(404, f"unknown approval '{approval_id}'")
    return approval


@router.post("/{approval_id}/approve", response_model=Approval)
async def approve(
    approval_id: str, payload: dict = Body(default={}), control=Depends(get_control), actor: str = Depends(operator)
) -> Approval:
    """Signing off releases exactly one action — not a standing permission."""
    try:
        return await control.decide_approval(approval_id, True, actor, payload.get("note"))
    except KeyError:
        raise HTTPException(404, f"unknown approval '{approval_id}'")


@router.post("/{approval_id}/reject", response_model=Approval)
async def reject(
    approval_id: str, payload: dict = Body(default={}), control=Depends(get_control), actor: str = Depends(operator)
) -> Approval:
    try:
        return await control.decide_approval(approval_id, False, actor, payload.get("note"))
    except KeyError:
        raise HTTPException(404, f"unknown approval '{approval_id}'")
