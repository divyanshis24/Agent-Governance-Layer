"""The in-house rules evaluator — the deny-by-default permission model.

Small on purpose: this is the fallback that guarantees AGL has no hard
dependency on an external policy engine. It answers one question — does this
agent's permission set admit this action, on this resource, over this data? —
and leaves spend, guardrails and human approval to the enforcement stages.
"""

from __future__ import annotations

import re

from ..models import AgentPolicy, AuthorizeRequest, ReasonCode
from .base import PermissionVerdict, PolicyDecisionPoint

#: Actions that read customer data, so the data-scope check applies.
READ_ACTIONS = {"read_field", "read_profile", "fetch_evidence", "list_transactions", "export_records"}

#: Actions that move money, so payment velocity and counterparty rules apply.
PAYMENT_ACTIONS = {
    "issue_refund",
    "transfer_funds",
    "settle_debt",
    "rebook_flight",
    "rebook_hotel",
    "waive_fee",
    "credit_account",
    "disburse_benefit",
}

#: Actions that cannot be undone once executed.
IRREVERSIBLE_ACTIONS = {"transfer_funds", "settle_debt", "close_account", "change_credit_limit"}


#: A data field is a dotted path (`cardmember.ssn`). An object identifier
#: (`case #221`, `acct #55210`) is a *resource*, not a field, and is governed by
#: the action permission rather than the data scope.
_FIELD_PATH = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_*]+)+$")


def looks_like_field_path(value: str) -> bool:
    return bool(_FIELD_PATH.match(value.strip()))


def scope_permits(scopes: list[str], field: str) -> bool:
    """`cardmember.*` covers `cardmember.profile`; `*` covers everything."""
    field = field.strip().lower()
    for scope in scopes:
        s = scope.strip().lower()
        if s in ("*", field):
            return True
        if s.endswith(".*") and field.startswith(s[:-1]):
            return True
        if s.endswith("*") and field.startswith(s[:-1]):
            return True
    return False


class RulesEngine(PolicyDecisionPoint):
    name = "rules"

    async def evaluate(self, request: AuthorizeRequest, policy: AgentPolicy) -> PermissionVerdict:
        action = request.action

        # 1. Deny by default: absent from the permission set means no.
        mode = policy.allowed_actions.get(action)
        if mode is None:
            return PermissionVerdict(
                False,
                ReasonCode.ACTION_NOT_PERMITTED,
                f"'{action}' is not in the permission set",
                engine=self.name,
            )
        if mode == "deny":
            return PermissionVerdict(
                False, ReasonCode.ACTION_NOT_PERMITTED, f"'{action}' is explicitly disabled", engine=self.name
            )

        # 2. Data scope: every field touched must be inside the agent's need-to-know.
        fields = list(request.context.fields)
        if (
            request.resource
            and action in READ_ACTIONS
            and looks_like_field_path(request.resource)
            and request.resource not in fields
        ):
            fields.append(request.resource)
        denied = [f for f in fields if not scope_permits(policy.data_scopes, f)]
        if denied:
            return PermissionVerdict(
                False,
                ReasonCode.DATA_SCOPE_DENIED,
                f"{', '.join(denied)} outside data scope",
                engine=self.name,
            )

        # 3. Conditions carried on the permission itself, e.g. "allow:<=50000".
        if mode.startswith("allow:<="):
            try:
                ceiling = int(mode.split("<=", 1)[1])
            except ValueError:
                ceiling = 0
            if ceiling and request.amount_cents > ceiling:
                return PermissionVerdict(
                    False,
                    ReasonCode.CONDITION_NOT_MET,
                    f"'{action}' limited to ${ceiling / 100:,.2f} by permission condition",
                    engine=self.name,
                )

        obligations: dict = {}
        if policy.guardrails.mask_pan_ssn and fields:
            obligations["mask_fields"] = [f for f in fields if _is_sensitive(f)]

        return PermissionVerdict(
            True,
            ReasonCode.WITHIN_POLICY,
            None,
            requires_approval=(mode == "approval"),
            obligations=obligations or None,
            engine=self.name,
        )


def _is_sensitive(field: str) -> bool:
    f = field.lower()
    return any(token in f for token in ("ssn", "pan", "card_number", "cvv", "dob", "tax_id"))


def is_payment(action: str, amount_cents: int) -> bool:
    return action in PAYMENT_ACTIONS or amount_cents > 0


def is_irreversible(action: str, flagged: bool = False) -> bool:
    return flagged or action in IRREVERSIBLE_ACTIONS
