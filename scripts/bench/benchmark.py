"""
ControlDB ingestion benchmark.

Measures p50/p95/p99 latency for:
  - Single event append
  - Batch event append (10 events)
  - Run start + commit round-trip

Usage:
    # Start collector first:
    #   CONTROLDB_API_KEY=bench-key uvicorn controldb_collector:app --port 8080
    python scripts/bench/benchmark.py [--url http://localhost:8080] [--key bench-key] [--n 500]
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path
from typing import Callable, List

import httpx

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "sdk" / "python"))


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _start_run(client: httpx.Client, api_key: str, agent_id: str = "bench-agent") -> str:
    r = client.post(
        "/v1/runs/start",
        json={"agent_id": agent_id, "environment": "bench"},
        headers=_headers(api_key),
    )
    r.raise_for_status()
    return r.json()["run_id"]


def _append_single(client: httpx.Client, api_key: str, run_id: str) -> None:
    r = client.post(
        f"/v1/runs/{run_id}/events",
        json={"events": [{"event_type": "model_call", "payload": {"model": "gpt-4o", "tokens": 512}}]},
        headers=_headers(api_key),
    )
    r.raise_for_status()


def _append_batch(client: httpx.Client, api_key: str, run_id: str, batch_size: int = 10) -> None:
    events = [
        {"event_type": "tool_call_start", "payload": {"tool": f"tool_{i}"}}
        for i in range(batch_size)
    ]
    r = client.post(
        f"/v1/runs/{run_id}/events",
        json={"events": events},
        headers=_headers(api_key),
    )
    r.raise_for_status()


def _commit(client: httpx.Client, api_key: str, run_id: str) -> None:
    r = client.post(f"/v1/runs/{run_id}/commit", headers=_headers(api_key))
    r.raise_for_status()


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

def measure(fn: Callable, n: int) -> List[float]:
    latencies: List[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        latencies.append((time.perf_counter() - t0) * 1000.0)
    return latencies


def percentile(data: List[float], p: float) -> float:
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100)
    return sorted_data[min(idx, len(sorted_data) - 1)]


def print_stats(label: str, latencies: List[float]) -> None:
    p50 = percentile(latencies, 50)
    p95 = percentile(latencies, 95)
    p99 = percentile(latencies, 99)
    mean = statistics.mean(latencies)
    print(
        f"  {label:<40} "
        f"mean={mean:6.1f}ms  p50={p50:6.1f}ms  p95={p95:6.1f}ms  p99={p99:6.1f}ms"
        f"  (n={len(latencies)})"
    )


# ---------------------------------------------------------------------------
# Benchmark suites
# ---------------------------------------------------------------------------

def bench_single_event(client: httpx.Client, api_key: str, n: int) -> None:
    run_id = _start_run(client, api_key, "bench-single")

    def _fn() -> None:
        _append_single(client, api_key, run_id)

    lats = measure(_fn, n)
    print_stats("single event append", lats)


def bench_batch_10(client: httpx.Client, api_key: str, n: int) -> None:
    run_id = _start_run(client, api_key, "bench-batch10")

    def _fn() -> None:
        _append_batch(client, api_key, run_id, batch_size=10)

    lats = measure(_fn, n)
    print_stats("batch append (10 events)", lats)


def bench_run_roundtrip(client: httpx.Client, api_key: str, n: int) -> None:
    def _fn() -> None:
        run_id = _start_run(client, api_key, "bench-rt")
        _append_single(client, api_key, run_id)
        _commit(client, api_key, run_id)

    lats = measure(_fn, n)
    print_stats("run start + 1 event + commit", lats)


def bench_run_10events_roundtrip(client: httpx.Client, api_key: str, n: int) -> None:
    def _fn() -> None:
        run_id = _start_run(client, api_key, "bench-rt10")
        _append_batch(client, api_key, run_id, batch_size=10)
        _commit(client, api_key, run_id)

    lats = measure(_fn, n)
    print_stats("run start + 10 events + commit", lats)


# ---------------------------------------------------------------------------
# SLO assertions
# ---------------------------------------------------------------------------

SLO_SINGLE_P95_MS = 100.0
SLO_BATCH10_P95_MS = 150.0
SLO_ROUNDTRIP_P95_MS = 200.0


def check_slo(label: str, latencies: List[float], slo_p95: float) -> bool:
    p95 = percentile(latencies, 95)
    passed = p95 <= slo_p95
    status = "PASS" if passed else "FAIL"
    print(f"  SLO [{status}] {label}: p95={p95:.1f}ms (limit={slo_p95:.0f}ms)")
    return passed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="ControlDB ingestion benchmark")
    parser.add_argument("--url", default="http://localhost:8080", help="Collector base URL")
    parser.add_argument("--key", default="test-key", help="API key")
    parser.add_argument("--n", type=int, default=200, help="Iterations per suite")
    parser.add_argument("--warmup", type=int, default=20, help="Warmup iterations (discarded)")
    parser.add_argument("--slo", action="store_true", help="Assert SLO thresholds and exit non-zero on failure")
    args = parser.parse_args()

    print(f"\nControlDB ingestion benchmark — {args.url}  (n={args.n}, warmup={args.warmup})\n")

    with httpx.Client(base_url=args.url, timeout=10.0) as client:
        # Verify connectivity
        r = client.get("/healthz")
        r.raise_for_status()
        print(f"  healthz: {r.json()}\n")

        results: dict[str, list] = {}

        def run_suite(label: str, fn: Callable, n: int) -> List[float]:
            # Warmup
            run_id_warmup = _start_run(client, args.key, "warmup")
            for _ in range(args.warmup):
                try:
                    fn()
                except Exception:
                    pass
            # Measure
            lats = measure(fn, n)
            print_stats(label, lats)
            results[label] = lats
            return lats

        single_run_id = _start_run(client, args.key, "bench-single")
        run_suite("single event append", lambda: _append_single(client, args.key, single_run_id), args.n)

        batch_run_id = _start_run(client, args.key, "bench-batch10")
        run_suite("batch append (10 events)", lambda: _append_batch(client, args.key, batch_run_id, 10), args.n)

        run_suite(
            "run start + 1 event + commit",
            lambda: (
                lambda rid: (_append_single(client, args.key, rid), _commit(client, args.key, rid))
            )(_start_run(client, args.key, "bench-rt")),
            args.n,
        )

        run_suite(
            "run start + 10 events + commit",
            lambda: (
                lambda rid: (_append_batch(client, args.key, rid, 10), _commit(client, args.key, rid))
            )(_start_run(client, args.key, "bench-rt10")),
            args.n,
        )

    print()
    if args.slo:
        print("SLO check:\n")
        passed = all([
            check_slo("single event append p95", results.get("single event append", [1e9]), SLO_SINGLE_P95_MS),
            check_slo("batch append p95", results.get("batch append (10 events)", [1e9]), SLO_BATCH10_P95_MS),
            check_slo("roundtrip p95", results.get("run start + 1 event + commit", [1e9]), SLO_ROUNDTRIP_P95_MS),
        ])
        print()
        if not passed:
            print("One or more SLOs failed.")
            sys.exit(1)
        print("All SLOs passed.")


if __name__ == "__main__":
    main()
