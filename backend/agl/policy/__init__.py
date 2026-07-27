from ..config import settings
from .base import PermissionVerdict, PolicyDecisionPoint
from .opa import OpaEngine
from .rules import (
    IRREVERSIBLE_ACTIONS,
    PAYMENT_ACTIONS,
    READ_ACTIONS,
    RulesEngine,
    is_irreversible,
    is_payment,
    scope_permits,
)


def build_pdp() -> PolicyDecisionPoint:
    """Select the policy engine named by POLICY_ENGINE."""
    if settings.policy_engine.lower() == "opa":
        return OpaEngine(
            settings.opa_url,
            settings.opa_decision_path,
            fallback=None if settings.fail_closed else RulesEngine(),
            fail_closed=settings.fail_closed,
        )
    return RulesEngine()


__all__ = [
    "PolicyDecisionPoint",
    "PermissionVerdict",
    "RulesEngine",
    "OpaEngine",
    "build_pdp",
    "is_payment",
    "is_irreversible",
    "scope_permits",
    "PAYMENT_ACTIONS",
    "READ_ACTIONS",
    "IRREVERSIBLE_ACTIONS",
]
