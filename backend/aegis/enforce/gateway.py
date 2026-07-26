"""The Policy Enforcement Point — the single checkpoint every action passes.

Gate order matches the authorization decision flow exactly:

    0. identity        unknown agent / no policy bound   -> DENY
    1. kill state      fleet halted or agent revoked     -> BLOCK
    2. permissions     deny-by-default via the PDP       -> DENY
    3. spend & rate    caps and velocity                 -> BLOCK
    4. guardrails      sanctions, data, AI safety        -> QUARANTINE
    5. human oversight high-value / irreversible         -> ESCALATE
    6. commit          atomic reserve, then              -> ALLOW

Two properties are worth calling out:

  * Gate 3 *checks* the limits so the deny reason is precise; the counters are
    only moved at gate 6, in one atomic operation, immediately before ALLOW.
    Nothing is consumed by an action that was later denied, and two concurrent
    requests cannot both spend the same remaining budget.
  * Every outcome — including the ones that never reach the money — is written
    to the hash chain before the response is returned.
"""

from __future__ import annotations

import time
import uuid

from ..audit import AuditChain
from ..models import (
    REASON_TEXT,
    Agent,
    AgentPolicy,
    Approval,
    AuthorizeRequest,
    AuthorizeResponse,
    Decision,
    GateResult,
    ReasonCode,
)
from ..policy import PolicyDecisionPoint, is_irreversible, is_payment
from ..store.state import Limits, StateStore
from .guardrails import GuardrailSuite


class _Timer:
    """Per-gate stopwatch that builds the decision trace."""

    def __init__(self) -> None:
        self.start = time.perf_counter()
        self.mark = self.start
        self.trace: list[GateResult] = []

    def gate(self, name: str, passed: bool, detail: str | None = None) -> None:
        now = time.perf_counter()
        self.trace.append(
            GateResult(gate=name, passed=passed, detail=detail, duration_us=int((now - self.mark) * 1_000_000))
        )
        self.mark = now

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.start) * 1000


class Gateway:
    """Stateless in the horizontal-scaling sense: all shared state is in the stores."""

    def __init__(
        self,
        *,
        repo,
        state: StateStore,
        pdp: PolicyDecisionPoint,
        chain: AuditChain,
        bus,
        fleet_cap_cents: int,
    ) -> None:
        self.repo = repo
        self.state = state
        self.pdp = pdp
        self.chain = chain
        self.bus = bus
        self.fleet_cap_cents = fleet_cap_cents
        #: Hot caches — the decision path must not hit the database.
        self._agents: dict[str, Agent] = {}
        self._policies: dict[str, AgentPolicy] = {}
        self.guardrails = GuardrailSuite()

    # --- cache management --------------------------------------------------
    async def refresh(self) -> None:
        self._agents = {a.id: a for a in await self.repo.list_agents()}
        self._policies = await self.repo.all_policies()

    def cache_agent(self, agent: Agent) -> None:
        self._agents[agent.id] = agent

    def cache_policy(self, policy: AgentPolicy) -> None:
        self._policies[policy.agent_id] = policy

    def forget_agent(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)
        self._policies.pop(agent_id, None)

    def agent(self, agent_id: str) -> Agent | None:
        return self._agents.get(agent_id)

    def policy(self, agent_id: str) -> AgentPolicy | None:
        return self._policies.get(agent_id)

    @property
    def agents(self) -> dict[str, Agent]:
        return self._agents

    @property
    def policies(self) -> dict[str, AgentPolicy]:
        return self._policies

    # --- the decision path -------------------------------------------------
    async def authorize(self, request: AuthorizeRequest) -> AuthorizeResponse:
        t = _Timer()
        agent = self._agents.get(request.agent_id)
        policy = self._policies.get(request.agent_id)

        # -- gate 0: identity ------------------------------------------------
        if agent is None:
            t.gate("identity", False, "unknown agent")
            return await self._finalize(request, agent, Decision.DENY, ReasonCode.AGENT_UNKNOWN, t,
                                        detail=f"'{request.agent_id}' is not a registered agent")
        if policy is None:
            t.gate("identity", False, "no policy bound")
            return await self._finalize(request, agent, Decision.DENY, ReasonCode.NO_POLICY_BOUND, t)
        t.gate("identity", True)

        # -- gate 1: kill state ----------------------------------------------
        halt = await self.state.get_halt()
        if halt:
            t.gate("kill_state", False, "fleet halted")
            return await self._finalize(request, agent, Decision.BLOCK, ReasonCode.FLEET_HALTED, t,
                                        detail=f"halted by {halt.get('by')}")
        status = await self.state.get_agent_status(request.agent_id) or agent.status
        if status == "revoked":
            t.gate("kill_state", False, "agent revoked")
            return await self._finalize(request, agent, Decision.BLOCK, ReasonCode.AGENT_REVOKED, t,
                                        detail=agent.revoke_reason)
        if status == "suspended":
            t.gate("kill_state", False, "agent suspended")
            return await self._finalize(request, agent, Decision.BLOCK, ReasonCode.AGENT_SUSPENDED, t)
        t.gate("kill_state", True)

        # -- gate 2: permissions (deny by default) ---------------------------
        verdict = await self.pdp.evaluate(request, policy)
        if not verdict.permitted:
            t.gate("permissions", False, verdict.detail)
            return await self._finalize(request, agent, Decision.DENY, verdict.reason_code, t, detail=verdict.detail)
        t.gate("permissions", True)

        payment = is_payment(request.action, request.amount_cents)
        limits = Limits(
            per_txn_cap_cents=policy.spend.per_txn_cap_cents,
            daily_cap_cents=policy.spend.daily_cap_cents,
            fleet_cap_cents=self.fleet_cap_cents,
            rate_limit_per_min=policy.spend.rate_limit_per_min,
            payment_rate_per_min=policy.spend.payment_rate_per_min,
        )

        # -- gate 3: spend & rate limits (check only) ------------------------
        peek = await self.state.peek(request.agent_id, request.amount_cents, payment, limits)
        if not peek.ok:
            t.gate("spend_rate", False, peek.detail)
            return await self._finalize(request, agent, Decision.BLOCK, peek.reason_code, t, detail=peek.detail)
        t.gate("spend_rate", True)

        # -- gate 4: guardrails ----------------------------------------------
        guard = self.guardrails.evaluate(request, policy)
        if not guard.passed:
            t.gate("guardrails", False, guard.detail)
            return await self._finalize(request, agent, Decision.QUARANTINE, guard.reason_code, t, detail=guard.detail)
        t.gate("guardrails", True)
        obligations = dict(guard.obligations or {})
        if verdict.obligations:
            obligations.setdefault("mask_fields", verdict.obligations.get("mask_fields", []))

        # -- gate 5: human in the loop ---------------------------------------
        needs_human = (
            verdict.requires_approval
            or (policy.hitl.approval_above_cents and request.amount_cents >= policy.hitl.approval_above_cents)
            or (policy.hitl.approve_irreversible and is_irreversible(request.action, request.context.irreversible))
        )
        approved_by: str | None = None
        if needs_human:
            signed_off, approval_err, approver = await self._check_approval(request)
            if signed_off:
                approved_by = approver
                t.gate("human_approval", True, f"approved by {approver}")
            else:
                approval = await self._raise_approval(request, agent, policy, reason=approval_err)
                t.gate("human_approval", False, "sent for human approval")
                if approval_err == "rejected":
                    return await self._finalize(request, agent, Decision.DENY, ReasonCode.APPROVAL_REJECTED, t,
                                                approval_id=request.approval_id)
                return await self._finalize(request, agent, Decision.ESCALATE, ReasonCode.HUMAN_APPROVAL_REQUIRED, t,
                                            approval_id=approval.id,
                                            detail=f"{policy.hitl.escalation_contact} must sign off")
        else:
            t.gate("human_approval", True, "not required")

        # -- gate 6: atomic commit, then allow -------------------------------
        commit = await self.state.try_commit(request.agent_id, request.amount_cents, payment, limits)
        if not commit.ok:
            # Lost a race against a concurrent request for the same budget.
            t.gate("commit", False, commit.detail)
            return await self._finalize(request, agent, Decision.BLOCK, commit.reason_code, t, detail=commit.detail)
        t.gate("commit", True)

        if request.amount_cents > 0:
            await self.repo.add_ledger(
                f"led_{uuid.uuid4().hex[:12]}",
                request.agent_id,
                request.action,
                request.amount_cents,
                request.counterparty,
                request.request_id,
            )

        reason_code = ReasonCode.HUMAN_APPROVED if approved_by else ReasonCode.WITHIN_POLICY
        return await self._finalize(request, agent, Decision.ALLOW, reason_code, t,
                                    obligations=obligations, policy_version=policy.version)

    # --- human approval ----------------------------------------------------
    async def _check_approval(self, request: AuthorizeRequest) -> tuple[bool, str | None, str | None]:
        """Validate a replayed approval token against this exact action."""
        if not request.approval_id:
            return False, None, None
        approval = await self.repo.get_approval(request.approval_id)
        if approval is None:
            return False, None, None
        if approval.status == "rejected":
            return False, "rejected", approval.decided_by
        if approval.status != "approved":
            return False, "pending", None
        matches = (
            approval.agent_id == request.agent_id
            and approval.action == request.action
            and approval.amount_cents == request.amount_cents
        )
        if not matches:
            # An approval is for one specific action, not a standing licence.
            return False, None, None
        return True, None, approval.decided_by or "operator"

    async def _raise_approval(self, request: AuthorizeRequest, agent: Agent, policy: AgentPolicy, reason: str | None) -> Approval:
        if request.approval_id:
            existing = await self.repo.get_approval(request.approval_id)
            # Only reuse a request that is still pending on *this* exact action;
            # anything else needs its own trip through the approval queue.
            if (
                existing is not None
                and existing.status == "pending"
                and existing.agent_id == request.agent_id
                and existing.action == request.action
                and existing.amount_cents == request.amount_cents
            ):
                return existing
        approval = Approval(
            id=f"apr_{uuid.uuid4().hex[:10]}",
            agent_id=request.agent_id,
            agent_name=agent.name,
            action=request.action,
            resource=request.resource,
            amount_cents=request.amount_cents,
            reason=(
                f"${request.amount_cents / 100:,.2f} exceeds the ${policy.hitl.approval_above_cents / 100:,.2f} "
                f"approval threshold"
                if request.amount_cents >= (policy.hitl.approval_above_cents or 0) and request.amount_cents
                else f"'{request.action}' requires a second pair of eyes"
            ),
            request_id=request.request_id,
        )
        await self.repo.save_approval(approval)
        await self.bus.publish("approval.created", {"approval": approval.model_dump()})
        return approval

    # --- shared exit path --------------------------------------------------
    async def _finalize(
        self,
        request: AuthorizeRequest,
        agent: Agent | None,
        decision: Decision,
        reason_code: ReasonCode,
        t: _Timer,
        *,
        detail: str | None = None,
        approval_id: str | None = None,
        obligations: dict | None = None,
        policy_version: int = 0,
    ) -> AuthorizeResponse:
        """Every path out of `authorize` runs through here — so nothing escapes the log."""
        reason = REASON_TEXT.get(reason_code, reason_code.value)
        if detail:
            reason = f"{reason} — {detail}"
        latency = t.elapsed_ms
        agent_name = agent.name if agent else request.agent_id

        entry = await self.chain.append(
            agent_id=request.agent_id,
            agent_name=agent_name,
            action=request.action,
            resource=request.resource,
            amount_cents=request.amount_cents,
            decision=decision,
            reason_code=reason_code,
            reason=reason,
            request_id=request.request_id,
            latency_ms=latency,
        )
        await self.state.record_outcome(request.agent_id, decision == Decision.ALLOW)

        response = AuthorizeResponse(
            decision=decision,
            reason_code=reason_code,
            reason=reason,
            request_id=request.request_id,
            agent_id=request.agent_id,
            action=request.action,
            resource=request.resource,
            amount_cents=request.amount_cents,
            approval_id=approval_id,
            obligations=obligations or {},
            policy_version=policy_version,
            audit_seq=entry.seq,
            audit_hash=entry.hash,
            decision_latency_ms=round(latency, 3),
            trace=t.trace,
        )
        await self.bus.publish(
            "decision",
            {
                "seq": entry.seq,
                "agent_id": request.agent_id,
                "agent_name": agent_name,
                "action": request.action,
                "resource": request.resource,
                "amount_cents": request.amount_cents,
                "decision": decision.value,
                "reason": reason,
                "reason_code": reason_code.value,
                "hash": entry.hash,
                "latency_ms": round(latency, 3),
                "request_id": request.request_id,
            },
        )
        return response
