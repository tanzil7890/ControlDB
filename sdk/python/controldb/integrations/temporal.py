"""Helpers for use inside Temporal workflows or activities."""

from __future__ import annotations

from typing import Optional


def attach_temporal_metadata(run, workflow_id: Optional[str] = None, run_attempt: Optional[int] = None, activity_id: Optional[str] = None) -> None:
    """Stamp the current ControlDB run with Temporal coordinates.

    Call inside a workflow with ``workflow.info()`` or inside an activity with
    ``activity.info()``. Both call sites are kept optional so the SDK does not
    depend on ``temporalio``.
    """

    metadata = {}
    if workflow_id is None:
        try:
            from temporalio import workflow  # type: ignore

            metadata["workflow_id"] = workflow.info().workflow_id
            metadata["workflow_run_id"] = workflow.info().run_id
            metadata["attempt"] = workflow.info().attempt
        except Exception:
            pass
    else:
        metadata["workflow_id"] = workflow_id
        if run_attempt is not None:
            metadata["attempt"] = run_attempt
    if activity_id is not None:
        metadata["activity_id"] = activity_id
    if metadata:
        run.metadata.update(metadata)
        run.emit(event_type="memory.write", payload={"store": "_meta_temporal", "key": "info", "value": metadata})
