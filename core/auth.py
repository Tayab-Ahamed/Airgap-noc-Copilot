"""Lightweight API-key auth as a FastAPI dependency.

If NOC_API_KEY is unset, auth is disabled (development). In production set the
key via environment / .env. Clients send it in the `X-API-Key` header.
"""
from __future__ import annotations

from fastapi import Header, HTTPException, status

from core.config import settings


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if not settings.api_key:
        return  # auth disabled in dev
    if x_api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
