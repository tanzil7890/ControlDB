"""Payload redaction.

Supports:
- Field redaction (key match anywhere in nested dicts/lists)
- Regex redaction (string values matching a regex)
- Hash-only payload capture
- Custom callbacks (callable(payload) -> payload)
"""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any, Callable, Iterable, List, Mapping, Optional, Sequence

REDACTED_SENTINEL = "<redacted>"


class RedactionRule:
    def __init__(
        self,
        field: Optional[str] = None,
        regex: Optional[str] = None,
        mode: str = "redact",
    ) -> None:
        if not field and not regex:
            raise ValueError("RedactionRule requires field or regex")
        if mode not in {"redact", "hash", "drop"}:
            raise ValueError(f"unknown redaction mode: {mode}")
        self.field = field
        self.regex = re.compile(regex) if regex else None
        self.mode = mode

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RedactionRule":
        return cls(field=raw.get("field"), regex=raw.get("regex"), mode=raw.get("mode", "redact"))


def _apply_mode(value: Any, mode: str) -> Any:
    if mode == "drop":
        return None
    if mode == "hash":
        digest = hashlib.sha256(repr(value).encode("utf-8")).hexdigest()
        return f"sha256:{digest}"
    return REDACTED_SENTINEL


def _redact_value(value: Any, rules: Sequence[RedactionRule]) -> Any:
    if isinstance(value, dict):
        return {k: _redact_field(k, v, rules) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v, rules) for v in value]
    if isinstance(value, str):
        for rule in rules:
            if rule.regex and rule.regex.search(value):
                return _apply_mode(value, rule.mode)
        return value
    return value


def _redact_field(key: str, value: Any, rules: Sequence[RedactionRule]) -> Any:
    for rule in rules:
        if rule.field and rule.field == key:
            return _apply_mode(value, rule.mode)
    return _redact_value(value, rules)


def apply_redaction(
    payload: Any,
    rules: Iterable[RedactionRule],
    callbacks: Optional[Iterable[Callable[[Any], Any]]] = None,
) -> Any:
    """Return a redacted deep-copy of the payload."""

    if payload is None:
        return None
    rules_list: List[RedactionRule] = list(rules)
    data = deepcopy(payload)
    data = _redact_value(data, rules_list)
    if callbacks:
        for cb in callbacks:
            data = cb(data)
    return data
