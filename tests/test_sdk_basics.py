"""SDK-only tests (no collector)."""

from __future__ import annotations

import json

import pytest

from controldb.hashing import canonical_json, compute_event_hash, compute_payload_hash
from controldb.ids import generate_event_id, generate_run_id, generate_step_id
from controldb.redaction import RedactionRule, apply_redaction


def test_canonical_json_is_deterministic():
    a = canonical_json({"b": 1, "a": [3, 2, 1]})
    b = canonical_json({"a": [3, 2, 1], "b": 1})
    assert a == b


def test_canonical_json_rejects_nan():
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_compute_event_hash_excludes_event_hash_field():
    e1 = {"event_id": "evt_1", "payload": {"a": 1}, "event_hash": "stale"}
    e2 = {"event_id": "evt_1", "payload": {"a": 1}}
    assert compute_event_hash(e1) == compute_event_hash(e2)


def test_payload_hash_stable():
    assert compute_payload_hash({"a": 1, "b": 2}) == compute_payload_hash({"b": 2, "a": 1})


def test_id_generators_have_prefixes_and_are_unique():
    ids = {generate_run_id() for _ in range(50)}
    assert all(i.startswith("run_") for i in ids)
    assert len(ids) == 50
    assert generate_event_id().startswith("evt_")
    assert generate_step_id().startswith("step_")


def test_redaction_field_mode_redact():
    rules = [RedactionRule(field="ssn", mode="redact")]
    out = apply_redaction({"ssn": "111-22-3333", "name": "Ada"}, rules)
    assert out["ssn"] == "<redacted>"
    assert out["name"] == "Ada"


def test_redaction_hash_mode():
    rules = [RedactionRule(field="card", mode="hash")]
    out = apply_redaction({"card": "4111111111111111"}, rules)
    assert out["card"].startswith("sha256:")


def test_redaction_regex_matches_strings():
    rules = [RedactionRule(regex=r"\b\d{3}-\d{2}-\d{4}\b", mode="redact")]
    out = apply_redaction({"note": "SSN is 111-22-3333"}, rules)
    assert "<redacted>" == out["note"]


def test_redaction_drops_when_mode_drop():
    rules = [RedactionRule(field="secret", mode="drop")]
    out = apply_redaction({"secret": "abc", "ok": 1}, rules)
    assert out["secret"] is None and out["ok"] == 1
