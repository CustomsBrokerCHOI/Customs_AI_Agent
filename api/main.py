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
from fastapi.middleware.cors import CORSMiddleware

from api.core.config import settings
from api.routers import auth, classify, health, hs

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

# 프런트(3000) ↔ API(8000) HttpOnly 쿠키 인증을 위해 credentials 허용.
# 와일드카드 origin 은 credentials 와 호환 안 되므로 명시적 리스트 필수.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(classify.router)
app.include_router(hs.router)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "name": "Customs AI Agent",
        "docs": "/docs",
        "health": "/health",
    }
