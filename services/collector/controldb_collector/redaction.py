"""Collector-side redaction. Mirrors the SDK to enforce server policy."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Iterable, List, Mapping, Optional, Sequence

REDACTED = "<redacted>"


class CollectorRedactionRule:
    def __init__(self, field: Optional[str] = None, regex: Optional[str] = None, mode: str = "redact"):
        self.field = field
        self.regex = re.compile(regex) if regex else None
        if mode not in {"redact", "hash", "drop"}:
            raise ValueError(f"invalid mode {mode}")
        self.mode = mode

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CollectorRedactionRule":
        return cls(field=raw.get("field"), regex=raw.get("regex"), mode=raw.get("mode", "redact"))


def _apply(value: Any, mode: str) -> Any:
    if mode == "drop":
        return None
    if mode == "hash":
        import hashlib

        return "sha256:" + hashlib.sha256(repr(value).encode("utf-8")).hexdigest()
    return REDACTED


def _walk(value: Any, rules: Sequence[CollectorRedactionRule]) -> Any:
    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
            matched = False
            for rule in rules:
                if rule.field and rule.field == k:
                    result[k] = _apply(v, rule.mode)
                    matched = True
                    break
            if not matched:
                result[k] = _walk(v, rules)
        return result
    if isinstance(value, list):
        return [_walk(v, rules) for v in value]
    if isinstance(value, str):
        for rule in rules:
            if rule.regex and rule.regex.search(value):
                return _apply(value, rule.mode)
    return value


def apply_collector_redaction(payload: Any, rules: Iterable[CollectorRedactionRule]) -> Any:
    if payload is None:
        return None
    return _walk(deepcopy(payload), list(rules))
