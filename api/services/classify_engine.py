"""Phase 3-E: 5단계 분류 알고리즘 오케스트레이터.

흐름
----
3-A Input Gate → (follow-up 필요 시 조기 반환) →
3-B Section 결정 + 쿼리 임베딩 + pgvector 검색 →
3-C Verification Gate (chapter 필터; 실패 시 1회 필터 없이 재검색) →
3-D Deep Verify (Top-N 병렬 RAG 검증) →
결과 조립 (verdict 우선순위 정렬, usage 집계, Draft 표시)

설계 결정
---------
- **AsyncSession ↔ sync Session 브리지**: 서비스 계층(pgvector/DB) 이 sync Session
  기반이므로 ``await db.run_sync(fn)`` 으로 래핑. LLM 호출만 async.
- **3-C 1회 retry**: chapter 필터로 verified 가 비면 필터 제거 후 1회 재검색.
  더 이상의 section 재결정 루프는 3-F 연결 후에 도입.
- **Queue 처리**: Top-N=3 만 Deep Verify 하고, mismatch 여도 결과에 포함 (관세사가
  근거 조항을 보고 판단). "불일치 시 Queue 다음 후보 재진입" 은 후속 최적화.
- **Draft 표시**: ``EngineResult.meta["draft"] = True`` + notice 에 Draft 문구.
- **Usage 집계**: Input Gate / determine_sections / verify_candidates 에서 수집.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import settings
from api.services.hs_sections import chapters_from_romans, section_by_roman
from api.services.input_gate import (
    InputGateResult,
    extract_features,
)
from api.services.rag_verify import (
    VerificationVerdict,
    fetch_note_bundle,
    verify_candidate,
)
from api.services.search import (
    HSCandidate,
    SearchResult,
    SectionCandidate,
    _load_hs_master_for_headings,
    aggregate_candidates,
    build_query_text,
    determine_sections,
    embed_text,
    search_cases,
    search_note_chunks,
)
from api.services.verify import verify_search_result

logger = logging.getLogger(__name__)

DEFAULT_TOP_N = settings.top_n_verified
# force_classify=True 시 경합 후보 확대 상한. Input Gate 정보 부족 상태에서
# 관세사가 "모른다" 로 진행하면 더 넓게 비교 후보를 보여준다.
FORCE_CLASSIFY_TOP_N = 5
DEFAULT_HSK_YEAR = 2022
# 사용자에게 제시하는 후보의 하한 confidence (0~1). 30% 미만은 근거가 너무 약해
# 관세사에게 혼란만 주므로 응답에서 제외. 모두 하한 미달이면 빈 후보 + 안내.
MIN_CONFIDENCE = 0.30
DRAFT_NOTICE = (
    "⚠️ 본 결과는 AI 보조 초안(Draft)입니다. 관세사의 최종 확인 없이 세관 신고에 사용할 수 없습니다."
)


# ---- 공개 데이터 구조 ----


@dataclass
class ClassifyInput:
    product_name: str
    description: str
    image_url: str | None = None
    # HS 개정 5년 주기 대응: 분류 시점의 HSK 연도 (ClassifyJob.hsk_year 와 동일).
    # 듀얼 운영 전환 시 라우터가 이 값을 주입.
    hsk_year: int = DEFAULT_HSK_YEAR
    # 관세사가 Input Gate follow-up 을 "모른다" 로 판단. True 면 게이트 우회 +
    # 경합 후보 ``FORCE_CLASSIFY_TOP_N`` 까지 확대.
    force_classify: bool = False
    # 요청별 최소 신뢰도 컷오프 (정수 %, 허용 31~69). None 이면 ``MIN_CONFIDENCE``.
    # 범위 검증은 API 스키마(Pydantic) 에서 수행 — 여기서는 값을 신뢰.
    min_confidence_pct: int | None = None


@dataclass
class CandidateOut:
    rank: int
    hs_code: str | None
    name_kr: str | None
    name_en: str | None
    heading: str
    sub_heading: str
    breadcrumb: list[str]
    confidence: float
    base_tariff_rate: str | None
    verified: bool
    verdict: str  # match / mismatch / uncertain / unverified
    citations: list[dict]


@dataclass
class EngineResult:
    candidates: list[CandidateOut]
    notice: str | None = None
    meta: dict = field(default_factory=dict)


# ---- mock (stub fallback) ----


def mock_result(product_name: str) -> EngineResult:
    """실엔진 연결 전까지 UI·폴링 플로우 검증용 가짜 결과."""
    return EngineResult(
        candidates=[
            CandidateOut(
                rank=1,
                hs_code="8471300000",
                name_kr="휴대용 자동자료처리기계(중량 10kg 이하...)",
                name_en="Portable automatic data processing machines...",
                heading="8471",
                sub_heading="30",
                breadcrumb=[
                    "제16부 기계류·전기기기",
                    "제84류 원자로·보일러·기계류",
                    "제8471호 자동자료처리기계",
                    "8471.30 휴대용",
                ],
                confidence=0.94,
                base_tariff_rate="8",
                verified=True,
                verdict="match",
                citations=[
                    {
                        "source_kind": "heading_note",
                        "heading": "8471",
                        "excerpt": "휴대용 자동자료처리기계란 중량 10kg 이하이며 적어도 CPU, 키보드, 디스플레이를 갖춘 기계를 말한다.",
                    }
                ],
            ),
        ],
        notice=f"[STUB] 실엔진 미연결 — mock 결과 (입력: {product_name[:50]})",
        meta={"engine": "stub", "draft": True},
    )


# ---- 실행 ----


async def run(
    inp: ClassifyInput,
    db: AsyncSession,
    *,
    openai_client: Any = None,
    claude_client: Any = None,
    top_n: int = DEFAULT_TOP_N,
) -> EngineResult:
    """5단계 알고리즘 전체 실행.

    :param db: AsyncSession. 서비스 계층 sync 호출은 ``db.run_sync`` 로 래핑.
    :param openai_client: sync OpenAI. None 이면 settings 로 생성.
    :param claude_client: AsyncAnthropic. None 이면 settings 로 생성.
    """
    logger.info(
        "classify.run start: product=%r force_classify=%s",
        inp.product_name[:60],
        inp.force_classify,
    )
    usage = _UsageAccumulator()
    stages: dict[str, Any] = {}

    # === 3-A Input Gate ===
    ig: InputGateResult = await extract_features(
        inp.product_name, inp.description, inp.image_url, client=claude_client
    )
    usage.add_from_meta(ig.meta)
    stages["input_gate"] = {
        "confidence": ig.features.confidence,
        "needs_more_info": ig.needs_more_info,
        "follow_up_count": len(ig.features.follow_up_questions),
        "bypassed": bool(inp.force_classify and ig.needs_more_info),
    }

    # force_classify=False + needs_more_info=True → 되묻기 루프 반환.
    # force_classify=True → 게이트 우회, 원본 입력 기반 경합 후보 확대(최대 5).
    if ig.needs_more_info and not inp.force_classify:
        return _build_need_info_result(ig, usage, stages)

    features = ig.features
    # force_classify 경로는 Deep Verify 대상을 넓혀 "경합 후보 3~5" UX 를 충족.
    if inp.force_classify:
        top_n = FORCE_CLASSIFY_TOP_N

    # === 3-B part 1: Section 결정 (async Claude) ===
    section_candidates: list[SectionCandidate] = await determine_sections(
        features, client=claude_client
    )
    stages["sections"] = [
        {"roman": s.section_roman, "confidence": s.confidence} for s in section_candidates
    ]

    # === 3-B part 2: query + embed (async OpenAI) ===
    query_text = build_query_text(features)
    if openai_client is None:
        from api.services.llm_client import make_openai_async_client

        openai_client = make_openai_async_client()
    query_vec = await embed_text(openai_client, query_text)
    stages["query"] = {"text": query_text[:200], "dim": len(query_vec)}

    # === 3-B part 3: pgvector + aggregate (sync DB) ===
    chapters = chapters_from_romans([s.section_roman for s in section_candidates])
    # Input Gate 힌트: chapter 는 section 으로 역추적 못하는 보완 (브랜드 지식 기반).
    hint_chapters = set(features.expected_chapter_numbers)
    hint_headings = set(features.expected_headings)
    # 힌트 heading 에서 파생된 chapter 도 필터에 포함 (heading '3304' → chapter 33)
    for h in hint_headings:
        if len(h) >= 2 and h[:2].isdigit():
            hint_chapters.add(int(h[:2]))
    chapters_combined = chapters | hint_chapters
    k = settings.top_k_candidates

    search_result: SearchResult = await db.run_sync(
        _build_sync_search_callable(
            query_vec, chapters_combined, k, section_candidates, query_text,
            hint_headings=hint_headings, hint_chapters=hint_chapters,
        )
    )
    stages["search"] = {
        "note_hits": search_result.meta.get("note_hits", 0),
        "case_hits": search_result.meta.get("case_hits", 0),
        "candidates": len(search_result.hs_candidates),
        "chapters_filter": sorted(chapters_combined) if chapters_combined else None,
        "hint_chapters": sorted(hint_chapters) if hint_chapters else None,
        "hint_headings": sorted(hint_headings) if hint_headings else None,
    }

    # === 3-C Verification Gate ===
    verify_result = verify_search_result(search_result, top_n=top_n * 2)
    stages["verify_gate"] = {
        "verified": len(verify_result.verified),
        "rejected": len(verify_result.rejected),
        "should_re_determine": verify_result.should_re_determine,
    }

    # 3-C 비었으면 chapter 필터 제거 + gate bypass (fail-open):
    # LLM 섹션 예측이 틀린 경우를 가정하고 pgvector 검색 결과를 그대로 사용.
    if verify_result.should_re_determine:
        logger.info("verify_gate empty → retry without filter, bypass gate")
        search_result = await db.run_sync(
            _build_sync_search_callable(
                query_vec, set(), k, section_candidates, query_text,
                hint_headings=hint_headings, hint_chapters=hint_chapters,
            )
        )
        verified_pool: list[HSCandidate] = search_result.hs_candidates[: top_n * 2]
        stages["verify_gate_retry"] = {
            "bypassed": True,
            "candidates": len(verified_pool),
        }
        if not verified_pool:
            return _build_uncertain_result(
                "결정된 부(Section)에 속하는 후보가 없고 필터 제거 후에도 확정 불가.",
                usage,
                stages,
            )
    else:
        verified_pool = verify_result.verified[: top_n * 2]
        if not verified_pool:
            return _build_uncertain_result(
                "검색 결과가 없어 Deep Verify 를 수행할 후보가 없습니다.",
                usage,
                stages,
            )

    # === 3-D Deep Verify (Top-N 병렬) ===
    top_candidates = verified_pool[:top_n]

    bundles = await db.run_sync(_build_sync_bundle_callable(top_candidates, hsk_year=inp.hsk_year))

    tasks = [
        verify_candidate(features, c, b, client=claude_client)
        for c, b in zip(top_candidates, bundles, strict=True)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    verdicts: list[VerificationVerdict] = []
    errors: list[dict] = []
    for cand, res in zip(top_candidates, results, strict=True):
        if isinstance(res, Exception):
            logger.warning("deep verify failed heading=%s: %s", cand.heading, res)
            errors.append({"heading": cand.heading, "message": str(res)[:300]})
        else:
            verdicts.append(res)

    stages["deep_verify"] = {
        "processed": len(top_candidates),
        "success": len(verdicts),
        "match": sum(1 for v in verdicts if v.verdict == "match"),
        "mismatch": sum(1 for v in verdicts if v.verdict == "mismatch"),
        "uncertain": sum(1 for v in verdicts if v.verdict == "uncertain"),
        "errors": errors,
        # 진단용: Deep Verify 에 실제 어떤 heading 이 갔고 각 verdict 가 무엇인지.
        # filter 후 후보 0건 상황에서도 관찰 가능하도록 meta 에 보존.
        "verdicts": [
            {
                "heading": v.candidate_heading,
                "verdict": v.verdict,
                "confidence": v.confidence,
                "reasoning": (v.reasoning or "")[:200],
                "matched": len(v.matched_clauses),
                "conflicting": len(v.conflicting_clauses),
                "unverified": len(v.unverified_citations),
            }
            for v in verdicts
        ],
        "candidate_headings": [c.heading for c in top_candidates],
    }

    # === 결과 조립 ===
    all_candidates = _build_candidate_outs(top_candidates, verdicts)

    # 요청별 신뢰도 컷오프. 미지정 시 엔진 기본값.
    threshold = (
        inp.min_confidence_pct / 100.0
        if inp.min_confidence_pct is not None
        else MIN_CONFIDENCE
    )
    threshold_pct = int(round(threshold * 100))

    # 하한 미만 후보 제거. rank 재부여.
    candidates_out = [c for c in all_candidates if c.confidence >= threshold]
    filtered_below = len(all_candidates) - len(candidates_out)
    for i, c in enumerate(candidates_out, 1):
        c.rank = i

    notice = _build_notice(candidates_out, errors)

    # 전부 하한 미달이면 명시적 안내.
    if all_candidates and not candidates_out:
        notice = (
            f"[유사도 부족] 검토된 {len(all_candidates)}건 중 {threshold_pct}% 이상 "
            f"신뢰 후보가 없습니다. 품명·설명을 보완해 재요청하거나 관세사가 직접 "
            f"근거 조항을 대조하세요. {DRAFT_NOTICE}"
        )

    # force_classify 경로: 원본 질문과 경합 안내를 notice 에 덧붙이고 메타에도 보존.
    follow_ups = list(ig.features.follow_up_questions) if ig.needs_more_info else []
    if inp.force_classify and ig.needs_more_info:
        followup_block = "\n".join(f"- {q}" for q in follow_ups)
        notice = (
            (notice + "\n\n") if notice else ""
        ) + (
            f"⚠️ 관세사 강제 진행(force_classify): Input Gate 가 아래 정보를 "
            f"필요로 했으나 '모름'으로 처리했습니다. 경합 후보 {len(candidates_out)}건을 "
            "근거와 함께 제시합니다. 최종 세번은 관세사가 대조·확정하세요.\n"
            + followup_block
        )

    meta = {
        "engine": "real",
        "draft": True,
        "stages": stages,
        "usage": usage.summary(),
        "force_classify": bool(inp.force_classify),
        "top_n": top_n,
        "min_confidence": threshold,
        "min_confidence_pct": threshold_pct,
        "filtered_below_threshold": filtered_below,
    }
    if follow_ups:
        meta["follow_up_questions"] = follow_ups

    return EngineResult(
        candidates=candidates_out,
        notice=notice,
        meta=meta,
    )


# ---- 동기 DB 콜러블 팩토리 (lambda 캡처 회피) ----


def _build_sync_search_callable(
    query_vec: list[float],
    chapters: set[int],
    k: int,
    section_candidates: list[SectionCandidate],
    query_text: str,
    *,
    hint_headings: set[str] | None = None,
    hint_chapters: set[int] | None = None,
):
    def _inner(session) -> SearchResult:
        filt = chapters or None
        note_hits = search_note_chunks(session, query_vec, k, filt)
        case_hits = search_cases(session, query_vec, k, filt)
        headings = list(
            {h.heading for h in note_hits} | {c.heading for c in case_hits if c.heading}
        )
        hs_master = _load_hs_master_for_headings(session, headings)
        hs_candidates = aggregate_candidates(
            note_hits,
            case_hits,
            hs_master,
            hint_headings=hint_headings,
            hint_chapters=hint_chapters,
        )
        return SearchResult(
            section_candidates=list(section_candidates),
            hs_candidates=hs_candidates,
            query=query_text,
            meta={
                "note_hits": len(note_hits),
                "case_hits": len(case_hits),
                "chapters_filter": sorted(chapters) if chapters else None,
                "hint_headings": sorted(hint_headings) if hint_headings else None,
                "hint_chapters": sorted(hint_chapters) if hint_chapters else None,
            },
        )

    return _inner


def _build_sync_bundle_callable(candidates: list[HSCandidate], hsk_year: int = DEFAULT_HSK_YEAR):
    def _inner(session):
        return [fetch_note_bundle(session, c.heading, hsk_year) for c in candidates]

    return _inner


# ---- 결과 빌더 ----


def _build_need_info_result(
    ig: InputGateResult, usage: _UsageAccumulator, stages: dict
) -> EngineResult:
    questions = ig.features.follow_up_questions
    notice = (
        "정보가 부족해 분류를 진행할 수 없습니다. 다음을 보완해주세요:\n"
        + "\n".join(f"- {q}" for q in questions)
        + (
            "\n\n위 항목을 알 수 없으면 `force_classify=true` 로 재요청하면 "
            "원본 정보만으로 경합 후보 최대 5건을 근거와 함께 제시합니다."
        )
    )
    return EngineResult(
        candidates=[],
        notice=notice,
        meta={
            "engine": "real",
            "draft": True,
            "stopped_at": "input_gate",
            "follow_up_questions": questions,
            "stages": stages,
            "usage": usage.summary(),
        },
    )


def _build_uncertain_result(reason: str, usage: _UsageAccumulator, stages: dict) -> EngineResult:
    return EngineResult(
        candidates=[],
        notice=f"[분류 불확실] {reason} {DRAFT_NOTICE}",
        meta={
            "engine": "real",
            "draft": True,
            "stopped_at": "verify_gate",
            "reason": reason,
            "stages": stages,
            "usage": usage.summary(),
        },
    )


def _build_candidate_outs(
    top_candidates: list[HSCandidate],
    verdicts: list[VerificationVerdict],
) -> list[CandidateOut]:
    """후보 + verdict 을 UI 용 ``CandidateOut`` 으로 변환 후 정렬."""
    verdict_by_heading: dict[str, VerificationVerdict] = {v.candidate_heading: v for v in verdicts}
    outs: list[CandidateOut] = []
    for c in top_candidates:
        v = verdict_by_heading.get(c.heading)
        if v is not None:
            verdict_str = v.verdict
            verified = v.verdict == "match"
            confidence = _combine_confidence(c.score, v.confidence, v.verdict)
            citations = [
                {
                    "source_kind": cite.source_kind,
                    "heading": cite.heading,
                    "excerpt": cite.excerpt,
                }
                for cite in v.matched_clauses
            ]
        else:
            verdict_str = "unverified"
            verified = False
            confidence = c.score * 0.5
            citations = []

        outs.append(
            CandidateOut(
                rank=0,  # 정렬 후 재부여
                hs_code=c.hs_code,
                name_kr=c.name_kr,
                name_en=c.name_en,
                heading=c.heading,
                sub_heading=_sub_heading_of(c.hs_code),
                breadcrumb=_build_breadcrumb(c),
                confidence=confidence,
                base_tariff_rate=None,  # TODO: tariff_rates 조회 연결
                verified=verified,
                verdict=verdict_str,
                citations=citations,
            )
        )

    # match > uncertain > unverified > mismatch, confidence 내림차순
    verdict_order = {"match": 0, "uncertain": 1, "unverified": 2, "mismatch": 3}
    outs.sort(key=lambda o: (verdict_order.get(o.verdict, 9), -o.confidence))
    for i, o in enumerate(outs, 1):
        o.rank = i
    return outs


def _sub_heading_of(hs_code: str | None) -> str:
    if hs_code and len(hs_code) >= 6:
        return hs_code[4:6]
    return ""


def _build_breadcrumb(c: HSCandidate) -> list[str]:
    parts: list[str] = []
    if c.section_roman:
        s = section_by_roman(c.section_roman)
        if s:
            parts.append(f"제{s.roman}부 {s.title_kr}")
    if c.heading:
        try:
            chapter = int(c.heading[:2])
            parts.append(f"제{chapter:02d}류")
        except ValueError:
            pass
        name = (c.name_kr or "").strip()
        parts.append(f"제{c.heading}호{' ' + name if name else ''}")
    sub = _sub_heading_of(c.hs_code)
    if sub:
        parts.append(f"{c.heading}.{sub}")
    return parts


def _combine_confidence(search_score: float, verdict_conf: float, verdict: str) -> float:
    """search_score 와 Deep Verify confidence 를 verdict 별 가중치로 결합."""
    s = max(0.0, min(1.0, search_score))
    v = max(0.0, min(1.0, verdict_conf))
    if verdict == "match":
        return min(1.0, 0.5 * s + 0.5 * v)
    if verdict == "mismatch":
        # 불일치 — confidence 낮게 유지
        return max(0.0, 0.2 * s * (1.0 - v))
    # uncertain / unverified
    return max(0.0, min(1.0, 0.3 * s + 0.2 * v))


def _build_notice(outs: list[CandidateOut], errors: list[dict]) -> str | None:
    prefix = DRAFT_NOTICE
    if not outs:
        return f"[분류 불확실] 후보를 찾지 못했습니다. {prefix}"
    match_count = sum(1 for o in outs if o.verdict == "match")
    if match_count == 0:
        return (
            f"[주의] 명시적으로 일치하는 후보가 없습니다. 원문 근거가 불완전하니 "
            f"관세사가 직접 검토하세요. {prefix}"
        )
    err_msg = ""
    if errors:
        err_msg = f" (Deep Verify 실패 {len(errors)}건은 meta.stages.deep_verify.errors 참조)"
    return f"{prefix}{err_msg}"


# ---- Usage 집계 ----


class _UsageAccumulator:
    """LLM 호출별 input/output 토큰 합산."""

    __slots__ = ("input_tokens", "output_tokens", "calls")

    def __init__(self) -> None:
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.calls: int = 0

    def add_from_meta(self, meta: dict | None) -> None:
        if not meta:
            return
        added = False
        i = meta.get("input_tokens")
        o = meta.get("output_tokens")
        if isinstance(i, int):
            self.input_tokens += i
            added = True
        if isinstance(o, int):
            self.output_tokens += o
            added = True
        if added:
            self.calls += 1

    def summary(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "calls": self.calls,
        }


# ---- 결과 직렬화 헬퍼 (router 에서 사용) ----


def result_to_dict(result: EngineResult) -> dict[str, Any]:
    """``EngineResult`` 를 JSON 직렬화 가능한 dict 로 변환 (ClassifyJob.result 저장용)."""
    return {
        "candidates": [asdict(c) for c in result.candidates],
        "notice": result.notice,
        "meta": result.meta,
    }
