"""Prometheus-compatible metrics endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from ..metrics import metrics

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics() -> str:
    return metrics.render_prometheus()


@router.get("/v1/metrics/summary")
async def metrics_summary() -> dict:
    return metrics.snapshot()
