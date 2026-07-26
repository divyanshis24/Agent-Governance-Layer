"""The control plane: lifecycle, operator actions, and fleet-wide views.

Everything the operator console does — revoke an agent, edit a policy, halt the
fleet, approve an escalation, verify the audit chain — lands here. Operator
actions are themselves recorded, so the log answers "who turned this off?" as
well as "what did the agent do?".
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from .audit import AuditChain
from .config import Settings
from .enforce.gateway import Gateway
from .events import EventBus
from .models import (
    Agent,
    AgentPolicy,
    AgentView,
    Approval,
    AuditEntry,
    ChainVerification,
    Decision,
    FleetState,
    ReasonCode,
)
from .policy import build_pdp
from .store import open_repository, open_state_store

THROTTLE_RATIO = 0.70


def start_of_day() -> float:
    now = datetime.now(tz=timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


class ControlPlane:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bus = EventBus()
        self.repo = None
        self.state = None
        self.pdp = None
        self.chain: AuditChain | None = None
        self.gateway: Gateway | None = None
        self.simulator = None
        self.started_at = time.time()
        self.fleet_cap_cents = settings.fleet_daily_cap_cents

    # --- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        self.repo = await open_repository(self.settings.database_url)
        self.state = await open_state_store(self.settings.redis_url)

        if self.settings.reset_on_start:
            await self.repo.reset()
            await self.state.reset()

        self.fleet_cap_cents = await self.repo.get_kv("fleet_daily_cap_cents", self.settings.fleet_daily_cap_cents)

        self.pdp = build_pdp()
        await self.pdp.start()

        self.chain = AuditChain(self.repo, on_append=self._on_audit, async_writes=self.settings.audit_async)
        await self.chain.start()

        self.gateway = Gateway(
            repo=self.repo,
            state=self.state,
            pdp=self.pdp,
            chain=self.chain,
            bus=self.bus,
            fleet_cap_cents=self.fleet_cap_cents,
        )

        if self.settings.seed_on_start:
            from .seed import seed_fleet

            await seed_fleet(self)

        await self.gateway.refresh()
        # Mirror durable agent status into the hot path.
        for agent in self.gateway.agents.values():
            await self.state.set_agent_status(agent.id, agent.status)

        from .simulator import FleetSimulator

        self.simulator = FleetSimulator(self)

    async def stop(self) -> None:
        if self.simulator:
            await self.simulator.stop()
        if self.chain:
            await self.chain.stop()
        if self.pdp:
            await self.pdp.stop()
        if self.state:
            await self.state.close()
        if self.repo:
            await self.repo.close()

    async def _on_audit(self, entry: AuditEntry) -> None:
        await self.bus.publish("audit", {"entry": entry.model_dump(mode="json")})

    # --- agents ------------------------------------------------------------
    async def register_agent(self, agent: Agent, policy: AgentPolicy) -> tuple[Agent, AgentPolicy]:
        await self.repo.upsert_agent(agent)
        saved = await self.repo.save_policy(policy)
        self.gateway.cache_agent(agent)
        self.gateway.cache_policy(saved)
        await self.state.set_agent_status(agent.id, agent.status)
        await self.bus.publish("agent.registered", {"agent": agent.model_dump()})
        return agent, saved

    async def fleet_view(self) -> list[AgentView]:
        halted = bool(await self.state.get_halt())
        views: list[AgentView] = []
        for agent in self.gateway.agents.values():
            counters = await self.state.counters(agent.id)
            policy = self.gateway.policy(agent.id)
            cap = policy.spend.daily_cap_cents if policy else 0
            status = await self.state.get_agent_status(agent.id) or agent.status

            if halted and status != "revoked":
                effective = "halted"
            elif status == "revoked":
                effective = "revoked"
            elif cap and counters.spend_today_cents >= cap * THROTTLE_RATIO:
                effective = "throttled"
            else:
                effective = status

            views.append(
                AgentView(
                    **agent.model_dump(),
                    spend_today_cents=counters.spend_today_cents,
                    daily_cap_cents=cap,
                    actions_today=counters.actions_today,
                    blocked_today=counters.blocked_today,
                    last_action_at=counters.last_action_at,
                    effective_status=effective,
                )
            )
        return views

    async def revoke_agent(self, agent_id: str, actor: str, reason: str) -> Agent:
        """Real-time revocation: the hot-path flag flips first, so the very next
        action from this agent fails even if the database is slow."""
        agent = self.gateway.agent(agent_id)
        if agent is None:
            raise KeyError(agent_id)
        await self.state.set_agent_status(agent_id, "revoked")
        agent.status = "revoked"
        agent.revoked_at = time.time()
        agent.revoked_by = actor
        agent.revoke_reason = reason
        self.gateway.cache_agent(agent)
        await self.repo.upsert_agent(agent)
        await self._operator_event("agent.revoked", actor, agent_id, reason)
        await self.bus.publish("agent.revoked", {"agent_id": agent_id, "by": actor, "reason": reason})
        return agent

    async def reinstate_agent(self, agent_id: str, actor: str) -> Agent:
        agent = self.gateway.agent(agent_id)
        if agent is None:
            raise KeyError(agent_id)
        await self.state.set_agent_status(agent_id, "active")
        agent.status = "active"
        agent.revoked_at = None
        agent.revoked_by = None
        agent.revoke_reason = None
        self.gateway.cache_agent(agent)
        await self.repo.upsert_agent(agent)
        await self._operator_event("agent.reinstated", actor, agent_id, "reinstated")
        await self.bus.publish("agent.reinstated", {"agent_id": agent_id, "by": actor})
        return agent

    # --- policies ----------------------------------------------------------
    async def update_policy(self, agent_id: str, policy: AgentPolicy, actor: str) -> AgentPolicy:
        """Policy edits take effect on the next decision — no redeploy, no restart."""
        policy.agent_id = agent_id
        policy.updated_by = actor
        saved = await self.repo.save_policy(policy)
        self.gateway.cache_policy(saved)
        await self._operator_event("policy.updated", actor, agent_id, f"version {saved.version}")
        await self.bus.publish("policy.updated", {"agent_id": agent_id, "version": saved.version, "by": actor})
        return saved

    # --- fleet emergency stop ----------------------------------------------
    async def halt_fleet(self, actor: str, reason: str) -> FleetState:
        halt = await self.state.set_halt(actor, reason)
        await self._operator_event("fleet.halted", actor, None, reason)
        await self.bus.publish("fleet.halted", {"by": actor, "reason": reason, "at": halt["at"]})
        return await self.fleet_state()

    async def resume_fleet(self, actor: str) -> FleetState:
        await self.state.clear_halt()
        await self._operator_event("fleet.resumed", actor, None, "controlled resume")
        await self.bus.publish("fleet.resumed", {"by": actor})
        return await self.fleet_state()

    async def fleet_state(self) -> FleetState:
        halt = await self.state.get_halt()
        return FleetState(
            halted=bool(halt),
            halted_at=halt.get("at") if halt else None,
            halted_by=halt.get("by") if halt else None,
            halt_reason=halt.get("reason") if halt else None,
            daily_cap_cents=self.fleet_cap_cents,
            spend_today_cents=await self.state.fleet_spend_today(),
        )

    async def set_fleet_cap(self, cents: int, actor: str) -> FleetState:
        self.fleet_cap_cents = cents
        self.gateway.fleet_cap_cents = cents
        await self.repo.set_kv("fleet_daily_cap_cents", cents)
        await self._operator_event("fleet.cap_updated", actor, None, f"${cents / 100:,.2f}")
        await self.bus.publish("fleet.cap_updated", {"cents": cents, "by": actor})
        return await self.fleet_state()

    # --- approvals ---------------------------------------------------------
    async def decide_approval(self, approval_id: str, approve: bool, actor: str, note: str | None) -> Approval:
        approval = await self.repo.get_approval(approval_id)
        if approval is None:
            raise KeyError(approval_id)
        approval.status = "approved" if approve else "rejected"
        approval.decided_at = time.time()
        approval.decided_by = actor
        approval.note = note
        await self.repo.save_approval(approval)
        await self._operator_event(
            "approval.approved" if approve else "approval.rejected", actor, approval.agent_id, approval.action
        )
        await self.bus.publish("approval.decided", {"approval": approval.model_dump()})
        return approval

    # --- stats -------------------------------------------------------------
    async def stats(self) -> dict:
        views = await self.fleet_view()
        fleet = await self.fleet_state()
        actions = sum(v.actions_today for v in views)
        blocked = sum(v.blocked_today for v in views)
        active = sum(1 for v in views if v.effective_status in ("active", "throttled"))
        pending = await self.repo.list_approvals(status="pending", limit=100)
        by_decision = await self.repo.audit_stats_today(start_of_day())
        return {
            "agents_total": len(views),
            "agents_active": active,
            "actions_today": actions,
            "blocked_today": blocked,
            "spend_today_cents": fleet.spend_today_cents,
            "fleet_cap_cents": self.fleet_cap_cents,
            "pending_approvals": len(pending),
            "fleet_halted": fleet.halted,
            "halted_by": fleet.halted_by,
            "halted_at": fleet.halted_at,
            "halt_reason": fleet.halt_reason,
            "audit_height": self.chain.height,
            "audit_head": self.chain.head_hash,
            "decisions_today": {
                "allow": by_decision.get(Decision.ALLOW.value, 0),
                "deny": by_decision.get(Decision.DENY.value, 0),
                "block": by_decision.get(Decision.BLOCK.value, 0),
                "quarantine": by_decision.get(Decision.QUARANTINE.value, 0),
                "escalate": by_decision.get(Decision.ESCALATE.value, 0),
            },
            "policy_engine": self.pdp.name,
            "state_backend": type(self.state).__name__,
            "db_backend": "postgresql" if self.repo.is_postgres else "sqlite",
            "uptime_s": round(time.time() - self.started_at, 1),
        }

    async def verify_chain(self) -> ChainVerification:
        return await self.chain.verify()

    # --- internal ----------------------------------------------------------
    async def _operator_event(self, kind: str, actor: str, target: str | None, detail: str) -> None:
        await self.repo.add_operator_event(f"evt_{uuid.uuid4().hex[:10]}", kind, actor, target, detail)
        # Operator actions belong in the same tamper-evident record as agent actions.
        await self.chain.append(
            agent_id=target or "_fleet",
            agent_name="OPERATOR",
            action=kind,
            resource=target,
            amount_cents=0,
            decision=Decision.ALLOW,
            reason_code=ReasonCode.WITHIN_POLICY,
            reason=f"{actor}: {detail}",
            request_id=f"op_{uuid.uuid4().hex[:10]}",
            actor=actor,
        )
