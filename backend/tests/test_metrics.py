"""Prometheus-style metrics exported from the decision path."""

from __future__ import annotations

from agl.metrics import metrics
from agl.models import Decision


async def test_metrics_record_allow_and_block(ask):
    before = metrics.snapshot()["decisions"]
    allow_before = before.get("allow", 0)
    block_before = before.get("block", 0)

    r1 = await ask("svc_agent", "issue_refund", amount_cents=2_500, counterparty="cardmember account")
    r2 = await ask("travel_concierge", "rebook_flight", amount_cents=400_000, counterparty="delta air lines")

    assert r1.decision is Decision.ALLOW
    assert r2.decision is Decision.BLOCK

    after = metrics.snapshot()["decisions"]
    assert after.get("allow", 0) >= allow_before + 1
    assert after.get("block", 0) >= block_before + 1


def test_prometheus_render_includes_counters():
    text = metrics.render_prometheus()
    assert "agl_decisions_total" in text
    assert "agl_decision_latency_ms" in text
