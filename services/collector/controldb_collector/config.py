"""Configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional


def _split(value: str) -> List[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get("CONTROLDB_DATABASE_URL", "sqlite:///./controldb.sqlite"))
    object_store_dir: str = field(default_factory=lambda: os.environ.get("CONTROLDB_OBJECT_STORE_DIR", "./controldb_artifacts"))
    jwt_secret: str = field(default_factory=lambda: os.environ.get("CONTROLDB_JWT_SECRET", "dev-jwt-secret"))
    encryption_key: str = field(default_factory=lambda: os.environ.get("CONTROLDB_ENCRYPTION_KEY", "dev-encryption-key"))
    public_url: str = field(default_factory=lambda: os.environ.get("CONTROLDB_PUBLIC_URL", "http://localhost:8080"))
    bootstrap_api_keys: List[str] = field(
        default_factory=lambda: _split(os.environ.get("CONTROLDB_BOOTSTRAP_API_KEYS", "test-key"))
    )
    bootstrap_org_id: str = field(default_factory=lambda: os.environ.get("CONTROLDB_BOOTSTRAP_ORG_ID", "org_default"))
    bootstrap_project_id: str = field(default_factory=lambda: os.environ.get("CONTROLDB_BOOTSTRAP_PROJECT_ID", "proj_default"))
    policies_dir: Optional[str] = field(default_factory=lambda: os.environ.get("CONTROLDB_POLICIES_DIR"))
    opa_url: Optional[str] = field(default_factory=lambda: os.environ.get("CONTROLDB_OPA_URL"))
    enable_dashboard: bool = field(default_factory=lambda: os.environ.get("CONTROLDB_ENABLE_DASHBOARD", "1") == "1")
    engine_backend: str = field(default_factory=lambda: os.environ.get("CONTROLDB_ENGINE_BACKEND", "sqlalchemy"))
    rocksdb_path: str = field(default_factory=lambda: os.environ.get("CONTROLDB_ROCKSDB_PATH", "./controldb_rocksdb"))
    schema_version: str = "2026-05-01"


SETTINGS = Settings()
