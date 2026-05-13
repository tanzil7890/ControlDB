"""HTTP transport with retry, batching, and an offline spool.

The transport is the only object that knows about the wire. The Run and Client
code build envelopes and hand them off here.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import httpx

from .errors import (
    AuthenticationError,
    AuthorizationError,
    CollectorUnavailableError,
    IdempotencyConflictError,
    PayloadTooLargeError,
    ValidationError,
)


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    body: Any
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    message = body.get("detail") if isinstance(body, dict) else str(body)
    if resp.status_code == 401:
        raise AuthenticationError(message or "unauthenticated")
    if resp.status_code == 403:
        raise AuthorizationError(message or "forbidden")
    if resp.status_code == 409:
        raise IdempotencyConflictError(message or "conflict")
    if resp.status_code == 413:
        raise PayloadTooLargeError(message or "payload too large")
    if resp.status_code == 422:
        raise ValidationError(message or "validation error")
    if resp.status_code >= 500:
        raise CollectorUnavailableError(message or f"collector status {resp.status_code}")
    raise CollectorUnavailableError(message or f"unexpected status {resp.status_code}")


class Spool:
    """Append-only on-disk spool for offline buffering."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, batch: Mapping[str, Any]) -> None:
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(batch) + "\n")

    def drain(self) -> List[Dict[str, Any]]:
        with self._lock:
            if not self.path.exists():
                return []
            entries: List[Dict[str, Any]] = []
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    entries.append(json.loads(line))
            self.path.unlink()
            return entries


class Transport:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 10.0,
        max_retries: int = 4,
        backoff_initial: float = 0.2,
        backoff_max: float = 5.0,
        fail_open: bool = False,
        spool_path: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_initial = backoff_initial
        self.backoff_max = backoff_max
        self.fail_open = fail_open
        self.spool = Spool(Path(spool_path)) if spool_path else None
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "controldb-python/0.1",
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        retry: bool = True,
    ) -> Dict[str, Any]:
        headers: Dict[str, str] = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        attempts = self.max_retries if retry else 1
        last_error: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                resp = self._client.request(method, path, json=json_body, headers=headers)
                _raise_for_status(resp)
                if resp.status_code == 204 or not resp.content:
                    return {}
                return resp.json()
            except (httpx.HTTPError, CollectorUnavailableError) as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    break
                sleep = min(self.backoff_max, self.backoff_initial * (2 ** attempt))
                sleep += random.uniform(0, sleep / 2)
                time.sleep(sleep)
            except (AuthenticationError, AuthorizationError, ValidationError, IdempotencyConflictError, PayloadTooLargeError):
                raise
        assert last_error is not None
        raise last_error

    # ---- High-level wire helpers used by the SDK ----

    def post_events(self, run_id: str, events: Sequence[Mapping[str, Any]], idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        try:
            return self.request(
                "POST",
                f"/v1/runs/{run_id}/events",
                json_body={"events": list(events)},
                idempotency_key=idempotency_key,
            )
        except (CollectorUnavailableError, httpx.HTTPError) as exc:
            if self.spool is not None:
                self.spool.write({"run_id": run_id, "events": list(events), "idempotency_key": idempotency_key})
                return {"spooled": True, "reason": str(exc)}
            if self.fail_open:
                return {"spooled": False, "fail_open": True, "reason": str(exc)}
            raise

    def start_run(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        return self.request("POST", "/v1/runs/start", json_body=payload)

    def commit_run(self, run_id: str) -> Dict[str, Any]:
        return self.request("POST", f"/v1/runs/{run_id}/commit")

    def rollback_run(self, run_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        return self.request("POST", f"/v1/runs/{run_id}/rollback", json_body={"reason": reason})

    def policy_check(self, body: Mapping[str, Any]) -> Dict[str, Any]:
        return self.request("POST", "/v1/policy/check", json_body=body)

    def request_approval(self, body: Mapping[str, Any]) -> Dict[str, Any]:
        return self.request("POST", "/v1/approvals/request", json_body=body)

    def get_timeline(self, run_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/v1/runs/{run_id}/timeline", retry=False)

    def verify_run(self, run_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/v1/runs/{run_id}/verify", retry=False)

    def flush_spool(self) -> int:
        if not self.spool:
            return 0
        drained = self.spool.drain()
        flushed = 0
        for entry in drained:
            try:
                self.request(
                    "POST",
                    f"/v1/runs/{entry['run_id']}/events",
                    json_body={"events": entry["events"]},
                    idempotency_key=entry.get("idempotency_key"),
                )
                flushed += 1
            except Exception:
                # Re-queue and stop draining on first error.
                self.spool.write(entry)
                break
        return flushed
