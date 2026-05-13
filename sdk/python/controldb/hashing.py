"""Canonical JSON + hash-chain utilities.

The same canonicalization rules must be used by SDK and collector to keep
hash chains verifiable across both sides.
"""

import hashlib
import json
from typing import Any, Mapping


def canonical_json(value: Any) -> bytes:
    """Deterministic JSON encoding used as input to event hashing.

    Keys are sorted, whitespace stripped, NaN/Infinity rejected, and UTF-8 used.
    """

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


def compute_payload_hash(payload: Any) -> str:
    return sha256_hex(canonical_json(payload if payload is not None else {}))


def compute_event_hash(event: Mapping[str, Any]) -> str:
    """Hash an event envelope excluding the event_hash field.

    event_hash = sha256(canonical_json(envelope_without_event_hash))

    Because previous_event_hash lives inside the envelope, the hash chain is
    already linked when we exclude only event_hash itself.
    """

    cloned = {k: v for k, v in event.items() if k != "event_hash"}
    return sha256_hex(canonical_json(cloned))
