"""Phase 3-F: LLM 클라이언트 팩토리 + 사용량 로거.

설계 결정
---------
- **SDK 내장 재시도·타임아웃**: Anthropic/OpenAI SDK 가 rate-limit·5xx·네트워크
  오류에 지수 백오프 재시도를 기본 제공. 앱 레벨 wrapper 를 중복 만들지 않고
  ``timeout`` + ``max_retries`` 만 조정.
- **비동기 통일**: 엔진 핫패스 (Input Gate, Section 결정, Deep Verify, 쿼리 임베딩)
  모두 ``AsyncAnthropic`` + ``AsyncOpenAI`` 로 통일.
- **배치 스크립트는 sync 유지**: ``build_embeddings``, ``eval_search`` 등은 기존
  sync ``OpenAI`` 클라이언트 계속 사용 (블록킹 OK, 코드 단순).
- **Usage JSONL**: 분류 1건당 1줄 append ``data/usage/YYYYMMDD.jsonl``. UTC 기준일.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from api.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_ANTHROPIC_TIMEOUT = 60.0
DEFAULT_OPENAI_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3

DEFAULT_USAGE_DIR = Path("data/usage")


# ---- 클라이언트 팩토리 ----


def make_anthropic_client(
    api_key: str | None = None,
    timeout: float = DEFAULT_ANTHROPIC_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> Any:
    """``AsyncAnthropic`` 인스턴스. SDK 가 rate-limit/5xx 재시도 내장."""
    try:
        from anthropic import AsyncAnthropic  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("anthropic 패키지 미설치: pip install 'anthropic>=0.40'") from exc
    key = api_key or settings.anthropic_api_key
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY 미설정 (.env)")
    return AsyncAnthropic(api_key=key, timeout=timeout, max_retries=max_retries)


def make_openai_async_client(
    api_key: str | None = None,
    timeout: float = DEFAULT_OPENAI_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> Any:
    """``AsyncOpenAI`` 인스턴스."""
    try:
        from openai import AsyncOpenAI  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("openai 패키지 미설치: pip install 'openai>=1.50'") from exc
    key = api_key or settings.openai_api_key
    if not key:
        raise RuntimeError("OPENAI_API_KEY 미설정 (.env)")
    return AsyncOpenAI(api_key=key, timeout=timeout, max_retries=max_retries)


# ---- Usage Logger ----


@dataclass
class UsageEvent:
    timestamp: str
    job_id: str | None
    user_id: str | None
    engine: str
    input_tokens: int
    output_tokens: int
    calls: int
    stopped_at: str | None = None
    notice: str | None = None
    stages: dict | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class UsageLogger:
    """데일리 JSONL 로거. 한 줄 = 분류 1건.

    파일 경로: ``{usage_dir}/YYYYMMDD.jsonl`` (UTC 날짜).
    """

    def __init__(self, usage_dir: Path | None = None) -> None:
        self._dir = Path(usage_dir) if usage_dir else DEFAULT_USAGE_DIR

    @property
    def directory(self) -> Path:
        return self._dir

    def _today_path(self, now: datetime | None = None) -> Path:
        now = now or datetime.now(timezone.utc)
        return self._dir / f"{now.strftime('%Y%m%d')}.jsonl"

    def log(self, event: UsageEvent | dict, now: datetime | None = None) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._today_path(now)
        payload = event.as_dict() if isinstance(event, UsageEvent) else dict(event)
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return path

    def log_job(
        self,
        *,
        job_id: str | None,
        user_id: str | None,
        engine_result_meta: dict,
        notice: str | None = None,
    ) -> Path:
        """``EngineResult.meta`` 에서 사용량·단계 정보를 뽑아 기록."""
        usage = engine_result_meta.get("usage") or {}
        event = UsageEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            job_id=job_id,
            user_id=user_id,
            engine=engine_result_meta.get("engine", "unknown"),
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            calls=int(usage.get("calls", 0) or 0),
            stopped_at=engine_result_meta.get("stopped_at"),
            notice=notice,
            stages=engine_result_meta.get("stages"),
        )
        try:
            return self.log(event)
        except OSError as exc:  # 파일시스템 문제는 분류 결과를 깨지 않음
            logger.warning("UsageLogger 기록 실패: %s", exc)
            return self._today_path()
