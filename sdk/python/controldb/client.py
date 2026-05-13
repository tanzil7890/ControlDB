"""ControlDB SDK client entrypoint."""

from __future__ import annotations

import atexit
import os
from typing import Any, Callable, Iterable, List, Mapping, Optional, Sequence

from .errors import ValidationError
from .events import PayloadMode
from .ids import generate_run_id
from .redaction import RedactionRule
from .run import Run
from .transport import Transport


class ControlDB:
    """Top-level SDK client.

    Example:
        from controldb import ControlDB

        control = ControlDB(api_key=..., project="aml", environment="prod")
        with control.run(agent_id="aml-agent", agent_version="v1") as run:
            with run.tool_call("lookup") as tool:
                tool.input({"id": 1})
                tool.output({"ok": True})
            run.commit()
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        project: Optional[str] = None,
        environment: str = "dev",
        org_id: Optional[str] = None,
        base_url: Optional[str] = None,
        default_payload_mode: str = PayloadMode.REDACTED_PAYLOAD.value,
        redaction_rules: Optional[Iterable[Mapping[str, Any]]] = None,
        redaction_callbacks: Optional[Iterable[Callable[[Any], Any]]] = None,
        batch_size: int = 16,
        fail_open: bool = False,
        spool_path: Optional[str] = None,
        transport: Optional[Transport] = None,
    ) -> None:
        api_key = api_key or os.environ.get("CONTROLDB_API_KEY")
        if not api_key:
            raise ValidationError("api_key is required (or set CONTROLDB_API_KEY)")
        if not project:
            project = os.environ.get("CONTROLDB_PROJECT")
        if not project:
            raise ValidationError("project is required")
        self.org_id = org_id or os.environ.get("CONTROLDB_ORG_ID") or "org_default"
        self.project = project
        self.environment = environment
        self.default_payload_mode = PayloadMode(default_payload_mode).value
        self.redaction_rules: List[RedactionRule] = [
            RedactionRule.from_dict(rule) for rule in (redaction_rules or [])
        ]
        self.redaction_callbacks = list(redaction_callbacks or [])
        self.batch_size = batch_size
        self.fail_open = fail_open
        resolved_base = base_url or os.environ.get("CONTROLDB_URL", "http://localhost:8080")
        self.transport = transport or Transport(
            resolved_base,
            api_key,
            fail_open=fail_open,
            spool_path=spool_path,
        )
        self._open_runs: List[Run] = []
        atexit.register(self._atexit_flush)

    def run(
        self,
        *,
        agent_id: str,
        agent_version: Optional[str] = None,
        environment: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        trace_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Run:
        body = {
            "agent_id": agent_id,
            "agent_version": agent_version,
            "environment": environment or self.environment,
            "org_id": self.org_id,
            "project_id": self.project,
            "metadata": dict(metadata or {}),
            "run_id": run_id,
        }
        response = self.transport.start_run(body)
        assigned_run_id = response.get("run_id") or run_id or generate_run_id()
        run = Run(
            client=self,
            run_id=assigned_run_id,
            agent_id=agent_id,
            agent_version=agent_version,
            environment=environment,
            metadata=metadata,
            trace_id=trace_id,
        )
        self._open_runs.append(run)
        return run

    # ---- dashboard / API helpers ----

    def timeline(self, run_id: str) -> Mapping[str, Any]:
        return self.transport.get_timeline(run_id)

    def verify(self, run_id: str) -> Mapping[str, Any]:
        return self.transport.verify_run(run_id)

    def flush_spool(self) -> int:
        return self.transport.flush_spool()

    def close(self) -> None:
        for run in list(self._open_runs):
            if not run._committed and not run._closed:
                try:
                    run._flush()
                except Exception:
                    pass
        self.transport.close()

    def _atexit_flush(self) -> None:
        try:
            for run in self._open_runs:
                if run._buffer:
                    try:
                        run._flush()
                    except Exception:
                        pass
        finally:
            try:
                self.transport.close()
            except Exception:
                pass
