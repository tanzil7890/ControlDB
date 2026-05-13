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
