"""Policy engine unit tests."""

from __future__ import annotations

from pathlib import Path

from controldb_collector.policy.engine import PolicyEngine


def test_refund_under_threshold_allowed():
    engine = PolicyEngine(policies_dir=str(Path(__file__).resolve().parents[1] / "deploy" / "policies"))
    decision = engine.evaluate("refund_requires_approval", {"action": "approve_refund", "amount": 1000})
    assert decision.allowed
    assert not decision.requires_approval


def test_refund_over_threshold_requires_approval():
    engine = PolicyEngine(policies_dir=str(Path(__file__).resolve().parents[1] / "deploy" / "policies"))
    decision = engine.evaluate("refund_requires_approval", {"action": "approve_refund", "amount": 7000})
    assert not decision.allowed
    assert decision.requires_approval
    assert decision.policy_hash.startswith("sha256:")


def test_unknown_policy_falls_through_allow():
    engine = PolicyEngine(policies_dir=str(Path(__file__).resolve().parents[1] / "deploy" / "policies"))
    decision = engine.evaluate("does_not_exist", {})
    assert decision.allowed
