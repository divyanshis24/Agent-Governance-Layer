"""Simulated core banking and external tools.

Stands in for money movement, card systems, customer data and partner APIs.
Nothing here is real, and that is the point: Aegis makes *real* decisions over
synthetic systems, so the governance layer is what is being demonstrated.

In production these are the systems that sit behind the gateway; in proxy mode
Aegis is the only caller, which is what makes the checkpoint unbypassable.
"""

from __future__ import annotations

import random
import uuid

from .models import AuthorizeRequest

#: Synthetic customer records. The SSN/PAN here are fake but shaped like the
#: real thing, so masking and PII detection are exercised honestly.
CUSTOMERS: dict[str, dict] = {
    "cm_0041": {
        "customer_id": "cm_0041",
        "name": "J. Whitfield",
        "ssn": "412-88-7734",
        "pan": "378282246310005",
        "segment": "Platinum",
        "email": "j.whitfield@example.com",
        "balance_cents": 412_355,
    },
    "cm_0198": {
        "customer_id": "cm_0198",
        "name": "R. Okafor",
        "ssn": "223-51-9087",
        "pan": "371449635398431",
        "segment": "Gold",
        "email": "r.okafor@example.com",
        "balance_cents": 88_200,
    },
}


class CoreBanking:
    """The downstream effect of an authorized action."""

    @staticmethod
    async def execute(request: AuthorizeRequest) -> dict:
        action, amount = request.action, request.amount_cents
        ref = f"{action[:3]}_{uuid.uuid4().hex[:10]}"

        if action in {"read_profile", "read_field", "fetch_evidence"}:
            customer = CUSTOMERS.get(request.context.customer_id or "cm_0041", CUSTOMERS["cm_0041"])
            record = dict(customer)
            if request.resource and "." in request.resource:
                field = request.resource.split(".")[-1].lower()
                if field in record:
                    return {"system": "customer data", "field": request.resource, "value": str(record[field]), "ref": ref}
            return {"system": "customer data", "record": {k: str(v) for k, v in record.items()}, "ref": ref}

        if amount > 0:
            return {
                "system": "money movement",
                "ref": ref,
                "status": "settled",
                "amount_cents": amount,
                "counterparty": request.counterparty,
                "settlement_id": f"stl_{uuid.uuid4().hex[:12]}",
                "network_latency_ms": round(random.uniform(40, 120), 1),
            }

        return {"system": "card systems", "ref": ref, "status": "applied", "action": action}
