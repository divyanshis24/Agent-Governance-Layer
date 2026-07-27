"""AGL control plane — application entrypoint.

    uvicorn agl.main:app --reload

Serves the gateway, the control-plane APIs, the live stream, and (when the
console has been built) the operator dashboard itself.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import ROUTERS
from .config import BASE_DIR, settings
from .control import ControlPlane

CONSOLE_DIR = BASE_DIR.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    control = ControlPlane(settings)
    await control.start()
    app.state.control = control
    stats = await control.stats()
    print(
        f"[agl] control plane up · policy engine={stats['policy_engine']} "
        f"state={stats['state_backend']} db={stats['db_backend']} "
        f"agents={stats['agents_total']} audit_height={stats['audit_height']}"
    )
    try:
        yield
    finally:
        await control.stop()


app = FastAPI(
    title="Agent Governance Layer",
    description="The mandatory checkpoint between every agent and the money, data, or tools it wants to touch.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in ROUTERS:
    app.include_router(router)


@app.get("/health", tags=["meta"])
async def health() -> JSONResponse:
    control = app.state.control
    return JSONResponse(
        {
            "status": "ok",
            "policy_engine": await control.pdp.health(),
            "state_backend": type(control.state).__name__,
            "fail_closed": control.settings.fail_closed,
            "audit_height": control.chain.height,
            "fleet_halted": bool(await control.state.get_halt()),
        }
    )


# --- operator console ------------------------------------------------------
if CONSOLE_DIR.exists():
    app.mount("/assets", StaticFiles(directory=CONSOLE_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def console(full_path: str):
        """Serve the single-page console for any non-API path."""
        candidate = CONSOLE_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(CONSOLE_DIR / "index.html")


def main() -> None:  # pragma: no cover - entrypoint
    import uvicorn

    uvicorn.run("agl.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":  # pragma: no cover
    main()
