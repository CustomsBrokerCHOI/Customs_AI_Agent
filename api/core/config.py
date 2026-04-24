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
    # Gemini Grounded Search: 물품명만/사진만 입력 시 웹 검색으로 description 보강.
    # 미설정 시 /classify/enrich 는 503 반환하고 분류 본 플로우는 영향 없음.
    gemini_api_key: str | None = None
    # DeepSeek (chat=V3, reasoner=R1). 저비용 고품질 한국어 추론 — Verify 에 적합.
    # 미설정 시 DeepSeek 포함된 체인에서 자동 스킵(그 단계만 다음 폴백 모델로 진행).
    deepseek_api_key: str | None = None
    # Groq (무료 티어 — Llama 3.3 / 4 고속 추론, tool use 지원). 테스트·개발 용.
    # 미설정 시 체인에서 자동 스킵.
    groq_api_key: str | None = None

    # --- 단계별 LLM 모델 (PydanticAI 라우팅) ---
    # pydantic-ai 모델 ID 포맷: "<provider>:<model>"
    #   - anthropic:claude-sonnet-4-6, anthropic:claude-opus-4-6
    #   - google-gla:gemini-2.5-flash, google-gla:gemini-2.5-pro, google-gla:gemini-2.0-flash
    #   - deepseek:deepseek-chat (V3), deepseek:deepseek-reasoner (R1)
    #   - openai:gpt-4o-mini
    # 폴백은 쉼표로 다중 지정 가능 — 앞에서부터 순차 시도(키/쿼터 부재 시 자동 스킵).
    input_gate_model: str = Field(
        "google-gla:gemini-2.5-flash",
        description="Input Gate (물품 특징 추출).",
    )
    input_gate_fallback_model: str | None = Field(
        "google-gla:gemini-2.0-flash,anthropic:claude-sonnet-4-6",
        description="쉼표 구분 폴백 체인. 쿼터가 분리된 2.0-flash 를 1차 폴백으로 두면 Flash 계열 과부하 시 회피 가능.",
    )
    search_model: str = Field(
        "google-gla:gemini-2.5-flash",
        description="Section 결정 (부 후보 선정).",
    )
    search_fallback_model: str | None = Field(
        "google-gla:gemini-2.0-flash,anthropic:claude-sonnet-4-6"
    )
    verify_model: str = Field(
        "anthropic:claude-sonnet-4-6",
        description="Deep Verify (법적 근거 대조). 품질 최우선.",
    )
    verify_fallback_model: str | None = Field(
        "deepseek:deepseek-chat,google-gla:gemini-2.5-pro",
        description="Claude 실패 시 DeepSeek V3 (저비용) → Gemini Pro 순으로 폴백.",
    )

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
