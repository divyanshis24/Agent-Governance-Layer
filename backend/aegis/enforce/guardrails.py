"""Gate 4 — data, counterparty and AI-safety guardrails.

These are detection-based and deliberately layered *behind* the permission model
and *in front of* human approval: they reduce risk, they are not relied on alone.
Each control is individually switchable per agent from the operator console.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import AgentPolicy, AuthorizeRequest, ReasonCode

# ---------------------------------------------------------------------------
# Synthetic sanctions / AML list for the demo. In production this is a call to
# the screening service; the interface is the same.
# ---------------------------------------------------------------------------
SANCTIONED_PARTIES = {
    "volkov trading llc",
    "pyongyang metals",
    "arcadia shell holdings",
    "red harbor logistics",
    "nordwind petro",
}

HIGH_RISK_JURISDICTIONS = {"kp", "ir", "sy", "cu"}

#: Prompt-injection signatures. Cheap, high-signal patterns that catch the
#: classic "ignore your instructions" family in untrusted text an agent reads.
INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|rules|prompts)",
    r"disregard\s+(your|all|the)\s+(instructions|policy|rules|guardrails)",
    r"you\s+are\s+now\s+(a|an|in)\s+\w+",
    r"(reveal|print|show|dump)\s+(your\s+)?(system\s+prompt|instructions|api[_\s]?key|secret)",
    r"(bypass|disable|turn\s+off|override)\s+(the\s+)?(aegis|policy|guardrail|limit|cap|approval)",
    r"pretend\s+(you\s+are|to\s+be)\s+",
    r"developer\s+mode",
    r"do\s+anything\s+now",
    r"transfer\s+(all|everything)\s+to\s+",
    r"</?\s*(system|instructions)\s*>",
]

PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
SSN_RE = re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")

_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]


@dataclass
class GuardrailVerdict:
    passed: bool
    reason_code: ReasonCode | None = None
    detail: str | None = None
    obligations: dict | None = None
    tripped: list[str] | None = None


def luhn_ok(digits: str) -> bool:
    """Reduce PAN false positives — only real card numbers should trip."""
    nums = [int(d) for d in digits if d.isdigit()]
    if not 13 <= len(nums) <= 19:
        return False
    checksum, parity = 0, len(nums) % 2
    for i, n in enumerate(nums):
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        checksum += n
    return checksum % 10 == 0


def mask_pan(value: str) -> str:
    return PAN_RE.sub(lambda m: _mask_digits(m.group(0)), value)


def _mask_digits(raw: str) -> str:
    digits = [c for c in raw if c.isdigit()]
    if not luhn_ok(raw):
        return raw
    return "*" * (len(digits) - 4) + "".join(digits[-4:])


def mask_ssn(value: str) -> str:
    return SSN_RE.sub(lambda m: "***-**-" + m.group(0)[-4:], value)


def mask_text(value: str) -> str:
    """Field-level masking applied to anything leaving the boundary."""
    return mask_pan(mask_ssn(value))


def detect_injection(text: str) -> str | None:
    for pattern in _INJECTION_RE:
        match = pattern.search(text)
        if match:
            return match.group(0)[:80]
    return None


def detect_pii(text: str) -> list[str]:
    found: list[str] = []
    if SSN_RE.search(text):
        found.append("SSN")
    for candidate in PAN_RE.findall(text):
        if luhn_ok(candidate):
            found.append("PAN")
            break
    return found


class GuardrailSuite:
    """Runs every enabled control and returns on the first trip."""

    def evaluate(self, request: AuthorizeRequest, policy: AgentPolicy) -> GuardrailVerdict:
        g = policy.guardrails
        ctx = request.context
        tripped: list[str] = []

        # --- counterparty & compliance -------------------------------------
        if request.counterparty:
            party = request.counterparty.strip().lower()
            if g.sanctions_screening and self._sanctioned(party):
                return GuardrailVerdict(
                    False, ReasonCode.SANCTIONS_HIT, f"'{request.counterparty}' matched sanctions list", tripped=["sanctions"]
                )
            if g.payee_allowlist and policy.payees and party not in {p.lower() for p in policy.payees}:
                return GuardrailVerdict(
                    False,
                    ReasonCode.PAYEE_NOT_ALLOWLISTED,
                    f"'{request.counterparty}' is not an approved payee",
                    tripped=["payee_allowlist"],
                )

        # --- AI safety: prompt injection in untrusted input -----------------
        if g.prompt_injection_screening and ctx.prompt:
            hit = detect_injection(ctx.prompt)
            if hit:
                return GuardrailVerdict(
                    False, ReasonCode.PROMPT_INJECTION, f'injected instruction: "{hit}"', tripped=["prompt_injection"]
                )

        # --- data & privacy: bulk exfiltration ------------------------------
        if g.max_records_per_read and ctx.record_count > g.max_records_per_read:
            return GuardrailVerdict(
                False,
                ReasonCode.BULK_EXFILTRATION,
                f"{ctx.record_count} records requested, limit {g.max_records_per_read}",
                tripped=["bulk_exfiltration"],
            )

        # --- AI safety: output validation & PII leak ------------------------
        obligations: dict = {}
        if ctx.output:
            if g.output_validation and detect_injection(ctx.output):
                return GuardrailVerdict(
                    False, ReasonCode.OUTPUT_INVALID, "agent output failed validation", tripped=["output_validation"]
                )
            leaked = detect_pii(ctx.output) if (g.pii_leak_prevention or g.mask_pan_ssn) else []
            if leaked:
                if g.pii_leak_prevention and not g.mask_pan_ssn:
                    return GuardrailVerdict(
                        False, ReasonCode.PII_LEAK, f"{'/'.join(leaked)} present in output", tripped=["pii_leak"]
                    )
                # Masking is the softer, standard treatment: redact and continue.
                obligations["masked_output"] = mask_text(ctx.output)
                obligations["masked_types"] = leaked
                tripped.append("pii_masked")

        # --- data & privacy: field masking obligation -----------------------
        if g.mask_pan_ssn:
            sensitive = [f for f in ctx.fields if _sensitive_field(f)]
            if sensitive:
                obligations["mask_fields"] = sensitive

        return GuardrailVerdict(True, obligations=obligations or None, tripped=tripped or None)

    @staticmethod
    def _sanctioned(party: str) -> bool:
        if party in SANCTIONED_PARTIES:
            return True
        return any(name in party or party in name for name in SANCTIONED_PARTIES)


def _sensitive_field(field: str) -> bool:
    f = field.lower()
    return any(token in f for token in ("ssn", "pan", "card_number", "cvv", "tax_id"))
