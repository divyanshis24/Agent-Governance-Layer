from __future__ import annotations

from fastapi import Header, Request

from ..control import ControlPlane


def get_control(request: Request) -> ControlPlane:
    return request.app.state.control


def operator(x_operator: str | None = Header(default=None, alias="X-Operator")) -> str:
    """Who is pulling the lever.

    A demo stand-in for authenticated operator identity (SSO/mTLS in
    production). It is recorded on every operator action, so the audit log
    always names a person.
    """
    return x_operator or "Risk Operator"
