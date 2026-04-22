"""Phase 3-C: 부-류 일치 검증 (Verification Gate).

3-B 가 낸 후보 HS CODE 의 ``heading`` 앞 2자리(chapter)가 같은 단계에서
결정된 부(Section)의 chapter 집합에 속하는지 확인한다. 불일치 후보는
reject 하고 남은 후보로만 다음 단계(3-D RAG 검증)를 진행한다.

설계 결정
---------
- **순수 함수 + 불변 결과**: 3-C 는 필터 + 시그널만 담당. 재결정/되묻기 루프는
  3-E ClassifyEngine 책임. 여기서 Session/LLM 을 건드리지 않는다.
- **Fail-open 전략**: 섹션 후보가 비어있거나 allowed_chapters 가 공집합이면
  게이트를 스킵하고 전체 후보를 통과시킨다. LLM 실패가 recall 치명상으로
  이어지는 것을 막기 위함.
- **재시도 상한**: ``MAX_SECTION_REDETERMINE_RETRIES`` 상수로 노출. Verifier
  자체는 재시도하지 않지만, orchestrator 가 참조할 값.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from api.services.hs_sections import chapters_from_romans
from api.services.search import HSCandidate, SearchResult

logger = logging.getLogger(__name__)


# 3-E orchestrator 가 루프 상한으로 참조
MAX_SECTION_REDETERMINE_RETRIES = 2


# reject 사유 상수
REJECT_INVALID_HEADING = "invalid_heading"
REJECT_CHAPTER_NOT_IN_SECTIONS = "chapter_not_in_sections"


@dataclass(frozen=True)
class RejectedCandidate:
    candidate: HSCandidate
    reason: str  # REJECT_* 상수


@dataclass
class VerificationResult:
    verified: list[HSCandidate]
    rejected: list[RejectedCandidate]
    allowed_chapters: frozenset[int]
    should_re_determine: bool
    meta: dict = field(default_factory=dict)


# ---- 단위 판정 ----


def verify_heading(heading: str, allowed_chapters: frozenset[int] | set[int]) -> tuple[bool, str]:
    """heading 4자리가 ``allowed_chapters`` 에 속하는지 판정.

    :returns: ``(ok, reason)``. ``ok=True`` 이면 ``reason`` 은 빈 문자열.
    """
    if not heading or len(heading) < 2:
        return False, REJECT_INVALID_HEADING
    try:
        chapter = int(heading[:2])
    except ValueError:
        return False, REJECT_INVALID_HEADING
    if chapter not in allowed_chapters:
        return False, REJECT_CHAPTER_NOT_IN_SECTIONS
    return True, ""


def partition_candidates(
    candidates: list[HSCandidate],
    allowed_chapters: frozenset[int] | set[int],
) -> tuple[list[HSCandidate], list[RejectedCandidate]]:
    """후보 리스트를 ``(verified, rejected)`` 로 분할. 순서는 입력 순서 유지."""
    verified: list[HSCandidate] = []
    rejected: list[RejectedCandidate] = []
    for c in candidates:
        ok, reason = verify_heading(c.heading, allowed_chapters)
        if ok:
            verified.append(c)
        else:
            rejected.append(RejectedCandidate(candidate=c, reason=reason))
    return verified, rejected


# ---- SearchResult 연계 ----


def verify_search_result(
    sr: SearchResult,
    *,
    top_n: int | None = None,
) -> VerificationResult:
    """3-B ``SearchResult`` 에 Verification Gate 적용.

    :param top_n: verified 상위 N 건만 남김. ``None`` 이면 전체 유지.
    :return: ``VerificationResult`` — ``verified`` 가 비면 ``should_re_determine=True``.

    섹션 후보가 없거나 allowed_chapters 가 공집합이면 **fail-open** 으로
    모든 후보를 통과시키고 ``should_re_determine=False``.
    """
    romans = [s.section_roman for s in sr.section_candidates]
    allowed = frozenset(chapters_from_romans(romans))

    if not allowed:
        verified = list(sr.hs_candidates)
        if top_n is not None:
            verified = verified[:top_n]
        logger.info(
            "verify_search_result: fail-open (allowed_chapters 공집합) verified=%d",
            len(verified),
        )
        return VerificationResult(
            verified=verified,
            rejected=[],
            allowed_chapters=allowed,
            should_re_determine=False,
            meta={"skipped": True, "reason": "no_allowed_chapters"},
        )

    verified, rejected = partition_candidates(sr.hs_candidates, allowed)
    if top_n is not None:
        verified = verified[:top_n]

    should_redetermine = len(verified) == 0 and len(sr.hs_candidates) > 0

    logger.info(
        "verify_search_result: allowed=%s verified=%d rejected=%d redet=%s",
        sorted(allowed),
        len(verified),
        len(rejected),
        should_redetermine,
    )

    return VerificationResult(
        verified=verified,
        rejected=rejected,
        allowed_chapters=allowed,
        should_re_determine=should_redetermine,
        meta={
            "verified_count": len(verified),
            "rejected_count": len(rejected),
            "skipped": False,
        },
    )


# ---- 오케스트레이터 힌트 ----


def summarize_rejected(rejected: list[RejectedCandidate]) -> dict[str, int]:
    """reject 사유별 카운트. 로그·디버깅용."""
    out: dict[str, int] = {}
    for r in rejected:
        out[r.reason] = out.get(r.reason, 0) + 1
    return out


def should_ask_user_more_info(
    attempt: int,
    result: VerificationResult,
    max_retries: int = MAX_SECTION_REDETERMINE_RETRIES,
) -> bool:
    """재시도 상한 넘어서도 verified 가 없으면 관세사에게 되물어야 함.

    3-E orchestrator 가 루프 종료 판정에 사용.
    """
    return result.should_re_determine and attempt >= max_retries
