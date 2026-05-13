"""High-level Run and ToolCall objects exposed to SDK users."""

from __future__ import annotations

import logging
import traceback
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .errors import ApprovalRequiredError, PolicyDeniedError, ValidationError
from .events import EVENT_TYPES, PayloadMode, SCHEMA_VERSION, Actor, EventEnvelope, utc_now_iso
from .hashing import compute_event_hash, compute_payload_hash
from .ids import generate_approval_id, generate_event_id, generate_step_id
from .redaction import RedactionRule, apply_redaction

_LOG = logging.getLogger("controldb.run")


class ToolCallContext:
    """Context manager returned by ``Run.tool_call``.

    Captures ``tool_call.started``, ``tool_call.completed`` (or ``.failed``).
    """

    def __init__(self, run: "Run", name: str, redact: bool = True):
        self.run = run
        self.name = name
        self.redact = redact
        self.step_id = generate_step_id()
        self._input: Any = None
        self._output: Any = None
        self._started = False

    def input(self, value: Any) -> None:
        self._input = value

    def output(self, value: Any, *, redact: Optional[bool] = None) -> None:
        self._output = value
        if redact is not None:
            self.redact = redact

    def __enter__(self) -> "ToolCallContext":
        self.run._emit(
            event_type="tool_call.started",
            payload={"name": self.name, "input": self._input},
            step_id=self.step_id,
            redact=self.redact,
        )
        self._started = True
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None:
            self.run._emit(
                event_type="tool_call.failed",
                payload={
                    "name": self.name,
                    "input": self._input,
                    "error": {"type": exc_type.__name__ if exc_type else "Exception", "message": str(exc)},
                },
                step_id=self.step_id,
                redact=self.redact,
            )
            return False  # propagate
        self.run._emit(
            event_type="tool_call.completed",
            payload={"name": self.name, "input": self._input, "output": self._output},
            step_id=self.step_id,
            redact=self.redact,
        )
        return False


class Run:
    """Single execution instance of an agent."""

    def __init__(
        self,
        client: "ControlDB",  # type: ignore[name-defined]  # forward declared
        run_id: str,
        agent_id: str,
        agent_version: Optional[str] = None,
        environment: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        self.client = client
        self.run_id = run_id
        self.agent_id = agent_id
        self.agent_version = agent_version
        self.environment = environment or client.environment
        self.metadata = dict(metadata or {})
        self.trace_id = trace_id
        self._buffer: List[Dict[str, Any]] = []
        self._step_counter = 0
        self._previous_event_hash: Optional[str] = None
        self._status = "running"
        self._committed = False
        self._closed = False

    # ---- internal event creation ----

    def _next_step_index(self) -> int:
        idx = self._step_counter
        self._step_counter += 1
        return idx

    def _build_envelope(
        self,
        event_type: str,
        payload: Any,
        *,
        step_id: Optional[str] = None,
        step_index: Optional[int] = None,
        actor: Optional[Actor] = None,
        payload_mode: Optional[str] = None,
        redact: bool = True,
    ) -> EventEnvelope:
        if event_type not in EVENT_TYPES:
            raise ValidationError(f"unknown event_type: {event_type}")

        capture_mode = PayloadMode(payload_mode or self.client.default_payload_mode).value
        prepared_payload: Optional[Dict[str, Any]] = None
        payload_hash: Optional[str] = None

        if payload is not None:
            redacted = apply_redaction(payload, self.client.redaction_rules, self.client.redaction_callbacks) if redact else payload
            if capture_mode == PayloadMode.METADATA_ONLY.value:
                prepared_payload = None
                payload_hash = compute_payload_hash(redacted)
            elif capture_mode == PayloadMode.HASH_ONLY.value:
                prepared_payload = None
                payload_hash = compute_payload_hash(redacted)
            elif capture_mode == PayloadMode.REDACTED_PAYLOAD.value:
                prepared_payload = redacted if isinstance(redacted, dict) else {"value": redacted}
                payload_hash = compute_payload_hash(redacted)
            elif capture_mode == PayloadMode.FULL_PAYLOAD.value:
                prepared_payload = payload if isinstance(payload, dict) else {"value": payload}
                payload_hash = compute_payload_hash(payload)
            elif capture_mode == PayloadMode.SELF_HOSTED_PAYLOAD.value:
                # Payload kept by the caller; only hash and URI are recorded.
                prepared_payload = {"artifact_uri": (payload.get("artifact_uri") if isinstance(payload, dict) else None)}
                payload_hash = compute_payload_hash(payload)

        envelope = EventEnvelope(
            event_id=generate_event_id(),
            schema_version=SCHEMA_VERSION,
            event_type=event_type,
            org_id=self.client.org_id,
            project_id=self.client.project,
            environment=self.environment,
            agent_id=self.agent_id,
            agent_version=self.agent_version,
            run_id=self.run_id,
            step_id=step_id,
            step_index=step_index if step_index is not None else self._next_step_index(),
            trace_id=self.trace_id,
            span_id=None,
            parent_span_id=None,
            actor=actor or Actor(type="agent", id=self.agent_id),
            payload_mode=capture_mode,
            payload=prepared_payload,
            payload_hash=payload_hash,
            previous_event_hash=self._previous_event_hash,
            event_hash="",  # filled in below
            occurred_at=utc_now_iso(),
            ingested_at=utc_now_iso(),
        )
        envelope_dict = envelope.to_dict()
        envelope_dict.pop("event_hash", None)
        envelope.event_hash = compute_event_hash(envelope_dict)
        self._previous_event_hash = envelope.event_hash
        return envelope

    def _emit(self, **kwargs: Any) -> Dict[str, Any]:
        envelope = self._build_envelope(**kwargs)
        event_dict = envelope.to_dict()
        idempotency_key = f"{self.run_id}:{envelope.step_index}:{envelope.event_type}"
        event_dict["idempotency_key"] = idempotency_key
        self._buffer.append(event_dict)
        if len(self._buffer) >= self.client.batch_size:
            self._flush()
        return event_dict

    def _flush(self) -> None:
        if not self._buffer:
            return
        events = list(self._buffer)
        self._buffer.clear()
        idempotency_key = f"{self.run_id}:batch:{events[0]['event_id']}:{events[-1]['event_id']}"
        try:
            self.client.transport.post_events(self.run_id, events, idempotency_key=idempotency_key)
        except Exception:
            # On failure, put events back at the head of the buffer so the next
            # flush retries them. The transport may also have spooled to disk.
            self._buffer[0:0] = events
            raise

    # ---- public, named event helpers ----

    def input(self, *, user_id: Optional[str] = None, input_hash: Optional[str] = None, metadata: Optional[Mapping[str, Any]] = None) -> None:
        self._emit(
            event_type="agent_run.started",
            payload={"user_id": user_id, "input_hash": input_hash, "metadata": dict(metadata or {})},
        )

    def model_call(
        self,
        *,
        model: str,
        prompt: Any = None,
        completion: Any = None,
        provider: Optional[str] = None,
        usage: Optional[Mapping[str, Any]] = None,
        redact: bool = True,
        success: bool = True,
    ) -> Dict[str, Any]:
        payload = {
            "model": model,
            "provider": provider,
            "prompt": prompt,
            "completion": completion,
            "usage": dict(usage or {}),
        }
        return self._emit(
            event_type="model_call.completed" if success else "model_call.failed",
            payload=payload,
            redact=redact,
        )

    def tool_call(self, name: str, *, redact: bool = True) -> ToolCallContext:
        return ToolCallContext(self, name=name, redact=redact)

    def memory_read(self, *, key: str, value: Any = None, store: str = "default", redact: bool = True) -> Dict[str, Any]:
        return self._emit(
            event_type="memory.read",
            payload={"store": store, "key": key, "value": value},
            redact=redact,
        )

    def memory_write(self, *, key: str, value: Any = None, store: str = "default", redact: bool = True) -> Dict[str, Any]:
        return self._emit(
            event_type="memory.write",
            payload={"store": store, "key": key, "value": value},
            redact=redact,
        )

    def state_change(
        self,
        *,
        entity_type: str,
        entity_id: str,
        before: Any,
        after: Any,
        reason: Optional[str] = None,
        applied: bool = True,
        redact: bool = True,
    ) -> Dict[str, Any]:
        event_type = "state.change.applied" if applied else "state.change.proposed"
        payload = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "before": before,
            "after": after,
            "reason": reason,
        }
        return self._emit(event_type=event_type, payload=payload, redact=redact)

    def policy_check(
        self,
        *,
        policy_id: str,
        input: Any,
        result: Optional[Mapping[str, Any]] = None,
        policy_version: Optional[str] = None,
        policy_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        if result is None:
            response = self.client.transport.policy_check(
                {"policy_id": policy_id, "input": input, "run_id": self.run_id}
            )
            result = response.get("result", response)
            policy_version = policy_version or response.get("policy_version")
            policy_hash = policy_hash or response.get("policy_hash")

        passed = bool(result.get("allowed", True)) or bool(result.get("requires_approval"))
        event_type = "policy.check.passed" if passed else "policy.check.failed"
        self._emit(
            event_type=event_type,
            payload={
                "policy_id": policy_id,
                "policy_version": policy_version,
                "policy_hash": policy_hash,
                "input": input,
                "result": dict(result),
            },
        )
        return dict(result)

    def enforce_policy(
        self,
        *,
        policy_id: str,
        input: Any,
    ) -> Dict[str, Any]:
        result = self.policy_check(policy_id=policy_id, input=input)
        if result.get("allowed") is False and not result.get("requires_approval"):
            self._emit(
                event_type="policy.enforcement.blocked",
                payload={"policy_id": policy_id, "input": input, "result": result},
            )
            raise PolicyDeniedError(
                result.get("reason", "policy denied"),
                policy_id=policy_id,
                reason=result.get("reason", ""),
            )
        if result.get("requires_approval"):
            approval = self.request_approval(
                policy_id=policy_id,
                input=input,
                reason=result.get("reason", "approval required"),
            )
            raise ApprovalRequiredError(
                result.get("reason", "approval required"),
                approval_id=approval.get("approval_id", ""),
                policy_id=policy_id,
            )
        self._emit(
            event_type="policy.enforcement.allowed",
            payload={"policy_id": policy_id, "input": input, "result": result},
        )
        return result

    def request_approval(
        self,
        *,
        policy_id: Optional[str] = None,
        input: Any = None,
        reason: Optional[str] = None,
        approval_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        approval_id = approval_id or generate_approval_id()
        body = {
            "approval_id": approval_id,
            "run_id": self.run_id,
            "policy_id": policy_id,
            "input": input,
            "reason": reason,
        }
        self._emit(
            event_type="approval.requested",
            payload=body,
        )
        # Flush so the dashboard can pick up the request before the API call.
        self._flush()
        return self.client.transport.request_approval(body)

    def human_approval(
        self,
        *,
        approval_id: str,
        reviewer_id: str,
        decision: str,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        if decision not in {"approved", "rejected", "expired", "overridden"}:
            raise ValidationError(f"invalid approval decision: {decision}")
        return self._emit(
            event_type=f"approval.{decision}",
            payload={
                "approval_id": approval_id,
                "reviewer_id": reviewer_id,
                "decision": decision,
                "reason": reason,
            },
            actor=Actor(type="human", id=reviewer_id),
        )

    def evidence_artifact(self, *, uri: str, sha256: str, kind: str = "blob", description: Optional[str] = None) -> Dict[str, Any]:
        return self._emit(
            event_type="evidence.artifact.created",
            payload={"uri": uri, "sha256": sha256, "kind": kind, "description": description},
        )

    def emit(self, event_type: str, payload: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        """Advanced: emit a raw event. Prefer the named helpers above."""

        return self._emit(event_type=event_type, payload=dict(payload or {}), **kwargs)

    # ---- lifecycle ----

    def commit(self) -> Dict[str, Any]:
        if self._closed:
            raise ValidationError("run already closed")
        self._emit(event_type="agent_run.committed", payload={})
        self._flush()
        self._status = "committed"
        self._committed = True
        self._closed = True
        return self.client.transport.commit_run(self.run_id)

    def rollback(self, reason: Optional[str] = None) -> Dict[str, Any]:
        if self._closed:
            raise ValidationError("run already closed")
        self._emit(event_type="agent_run.rolled_back", payload={"reason": reason})
        self._flush()
        self._status = "rolled_back"
        self._closed = True
        return self.client.transport.rollback_run(self.run_id, reason=reason)

    def fail(self, error: BaseException) -> Dict[str, Any]:
        if self._closed:
            return {}
        self._emit(
            event_type="agent_run.failed",
            payload={
                "error": {
                    "type": type(error).__name__,
                    "message": str(error),
                    "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__)),
                }
            },
        )
        self._flush()
        self._status = "failed"
        self._closed = True
        return {}

    # ---- context manager glue ----

    def __enter__(self) -> "Run":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None:
            try:
                self.fail(exc)
            except Exception:
                _LOG.exception("controldb: failed to record run failure")
            return False
        if not self._committed:
            try:
                self.commit()
            except Exception:
                _LOG.exception("controldb: failed to commit run")
                raise
        return False
