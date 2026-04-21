"""Customs AI Agent FastAPI 진입점.

실행::

    cd /path/to/repo
    uvicorn api.main:app --reload

OpenAPI docs: http://localhost:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.core.config import settings
from api.routers import auth, health

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Customs AI Agent API (env=%s)", settings.env)
    yield
    logger.info("Shutting down")


app = FastAPI(
    title="Customs AI Agent",
    description="HS CODE 자동 품목분류 SaaS (Draft 초안 생성)",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(auth.router)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "name": "Customs AI Agent",
        "docs": "/docs",
        "health": "/health",
    }
