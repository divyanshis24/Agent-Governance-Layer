#!/usr/bin/env python3
"""The five-beat demo, run against a live Aegis control plane.

    python demo/demo_scenario.py            # narrated, paced for a walkthrough
    python demo/demo_scenario.py --fast     # no pauses

Every beat goes through the real gateway over HTTP using the SDK, so what you
see in the terminal is the same decision the console shows and the same entry
the audit chain seals.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sdk" / "python"))

from aegis_sdk import AegisClient  # noqa: E402

BASE = "http://localhost:8000"

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, AMBER, BLUE, GREY = "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[90m"

COLOURS = {"allow": GREEN, "deny": RED, "block": RED, "quarantine": AMBER, "escalate": AMBER}

PAUSE = 1.6


async def beat(n: int, title: str, detail: str) -> None:
    print(f"\n{BOLD}{BLUE}BEAT {n}{RESET}  {BOLD}{title}{RESET}\n{DIM}{detail}{RESET}")
    await asyncio.sleep(PAUSE * 0.4)


def show(decision) -> None:
    colour = COLOURS.get(decision.decision, "")
    print(
        f"  {colour}{BOLD}{decision.decision.upper():<11}{RESET}"
        f"{decision.reason:<66} {GREY}{decision.decision_latency_ms:.2f} ms · "
        f"audit #{decision.audit_seq} · {(decision.audit_hash or '')[:8]}…{RESET}"
    )


async def main(fast: bool) -> int:
    global PAUSE
    if fast:
        PAUSE = 0.05

    async with httpx.AsyncClient(base_url=BASE, timeout=10) as admin:
        try:
            await admin.get("/health")
        except Exception:
            print(f"{RED}Aegis is not running at {BASE}. Start it with ./run.sh{RESET}")
            return 1

        # A clean slate for the fleet controls the demo touches.
        await admin.post("/v1/fleet/resume")
        await admin.post("/v1/agents/onboarding_bot/revoke", json={"reason": "anomalous credit-limit raise pattern"})

        print(f"\n{BOLD}AEGIS — governance layer for financial AI agents{RESET}")
        print(f"{DIM}Five beats. Every one of them a real decision, logged and provable.{RESET}")

        svc = AegisClient(BASE, agent_id="svc_agent")
        benefits = AegisClient(BASE, agent_id="benefits_engine")
        travel = AegisClient(BASE, agent_id="travel_concierge")
        onboarding = AegisClient(BASE, agent_id="onboarding_bot")

        # ---------------------------------------------------------------
        await beat(1, "A $25 fee reversal is allowed and logged",
                   "Inside the servicing agent's permissions and well under its caps.")
        show(await svc.authorize("issue_refund", amount_cents=2_500, resource="txn #8841",
                                 counterparty="cardmember account"))
        await asyncio.sleep(PAUSE)

        # ---------------------------------------------------------------
        await beat(2, "Reading a customer's SSN is denied on data scope",
                   "The benefits agent has claims access. It does not have need-to-know for SSN.")
        show(await benefits.authorize("read_field", resource="cardmember.SSN", fields=["cardmember.SSN"]))
        await asyncio.sleep(PAUSE)

        # ---------------------------------------------------------------
        await beat(3, "A $4,000 rebooking over a $2,500 cap is blocked in real time",
                   "The spend governor stops it before any money moves.")
        show(await travel.authorize("rebook_flight", amount_cents=400_000, resource="$4,000",
                                    counterparty="delta air lines"))
        print(f"  {DIM}…and the same agent's $180 hotel rebooking still goes through:{RESET}")
        show(await travel.authorize("rebook_hotel", amount_cents=18_000, resource="$180", counterparty="marriott"))
        await asyncio.sleep(PAUSE)

        # ---------------------------------------------------------------
        await beat(4, "A misbehaving agent is revoked — its next action fails instantly",
                   "Revocation is a hot-path flag, so containment is immediate.")
        show(await onboarding.authorize("raise_limit", resource="acct #55210"))
        await asyncio.sleep(PAUSE)

        # ---------------------------------------------------------------
        await beat(5, "The operator triggers the fleet-wide emergency stop",
                   "One control. Every agent frozen mid-flight.")
        import time

        t0 = time.perf_counter()
        await admin.post("/v1/fleet/halt", json={"reason": "demo — fleet-wide emergency stop"})
        halt_ms = (time.perf_counter() - t0) * 1000
        print(f"  {RED}{BOLD}FLEET HALTED{RESET} in {halt_ms:.0f} ms — every agent, one flag\n")
        for client, action, kw in (
            (svc, "issue_refund", {"amount_cents": 1_000, "counterparty": "cardmember account"}),
            (travel, "rebook_hotel", {"amount_cents": 12_000, "counterparty": "hilton"}),
            (benefits, "prefill_claim", {"resource": "claim #77"}),
        ):
            show(await client.authorize(action, **kw))
        await asyncio.sleep(PAUSE)

        print(f"\n  {DIM}controlled resume…{RESET}")
        await admin.post("/v1/fleet/resume")
        show(await svc.authorize("issue_refund", amount_cents=1_000, counterparty="cardmember account"))

        # ---------------------------------------------------------------
        print(f"\n{BOLD}{BLUE}CLOSING{RESET}  {BOLD}The audit log{RESET}")
        verification = (await admin.get("/v1/audit/verify")).json()
        head = (await admin.get("/v1/audit/head")).json()
        mark = f"{GREEN}✓ CHAIN VERIFIED{RESET}" if verification["ok"] else f"{RED}✗ CHAIN BROKEN{RESET}"
        print(f"  {mark}  {verification['detail']}  {GREY}({verification['duration_ms']:.1f} ms){RESET}")
        print(f"  {GREY}head {head['head_hash'][:24]}… at height {head['height']}{RESET}")

        print(f"\n{DIM}  Now prove it is tamper-evident — edit a past entry directly in the database:{RESET}")
        tampered = (await admin.post("/v1/audit/_tamper", json={})).json()
        v = tampered["verification"]
        print(f"  {RED}✗ CHAIN BROKEN{RESET}  {v['detail']}")
        await admin.post("/v1/audit/_restore")
        restored = (await admin.get("/v1/audit/verify")).json()
        print(f"  {DIM}(chain rebuilt for the next run — {restored['detail']}){RESET}\n")

        for client in (svc, benefits, travel, onboarding):
            await client.aclose()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true", help="skip the pacing pauses")
    raise SystemExit(asyncio.run(main(parser.parse_args().fast)))
