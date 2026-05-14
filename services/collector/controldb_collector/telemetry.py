"""Lightweight OpenTelemetry-style metrics counter.

We avoid a hard OTel dependency in the MVP. The exporter shape mirrors the
OTel SDK so adding ``opentelemetry-api`` later is a drop-in replacement.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Dict


class Metrics:
    def __init__(self) -> None:
        self._counters: Dict[str, float] = defaultdict(float)
        self._histograms: Dict[str, list] = defaultdict(list)
        self._lock = threading.Lock()

    def inc(self, name: str, by: float = 1.0) -> None:
        with self._lock:
            self._counters[name] += by

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            self._histograms[name].append(value)

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "histograms": {k: list(v) for k, v in self._histograms.items()},
            }


METRICS = Metrics()

# Pre-seed all documented metric names so snapshot() always returns them.
_METRIC_NAMES = [
    "controldb.events.ingested",
    "controldb.events.failed",
    "controldb.runs.started",
    "controldb.runs.committed",
    "controldb.runs.failed",
    "controldb.policy.allowed",
    "controldb.policy.blocked",
    "controldb.approvals.requested",
    "controldb.exports.completed",
]
for _name in _METRIC_NAMES:
    METRICS._counters[_name]  # defaultdict touch — no lock needed at import time

_HISTOGRAM_NAMES = [
    "controldb.ingestion.latency_ms",
    "controldb.replay.latency_ms",
]
for _name in _HISTOGRAM_NAMES:
    METRICS._histograms[_name]
