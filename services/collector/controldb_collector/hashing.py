"""Collector-side canonical JSON + hashing (mirrors SDK)."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_default,
    ).encode("utf-8")


def _default(obj: Any) -> Any:
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    raise TypeError(f"Unserializable type: {type(obj).__name__}")


def sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def compute_event_hash(event: Mapping[str, Any]) -> str:
    cloned = {k: v for k, v in event.items() if k != "event_hash"}
    return sha256_hex(canonical_json(cloned))


def compute_payload_hash(payload: Any) -> str:
    return sha256_hex(canonical_json(payload if payload is not None else {}))
