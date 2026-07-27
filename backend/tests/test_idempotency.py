"""Idempotency keys: safe retries without double spend or conflicting replays."""

from __future__ import annotations

import asyncio

from aegis.models import Decision, ReasonCode


async def test_idempotent_retry_returns_cached_decision(ask):
    key = "retry-001"
    r1 = await ask(
        "svc_agent",
        "issue_refund",
        amount_cents=2_500,
        counterparty="cardmember account",
        idempotency_key=key,
    )
    r2 = await ask(
        "svc_agent",
        "issue_refund",
        amount_cents=2_500,
        counterparty="cardmember account",
        idempotency_key=key,
    )
    assert r1.decision is Decision.ALLOW
    assert r2.decision is Decision.ALLOW
    assert r2.obligations.get("idempotent_replay") is True
    assert r2.audit_seq == r1.audit_seq


async def test_idempotent_retry_does_not_double_spend(control, ask):
    key = "retry-002"
    before = (await control.state.counters("travel_concierge")).spend_today_cents

    for _ in range(3):
        r = await ask(
            "travel_concierge",
            "rebook_hotel",
            amount_cents=50_000,
            counterparty="marriott",
            idempotency_key=key,
        )
        assert r.decision is Decision.ALLOW

    after = (await control.state.counters("travel_concierge")).spend_today_cents
    assert after - before == 50_000


async def test_idempotency_conflict_on_different_payload(ask):
    key = "conflict-001"
    await ask(
        "svc_agent",
        "issue_refund",
        amount_cents=1_000,
        counterparty="cardmember account",
        idempotency_key=key,
    )
    conflict = await ask(
        "svc_agent",
        "issue_refund",
        amount_cents=2_000,
        counterparty="cardmember account",
        idempotency_key=key,
    )
    assert conflict.decision is Decision.DENY
    assert conflict.reason_code is ReasonCode.IDEMPOTENCY_CONFLICT


async def test_concurrent_idempotent_requests_commit_once(control, ask):
    key = "concurrent-001"
    before = (await control.state.counters("travel_concierge")).spend_today_cents
    results = await asyncio.gather(
        *[
            ask(
                "travel_concierge",
                "rebook_hotel",
                amount_cents=30_000,
                counterparty="hilton",
                idempotency_key=key,
            )
            for _ in range(10)
        ]
    )
    after = (await control.state.counters("travel_concierge")).spend_today_cents

    assert all(r.decision is Decision.ALLOW for r in results)
    assert sum(1 for r in results if r.obligations.get("idempotent_replay")) == 9
    assert after - before == 30_000
