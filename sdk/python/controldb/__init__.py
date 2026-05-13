"""ControlDB Python SDK.

Add one SDK to an AI agent stack and get a replayable, queryable,
tamper-evident audit trail of every tool call, memory update, state change,
policy check, human approval, and final decision.
"""

from .client import ControlDB
from .run import Run, ToolCallContext
from .errors import (
    ControlDBError,
    AuthenticationError,
    AuthorizationError,
    ValidationError,
    RedactionError,
    PolicyDeniedError,
    ApprovalRequiredError,
    CollectorUnavailableError,
    PayloadTooLargeError,
    IdempotencyConflictError,
)
from .events import EventEnvelope, PayloadMode, SCHEMA_VERSION
from .ids import generate_event_id, generate_run_id, generate_step_id
from .hashing import canonical_json, compute_event_hash

__version__ = "0.1.0"

__all__ = [
    "ControlDB",
    "Run",
    "ToolCallContext",
    "EventEnvelope",
    "PayloadMode",
    "SCHEMA_VERSION",
    "ControlDBError",
    "AuthenticationError",
    "AuthorizationError",
    "ValidationError",
    "RedactionError",
    "PolicyDeniedError",
    "ApprovalRequiredError",
    "CollectorUnavailableError",
    "PayloadTooLargeError",
    "IdempotencyConflictError",
    "generate_event_id",
    "generate_run_id",
    "generate_step_id",
    "canonical_json",
    "compute_event_hash",
]
