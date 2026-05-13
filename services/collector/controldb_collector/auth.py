"""API key authentication and RBAC scope check."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select

from .storage import ApiKey, get_db


@dataclass
class Principal:
    key_id: str
    org_id: str
    project_id: str
    scopes: dict


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


async def current_principal(authorization: Optional[str] = Header(default=None)) -> Principal:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid Authorization header")
    db = get_db()
    with db.session() as session:
        row = session.scalars(select(ApiKey).where(ApiKey.api_key_hash == _hash_key(token))).first()
        if row is None or row.revoked_at is not None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
        return Principal(
            key_id=row.key_id,
            org_id=row.org_id,
            project_id=row.project_id,
            scopes=dict(row.scopes or {}),
        )


def require_scope(resource: str, action: str):
    """FastAPI dependency that enforces a scope on the principal."""

    async def _dep(principal: Principal = Depends(current_principal)) -> Principal:
        granted = principal.scopes.get(resource)
        if granted is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"missing scope {resource}:{action}")
        if granted == "write" or granted == action or granted == "*":
            return principal
        if granted == "read" and action == "read":
            return principal
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"scope {resource}:{granted} cannot {action}")

    return _dep
