"""PydanticAI 기반 단계별 LLM 에이전트 팩토리.

설계 결정
---------
- **단계별 모델 분리**: ``settings.<stage>_model`` + ``<stage>_fallback_model`` 로 Input
  Gate · Section · Deep Verify 가 각각 다른 프로바이더를 사용할 수 있다. Gemini Flash
  로 구조화 추출, Claude 로 법적 근거 대조 하는 식의 하이브리드 전략이 기본.
- **프로바이더 폴백**: ``FallbackModel`` 로 감싸 프라이머리 실패(크레딧 부족, rate
  limit, 5xx) 시 지정된 대체 모델이 자동 시도된다.
- **구조화 출력 강제**: Agent 의 ``output_type`` 으로 Pydantic 모델을 전달 — pydantic-ai
  가 프로바이더별 tool/function-calling 혹은 JSON 모드를 자동 선택. 검증 실패 시
  ``retries`` 횟수만큼 에러 메시지와 함께 재프롬프트.
- **테스트 친화**: ``Agent.override(model=TestModel(...))`` 로 네트워크 없이 단위 테스트
  가능. 프로덕션 함수가 모듈 레벨 factory (``lru_cache``) 로 Agent 를 얻고, 테스트는
  그 Agent 에 override 를 걸어 주입.

관측성
------
``extract_usage(result)`` 로 ``AgentRunResult`` 의 토큰·모델 요약을 ``StageUsage`` 로
정규화. 기존 ``UsageLogger`` 가 이 값을 소비.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.fallback import FallbackModel

from api.core.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_AGENT_RETRIES = 2
# 프로바이더 transient 오류 재시도. Gemini 과부하(503) 가 수 분 지속되는 사례가 있어
# 백오프를 길게 잡아 살아남는 확률을 높임. 분류 전체 타임아웃(settings.classify_timeout_seconds)
# 내에서 의미있게 끝나도록 N·간격 균형 조정.
TRANSIENT_RETRIES = 3
TRANSIENT_BACKOFF_S = 3.0  # 1st: 3s, 2nd: 6s, 3rd: 12s


def _ensure_provider_env() -> None:
    """pydantic-ai 프로바이더가 요구하는 환경변수를 Settings 에서 주입.

    pydantic-ai 는 모델 ID 로부터 프로바이더를 결정하고 ``GEMINI_API_KEY`` /
    ``ANTHROPIC_API_KEY`` / ``DEEPSEEK_API_KEY`` / ``OPENAI_API_KEY`` 를 환경에서
    읽는다. FastAPI 프로세스의 ``settings`` 에는 채워져 있더라도 ``os.environ`` 에
    없으면 프로바이더가 인식 못 함.
    """
    if settings.gemini_api_key and not os.environ.get("GEMINI_API_KEY"):
        os.environ["GEMINI_API_KEY"] = settings.gemini_api_key
        os.environ.setdefault("GOOGLE_API_KEY", settings.gemini_api_key)
    if settings.anthropic_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
    if settings.deepseek_api_key and not os.environ.get("DEEPSEEK_API_KEY"):
        os.environ["DEEPSEEK_API_KEY"] = settings.deepseek_api_key
    if settings.openai_api_key and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = settings.openai_api_key
    if settings.groq_api_key and not os.environ.get("GROQ_API_KEY"):
        os.environ["GROQ_API_KEY"] = settings.groq_api_key


def _provider_of(model_id: str) -> str:
    """모델 ID 에서 프로바이더 prefix 추출 (``"anthropic:claude-..."`` → ``"anthropic"``)."""
    return model_id.split(":", 1)[0] if ":" in model_id else model_id


def _has_credentials_for(provider: str) -> bool:
    """해당 프로바이더가 요구하는 키가 settings 에 있는지 — 없으면 체인에서 스킵."""
    if provider in ("google-gla", "google-vertex"):
        return bool(settings.gemini_api_key)
    if provider == "anthropic":
        return bool(settings.anthropic_api_key)
    if provider == "deepseek":
        return bool(settings.deepseek_api_key)
    if provider == "openai":
        return bool(settings.openai_api_key)
    if provider == "groq":
        return bool(settings.groq_api_key)
    # 알 수 없는 프로바이더는 일단 포함 (pydantic-ai 자체 키 탐지에 맡김)
    return True


def _filter_available(model_ids: list[str]) -> list[str]:
    """키 없는 프로바이더의 모델을 체인에서 제거. 전부 빠지면 원본 유지(의미있는 에러 유도)."""
    available = [m for m in model_ids if _has_credentials_for(_provider_of(m))]
    if not available:
        logger.warning(
            "모든 체인 모델의 키가 없음. 원본 유지: %s (에러는 pydantic-ai 로 전파)",
            model_ids,
        )
        return model_ids
    if available != model_ids:
        skipped = [m for m in model_ids if m not in available]
        logger.info("키 부재로 체인에서 스킵: %s → 사용 체인: %s", skipped, available)
    return available


def _parse_chain(fallback_csv: str | None) -> list[str]:
    if not fallback_csv:
        return []
    return [s.strip() for s in fallback_csv.split(",") if s.strip()]


def _resolve_model(primary_id: str, fallback_csv: str | None) -> Any:
    """체인(primary + 쉼표구분 fallback N개) → 단일 ID 또는 FallbackModel.

    키가 없는 프로바이더는 자동으로 체인에서 제거. 체인에 1개만 남으면 FallbackModel 로
    감싸지 않고 단일 모델 ID 반환.
    """
    _ensure_provider_env()  # 프로바이더 생성자가 env 를 읽을 때 실패하지 않도록 선주입
    chain = [primary_id] + _parse_chain(fallback_csv)
    # 중복 제거(순서 유지)
    seen: set[str] = set()
    uniq: list[str] = []
    for m in chain:
        if m not in seen:
            seen.add(m)
            uniq.append(m)
    uniq = _filter_available(uniq)
    if len(uniq) == 1:
        return uniq[0]
    return FallbackModel(*uniq)


def build_stage_agent(
    *,
    stage: str,
    output_type: type[T],
    system_prompt: str,
    max_tokens: int = 2048,
    retries: int = DEFAULT_AGENT_RETRIES,
) -> Agent[None, T]:
    """단계별 Agent 생성.

    :param stage: ``"input_gate"`` / ``"search"`` / ``"verify"`` — settings 에서
        ``{stage}_model`` / ``{stage}_fallback_model`` 을 읽는다.
    :param output_type: Pydantic 모델. Agent 는 모델이 이 스키마로 구조화 응답하도록
        유도하고 반환값을 자동 검증.
    :param system_prompt: 로드된 시스템 프롬프트 문자열 (파일 → str).
    :param max_tokens: 응답 토큰 상한.
    :param retries: 구조화 검증 실패 시 재프롬프트 횟수 (기본 2).
    """
    _ensure_provider_env()
    primary = getattr(settings, f"{stage}_model")
    fallback = getattr(settings, f"{stage}_fallback_model", None)
    model = _resolve_model(primary, fallback)
    agent: Agent[None, T] = Agent(
        model,
        output_type=output_type,
        system_prompt=system_prompt,
        retries=retries,
        model_settings={"max_tokens": max_tokens},
    )
    logger.info(
        "Agent built stage=%s primary=%s fallback=%s output=%s",
        stage,
        primary,
        fallback,
        output_type.__name__,
    )
    return agent


@dataclass
class StageUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    model_used: str | None = None


def _is_transient_error(exc: BaseException) -> bool:
    """503/429/overloaded 류의 일시적 오류인지 판정.

    ``ExceptionGroup`` (FallbackExceptionGroup 포함) 인 경우 하위 예외 중 하나라도
    일시적이면 True — 재시도하면 그 프로바이더가 회복해 성공할 여지가 있다.
    """
    subs = getattr(exc, "exceptions", None)
    if subs:
        return any(_is_transient_error(e) for e in subs)

    s = str(exc).lower()
    # 영구 오류는 재시도 안 함
    if "credit balance" in s or "credit_balance" in s:
        return False
    if "insufficient_quota" in s or "invalid_api_key" in s:
        return False
    # 일시적 시그널
    if "status_code: 503" in s or "status_code: 429" in s:
        return True
    if "error code: 529" in s or "overloaded_error" in s:
        return True
    if "rate_limit_error" in s or "error code: 429" in s:
        return True
    if "unavailable" in s and "currently" in s:
        return True
    return False


async def run_agent_with_retry(
    agent: Agent[None, T],
    user_prompt: Any,
    *,
    max_retries: int = TRANSIENT_RETRIES,
    backoff_s: float = TRANSIENT_BACKOFF_S,
) -> Any:
    """Agent.run 을 transient 오류에 한해 지수 백오프로 재시도.

    Gemini 503 (high demand) 는 보통 몇 초 내 회복되므로 이 얇은 재시도만 있어도
    FallbackModel 체인이 불필요하게 모두 소진되는 사례를 크게 줄인다. 영구 오류
    (크레딧·API 키 등) 는 즉시 throw — 재시도 낭비 방지.
    """
    last: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            return await agent.run(user_prompt)
        except BaseException as exc:  # noqa: BLE001
            if not _is_transient_error(exc):
                raise
            last = exc
            if attempt >= max_retries:
                break
            delay = backoff_s * (2**attempt)
            logger.warning(
                "transient LLM error (attempt %d/%d, retry in %.1fs): %s",
                attempt + 1,
                max_retries + 1,
                delay,
                type(exc).__name__,
            )
            await asyncio.sleep(delay)
    assert last is not None
    raise last


def extract_usage(result: Any) -> StageUsage:
    """``AgentRunResult.usage()`` 에서 토큰·모델 정보를 정규화.

    pydantic-ai 1.x 의 ``Usage`` 객체는 ``request_tokens`` / ``response_tokens`` 를
    노출. FallbackModel 의 경우 실제 사용된 모델은 ``result.all_messages()`` 의 마지막
    ModelResponse 의 ``model_name`` 에 저장됨.
    """
    try:
        usage = result.usage()
        # pydantic-ai 1.x: input_tokens / output_tokens (레거시 request_/response_ 병존)
        in_t = int(getattr(usage, "input_tokens", 0) or 0)
        out_t = int(getattr(usage, "output_tokens", 0) or 0)
    except Exception:  # noqa: BLE001
        in_t, out_t = 0, 0

    model_used: str | None = None
    try:
        messages = result.all_messages()
        for msg in reversed(messages):
            mn = getattr(msg, "model_name", None)
            if mn:
                model_used = str(mn)
                break
    except Exception:  # noqa: BLE001
        pass

    return StageUsage(input_tokens=in_t, output_tokens=out_t, model_used=model_used)
