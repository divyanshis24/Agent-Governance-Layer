"""Runtime configuration.

Aegis runs in two modes with the same code path:

  * zero-infra (default) — in-process state store + SQLite. One command, no daemons.
  * production          — set REDIS_URL and/or DATABASE_URL and the same interfaces
                          are served by Redis (atomic counters, kill flags) and
                          PostgreSQL (policies, ledger, audit chain).

The policy engine is likewise swappable: POLICY_ENGINE=rules uses the in-house
evaluator, POLICY_ENGINE=opa talks to an Open Policy Agent sidecar.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # --- storage -----------------------------------------------------------
    redis_url: str | None = field(default_factory=lambda: os.getenv("REDIS_URL") or None)
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL")
        or f"sqlite:///{BASE_DIR / 'data' / 'aegis.db'}"
    )

    # --- policy engine -----------------------------------------------------
    policy_engine: str = field(default_factory=lambda: os.getenv("POLICY_ENGINE", "rules"))
    opa_url: str = field(default_factory=lambda: os.getenv("OPA_URL", "http://localhost:8181"))
    opa_decision_path: str = field(
        default_factory=lambda: os.getenv("OPA_DECISION_PATH", "aegis/authz/decision")
    )

    # --- fleet defaults ----------------------------------------------------
    fleet_daily_cap_cents: int = field(
        default_factory=lambda: int(os.getenv("FLEET_DAILY_CAP_CENTS", 5_000_000))  # $50,000
    )

    # --- behaviour ---------------------------------------------------------
    demo_mode: bool = field(default_factory=lambda: _env_bool("DEMO_MODE", True))
    seed_on_start: bool = field(default_factory=lambda: _env_bool("SEED_ON_START", True))
    reset_on_start: bool = field(default_factory=lambda: _env_bool("RESET_ON_START", False))
    audit_async: bool = field(default_factory=lambda: _env_bool("AUDIT_ASYNC", True))
    #: When true, configured Redis/PostgreSQL must be reachable — no silent fallback.
    fail_closed: bool = field(default_factory=lambda: _env_bool("FAIL_CLOSED", False))

    # --- server ------------------------------------------------------------
    host: str = field(default_factory=lambda: os.getenv("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", 8000)))
    cors_origins: list[str] = field(
        default_factory=lambda: os.getenv(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",")
    )

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith(("postgres://", "postgresql://"))

    @property
    def sqlite_path(self) -> Path:
        return Path(self.database_url.replace("sqlite:///", ""))


settings = Settings()
