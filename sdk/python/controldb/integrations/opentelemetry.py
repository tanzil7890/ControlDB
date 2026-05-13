"""OpenTelemetry trace propagation helpers.

If ``opentelemetry-api`` is installed, ``current_trace_ids`` reads the active
span and returns the W3C trace context the SDK should attach to events. When
OTel is not installed, returns ``(None, None, None)``.
"""

from __future__ import annotations

from typing import Optional, Tuple


def current_trace_ids() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    try:
        from opentelemetry import trace  # type: ignore
    except Exception:  # pragma: no cover - OTel optional
        return None, None, None
    span = trace.get_current_span()
    context = span.get_span_context() if span else None
    if context is None or not context.is_valid:
        return None, None, None
    trace_id = format(context.trace_id, "032x")
    span_id = format(context.span_id, "016x")
    return trace_id, span_id, None
