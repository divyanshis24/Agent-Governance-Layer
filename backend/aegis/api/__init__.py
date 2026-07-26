from . import approvals, audit_api, authorize, fleet, policies, sim, stream

ROUTERS = [
    authorize.router,
    fleet.router,
    policies.router,
    audit_api.router,
    approvals.router,
    stream.router,
    sim.router,
]

__all__ = ["ROUTERS"]
