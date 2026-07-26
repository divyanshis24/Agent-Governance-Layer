"""The audit chain: completeness, integrity, and tamper evidence."""

from __future__ import annotations

from aegis.audit import GENESIS_HASH
from aegis.models import Decision


async def test_exactly_one_entry_per_action(control, ask):
    start = control.chain.height
    for _ in range(10):
        await ask("svc_agent", "issue_refund", amount_cents=1_000, counterparty="cardmember account")
    assert control.chain.height == start + 10


async def test_chain_links_every_entry(control, ask):
    for i in range(6):
        await ask("svc_agent", "issue_refund", amount_cents=1_000 + i, counterparty="cardmember account")
    entries = await control.repo.audit_all_ascending()
    prev = GENESIS_HASH
    for entry in entries:
        assert entry.prev_hash == prev
        prev = entry.hash


async def test_verification_passes_on_an_untouched_log(control, ask):
    for _ in range(20):
        await ask("svc_agent", "issue_refund", amount_cents=1_000, counterparty="cardmember account")
    result = await control.verify_chain()
    assert result.ok
    assert result.entries_checked == control.chain.height


async def test_editing_a_past_entry_is_detected(control, ask):
    for _ in range(10):
        await ask("travel_concierge", "rebook_flight", amount_cents=400_000, counterparty="delta air lines")
    assert (await control.verify_chain()).ok

    # The adversary reaches past the append-only API and rewrites history.
    await control.repo.execute(
        "UPDATE audit SET decision = ?, reason = ? WHERE seq = ?", (Decision.ALLOW.value, "within policy", 4)
    )

    result = await control.verify_chain()
    assert not result.ok
    assert result.broken_at == 4
    assert "modified" in result.detail


async def test_deleting_an_entry_is_detected(control, ask):
    for _ in range(8):
        await ask("svc_agent", "issue_refund", amount_cents=1_000, counterparty="cardmember account")
    await control.repo.execute("DELETE FROM audit WHERE seq = ?", (5,))

    result = await control.verify_chain()
    assert not result.ok
    assert result.broken_at == 6
    assert "deleted" in result.detail


async def test_changing_an_amount_is_detected(control, ask):
    for _ in range(5):
        await ask("svc_agent", "issue_refund", amount_cents=1_000, counterparty="cardmember account")
    await control.repo.execute("UPDATE audit SET amount_cents = ? WHERE seq = ?", (999_999, 2))
    result = await control.verify_chain()
    assert not result.ok
    assert result.broken_at == 2


async def test_operator_actions_are_in_the_same_chain(control):
    await control.halt_fleet("Risk Operator", "test")
    await control.resume_fleet("Risk Operator")
    entries = await control.repo.audit_all_ascending()
    kinds = [e.action for e in entries]
    assert "fleet.halted" in kinds
    assert "fleet.resumed" in kinds
    assert (await control.verify_chain()).ok


async def test_proof_of_inclusion(control, ask):
    for _ in range(5):
        await ask("svc_agent", "issue_refund", amount_cents=1_000, counterparty="cardmember account")
    proof = await control.chain.proof(3)
    assert proof["found"]
    assert proof["entry"]["seq"] == 3
    assert len(proof["links"]) == 3  # the entry plus both neighbours


async def test_reason_survives_a_restart(control, ask):
    """The chain head is recovered from disk, so restarting does not break it."""
    await ask("svc_agent", "issue_refund", amount_cents=1_000, counterparty="cardmember account")
    head_before, height_before = control.chain.head_hash, control.chain.height

    await control.chain.stop()
    await control.chain.start()

    assert control.chain.head_hash == head_before
    assert control.chain.height == height_before
    await ask("svc_agent", "issue_refund", amount_cents=1_000, counterparty="cardmember account")
    assert (await control.verify_chain()).ok
