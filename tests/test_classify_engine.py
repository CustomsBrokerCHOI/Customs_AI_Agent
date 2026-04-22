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
