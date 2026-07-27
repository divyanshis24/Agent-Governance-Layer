"""Open Policy Agent adapter.

Set POLICY_ENGINE=opa (and run OPA as a sidecar with backend/policies/agl.rego
loaded) to move permission decisions into Rego without touching the enforcement
path. Because OPA runs as a sidecar, evaluation is a loopback call, not a
network hop. If the sidecar is unreachable the engine fails closed for the
permission gate and reports unhealthy, so the fallback engine can be selected.
"""

from __future__ import annotations

import httpx

from ..models import AgentPolicy, AuthorizeRequest, ReasonCode
from .base import PermissionVerdict, PolicyDecisionPoint
from .rules import RulesEngine


class OpaEngine(PolicyDecisionPoint):
    name = "opa"

    def __init__(
        self,
        url: str,
        decision_path: str,
        fallback: PolicyDecisionPoint | None = None,
        *,
        fail_closed: bool = False,
    ) -> None:
        self.url = url.rstrip("/")
        self.decision_path = decision_path.strip("/")
        self.fallback = fallback
        self.fail_closed = fail_closed
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(base_url=self.url, timeout=2.0)

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    async def evaluate(self, request: AuthorizeRequest, policy: AgentPolicy) -> PermissionVerdict:
        if self._client is None:
            await self.start()
        payload = {
            "input": {
                "agent_id": request.agent_id,
                "action": request.action,
                "resource": request.resource,
                "amount_cents": request.amount_cents,
                "fields": request.context.fields,
                "policy": {
                    "allowed_actions": policy.allowed_actions,
                    "data_scopes": policy.data_scopes,
                },
            }
        }
        try:
            resp = await self._client.post(f"/v1/data/{self.decision_path}", json=payload)
            resp.raise_for_status()
            result = resp.json().get("result") or {}
        except Exception as exc:
            if self.fail_closed or self.fallback is None:
                return PermissionVerdict(
                    False,
                    ReasonCode.POLICY_UNAVAILABLE,
                    f"policy engine unavailable: {exc}",
                    engine=self.name,
                )
            return await self.fallback.evaluate(request, policy)

        if result.get("allow"):
            return PermissionVerdict(
                True,
                ReasonCode.WITHIN_POLICY,
                requires_approval=bool(result.get("requires_approval")),
                obligations=result.get("obligations") or None,
                engine=self.name,
            )
        code = result.get("reason_code", ReasonCode.ACTION_NOT_PERMITTED.value)
        try:
            reason_code = ReasonCode(code)
        except ValueError:
            reason_code = ReasonCode.ACTION_NOT_PERMITTED
        return PermissionVerdict(False, reason_code, result.get("detail"), engine=self.name)

    async def health(self) -> dict:
        try:
            if self._client is None:
                await self.start()
            resp = await self._client.get("/health")
            return {"engine": self.name, "healthy": resp.status_code == 200, "url": self.url}
        except Exception as exc:
            return {"engine": self.name, "healthy": False, "url": self.url, "error": str(exc)}
