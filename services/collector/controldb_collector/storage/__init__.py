"""Storage layer for the collector."""

import os

from .models import (
    Base,
    Organization,
    Project,
    AgentRun,
    AuditEvent,
    ApiKey,
    Approval,
    PolicyRecord,
    EvidenceExport,
    DashboardAuditLog,
)
from .db import Database, get_db

__all__ = [
    "Base",
    "Organization",
    "Project",
    "AgentRun",
    "AuditEvent",
    "ApiKey",
    "Approval",
    "PolicyRecord",
    "EvidenceExport",
    "DashboardAuditLog",
    "Database",
    "get_db",
]

if os.environ.get("CONTROLDB_ENGINE_BACKEND", "sqlalchemy") == "rocksdb":
    try:
        from .rocksdb_backend import RocksDBDatabase
        __all__ += ["RocksDBDatabase"]
    except ImportError:
        pass
