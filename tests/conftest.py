"""Shared pytest fixtures: hermetic SQLite-backed collector + in-process SDK client."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Iterator

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk" / "python"))
sys.path.insert(0, str(ROOT / "services" / "collector"))


@pytest.fixture
def collector_env(tmp_path, monkeypatch) -> dict:
    db_path = tmp_path / "controldb.sqlite"
    artifact_dir = tmp_path / "artifacts"
    policies_dir = ROOT / "deploy" / "policies"
    env = {
        "CONTROLDB_DATABASE_URL": f"sqlite:///{db_path}",
        "CONTROLDB_OBJECT_STORE_DIR": str(artifact_dir),
        "CONTROLDB_BOOTSTRAP_API_KEYS": "test-key",
        "CONTROLDB_BOOTSTRAP_ORG_ID": "org_test",
        "CONTROLDB_BOOTSTRAP_PROJECT_ID": "proj_test",
        "CONTROLDB_POLICIES_DIR": str(policies_dir),
        "CONTROLDB_ENABLE_DASHBOARD": "0",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    # Reset cached singletons so the env vars take effect.
    from controldb_collector.config import Settings, SETTINGS
    fresh = Settings()
    for field_name, value in fresh.__dict__.items():
        setattr(SETTINGS, field_name, value)
    from controldb_collector.storage import db as db_module
    db_module.Database.reset()
    from controldb_collector.policy.engine import reset_policy_engine
    reset_policy_engine()
    return env


@pytest.fixture
def app(collector_env):
    from controldb_collector.app import create_app

    return create_app()


@pytest.fixture
def http_client(app):
    """FastAPI TestClient backed by Starlette's transport.

    The TestClient API is compatible with httpx (``get``/``post``/``json``).
    """

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        yield client


@pytest.fixture
def sdk(app, monkeypatch):
    """In-process SDK client backed by the same FastAPI app via the test client."""

    from controldb.transport import Transport
    from controldb import ControlDB
    from fastapi.testclient import TestClient

    test_client = TestClient(app)

    class InProcessTransport(Transport):
        def __init__(self, base_url: str, api_key: str, **kwargs):
            super().__init__(base_url, api_key, **kwargs)
            try:
                self._client.close()
            except Exception:
                pass
            self._client = test_client
            self._client.headers.update({
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "controldb-python/0.1",
                "Content-Type": "application/json",
            })

    monkeypatch.setenv("CONTROLDB_URL", "http://controldb.test")
    control = ControlDB(
        api_key="test-key",
        project="proj_test",
        org_id="org_test",
        environment="dev",
        base_url="http://controldb.test",
        transport=InProcessTransport("http://controldb.test", "test-key", max_retries=1),
        default_payload_mode="full_payload",
    )
    yield control
    control.close()
