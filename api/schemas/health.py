"""Health check 응답 스키마."""

from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    env: str
    db: str  # "ok" / "down" / "unknown"
    version: str = "0.1.0"
