"""Phase 3-E ClassifyEngine orchestrator 단위 테스트 (모든 외부 의존성 모킹)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.services.classify_engine import (
    DRAFT_NOTICE,
    CandidateOut,
    ClassifyInput,
    _build_breadcrumb,
    _build_candidate_outs,
    _build_notice,
    _combine_confidence,
    _sub_heading_of,
    _UsageAccumulator,
    mock_result,
    result_to_dict,
    run,
)
from api.services.input_gate import InputGateResult, ProductFeatures
from api.services.rag_verify import CitationRef, VerificationVerdict
from api.services.search import HSCandidate, SearchResult, SectionCandidate

# ---- 순수 함수 ----


def test_sub_heading_of_extracts_digits_4_5() -> None:
    assert _sub_heading_of("8471300000") == "30"
    assert _sub_heading_of("84713") == ""  # too short
    assert _sub_heading_of(None) == ""


def test_combine_confidence_match_averages_sources() -> None:
    c = _combine_confidence(0.8, 0.6, "match")
    assert c == pytest.approx(0.7)  # 0.5*0.8 + 0.5*0.6


def test_combine_confidence_mismatch_stays_low() -> None:
    c = _combine_confidence(0.9, 0.9, "mismatch")
    assert 0.0 <= c <= 0.1


def test_combine_confidence_uncertain_modest() -> None:
    c = _combine_confidence(1.0, 1.0, "uncertain")
    # 0.3 * 1.0 + 0.2 * 1.0 = 0.5
    assert c == pytest.approx(0.5)


def test_combine_confidence_clips_range() -> None:
    # score > 1 / confidence > 1 등 비정상 입력도 [0,1] 로 클리핑
    assert 0.0 <= _combine_confidence(2.0, 2.0, "match") <= 1.0
    assert 0.0 <= _combine_confidence(-1.0, -1.0, "match") <= 1.0


def test_build_breadcrumb_includes_section_chapter_heading_subheading() -> None:
    c = HSCandidate(
        heading="8471",
        hs_code="8471300000",
        name_kr="휴대용 자동자료처리기계",
        score=0.9,
        section_roman="XVI",
    )
    crumbs = _build_breadcrumb(c)
    assert any("제XVI부" in cr for cr in crumbs)
    # 아라비아 숫자 병기 — 로마숫자에 익숙하지 않은 실무 사용자 배려
    assert any("(제16부)" in cr for cr in crumbs)
    assert any("제84류" in cr for cr in crumbs)
    assert any("제8471호" in cr for cr in crumbs)
    assert any("8471.30" in cr for cr in crumbs)


def test_build_breadcrumb_without_hs_code_skips_subheading() -> None:
    c = HSCandidate(heading="6109", score=0.5, section_roman="XI")
    crumbs = _build_breadcrumb(c)
    assert not any("." in cr for cr in crumbs)  # 서브헤딩 없음


# ---- _UsageAccumulator ----


def test_usage_accumulator_sums() -> None:
    u = _UsageAccumulator()
    u.add_from_meta({"input_tokens": 100, "output_tokens": 50})
    u.add_from_meta({"input_tokens": 200, "output_tokens": 80})
    u.add_from_meta(None)
    u.add_from_meta({})
    s = u.summary()
    assert s == {"input_tokens": 300, "output_tokens": 130, "calls": 2}


def test_usage_accumulator_ignores_non_int() -> None:
    u = _UsageAccumulator()
    u.add_from_meta({"input_tokens": "bad", "output_tokens": None})
    assert u.summary() == {"input_tokens": 0, "output_tokens": 0, "calls": 0}


# ---- _build_candidate_outs ----


def _mk_candidate(heading: str, score: float = 0.8, section: str = "XVI") -> HSCandidate:
    return HSCandidate(
        heading=heading,
        hs_code=f"{heading}000000",
        name_kr="name",
        name_en="name",
        score=score,
        section_roman=section,
    )


def _mk_verdict(heading: str, verdict: str, conf: float) -> VerificationVerdict:
    return VerificationVerdict(
        candidate_heading=heading,
        verdict=verdict,
        confidence=conf,
        matched_clauses=(
            [CitationRef(source_kind="heading_note", heading=heading, excerpt="ex")]
            if verdict == "match"
            else []
        ),
        reasoning="r",
    )


def test_build_candidate_outs_sorts_match_before_mismatch() -> None:
    cands = [
        _mk_candidate("6109", 0.9),
        _mk_candidate("8471", 0.7),
    ]
    verdicts = [
        _mk_verdict("6109", "mismatch", 0.8),
        _mk_verdict("8471", "match", 0.9),
    ]
    outs = _build_candidate_outs(cands, verdicts)
    assert outs[0].heading == "8471"
    assert outs[0].verdict == "match"
    assert outs[0].rank == 1
    assert outs[1].heading == "6109"
    assert outs[1].verdict == "mismatch"


def test_build_candidate_outs_missing_verdict_becomes_unverified() -> None:
    cands = [_mk_candidate("8471", 0.8)]
    outs = _build_candidate_outs(cands, [])
    assert outs[0].verdict == "unverified"
    assert outs[0].verified is False
    # unverified 는 search score * 0.5
    assert outs[0].confidence == pytest.approx(0.4)


def test_build_candidate_outs_match_emits_citations() -> None:
    cands = [_mk_candidate("8471", 0.8)]
    verdicts = [_mk_verdict("8471", "match", 0.9)]
    outs = _build_candidate_outs(cands, verdicts)
    assert len(outs[0].citations) == 1
    assert outs[0].citations[0]["source_kind"] == "heading_note"


# ---- _build_notice ----


def test_build_notice_returns_draft_when_match_present() -> None:
    outs = [
        CandidateOut(
            rank=1,
            hs_code="1",
            name_kr=None,
            name_en=None,
            heading="8471",
            sub_heading="",
            breadcrumb=[],
            confidence=0.9,
            base_tariff_rate=None,
            verified=True,
            verdict="match",
            citations=[],
        )
    ]
    notice = _build_notice(outs, [])
    assert notice is not None
    assert DRAFT_NOTICE in notice


def test_build_notice_warns_when_no_match() -> None:
    outs = [
        CandidateOut(
            rank=1,
            hs_code="1",
            name_kr=None,
            name_en=None,
            heading="8471",
            sub_heading="",
            breadcrumb=[],
            confidence=0.4,
            base_tariff_rate=None,
            verified=False,
            verdict="uncertain",
            citations=[],
        )
    ]
    notice = _build_notice(outs, [])
    assert "주의" in notice
    assert DRAFT_NOTICE in notice


def test_build_notice_empty_candidates_uncertain() -> None:
    notice = _build_notice([], [])
    assert "불확실" in notice


def test_build_notice_reports_deep_verify_errors() -> None:
    outs = [
        CandidateOut(
            rank=1,
            hs_code="1",
            name_kr=None,
            name_en=None,
            heading="8471",
            sub_heading="",
            breadcrumb=[],
            confidence=0.9,
            base_tariff_rate=None,
            verified=True,
            verdict="match",
            citations=[],
        )
    ]
    notice = _build_notice(outs, [{"heading": "6109", "message": "boom"}])
    assert "실패 1건" in notice


# ---- result_to_dict ----


def test_result_to_dict_is_json_friendly() -> None:
    r = mock_result("노트북")
    d = result_to_dict(r)
    import json

    # 직렬화 가능해야 함
    dumped = json.dumps(d, ensure_ascii=False)
    parsed = json.loads(dumped)
    assert parsed["candidates"][0]["heading"] == "8471"
    assert parsed["meta"]["engine"] == "stub"


# ---- run() 통합 (모든 의존성 모킹) ----


def _input_gate_result(
    needs_more_info: bool = False,
    follow_ups: list[str] | None = None,
) -> InputGateResult:
    features = ProductFeatures(
        product_name_normalized="노트북",
        materials=["알루미늄"],
        functions=["연산"],
        confidence=0.85,
        follow_up_questions=follow_ups or [],
    )
    return InputGateResult(
        features=features,
        needs_more_info=needs_more_info,
        meta={"model": "claude-sonnet-4-6", "input_tokens": 50, "output_tokens": 20},
    )


class _FakeAsyncSession:
    """AsyncSession 대체 — run_sync 가 제공 callable 을 바로 호출."""

    def __init__(self, sync_session: MagicMock | None = None):
        self._sync = sync_session or MagicMock()

    async def run_sync(self, fn, *args, **kwargs):
        return fn(self._sync, *args, **kwargs)


@pytest.fixture
def fake_openai_client():
    # AsyncOpenAI 시뮬레이션 — embeddings.create 가 coroutine.
    client = MagicMock()
    resp = SimpleNamespace(data=[SimpleNamespace(embedding=[0.1] * 1536)])
    client.embeddings.create = AsyncMock(return_value=resp)
    return client


@pytest.mark.asyncio
async def test_run_returns_follow_up_when_input_gate_needs_info(
    monkeypatch, fake_openai_client
) -> None:
    from api.services import classify_engine as ce

    async def fake_extract(*a, **kw):
        return _input_gate_result(needs_more_info=True, follow_ups=["재질은?", "용도는?"])

    monkeypatch.setattr(ce, "extract_features", fake_extract)

    result = await run(
        ClassifyInput(product_name="x", description="y"),
        _FakeAsyncSession(),
        openai_client=fake_openai_client,
        claude_client=AsyncMock(),
    )

    assert result.candidates == []
    assert "재질은?" in result.notice
    assert result.meta["stopped_at"] == "input_gate"
    assert result.meta["follow_up_questions"] == ["재질은?", "용도는?"]
    assert result.meta["draft"] is True


@pytest.mark.asyncio
async def test_force_classify_bypasses_input_gate_and_uses_default_top_n(
    monkeypatch, fake_openai_client
) -> None:
    """Input Gate 가 follow-up 을 요구해도 force_classify=True 면 게이트 우회.

    - 후보는 기본 DEFAULT_TOP_N(3) 유지 (토큰·시간 절약 목적으로 확대 미시행).
      대신 aggregate 단계에서 hint_headings 강제 주입으로 정답 누락 대응.
    - follow_up_questions 가 meta 에 보존되어 UI 에 노출 가능
    - notice 에 "관세사 강제 진행" 안내 포함
    """
    from api.services import classify_engine as ce

    async def fake_extract(*a, **kw):
        # follow_up 은 HS 분기 질문(게이트 통과). '원산지' 같은 행정 질문은 Input Gate
        # 키워드 필터에 걸려 드랍되므로 테스트에 부적합.
        return _input_gate_result(
            needs_more_info=True,
            follow_ups=["중량 10kg 이하 여부?", "완제품·부분품 여부?"],
        )

    async def fake_determine_sections(features, *, client=None, **kw):
        return [SectionCandidate(section_roman="XVI", confidence=0.9, reasoning="r")]

    # 5개 후보 제공 (top_n 확대 확인용)
    hs_cands = [
        HSCandidate(heading=f"847{i}", hs_code=f"847{i}000000", score=0.9 - 0.05 * i,
                    name_kr=f"품명{i}", section_roman="XVI")
        for i in range(6)
    ]
    fake_search = SearchResult(
        section_candidates=[SectionCandidate(section_roman="XVI", confidence=0.9, reasoning="r")],
        hs_candidates=hs_cands,
        query="q",
        meta={"note_hits": 10, "case_hits": 2},
    )

    async def fake_verify_candidate(features, candidate, bundle, *, client=None, **kw):
        return _mk_verdict(candidate.heading, "match", 0.8)

    def fake_fetch_note_bundle(session, heading, hsk_year=2022):
        from api.services.rag_verify import NoteBundle

        return NoteBundle(
            heading=heading, hsk_year=hsk_year,
            notes={("heading_note", "ko"): "설명"},
        )

    monkeypatch.setattr(ce, "extract_features", fake_extract)
    monkeypatch.setattr(ce, "determine_sections", fake_determine_sections)
    monkeypatch.setattr(ce, "verify_candidate", fake_verify_candidate)
    monkeypatch.setattr(ce, "fetch_note_bundle", fake_fetch_note_bundle)
    monkeypatch.setattr(
        ce, "_build_sync_search_callable", lambda *a, **kw: lambda _s: fake_search
    )

    result = await run(
        ClassifyInput(product_name="x", description="y", force_classify=True),
        _FakeAsyncSession(),
        openai_client=fake_openai_client,
        claude_client=AsyncMock(),
    )

    # 게이트 우회: 후보가 비어있지 않아야 함
    assert result.candidates, "force_classify=True 인데 후보 0건"
    # 기본 Top-N(3) 유지
    assert len(result.candidates) == 3
    # meta 에 flag + 질문 보존
    assert result.meta["force_classify"] is True
    assert result.meta["top_n"] == 3
    assert result.meta["follow_up_questions"] == [
        "중량 10kg 이하 여부?",
        "완제품·부분품 여부?",
    ]
    # stage 에 bypass 표시
    assert result.meta["stages"]["input_gate"]["bypassed"] is True
    # notice 에 안내 + 질문 목록 포함
    assert "force_classify" in result.notice or "강제 진행" in result.notice
    assert "중량 10kg 이하 여부?" in result.notice


@pytest.mark.asyncio
async def test_low_confidence_candidates_shown_as_review_fallback(
    monkeypatch, fake_openai_client
) -> None:
    """전부 mismatch (confidence≈0) 여도 fallback 으로 상위 후보 노출.

    빈 화면 대신 관세사가 mismatch reasoning 을 직접 대조해 판단할 수 있도록
    ``FALLBACK_REVIEW_COUNT`` 만큼 노출하고 notice 로 상황 안내.
    """
    from api.services import classify_engine as ce

    async def fake_extract(*a, **kw):
        return _input_gate_result()

    async def fake_determine_sections(features, *, client=None, **kw):
        return [SectionCandidate(section_roman="XVI", confidence=0.9, reasoning="r")]

    fake_search = SearchResult(
        section_candidates=[SectionCandidate(section_roman="XVI", confidence=0.9, reasoning="r")],
        hs_candidates=[
            HSCandidate(heading="8471", hs_code="8471300000", score=0.8,
                        name_kr="NB", section_roman="XVI"),
            HSCandidate(heading="8472", hs_code="8472000000", score=0.7,
                        name_kr="기타", section_roman="XVI"),
        ],
        query="q",
        meta={"note_hits": 5, "case_hits": 0},
    )

    async def fake_verify_mismatch(features, candidate, bundle, *, client=None, **kw):
        # mismatch 는 _combine_confidence 가 0.2*s*(1-v) 로 매우 낮게 계산
        return _mk_verdict(candidate.heading, "mismatch", 0.9)

    def fake_bundle(session, heading, hsk_year=2022):
        from api.services.rag_verify import NoteBundle

        return NoteBundle(heading=heading, hsk_year=hsk_year,
                          notes={("heading_note", "ko"): "n"})

    monkeypatch.setattr(ce, "extract_features", fake_extract)
    monkeypatch.setattr(ce, "determine_sections", fake_determine_sections)
    monkeypatch.setattr(ce, "verify_candidate", fake_verify_mismatch)
    monkeypatch.setattr(ce, "fetch_note_bundle", fake_bundle)
    monkeypatch.setattr(
        ce, "_build_sync_search_callable", lambda *a, **kw: lambda _s: fake_search
    )

    result = await run(
        ClassifyInput(product_name="x", description="y"),
        _FakeAsyncSession(),
        openai_client=fake_openai_client,
        claude_client=AsyncMock(),
        top_n=2,
    )

    # 둘 다 mismatch 지만 fallback 경로로 노출. 빈 결과는 아님.
    from api.services.classify_engine import FALLBACK_REVIEW_COUNT

    assert len(result.candidates) == FALLBACK_REVIEW_COUNT
    # 모든 후보가 임계값 미만인 상태였음이 메타에 기록
    assert result.meta["min_confidence"] == 0.30
    assert "유사도 부족" in result.notice
    assert "근거 검토용" in result.notice
    # 모든 후보가 mismatch verdict 로 유지됨
    assert all(c.verdict == "mismatch" for c in result.candidates)


@pytest.mark.asyncio
async def test_user_provided_min_confidence_pct_overrides_default(
    monkeypatch, fake_openai_client
) -> None:
    """min_confidence_pct=65 이면 65% 미만 후보 모두 drop.

    기본값 30% 였으면 살아남았을 match (conf≈0.5) 가 사용자 지정 65% 에서는 탈락.
    """
    from api.services import classify_engine as ce

    async def fake_extract(*a, **kw):
        return _input_gate_result()

    async def fake_determine_sections(features, *, client=None, **kw):
        return [SectionCandidate(section_roman="XVI", confidence=0.9, reasoning="r")]

    fake_search = SearchResult(
        section_candidates=[SectionCandidate(section_roman="XVI", confidence=0.9, reasoning="r")],
        hs_candidates=[
            HSCandidate(heading="8471", hs_code="8471300000", score=0.5,
                        name_kr="NB", section_roman="XVI"),  # combined ~0.5 (match)
            HSCandidate(heading="8473", hs_code="8473300000", score=0.9,
                        name_kr="기타", section_roman="XVI"),  # combined ~0.85 (match)
        ],
        query="q",
        meta={"note_hits": 2, "case_hits": 0},
    )

    async def fake_verify(features, candidate, bundle, *, client=None, **kw):
        return _mk_verdict(candidate.heading, "match", 0.5)

    def fake_bundle(session, heading, hsk_year=2022):
        from api.services.rag_verify import NoteBundle

        return NoteBundle(heading=heading, hsk_year=hsk_year,
                          notes={("heading_note", "ko"): "n"})

    monkeypatch.setattr(ce, "extract_features", fake_extract)
    monkeypatch.setattr(ce, "determine_sections", fake_determine_sections)
    monkeypatch.setattr(ce, "verify_candidate", fake_verify)
    monkeypatch.setattr(ce, "fetch_note_bundle", fake_bundle)
    monkeypatch.setattr(
        ce, "_build_sync_search_callable", lambda *a, **kw: lambda _s: fake_search
    )

    # 기본(30%) 로는 둘 다 통과해야 함
    result_default = await run(
        ClassifyInput(product_name="x", description="y"),
        _FakeAsyncSession(),
        openai_client=fake_openai_client,
        claude_client=AsyncMock(),
        top_n=2,
    )
    assert len(result_default.candidates) == 2
    assert result_default.meta["min_confidence_pct"] == 30

    # 65% 로 올리면 combined≈0.5 인 8471 은 탈락, 0.85 인 8473 만 남음
    result_strict = await run(
        ClassifyInput(product_name="x", description="y", min_confidence_pct=65),
        _FakeAsyncSession(),
        openai_client=fake_openai_client,
        claude_client=AsyncMock(),
        top_n=2,
    )
    assert len(result_strict.candidates) == 1
    assert result_strict.candidates[0].heading == "8473"
    assert result_strict.meta["min_confidence_pct"] == 65
    assert result_strict.meta["filtered_below_threshold"] == 1


def test_classify_request_rejects_min_confidence_pct_out_of_range() -> None:
    """Pydantic 경계 검증: 30·70 은 금지 (gt=30, lt=70)."""
    from pydantic import ValidationError

    from api.schemas.classify import ClassifyRequest

    # 유효 경계값: 31, 69
    ClassifyRequest(product_name="x", description="y", min_confidence_pct=31)
    ClassifyRequest(product_name="x", description="y", min_confidence_pct=69)

    # 금지 경계: 30, 70
    with pytest.raises(ValidationError):
        ClassifyRequest(product_name="x", description="y", min_confidence_pct=30)
    with pytest.raises(ValidationError):
        ClassifyRequest(product_name="x", description="y", min_confidence_pct=70)
    # 훨씬 바깥
    with pytest.raises(ValidationError):
        ClassifyRequest(product_name="x", description="y", min_confidence_pct=0)
    with pytest.raises(ValidationError):
        ClassifyRequest(product_name="x", description="y", min_confidence_pct=100)


@pytest.mark.asyncio
async def test_threshold_keeps_above_and_drops_below(monkeypatch, fake_openai_client) -> None:
    """일부 match (conf 높음) + 일부 mismatch (conf 낮음) 섞인 경우: match 만 남는다."""
    from api.services import classify_engine as ce

    async def fake_extract(*a, **kw):
        return _input_gate_result()

    async def fake_determine_sections(features, *, client=None, **kw):
        return [SectionCandidate(section_roman="XVI", confidence=0.9, reasoning="r")]

    fake_search = SearchResult(
        section_candidates=[SectionCandidate(section_roman="XVI", confidence=0.9, reasoning="r")],
        hs_candidates=[
            HSCandidate(heading="8471", hs_code="8471300000", score=0.9,
                        name_kr="NB", section_roman="XVI"),
            HSCandidate(heading="8472", hs_code="8472000000", score=0.6,
                        name_kr="기타", section_roman="XVI"),
        ],
        query="q",
        meta={"note_hits": 2, "case_hits": 0},
    )

    async def mixed_verify(features, candidate, bundle, *, client=None, **kw):
        # 8471: match (conf 높음), 8472: mismatch (conf 낮음)
        if candidate.heading == "8471":
            return _mk_verdict("8471", "match", 0.9)
        return _mk_verdict("8472", "mismatch", 0.9)

    def fake_bundle(session, heading, hsk_year=2022):
        from api.services.rag_verify import NoteBundle

        return NoteBundle(heading=heading, hsk_year=hsk_year,
                          notes={("heading_note", "ko"): "n"})

    monkeypatch.setattr(ce, "extract_features", fake_extract)
    monkeypatch.setattr(ce, "determine_sections", fake_determine_sections)
    monkeypatch.setattr(ce, "verify_candidate", mixed_verify)
    monkeypatch.setattr(ce, "fetch_note_bundle", fake_bundle)
    monkeypatch.setattr(
        ce, "_build_sync_search_callable", lambda *a, **kw: lambda _s: fake_search
    )

    result = await run(
        ClassifyInput(product_name="x", description="y"),
        _FakeAsyncSession(),
        openai_client=fake_openai_client,
        claude_client=AsyncMock(),
        top_n=2,
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].heading == "8471"
    assert result.candidates[0].verdict == "match"
    assert result.candidates[0].confidence >= 0.30
    assert result.candidates[0].rank == 1  # 재부여 확인
    assert result.meta["filtered_below_threshold"] == 1


@pytest.mark.asyncio
async def test_force_classify_without_follow_ups_keeps_default_top_n_behavior(
    monkeypatch, fake_openai_client
) -> None:
    """force_classify=True 이지만 Input Gate 가 정보 충분하다 판단(follow_ups=[])한 경우:

    - 우회 경로가 아닌 정상 경로. ``bypassed=False`` 여야 함
    - 요청 플래그 자체는 meta 에 기록되고 top_n 은 DEFAULT_TOP_N(3) 유지
    - follow_up_questions 는 meta 에 없음 (빈 배열)
    """
    from api.services import classify_engine as ce

    async def fake_extract(*a, **kw):
        return _input_gate_result(needs_more_info=False, follow_ups=[])

    async def fake_determine_sections(features, *, client=None, **kw):
        return [SectionCandidate(section_roman="XVI", confidence=0.9, reasoning="r")]

    fake_search = SearchResult(
        section_candidates=[SectionCandidate(section_roman="XVI", confidence=0.9, reasoning="r")],
        hs_candidates=[
            HSCandidate(heading="8471", hs_code="8471300000", score=0.9,
                        name_kr="노트북", section_roman="XVI"),
        ],
        query="q",
        meta={"note_hits": 1, "case_hits": 0},
    )

    async def fake_verify(features, candidate, bundle, *, client=None, **kw):
        return _mk_verdict(candidate.heading, "match", 0.9)

    def fake_bundle(session, heading, hsk_year=2022):
        from api.services.rag_verify import NoteBundle

        return NoteBundle(heading=heading, hsk_year=hsk_year,
                          notes={("heading_note", "ko"): "text"})

    monkeypatch.setattr(ce, "extract_features", fake_extract)
    monkeypatch.setattr(ce, "determine_sections", fake_determine_sections)
    monkeypatch.setattr(ce, "verify_candidate", fake_verify)
    monkeypatch.setattr(ce, "fetch_note_bundle", fake_bundle)
    monkeypatch.setattr(
        ce, "_build_sync_search_callable", lambda *a, **kw: lambda _s: fake_search
    )

    result = await run(
        ClassifyInput(product_name="x", description="y", force_classify=True),
        _FakeAsyncSession(),
        openai_client=fake_openai_client,
        claude_client=AsyncMock(),
    )

    assert result.meta["force_classify"] is True
    assert result.meta["top_n"] == 3
    assert result.meta["stages"]["input_gate"]["bypassed"] is False
    assert "follow_up_questions" not in result.meta


def test_inject_hint_headings_adds_missing_hint_with_base_score() -> None:
    """hint_headings 중 후보 풀에 없는 것은 HINT_FORCED_BASE_SCORE 로 강제 주입."""
    from api.services.classify_engine import _inject_hint_headings
    from api.services.search import HINT_FORCED_BASE_SCORE

    class _HSStub:
        def __init__(self, code, kr):
            self.hs_code = code
            self.name_kr = kr
            self.name_en = None

    existing = [
        HSCandidate(heading="9503", hs_code="9503003700", score=0.7,
                    name_kr="완구", section_roman="XX"),
    ]
    hs_master = {"9506": _HSStub("9506910000", "운동용구")}
    forced = _inject_hint_headings(existing, {"9506"}, hs_master)
    assert forced == ["9506"]
    injected = next(c for c in existing if c.heading == "9506")
    assert injected.score == HINT_FORCED_BASE_SCORE
    assert injected.hs_code == "9506910000"
    assert injected.name_kr == "운동용구"


def test_inject_hint_headings_noop_when_hint_already_in_pool() -> None:
    from api.services.classify_engine import _inject_hint_headings

    existing = [
        HSCandidate(heading="9506", hs_code="9506910000", score=0.6,
                    name_kr="운동용구", section_roman="XX"),
    ]
    forced = _inject_hint_headings(existing, {"9506"}, {})
    assert forced == []
    assert len(existing) == 1
    # 기존 score 유지 (부스트는 aggregate 에서 이미 적용됨)
    assert existing[0].score == 0.6


def test_inject_hint_headings_handles_empty_hint() -> None:
    from api.services.classify_engine import _inject_hint_headings

    existing = [
        HSCandidate(heading="9503", hs_code="9503003700", score=0.7,
                    name_kr="완구", section_roman="XX"),
    ]
    forced = _inject_hint_headings(existing, set(), {})
    assert forced == []
    assert len(existing) == 1


@pytest.mark.asyncio
async def test_run_full_happy_path(monkeypatch, fake_openai_client) -> None:
    from api.services import classify_engine as ce

    async def fake_extract(*a, **kw):
        return _input_gate_result()

    async def fake_determine_sections(features, *, client=None, **kw):
        return [SectionCandidate(section_roman="XVI", confidence=0.9, reasoning="기기")]

    # 3-B 검색 결과
    fake_search = SearchResult(
        section_candidates=[SectionCandidate(section_roman="XVI", confidence=0.9, reasoning="r")],
        hs_candidates=[
            HSCandidate(
                heading="8471",
                hs_code="8471300000",
                name_kr="노트북",
                name_en="NB",
                score=0.85,
                section_roman="XVI",
            ),
            HSCandidate(
                heading="8472",
                hs_code="8472000000",
                name_kr="기타",
                name_en="",
                score=0.6,
                section_roman="XVI",
            ),
        ],
        query="q",
        meta={"note_hits": 10, "case_hits": 2},
    )

    async def fake_verify_candidate(features, candidate, bundle, *, client=None, **kw):
        return _mk_verdict(candidate.heading, "match", 0.9)

    def fake_fetch_note_bundle(session, heading, hsk_year=2022):
        from api.services.rag_verify import NoteBundle

        return NoteBundle(
            heading=heading,
            hsk_year=hsk_year,
            notes={("heading_note", "ko"): "휴대용 자동자료처리기계 설명"},
        )

    monkeypatch.setattr(ce, "extract_features", fake_extract)
    monkeypatch.setattr(ce, "determine_sections", fake_determine_sections)
    monkeypatch.setattr(ce, "verify_candidate", fake_verify_candidate)
    monkeypatch.setattr(ce, "fetch_note_bundle", fake_fetch_note_bundle)
    # _build_sync_search_callable 의 반환 callable 이 부르는 search_* 등을 우회하기 위해
    # 직접 SearchResult 를 주입
    monkeypatch.setattr(
        ce,
        "_build_sync_search_callable",
        lambda *args, **kw: lambda _s: fake_search,
    )

    result = await run(
        ClassifyInput(product_name="노트북", description="휴대용 컴퓨터"),
        _FakeAsyncSession(),
        openai_client=fake_openai_client,
        claude_client=AsyncMock(),
        top_n=2,
    )

    assert len(result.candidates) == 2
    assert result.candidates[0].verdict == "match"
    assert result.candidates[0].rank == 1
    assert result.candidates[0].heading == "8471"
    assert result.meta["engine"] == "real"
    assert result.meta["draft"] is True
    assert result.meta["stages"]["deep_verify"]["match"] == 2
    assert result.meta["usage"]["input_tokens"] > 0
    assert DRAFT_NOTICE in result.notice


@pytest.mark.asyncio
async def test_run_retry_without_filter_when_verify_gate_empty(
    monkeypatch, fake_openai_client
) -> None:
    from api.services import classify_engine as ce

    async def fake_extract(*a, **kw):
        return _input_gate_result()

    async def fake_determine_sections(features, *, client=None, **kw):
        return [SectionCandidate(section_roman="XVI", confidence=0.9, reasoning="r")]

    # 1회차: chapter 필터 때문에 verified 공집합 (후보가 다른 chapter)
    search_first = SearchResult(
        section_candidates=[SectionCandidate(section_roman="XVI", confidence=0.9, reasoning="r")],
        hs_candidates=[
            # section XVI의 chapter 84/85 밖
            HSCandidate(heading="6109", hs_code="6109100000", score=0.8, section_roman="XI"),
        ],
        query="q",
        meta={"note_hits": 1, "case_hits": 0},
    )
    # 2회차 (retry, chapter 필터 없음): verified 가능한 후보
    search_retry = SearchResult(
        section_candidates=[SectionCandidate(section_roman="XVI", confidence=0.9, reasoning="r")],
        hs_candidates=[
            HSCandidate(heading="6109", hs_code="6109100000", score=0.8, section_roman="XI"),
        ],
        query="q",
        meta={"note_hits": 1, "case_hits": 0},
    )
    search_returns = iter([search_first, search_retry])

    def fake_factory(*args, **kwargs):
        sr = next(search_returns)
        return lambda _s: sr

    async def fake_verify_candidate(features, candidate, bundle, *, client=None, **kw):
        return _mk_verdict(candidate.heading, "match", 0.7)

    def fake_fetch_note_bundle(session, heading, hsk_year=2022):
        from api.services.rag_verify import NoteBundle

        return NoteBundle(
            heading=heading,
            hsk_year=hsk_year,
            notes={("heading_note", "ko"): "면 편물 티셔츠"},
        )

    monkeypatch.setattr(ce, "extract_features", fake_extract)
    monkeypatch.setattr(ce, "determine_sections", fake_determine_sections)
    monkeypatch.setattr(ce, "verify_candidate", fake_verify_candidate)
    monkeypatch.setattr(ce, "fetch_note_bundle", fake_fetch_note_bundle)
    monkeypatch.setattr(ce, "_build_sync_search_callable", fake_factory)

    result = await run(
        ClassifyInput(product_name="티셔츠", description="면"),
        _FakeAsyncSession(),
        openai_client=fake_openai_client,
        claude_client=AsyncMock(),
        top_n=2,
    )

    # retry 단계가 기록되고, gate bypass 되어 retry 검색의 후보가 그대로 사용됨
    assert "verify_gate_retry" in result.meta["stages"]
    assert result.meta["stages"]["verify_gate_retry"]["bypassed"] is True
    assert len(result.candidates) == 1
    assert result.candidates[0].heading == "6109"


@pytest.mark.asyncio
async def test_run_uncertain_when_verify_gate_still_empty_after_retry(
    monkeypatch, fake_openai_client
) -> None:
    from api.services import classify_engine as ce

    async def fake_extract(*a, **kw):
        return _input_gate_result()

    async def fake_determine_sections(features, *, client=None, **kw):
        return [SectionCandidate(section_roman="XVI", confidence=0.9, reasoning="r")]

    empty_search = SearchResult(
        section_candidates=[SectionCandidate(section_roman="XVI", confidence=0.9, reasoning="r")],
        hs_candidates=[
            HSCandidate(heading="6109", score=0.5, section_roman="XI"),
        ],
        query="q",
        meta={"note_hits": 0, "case_hits": 0},
    )
    retry_empty = SearchResult(
        section_candidates=[SectionCandidate(section_roman="XVI", confidence=0.9, reasoning="r")],
        hs_candidates=[],  # retry 도 결과 0건
        query="q",
        meta={"note_hits": 0, "case_hits": 0},
    )
    returns = iter([empty_search, retry_empty])

    def factory(*a, **kw):
        sr = next(returns)
        return lambda _s: sr

    monkeypatch.setattr(ce, "extract_features", fake_extract)
    monkeypatch.setattr(ce, "determine_sections", fake_determine_sections)
    monkeypatch.setattr(ce, "_build_sync_search_callable", factory)

    result = await run(
        ClassifyInput(product_name="x", description="y"),
        _FakeAsyncSession(),
        openai_client=fake_openai_client,
        claude_client=AsyncMock(),
    )

    assert result.candidates == []
    assert "분류 불확실" in result.notice
    assert result.meta["stopped_at"] == "verify_gate"
    assert DRAFT_NOTICE in result.notice


@pytest.mark.asyncio
async def test_run_deep_verify_errors_are_captured_not_raised(
    monkeypatch, fake_openai_client
) -> None:
    from api.services import classify_engine as ce

    async def fake_extract(*a, **kw):
        return _input_gate_result()

    async def fake_determine_sections(features, *, client=None, **kw):
        return [SectionCandidate(section_roman="XVI", confidence=0.9, reasoning="r")]

    search = SearchResult(
        section_candidates=[SectionCandidate(section_roman="XVI", confidence=0.9, reasoning="r")],
        hs_candidates=[
            HSCandidate(heading="8471", hs_code="8471000000", score=0.8, section_roman="XVI"),
        ],
        query="q",
        meta={"note_hits": 1, "case_hits": 0},
    )

    async def fake_verify_candidate(features, candidate, bundle, *, client=None, **kw):
        raise RuntimeError("simulated deep verify failure")

    def fake_fetch(session, heading, hsk_year=2022):
        from api.services.rag_verify import NoteBundle

        return NoteBundle(heading=heading, hsk_year=hsk_year, notes={("heading_note", "ko"): "x"})

    monkeypatch.setattr(ce, "extract_features", fake_extract)
    monkeypatch.setattr(ce, "determine_sections", fake_determine_sections)
    monkeypatch.setattr(ce, "verify_candidate", fake_verify_candidate)
    monkeypatch.setattr(ce, "fetch_note_bundle", fake_fetch)
    monkeypatch.setattr(ce, "_build_sync_search_callable", lambda *a, **kw: lambda _s: search)

    result = await run(
        ClassifyInput(product_name="x", description="y"),
        _FakeAsyncSession(),
        openai_client=fake_openai_client,
        claude_client=AsyncMock(),
        top_n=1,
    )

    # 실패해도 예외 없이 결과 반환
    assert len(result.candidates) == 1
    # verdict 누락 → unverified
    assert result.candidates[0].verdict == "unverified"
    errors = result.meta["stages"]["deep_verify"]["errors"]
    assert len(errors) == 1
    assert errors[0]["heading"] == "8471"
