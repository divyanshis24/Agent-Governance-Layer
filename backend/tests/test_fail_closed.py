"""Fail-closed behaviour when configured infrastructure is unavailable."""

from __future__ import annotations

import pytest

from agl.config import Settings
from agl.control import ControlPlane
from agl.models import Decision, ReasonCode


async def test_chaos_policy_down_denies_every_request(ask, control):
    control.gateway.chaos_policy_down = True
    r = await ask("svc_agent", "issue_refund", amount_cents=1_000, counterparty="cardmember account")
    assert r.decision is Decision.DENY
    assert r.reason_code is ReasonCode.POLICY_UNAVAILABLE
    control.gateway.chaos_policy_down = False


async def test_chaos_state_down_denies_every_request(ask, control):
    control.gateway.chaos_state_down = True
    r = await ask("svc_agent", "read_profile", resource="cardmember.profile")
    assert r.decision is Decision.DENY
    assert r.reason_code is ReasonCode.STATE_UNAVAILABLE
    control.gateway.chaos_state_down = False


async def test_fail_closed_refuses_redis_fallback(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/fc.db",
        redis_url="redis://127.0.0.1:6399/0",
        fail_closed=True,
        seed_on_start=False,
        reset_on_start=True,
        audit_async=False,
    )
    cp = ControlPlane(settings)
    with pytest.raises(RuntimeError, match="Redis required"):
        await cp.start()
