"""Reserve → execute → settle lifecycle (SpendGuard-style)."""

from __future__ import annotations

from aegis.models import ActionContext, AuthorizeRequest, Decision


async def test_authorize_settles_immediately(control, ask):
    before = (await control.state.counters("svc_agent")).spend_today_cents
    r = await ask("svc_agent", "issue_refund", amount_cents=2_500, counterparty="cardmember account")
    after = (await control.state.counters("svc_agent")).spend_today_cents

    assert r.decision is Decision.ALLOW
    assert r.obligations.get("settled_cents") == 2_500
    assert after - before == 2_500


async def test_proxy_settles_actual_amount_below_reserve(control):
    request = AuthorizeRequest(
        agent_id="svc_agent",
        action="issue_refund",
        amount_cents=5_000,
        counterparty="cardmember account",
        defer_settlement=True,
        context=ActionContext(metadata={"settle_actual_cents": 4_500}),
    )
    before = (await control.state.counters("svc_agent")).spend_today_cents
    decision = await control.gateway.authorize(request)
    assert decision.decision is Decision.ALLOW
    assert decision.obligations.get("reserved_cents") == 5_000
    assert "settled_cents" not in decision.obligations

    settlement = await control.gateway.complete_settlement(request, 4_500)
    after = (await control.state.counters("svc_agent")).spend_today_cents

    assert settlement["settled_cents"] == 4_500
    assert settlement["released_cents"] == 500
    assert after - before == 4_500


async def test_release_restores_full_reservation(control):
    request = AuthorizeRequest(
        agent_id="svc_agent",
        action="issue_refund",
        amount_cents=3_000,
        counterparty="cardmember account",
        defer_settlement=True,
    )
    before = (await control.state.counters("svc_agent")).spend_today_cents
    await control.gateway.authorize(request)
    mid = (await control.state.counters("svc_agent")).spend_today_cents
    released = await control.state.release(request.agent_id, request.request_id)
    after = (await control.state.counters("svc_agent")).spend_today_cents

    assert mid - before == 3_000
    assert released == 3_000
    assert after == before
