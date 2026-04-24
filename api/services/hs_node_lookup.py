"""hs_nodes 기반 계층 구조 룩업 + 결정적 배제 사전점검.

5인 회의 설계의 결정적 게이트 구현:
- Deep Verify(LLM) 호출 전에 chapter·section 의 ``exclusion_keywords`` 를 substring
  매칭해 명백한 배제를 먼저 걸러낸다. LLM 비용·레이턴시 절감 + 환각 위험 차단.
- 룩업 결과는 메타에 남겨 감사 로그(법무 요구)에도 기록.

구조:
- ``fetch_section_for_chapter``: chapter 코드(2자리) → section 로마.
- ``fetch_node``: (version, code) → HSNode (cache).
- ``get_exclusion_context_for_heading``: heading(4자리) → 해당 chapter + section 의
  exclusion_keywords 병합 + source_reference 메타.
- ``check_product_excluded``: 제품 특성 문자열 vs exclusion_keywords substring 매칭.
  매치되면 verdict="mismatch" 근거로 쓸 수 있는 정보 반환.

주의:
- exclusion_keywords 는 정규식 자동 파싱이라 일부 잡음 포함 — Sprint B 수동 교정 전까지는
  "확실한 배제" 만 True 로 판정하려면 매치 확신도(키워드 길이·제품 설명 매치 비율) 를 보는
  가드가 필요하다. 현재는 보수적으로 **키워드 길이 ≥ 8자 + 제품 설명 substring 매치** 만
  결정적으로 처리. 그 외는 LLM Deep Verify 로 넘긴다.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db.models import HSNode

logger = logging.getLogger(__name__)

DEFAULT_VERSION = "HSK-2022"

# 자동 파싱 잡음을 고려한 보수적 임계값.
# 너무 짧은 키워드(2~5자)는 우연 매치 위험 커서 결정적 판정에서 제외.
MIN_DETERMINISTIC_KEYWORD_LEN = 8


_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """NFKC 정규화 + 공백 압축 + lowercase. 전각·반각 차이 흡수."""
    if not text:
        return ""
    return _WS_RE.sub(" ", unicodedata.normalize("NFKC", text)).strip().lower()


@dataclass
class ExclusionHit:
    """product vs exclusion_keyword 매치 결과."""

    keyword: str
    source_level: int  # 0 (section) or 2 (chapter)
    source_code: str  # 'XVI' or '84'
    source_reference: str | None  # '제16부 주 1 (가)'


@dataclass
class NodeContext:
    """heading 하나에 대한 section+chapter 메타. Deep Verify 프롬프트 주입용."""

    chapter: HSNode | None
    section: HSNode | None

    @property
    def exclusion_keywords(self) -> list[tuple[int, str, str]]:
        """[(level, code, keyword)] 병합 리스트. chapter 먼저, section 뒤."""
        out: list[tuple[int, str, str]] = []
        if self.chapter and self.chapter.exclusion_keywords:
            for kw in self.chapter.exclusion_keywords:
                out.append((2, self.chapter.code, kw))
        if self.section and self.section.exclusion_keywords:
            for kw in self.section.exclusion_keywords:
                out.append((0, self.section.code, kw))
        return out

    @property
    def essential_character(self) -> str | None:
        """chapter 의 축 우선, 없으면 section."""
        if self.chapter and self.chapter.essential_character:
            return self.chapter.essential_character
        if self.section and self.section.essential_character:
            return self.section.essential_character
        return None


# ---- 조회 헬퍼 ----


def _fetch_node(session: Session, code: str, version: str = DEFAULT_VERSION) -> HSNode | None:
    stmt = select(HSNode).where(HSNode.version == version, HSNode.code == code)
    return session.execute(stmt).scalar_one_or_none()


def fetch_node_context(
    session: Session, heading: str, version: str = DEFAULT_VERSION
) -> NodeContext:
    """heading(4자리) → 소속 chapter + section 노드.

    heading 이 DB 의 hs_nodes 에 없어도 (Sprint A 는 부+류만 적재) chapter 코드는
    heading 의 앞 2자리에서 파생 가능. Section 은 chapter 의 parent_code.
    """
    if not heading or len(heading) < 2 or not heading[:2].isdigit():
        return NodeContext(chapter=None, section=None)

    chapter_code = heading[:2]
    chapter = _fetch_node(session, chapter_code, version)
    section = None
    if chapter and chapter.parent_code:
        section = _fetch_node(session, chapter.parent_code, version)
    return NodeContext(chapter=chapter, section=section)


# ---- 결정적 배제 사전점검 ----


def _product_haystack(*parts: str | None) -> str:
    """제품 특성 문자열을 하나로 합치고 정규화 — substring 매칭 건초더미."""
    return _normalize(" ".join(p or "" for p in parts))


def check_product_excluded(
    ctx: NodeContext,
    *,
    product_name: str,
    description: str = "",
    materials: Iterable[str] | None = None,
    functions: Iterable[str] | None = None,
) -> ExclusionHit | None:
    """제품이 chapter/section 의 exclusion_keywords 에 명시적으로 걸리는지 검사.

    보수적 판정: 키워드가 너무 짧으면(<8자) 결정적 배제로 취급하지 않고 LLM 에 위임.
    **True 반환 = 즉시 mismatch 확정**.

    :returns: 매치 시 ExclusionHit, 없으면 None.
    """
    haystack = _product_haystack(
        product_name,
        description,
        " ".join(materials or []),
        " ".join(functions or []),
    )
    if not haystack:
        return None

    for level, code, keyword in ctx.exclusion_keywords:
        kw_norm = _normalize(keyword)
        if len(kw_norm) < MIN_DETERMINISTIC_KEYWORD_LEN:
            continue
        if kw_norm in haystack:
            logger.info(
                "deterministic exclusion: level=%d code=%s keyword=%r 매치",
                level,
                code,
                keyword[:60],
            )
            source = ctx.chapter if level == 2 else ctx.section
            return ExclusionHit(
                keyword=keyword,
                source_level=level,
                source_code=code,
                source_reference=(source.source_reference if source else None),
            )
    return None


# ---- 캐시 무효화 (테스트용) ----


def clear_caches() -> None:
    """hs_nodes 데이터가 테스트 중 갱신된 경우 호출."""
    pass  # 현재 lru_cache 미사용. 확장 포인트만 유지.
