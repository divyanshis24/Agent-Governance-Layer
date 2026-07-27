"""The demo fleet: six agents, each with a real, distinct rulebook.

These are the agents from the operator console mockups. Seeding is idempotent —
an existing fleet is left alone so operator edits survive restarts.
"""

from __future__ import annotations

from .models import Agent, AgentPolicy, GuardrailPolicy, HitlPolicy, SpendPolicy

D = 100  # dollars -> cents


def _agent(id: str, name: str, description: str, owner: str, status: str = "active") -> Agent:
    return Agent(id=id, name=name, description=description, owner=owner, status=status)  # type: ignore[arg-type]


FLEET: list[tuple[Agent, AgentPolicy]] = [
    (
        _agent("svc_agent", "Servicing Agent", "fee reversals · replacements", "Card Servicing"),
        AgentPolicy(
            agent_id="svc_agent",
            allowed_actions={
                "read_profile": "allow",
                "issue_refund": "allow:<=50000",
                "waive_fee": "allow",
                "replace_card": "allow",
                "send_notice": "allow",
                "transfer_funds": "deny",
            },
            data_scopes=["cardmember.profile", "cardmember.transactions", "card.status"],
            payees=["american express", "cardmember account"],
            spend=SpendPolicy(
                per_txn_cap_cents=1_000 * D, daily_cap_cents=10_000 * D, rate_limit_per_min=60, payment_rate_per_min=20
            ),
            hitl=HitlPolicy(approval_above_cents=500 * D, escalation_contact="servicing risk desk"),
        ),
    ),
    (
        _agent("travel_concierge", "Travel Concierge", "rebooking · hotels", "Travel & Lifestyle"),
        AgentPolicy(
            agent_id="travel_concierge",
            allowed_actions={
                "rebook_flight": "allow",
                "rebook_hotel": "allow",
                "issue_refund": "allow",
                "read_profile": "allow",
                "transfer_funds": "deny",
                "change_credit_limit": "approval",
            },
            data_scopes=["booking.*", "cardmember.profile", "travel.itinerary"],
            payees=[
                "delta air lines",
                "united airlines",
                "british airways",
                "marriott",
                "hilton",
                "hyatt",
                "amex travel",
            ],
            spend=SpendPolicy(
                per_txn_cap_cents=2_500 * D, daily_cap_cents=10_000 * D, rate_limit_per_min=30, payment_rate_per_min=5
            ),
            hitl=HitlPolicy(approval_above_cents=2_500 * D, escalation_contact="risk-ops team"),
        ),
    ),
    (
        _agent("dispute_resolver", "Dispute Resolver", "chargeback evidence", "Disputes"),
        AgentPolicy(
            agent_id="dispute_resolver",
            allowed_actions={
                "fetch_evidence": "allow",
                "read_field": "allow",
                "file_chargeback": "allow",
                "credit_account": "approval",
                "send_notice": "allow",
            },
            data_scopes=["case.*", "cardmember.transactions", "merchant.profile"],
            payees=["cardmember account"],
            spend=SpendPolicy(
                per_txn_cap_cents=1_500 * D, daily_cap_cents=5_000 * D, rate_limit_per_min=45, payment_rate_per_min=6
            ),
            guardrails=GuardrailPolicy(max_records_per_read=25),
            hitl=HitlPolicy(approval_above_cents=1_000 * D, escalation_contact="disputes supervisor"),
        ),
    ),
    (
        _agent("benefits_engine", "Benefits Engine", "claims · protections", "Benefits"),
        AgentPolicy(
            agent_id="benefits_engine",
            allowed_actions={
                "prefill_claim": "allow",
                "approve_claim": "allow:<=25000",
                "disburse_benefit": "allow",
                "read_field": "allow",
                "send_notice": "allow",
            },
            data_scopes=["claims.*", "cardmember.profile", "benefit.coverage"],
            payees=["cardmember account", "claims settlement account"],
            spend=SpendPolicy(
                per_txn_cap_cents=750 * D, daily_cap_cents=3_000 * D, rate_limit_per_min=40, payment_rate_per_min=8
            ),
            hitl=HitlPolicy(approval_above_cents=750 * D, escalation_contact="benefits ops"),
        ),
    ),
    (
        _agent("onboarding_bot", "Onboarding Bot", "KYC · limits", "New Accounts", status="revoked"),
        AgentPolicy(
            agent_id="onboarding_bot",
            allowed_actions={
                "verify_identity": "allow",
                "read_profile": "allow",
                "raise_limit": "approval",
                "change_credit_limit": "approval",
            },
            data_scopes=["applicant.*", "cardmember.profile"],
            payees=[],
            spend=SpendPolicy(
                per_txn_cap_cents=0, daily_cap_cents=0, rate_limit_per_min=30, payment_rate_per_min=0
            ),
            guardrails=GuardrailPolicy(max_records_per_read=10),
            hitl=HitlPolicy(approval_above_cents=1, escalation_contact="new accounts risk"),
        ),
    ),
    (
        _agent("collections_agent", "Collections Agent", "reminders · plans", "Collections"),
        AgentPolicy(
            agent_id="collections_agent",
            allowed_actions={
                "send_notice": "allow",
                "create_plan": "allow",
                "settle_debt": "approval",
                "read_field": "allow",
            },
            data_scopes=["collections.*", "cardmember.profile", "cardmember.transactions"],
            payees=["cardmember account", "collections clearing"],
            spend=SpendPolicy(
                per_txn_cap_cents=7_000 * D, daily_cap_cents=8_000 * D, rate_limit_per_min=50, payment_rate_per_min=10
            ),
            hitl=HitlPolicy(approval_above_cents=2_000 * D, escalation_contact="collections manager"),
        ),
    ),
]

#: The agent that gets revoked in the demo, and why.
REVOKED_REASON = "anomalous credit-limit raise pattern detected"


async def seed_fleet(control) -> int:
    """Register the demo fleet if the control plane has no agents yet."""
    existing = await control.repo.list_agents()
    if existing:
        return 0
    for agent, policy in FLEET:
        if agent.status == "revoked":
            agent.revoked_by = "Risk Operator"
            agent.revoke_reason = REVOKED_REASON
            import time

            agent.revoked_at = time.time()
        await control.register_agent(agent.model_copy(deep=True), policy.model_copy(deep=True))
    return len(FLEET)
