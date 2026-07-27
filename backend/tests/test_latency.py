"""Latency and throughput targets: < 10 ms at the decision point (p99)."""

from __future__ import annotations

import asyncio
import statistics
import time

from agl.models import Decision


async def test_decision_latency_p99_under_10ms(ask):
    # Warm the caches the same way steady-state traffic would.
    for _ in range(20):
        await ask("svc_agent", "read_profile", resource="cardmember.profile")

    samples = []
    for _ in range(500):
        started = time.perf_counter()
        await ask("svc_agent", "read_profile", resource="cardmember.profile")
        samples.append((time.perf_counter() - started) * 1000)

    samples.sort()
    p50 = statistics.median(samples)
    p99 = samples[int(len(samples) * 0.99)]
    print(f"\n  decision latency p50={p50:.3f}ms p99={p99:.3f}ms max={samples[-1]:.3f}ms")
    assert p99 < 10.0


async def test_reported_latency_matches_the_target(ask):
    latencies = []
    for _ in range(200):
        r = await ask("dispute_resolver", "fetch_evidence", resource="case #1")
        latencies.append(r.decision_latency_ms)
    latencies.sort()
    assert latencies[int(len(latencies) * 0.99)] < 10.0


async def test_blocked_paths_are_fast_too(ask):
    """A denial must not cost more than an allow — the cheap gates come first."""
    samples = []
    for _ in range(200):
        started = time.perf_counter()
        r = await ask("travel_concierge", "rebook_flight", amount_cents=400_000, counterparty="delta air lines")
        samples.append((time.perf_counter() - started) * 1000)
        assert r.decision is Decision.BLOCK
    samples.sort()
    assert samples[int(len(samples) * 0.99)] < 10.0


async def test_throughput_under_concurrency(ask):
    started = time.perf_counter()
    results = await asyncio.gather(
        *[ask("dispute_resolver", "fetch_evidence", resource="case #9") for _ in range(300)]
    )
    elapsed = time.perf_counter() - started
    rps = len(results) / elapsed
    print(f"\n  {len(results)} concurrent decisions in {elapsed * 1000:.0f}ms ({rps:,.0f}/s)")
    assert len(results) == 300
    assert rps > 500
