"""환경 설정. Pydantic Settings 로 ``.env`` 자동 로드."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """애플리케이션 전역 설정.

    .env 파일에서 자동 로드되며, 환경변수가 최우선.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- 기본 ---
    env: str = Field("development", description="development / staging / production")
    log_level: str = Field("INFO")

    # --- DB ---
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/customs",
        description="PostgreSQL+pgvector 연결. asyncpg 드라이버 사용.",
    )

    # --- 인증 ---
    jwt_secret: str = Field("change-me-in-env", min_length=16)
    jwt_algorithm: str = Field("HS256")
    jwt_access_ttl_seconds: int = Field(3600)
    jwt_refresh_ttl_seconds: int = Field(14 * 24 * 3600)

    # --- LLM ---
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None  # 임베딩용
    huggingface_token: str | None = None  # bge-m3-ko 평가용

    # --- 분류 엔진 ---
    top_k_candidates: int = Field(30, description="pgvector Top-K 후보 수")
    top_n_verified: int = Field(3, description="RAG 검증하여 사용자에 반환할 수")
    classify_timeout_seconds: int = Field(60)

    # --- Rate limit ---
    rate_limit_per_day: int = Field(100, description="관세사당 일일 분류 요청 상한")

    # --- CORS ---
    cors_origins: str = Field(
        default="http://localhost:3000",
        description="허용 origin 쉼표 구분. 프런트 분리 배포 시 production URL 추가.",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
