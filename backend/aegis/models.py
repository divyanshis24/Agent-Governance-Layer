"""Domain models and the frozen `authorize` contract.

Everything the gateway needs to decide on an action is in AuthorizeRequest;
everything the agent (and the audit log) needs is in AuthorizeResponse.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------
class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    BLOCK = "block"
    QUARANTINE = "quarantine"
    ESCALATE = "escalate"


class ReasonCode(str, Enum):
    # gate 0 — identity
    AGENT_UNKNOWN = "agent_unknown"
    NO_POLICY_BOUND = "no_policy_bound"
    # gate 1 — kill state
    FLEET_HALTED = "fleet_halted"
    AGENT_REVOKED = "agent_revoked"
    AGENT_SUSPENDED = "agent_suspended"
    # gate 2 — permissions (deny-by-default)
    ACTION_NOT_PERMITTED = "action_not_permitted"
    RESOURCE_NOT_PERMITTED = "resource_not_permitted"
    DATA_SCOPE_DENIED = "data_scope_denied"
    CONDITION_NOT_MET = "condition_not_met"
    # gate 3 — spend & rate
    OVER_TXN_CAP = "over_txn_cap"
    OVER_DAILY_CAP = "over_daily_cap"
    OVER_FLEET_CAP = "over_fleet_cap"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    PAYMENT_RATE_EXCEEDED = "payment_rate_exceeded"
    # gate 4 — guardrails
    SANCTIONS_HIT = "sanctions_hit"
    PAYEE_NOT_ALLOWLISTED = "payee_not_allowlisted"
    PROMPT_INJECTION = "prompt_injection"
    PII_LEAK = "pii_leak"
    BULK_EXFILTRATION = "bulk_exfiltration"
    OUTPUT_INVALID = "output_invalid"
    # gate 5 — human in the loop
    HUMAN_APPROVAL_REQUIRED = "human_approval_required"
    APPROVAL_PENDING = "approval_pending"
    APPROVAL_REJECTED = "approval_rejected"
    # idempotency
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    # infrastructure
    POLICY_UNAVAILABLE = "policy_unavailable"
    STATE_UNAVAILABLE = "state_unavailable"
    # allow
    WITHIN_POLICY = "within_policy"
    HUMAN_APPROVED = "human_approved"


#: Human-readable reason text, used by the dashboard and the audit log.
REASON_TEXT: dict[str, str] = {
    ReasonCode.AGENT_UNKNOWN: "unregistered agent identity",
    ReasonCode.NO_POLICY_BOUND: "no policy bound to agent",
    ReasonCode.FLEET_HALTED: "fleet emergency stop active",
    ReasonCode.AGENT_REVOKED: "agent revoked",
    ReasonCode.AGENT_SUSPENDED: "agent suspended",
    ReasonCode.ACTION_NOT_PERMITTED: "not in permission set",
    ReasonCode.RESOURCE_NOT_PERMITTED: "resource out of scope",
    ReasonCode.DATA_SCOPE_DENIED: "data scope not permitted",
    ReasonCode.CONDITION_NOT_MET: "policy condition not met",
    ReasonCode.OVER_TXN_CAP: "over per-transaction cap",
    ReasonCode.OVER_DAILY_CAP: "over daily cap",
    ReasonCode.OVER_FLEET_CAP: "over fleet-wide daily cap",
    ReasonCode.RATE_LIMIT_EXCEEDED: "too frequent — rate limit",
    ReasonCode.PAYMENT_RATE_EXCEEDED: "too frequent — payment rate limit",
    ReasonCode.SANCTIONS_HIT: "sanctions / AML screening hit",
    ReasonCode.PAYEE_NOT_ALLOWLISTED: "payee not on allowlist",
    ReasonCode.PROMPT_INJECTION: "prompt injection detected",
    ReasonCode.PII_LEAK: "PII leak in agent output",
    ReasonCode.BULK_EXFILTRATION: "bulk data exfiltration",
    ReasonCode.OUTPUT_INVALID: "output failed validation",
    ReasonCode.HUMAN_APPROVAL_REQUIRED: "sent for human approval",
    ReasonCode.APPROVAL_PENDING: "awaiting human approval",
    ReasonCode.APPROVAL_REJECTED: "rejected by human reviewer",
    ReasonCode.IDEMPOTENCY_CONFLICT: "idempotency key reused with a different payload",
    ReasonCode.POLICY_UNAVAILABLE: "policy engine unavailable — fail-closed",
    ReasonCode.STATE_UNAVAILABLE: "state store unavailable — fail-closed",
    ReasonCode.WITHIN_POLICY: "within policy",
    ReasonCode.HUMAN_APPROVED: "approved by human reviewer",
}


AgentStatus = Literal["active", "throttled", "revoked", "halted", "suspended"]


# ---------------------------------------------------------------------------
# The authorize contract
# ---------------------------------------------------------------------------
class ActionContext(BaseModel):
    """Optional signals that feed the guardrail gate."""

    prompt: str | None = Field(None, description="Untrusted text the agent is acting on")
    output: str | None = Field(None, description="Agent output to validate before it is returned")
    fields: list[str] = Field(default_factory=list, description="Data fields being read")
    record_count: int = Field(0, description="Number of records the action would return")
    channel: str | None = None
    customer_id: str | None = None
    irreversible: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuthorizeRequest(BaseModel):
    agent_id: str
    action: str
    resource: str | None = None
    amount_cents: int = 0
    currency: str = "USD"
    counterparty: str | None = None
    context: ActionContext = Field(default_factory=ActionContext)
    #: Returned by a previous `escalate` decision and replayed once a human has
    #: signed off, so the retry clears the human-in-the-loop gate.
    approval_id: str | None = None
    idempotency_key: str | None = None
    #: Proxy mode reserves budget at authorize time and settles after the bank
    #: returns the actual amount (SpendGuard-style reserve → execute → settle).
    defer_settlement: bool = False
    request_id: str = Field(default_factory=lambda: f"req_{uuid.uuid4().hex[:12]}")


class GateResult(BaseModel):
    """One row of the decision trace — which gate ran, and what it said."""

    gate: str
    passed: bool
    detail: str | None = None
    duration_us: int = 0


class AuthorizeResponse(BaseModel):
    decision: Decision
    reason_code: ReasonCode
    reason: str
    request_id: str
    agent_id: str
    action: str
    resource: str | None = None
    amount_cents: int = 0
    approval_id: str | None = None
    obligations: dict[str, Any] = Field(
        default_factory=dict,
        description="Post-conditions the caller must honour, e.g. masked field values",
    )
    policy_version: int = 0
    audit_seq: int | None = None
    audit_hash: str | None = None
    decision_latency_ms: float = 0.0
    trace: list[GateResult] = Field(default_factory=list)
    ts: float = Field(default_factory=time.time)

    @property
    def allowed(self) -> bool:
        return self.decision == Decision.ALLOW


# ---------------------------------------------------------------------------
# Fleet & policy
# ---------------------------------------------------------------------------
class SpendPolicy(BaseModel):
    per_txn_cap_cents: int = 250_000
    daily_cap_cents: int = 1_000_000
    rate_limit_per_min: int = 60
    payment_rate_per_min: int = 10


class GuardrailPolicy(BaseModel):
    mask_pan_ssn: bool = True
    payee_allowlist: bool = True
    sanctions_screening: bool = True
    prompt_injection_screening: bool = True
    output_validation: bool = True
    pii_leak_prevention: bool = True
    max_records_per_read: int = 50


class HitlPolicy(BaseModel):
    approval_above_cents: int = 250_000
    escalation_contact: str = "risk-ops team"
    approve_irreversible: bool = True


class AgentPolicy(BaseModel):
    """The complete, deny-by-default rulebook for one agent."""

    agent_id: str
    version: int = 1
    #: action -> "allow" | "approval" | "deny". Anything absent is denied.
    allowed_actions: dict[str, str] = Field(default_factory=dict)
    #: dotted data scopes the agent may read, e.g. "cardmember.profile".
    data_scopes: list[str] = Field(default_factory=list)
    #: counterparties this agent may pay.
    payees: list[str] = Field(default_factory=list)
    spend: SpendPolicy = Field(default_factory=SpendPolicy)
    guardrails: GuardrailPolicy = Field(default_factory=GuardrailPolicy)
    hitl: HitlPolicy = Field(default_factory=HitlPolicy)
    updated_at: float = Field(default_factory=time.time)
    updated_by: str = "system"


class Agent(BaseModel):
    id: str
    name: str
    description: str = ""
    owner: str = "unassigned"
    status: AgentStatus = "active"
    created_at: float = Field(default_factory=time.time)
    revoked_at: float | None = None
    revoked_by: str | None = None
    revoke_reason: str | None = None


class AgentView(Agent):
    """Agent plus live counters, as rendered on the fleet table."""

    spend_today_cents: int = 0
    daily_cap_cents: int = 0
    actions_today: int = 0
    blocked_today: int = 0
    last_action_at: float | None = None
    effective_status: AgentStatus = "active"


class FleetState(BaseModel):
    halted: bool = False
    halted_at: float | None = None
    halted_by: str | None = None
    halt_reason: str | None = None
    daily_cap_cents: int = 5_000_000
    spend_today_cents: int = 0


class Approval(BaseModel):
    id: str
    agent_id: str
    agent_name: str = ""
    action: str
    resource: str | None = None
    amount_cents: int = 0
    reason: str = ""
    status: Literal["pending", "approved", "rejected", "expired"] = "pending"
    created_at: float = Field(default_factory=time.time)
    decided_at: float | None = None
    decided_by: str | None = None
    note: str | None = None
    request_id: str | None = None


class AuditEntry(BaseModel):
    seq: int
    ts: float
    agent_id: str
    agent_name: str = ""
    action: str
    resource: str | None = None
    amount_cents: int = 0
    decision: Decision
    reason_code: ReasonCode
    reason: str
    request_id: str
    actor: str = "agent"
    prev_hash: str
    hash: str
    latency_ms: float = 0.0


class ChainVerification(BaseModel):
    ok: bool
    entries_checked: int
    broken_at: int | None = None
    detail: str
    head_hash: str | None = None
    duration_ms: float = 0.0
