"""Proxy mode: Aegis makes the downstream call, so nothing bypasses the checkpoint."""

from __future__ import annotations

from aegis.api.authorize import _apply_masking
from aegis.bank import CUSTOMERS, CoreBanking
from aegis.models import ActionContext, AuthorizeRequest


async def test_denied_action_never_reaches_the_bank(control):
    """The gateway decides before CoreBanking is ever called."""
    request = AuthorizeRequest(
        agent_id="benefits_engine",
        action="read_field",
        resource="cardmember.SSN",
        context=ActionContext(fields=["cardmember.SSN"]),
    )
    decision = await control.gateway.authorize(request)
    assert decision.decision.value == "deny"


async def test_downstream_pii_is_masked_on_the_way_out():
    """A record can carry PAN/SSN the agent never asked for — mask the response."""
    request = AuthorizeRequest(
        agent_id="svc_agent",
        action="read_profile",
        resource="cardmember.profile",
        context=ActionContext(customer_id="cm_0041"),
    )
    raw = await CoreBanking.execute(request)
    assert CUSTOMERS["cm_0041"]["ssn"] in str(raw)  # the bank returns it in the clear

    masked = _apply_masking(raw)
    text = str(masked)
    assert CUSTOMERS["cm_0041"]["ssn"] not in text
    assert CUSTOMERS["cm_0041"]["pan"] not in text
    assert "***-**-7734" in text  # last four preserved for servicing
    assert "0005" in text  # PAN last four preserved


async def test_masking_recurses_through_nested_payloads():
    payload = {
        "record": {"ssn": "412-88-7734", "notes": ["card 378282246310005", "no PII here"]},
        "count": 2,
    }
    masked = _apply_masking(payload)
    assert masked["record"]["ssn"] == "***-**-7734"
    assert "378282246310005" not in str(masked["record"]["notes"])
    assert masked["record"]["notes"][1] == "no PII here"
    assert masked["count"] == 2


async def test_allowed_action_executes_and_settles(control):
    request = AuthorizeRequest(
        agent_id="svc_agent",
        action="issue_refund",
        amount_cents=2_500,
        counterparty="cardmember account",
    )
    decision = await control.gateway.authorize(request)
    assert decision.decision.value == "allow"

    result = await CoreBanking.execute(request)
    assert result["system"] == "money movement"
    assert result["amount_cents"] == 2_500
    assert result["status"] == "settled"


async def test_spend_is_recorded_in_the_durable_ledger(control):
    await control.gateway.authorize(
        AuthorizeRequest(agent_id="svc_agent", action="issue_refund", amount_cents=4_200,
                         counterparty="cardmember account")
    )
    ledger = await control.repo.spend_by_agent(0)
    assert ledger["svc_agent"] == 4_200
