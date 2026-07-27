"""The Policy Decision Point interface.

The gateway never talks to a policy engine directly — it talks to this. That is
what makes the engine swappable: the in-house `RulesEngine` ships as the default
and the zero-dependency fallback, and `OpaEngine` speaks to an Open Policy Agent
sidecar (or AWS Cedar, via the same shape) without the enforcement path changing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..models import AgentPolicy, AuthorizeRequest, ReasonCode


@dataclass
class PermissionVerdict:
    permitted: bool
    reason_code: ReasonCode = ReasonCode.WITHIN_POLICY
    detail: str | None = None
    #: The policy marks this action as requiring a human signature.
    requires_approval: bool = False
    #: Post-conditions the caller must apply, e.g. {"mask_fields": [...]}.
    obligations: dict | None = None
    engine: str = "rules"


class PolicyDecisionPoint(ABC):
    name: str = "pdp"

    async def start(self) -> None:  # pragma: no cover - trivial
        return None

    async def stop(self) -> None:  # pragma: no cover - trivial
        return None

    @abstractmethod
    async def evaluate(self, request: AuthorizeRequest, policy: AgentPolicy) -> PermissionVerdict:
        """Decide whether the permission model admits this action at all."""

    async def health(self) -> dict:
        return {"engine": self.name, "healthy": True}
