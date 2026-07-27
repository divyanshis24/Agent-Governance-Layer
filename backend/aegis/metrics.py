"""In-process metrics for governance latency and decision counts."""

from __future__ import annotations

import threading
from collections import defaultdict


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.decisions: dict[str, int] = defaultdict(int)
        self.latency_ms: list[float] = []
        self._max_samples = 10_000

    def record(self, decision: str, latency_ms: float) -> None:
        with self._lock:
            self.decisions[decision] += 1
            self.latency_ms.append(latency_ms)
            if len(self.latency_ms) > self._max_samples:
                self.latency_ms = self.latency_ms[-self._max_samples :]

    def snapshot(self) -> dict:
        with self._lock:
            samples = sorted(self.latency_ms)
            n = len(samples)
            if n == 0:
                p50 = p99 = 0.0
            else:
                p50 = samples[int(n * 0.50)]
                p99 = samples[min(int(n * 0.99), n - 1)]
            return {
                "decisions": dict(self.decisions),
                "latency_count": n,
                "latency_p50_ms": round(p50, 3),
                "latency_p99_ms": round(p99, 3),
            }

    def render_prometheus(self) -> str:
        snap = self.snapshot()
        lines = [
            "# HELP aegis_decisions_total Governance decisions by outcome",
            "# TYPE aegis_decisions_total counter",
        ]
        for decision, count in snap["decisions"].items():
            lines.append(f'aegis_decisions_total{{decision="{decision}"}} {count}')
        lines.extend(
            [
                "# HELP aegis_decision_latency_ms Governance decision latency",
                "# TYPE aegis_decision_latency_ms summary",
                f"aegis_decision_latency_ms{{quantile=\"0.5\"}} {snap['latency_p50_ms']}",
                f"aegis_decision_latency_ms{{quantile=\"0.99\"}} {snap['latency_p99_ms']}",
                f"aegis_decision_latency_ms_count {snap['latency_count']}",
            ]
        )
        return "\n".join(lines) + "\n"


metrics = Metrics()
