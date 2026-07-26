"""Aegis SDK — the one-import way to put an agent behind the checkpoint.

SDK mode (ask, then act)::

    aegis = AegisClient("http://localhost:8000", agent_id="travel_concierge")

    decision = await aegis.authorize("rebook_flight", amount_cents=180_00,
                                     counterparty="delta air lines")
    if decision.allowed:
        book_the_flight()

Proxy mode (Aegis makes the call itself, so the agent never touches the money)::

    result = await aegis.execute("issue_refund", amount_cents=25_00,
                                 counterparty="cardmember account")

Decorator form, for wrapping an existing tool::

    @aegis.governed("issue_refund")
    async def issue_refund(amount_cents: int, **kw): ...
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx


class AegisError(RuntimeError):
    """The control plane could not be reached."""


class AegisDenied(RuntimeError):
    """The action was not authorized. Carries the full decision for logging."""

    def __init__(self, decision: "Decision") -> None:
        super().__init__(f"{decision.decision}: {decision.reason}")
        self.decision = decision


@dataclass
class Decision:
    decision: str
    reason: str
    reason_code: str = ""
    request_id: str = ""
    approval_id: str | None = None
    obligations: dict = field(default_factory=dict)
    audit_seq: int | None = None
    audit_hash: str | None = None
    decision_latency_ms: float = 0.0
    raw: dict = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    @property
    def escalated(self) -> bool:
        return self.decision == "escalate"

    @classmethod
    def from_payload(cls, payload: dict) -> "Decision":
        return cls(
            decision=payload.get("decision", "deny"),
            reason=payload.get("reason", ""),
            reason_code=payload.get("reason_code", ""),
            request_id=payload.get("request_id", ""),
            approval_id=payload.get("approval_id"),
            obligations=payload.get("obligations") or {},
            audit_seq=payload.get("audit_seq"),
            audit_hash=payload.get("audit_hash"),
            decision_latency_ms=payload.get("decision_latency_ms", 0.0),
            raw=payload,
        )


class AegisClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        *,
        agent_id: str,
        timeout: float = 5.0,
        fail_closed: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.timeout = timeout
        #: If the control plane is unreachable, refuse the action rather than
        #: proceeding ungoverned. This is the safe default and should stay on.
        self.fail_closed = fail_closed
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AegisClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    def _payload(self, action: str, **kw: Any) -> dict:
        context = {
            "prompt": kw.pop("prompt", None),
            "output": kw.pop("output", None),
            "fields": kw.pop("fields", []),
            "record_count": kw.pop("record_count", 0),
            "customer_id": kw.pop("customer_id", None),
            "irreversible": kw.pop("irreversible", False),
            "metadata": kw.pop("metadata", {}),
        }
        return {
            "agent_id": self.agent_id,
            "action": action,
            "resource": kw.pop("resource", None),
            "amount_cents": kw.pop("amount_cents", 0),
            "counterparty": kw.pop("counterparty", None),
            "approval_id": kw.pop("approval_id", None),
            "idempotency_key": kw.pop("idempotency_key", None),
            "context": context,
        }

    async def authorize(self, action: str, **kw: Any) -> Decision:
        """Ask the control plane whether this action may proceed."""
        try:
            resp = await self._client.post("/v1/authorize", json=self._payload(action, **kw))
            resp.raise_for_status()
        except Exception as exc:
            if self.fail_closed:
                return Decision(decision="deny", reason=f"control plane unreachable: {exc}",
                                reason_code="control_plane_unavailable")
            raise AegisError(str(exc)) from exc
        return Decision.from_payload(resp.json())

    async def require(self, action: str, **kw: Any) -> Decision:
        """Like `authorize`, but raises `AegisDenied` on anything but allow."""
        decision = await self.authorize(action, **kw)
        if not decision.allowed:
            raise AegisDenied(decision)
        return decision

    async def execute(self, action: str, **kw: Any) -> dict:
        """Proxy mode: authorize and, if allowed, let Aegis make the downstream call."""
        try:
            resp = await self._client.post("/v1/proxy", json=self._payload(action, **kw))
            resp.raise_for_status()
        except Exception as exc:
            if self.fail_closed:
                return {"executed": False, "authorization": {"decision": "deny", "reason": str(exc)}, "result": None}
            raise AegisError(str(exc)) from exc
        return resp.json()

    async def approval_status(self, approval_id: str) -> dict:
        resp = await self._client.get(f"/v1/approvals/{approval_id}")
        resp.raise_for_status()
        return resp.json()

    def governed(self, action: str, amount_arg: str = "amount_cents") -> Callable:
        """Decorator that puts an existing tool behind the checkpoint."""

        def decorator(fn: Callable) -> Callable:
            @functools.wraps(fn)
            async def wrapper(*args: Any, **kwargs: Any):
                decision = await self.authorize(
                    action,
                    amount_cents=kwargs.get(amount_arg, 0),
                    resource=kwargs.get("resource"),
                    counterparty=kwargs.get("counterparty"),
                )
                if not decision.allowed:
                    raise AegisDenied(decision)
                return await fn(*args, **kwargs)

            return wrapper

        return decorator
