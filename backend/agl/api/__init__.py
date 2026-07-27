from . import approvals, audit_api, authorize, fleet, metrics_api, policies, sim, stream

ROUTERS = [
    authorize.router,
    fleet.router,
    policies.router,
    audit_api.router,
    approvals.router,
    stream.router,
    sim.router,
    metrics_api.router,
]

__all__ = ["ROUTERS"]
