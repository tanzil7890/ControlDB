"""YAML-driven policy engine with versioning + hashing.

Policies are loaded from a directory of ``*.yaml`` files. Each policy file has
the shape:

    policy_id: refund_requires_approval
    policy_version: "2026-05-01"
    description: "Refund > 5000 requires approval"
    rules:
      - when: {amount: {gt: 5000}}
        result: {allowed: false, requires_approval: true, reason: "Amount exceeds threshold"}
      - default:
        result: {allowed: true}

For deployments that need richer policy-as-code, set ``CONTROLDB_OPA_URL`` to
forward evaluation to an Open Policy Agent instance.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

import httpx
import yaml

from ..config import SETTINGS
from ..hashing import canonical_json


@dataclass
class PolicyDecision:
    allowed: bool
    requires_approval: bool
    reason: str
    policy_id: str
    policy_version: str
    policy_hash: str
    raw: Dict[str, Any]


class PolicyEngine:
    def __init__(self, policies_dir: Optional[str] = None, opa_url: Optional[str] = None):
        self.policies_dir = Path(policies_dir) if policies_dir else None
        self.opa_url = opa_url
        self._policies: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.policies_dir or not self.policies_dir.exists():
            return
        with self._lock:
            for path in sorted(self.policies_dir.glob("*.yaml")):
                with path.open("r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh)
                if not isinstance(data, dict) or "policy_id" not in data:
                    continue
                policy_hash = "sha256:" + hashlib.sha256(canonical_json(data)).hexdigest()
                data["_policy_hash"] = policy_hash
                self._policies[data["policy_id"]] = data

    def list_policies(self) -> List[Dict[str, Any]]:
        return [
            {
                "policy_id": p["policy_id"],
                "policy_version": p.get("policy_version"),
                "policy_hash": p["_policy_hash"],
                "description": p.get("description"),
            }
            for p in self._policies.values()
        ]

    def evaluate(self, policy_id: str, input_data: Mapping[str, Any]) -> PolicyDecision:
        if self.opa_url:
            return self._evaluate_opa(policy_id, input_data)
        policy = self._policies.get(policy_id)
        if not policy:
            decision = {"allowed": True, "requires_approval": False, "reason": "no policy registered"}
            return PolicyDecision(
                allowed=True,
                requires_approval=False,
                reason=decision["reason"],
                policy_id=policy_id,
                policy_version="unversioned",
                policy_hash="sha256:0",
                raw=decision,
            )
        for rule in policy.get("rules", []):
            if "default" in rule:
                result = dict(rule.get("result", {}))
                return self._wrap(policy, result)
            if "when" in rule and _match(rule["when"], input_data):
                result = dict(rule.get("result", {}))
                return self._wrap(policy, result)
        return self._wrap(policy, {"allowed": True, "requires_approval": False, "reason": "no rule matched"})

    def _wrap(self, policy: Mapping[str, Any], result: Dict[str, Any]) -> PolicyDecision:
        allowed = bool(result.get("allowed", True))
        requires_approval = bool(result.get("requires_approval", False))
        return PolicyDecision(
            allowed=allowed,
            requires_approval=requires_approval,
            reason=str(result.get("reason", "")),
            policy_id=policy["policy_id"],
            policy_version=policy.get("policy_version", "unversioned"),
            policy_hash=policy["_policy_hash"],
            raw=result,
        )

    def _evaluate_opa(self, policy_id: str, input_data: Mapping[str, Any]) -> PolicyDecision:
        url = f"{self.opa_url.rstrip('/')}/v1/data/{policy_id.replace('.', '/')}"
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(url, json={"input": dict(input_data)})
            resp.raise_for_status()
            body = resp.json().get("result", {})
        return PolicyDecision(
            allowed=bool(body.get("allowed", True)),
            requires_approval=bool(body.get("requires_approval", False)),
            reason=str(body.get("reason", "")),
            policy_id=policy_id,
            policy_version=str(body.get("policy_version", "opa")),
            policy_hash=str(body.get("policy_hash", "sha256:opa")),
            raw=body,
        )


def _match(condition: Mapping[str, Any], input_data: Mapping[str, Any]) -> bool:
    for key, expected in condition.items():
        actual = input_data.get(key)
        if isinstance(expected, Mapping):
            if "eq" in expected and actual != expected["eq"]:
                return False
            if "ne" in expected and actual == expected["ne"]:
                return False
            if "gt" in expected and not (isinstance(actual, (int, float)) and actual > expected["gt"]):
                return False
            if "gte" in expected and not (isinstance(actual, (int, float)) and actual >= expected["gte"]):
                return False
            if "lt" in expected and not (isinstance(actual, (int, float)) and actual < expected["lt"]):
                return False
            if "lte" in expected and not (isinstance(actual, (int, float)) and actual <= expected["lte"]):
                return False
            if "in" in expected and actual not in expected["in"]:
                return False
            if "not_in" in expected and actual in expected["not_in"]:
                return False
        else:
            if actual != expected:
                return False
    return True


_engine: Optional[PolicyEngine] = None
_engine_lock = threading.Lock()


def get_policy_engine() -> PolicyEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = PolicyEngine(policies_dir=SETTINGS.policies_dir, opa_url=SETTINGS.opa_url)
        return _engine


def reset_policy_engine() -> None:
    global _engine
    with _engine_lock:
        _engine = None
