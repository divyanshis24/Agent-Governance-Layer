from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aegis.config import Settings  # noqa: E402
from aegis.control import ControlPlane  # noqa: E402
from aegis.models import ActionContext, AuthorizeRequest  # noqa: E402


@pytest.fixture
async def control(tmp_path):
    """A fully wired control plane on a throwaway database."""
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/aegis-test.db",
        redis_url=None,
        seed_on_start=True,
        reset_on_start=True,
        audit_async=False,
        demo_mode=True,
    )
    cp = ControlPlane(settings)
    await cp.start()
    try:
        yield cp
    finally:
        await cp.stop()


@pytest.fixture
def ask(control):
    """Shorthand for pushing one action through the gateway."""

    async def _ask(agent_id: str, action: str, **kw):
        context = ActionContext(
            fields=kw.pop("fields", []),
            prompt=kw.pop("prompt", None),
            output=kw.pop("output", None),
            record_count=kw.pop("record_count", 0),
            irreversible=kw.pop("irreversible", False),
        )
        return await control.gateway.authorize(
            AuthorizeRequest(agent_id=agent_id, action=action, context=context, **kw)
        )

    return _ask
