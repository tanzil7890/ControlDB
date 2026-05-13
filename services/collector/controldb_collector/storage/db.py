"""Database engine + session helpers."""

from __future__ import annotations

import hashlib
import os
import threading
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import SETTINGS
from .models import ApiKey, Base, Organization, Project


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class Database:
    _instance: Optional["Database"] = None
    _lock = threading.Lock()

    def __init__(self, url: str):
        kwargs = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        self.engine = create_engine(url, future=True, **kwargs)
        self.SessionLocal = sessionmaker(self.engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(self.engine)
        self._bootstrap()

    @classmethod
    def instance(cls, url: Optional[str] = None) -> "Database":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(url or SETTINGS.database_url)
            return cls._instance

    @classmethod
    def reset(cls, url: Optional[str] = None) -> "Database":
        """Force a fresh database (used by tests)."""
        with cls._lock:
            cls._instance = cls(url or SETTINGS.database_url)
            return cls._instance

    def _bootstrap(self) -> None:
        with self.SessionLocal() as session:
            org = session.get(Organization, SETTINGS.bootstrap_org_id)
            if not org:
                session.add(Organization(org_id=SETTINGS.bootstrap_org_id, name="Default Org"))
            project = session.get(Project, SETTINGS.bootstrap_project_id)
            if not project:
                session.add(Project(project_id=SETTINGS.bootstrap_project_id, org_id=SETTINGS.bootstrap_org_id, name="Default Project"))
            existing_keys = {row.api_key_hash for row in session.query(ApiKey).all()}
            for raw_key in SETTINGS.bootstrap_api_keys:
                key_hash = _sha256(raw_key)
                if key_hash in existing_keys:
                    continue
                session.add(
                    ApiKey(
                        key_id=f"key_{key_hash[:12]}",
                        api_key_hash=key_hash,
                        org_id=SETTINGS.bootstrap_org_id,
                        project_id=SETTINGS.bootstrap_project_id,
                        name="bootstrap",
                        scopes={"runs": "write", "events": "write", "policies": "read", "approvals": "review", "exports": "create"},
                    )
                )
            session.commit()

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def get_db() -> Database:
    return Database.instance()
