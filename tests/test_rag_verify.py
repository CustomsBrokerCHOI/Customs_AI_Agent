"""Phase 3-D Deep Verify 단위 테스트 (DB/네트워크 없음)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.services.input_gate import ProductFeatures
from api.services.rag_verify import (
    CITATION_DOWNGRADE_FACTOR,
    CONSISTENCY_DOWNGRADE_FACTOR,
    MAX_CHAPTER_NOTE_CHARS,
    MAX_HEADING_NOTE_CHARS,
    MAX_SECTION_NOTE_CHARS,
    MIN_CITATION_LEN,
    TOOL_NAME_VERIFY,
    CitationRef,
    DeepVerifyResult,
    NoteBundle,
    VerificationVerdict,
    _apply_citation_guard,
    _is_excerpt_in_source,
    _is_verdict_inconsistent,
    _parse_verdict,
    _render_notes_block,
    build_verification_messages,
    validate_citations,
    verify_candidate,
    verify_candidates,
)
from api.services.search import HSCandidate

# ---- 헬퍼 ----


def _features(**kw) -> ProductFeatures:
    defaults = {
        "product_name_normalized": "노트북",
        "materials": [],
        "functions": [],
        "confidence": 0.8,
        "follow_up_questions": [],
    }
    defaults.update(kw)
    return ProductFeatures(**defaults)


def _candidate(heading: str = "8471") -> HSCandidate:
    return HSCandidate(
        heading=heading,
        hs_code=f"{heading}300000",
        name_kr="노트북 컴퓨터",
        name_en="Notebook Computer",
        score=0.9,
    )


def _bundle_8471() -> NoteBundle:
    return NoteBundle(
        heading="8471",
        hsk_year=2022,
        notes={
            (
                "general_rule",
                "ko",
            ): "관세율표 해석에 관한 통칙은 다음과 같다. 제1호부터 순차로 적용한다.",
            (
                "heading_note",
                "ko",
            ): "이 호에는 휴대용 자동자료처리기계로서 중량 10킬로그램 이하의 것을 포함한다.",
            (
                "chapter_note",
                "ko",
            ): "이 류에는 다음 각 목의 물품은 제외한다. 가. 기계의 부분품은 제84.31호로 분류한다.",
        },
    )


def _tool_use_block(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=TOOL_NAME_VERIFY, input=payload)


def _make_response(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        content=[_tool_use_block(payload)],
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=500, output_tokens=120),
    )


# ---- NoteBundle ----


def test_note_bundle_get_and_best() -> None:
    b = _bundle_8471()
    assert b.get("heading_note") == b.notes[("heading_note", "ko")]
    assert b.best("general_rule")[0] == "ko"
    assert b.best("section_note") is None  # 없음
    assert b.has_any() is True


def test_note_bundle_best_falls_back_to_english() -> None:
    b = NoteBundle(
        heading="8471",
        hsk_year=2022,
        notes={("heading_note", "en"): "English only"},
    )
    lang, content = b.best("heading_note")
    assert lang == "en"
    assert content == "English only"


def test_note_bundle_empty_has_any_false() -> None:
    assert NoteBundle(heading="0000", hsk_year=2022).has_any() is False


# ---- _is_excerpt_in_source ----


def test_excerpt_matching_respects_whitespace_normalization() -> None:
    source = "이 호에는  휴대용\t자동자료처리기계를\n포함한다."
    excerpt = "휴대용 자동자료처리기계"
    assert _is_excerpt_in_source(excerpt, source) is True


def test_excerpt_matching_rejects_too_short() -> None:
    assert _is_excerpt_in_source("x", "source text long enough") is False


def test_excerpt_matching_rejects_missing() -> None:
    assert _is_excerpt_in_source("이 문장은 없다", "완전히 다른 내용입니다") is False


def test_excerpt_matching_none_source() -> None:
    assert _is_excerpt_in_source("exc", None) is False


def test_min_citation_len_sane() -> None:
    assert MIN_CITATION_LEN >= 3


# ---- validate_citations ----


def test_validate_citations_splits_verified_and_unverified() -> None:
    b = _bundle_8471()
    cites = [
        CitationRef(
            source_kind="heading_note",
            heading="8471",
            excerpt="휴대용 자동자료처리기계",
        ),
        CitationRef(
            source_kind="heading_note",
            heading="8471",
            excerpt="환각으로 만든 문장",
        ),
        CitationRef(
            source_kind="general_rule",
            heading=None,
            excerpt="제1호부터 순차로 적용",
        ),
    ]
    verified, unverified = validate_citations(cites, b)
    assert len(verified) == 2
    assert len(unverified) == 1
    assert unverified[0].excerpt == "환각으로 만든 문장"


def test_validate_citations_unknown_source_kind_is_unverified() -> None:
    b = _bundle_8471()
    cites = [CitationRef(source_kind="bogus", heading="8471", excerpt="long enough string")]
    verified, unverified = validate_citations(cites, b)
    assert verified == []
    assert len(unverified) == 1


# ---- _apply_citation_guard ----


def test_guard_downgrades_match_when_all_matched_unverified() -> None:
    b = _bundle_8471()
    v = VerificationVerdict(
        candidate_heading="8471",
        verdict="match",
        confidence=0.8,
        matched_clauses=[
            CitationRef(
                source_kind="heading_note", heading="8471", excerpt="존재하지 않는 인용문자열"
            ),
        ],
        conflicting_clauses=[],
        reasoning="x",
    )
    out = _apply_citation_guard(v, b)
    assert out.verdict == "uncertain"
    assert out.confidence == pytest.approx(0.8 * CITATION_DOWNGRADE_FACTOR)
    assert out.matched_clauses == []
    assert len(out.unverified_citations) == 1


def test_guard_keeps_match_when_any_verified() -> None:
    b = _bundle_8471()
    v = VerificationVerdict(
        candidate_heading="8471",
        verdict="match",
        confidence=0.8,
        matched_clauses=[
            CitationRef(
                source_kind="heading_note", heading="8471", excerpt="휴대용 자동자료처리기계"
            ),
            CitationRef(source_kind="heading_note", heading="8471", excerpt="환각 인용"),
        ],
        conflicting_clauses=[],
        reasoning="x",
    )
    out = _apply_citation_guard(v, b)
    assert out.verdict == "match"
    assert out.confidence == 0.8
    assert len(out.matched_clauses) == 1
    assert len(out.unverified_citations) == 1


def test_guard_downgrades_mismatch_when_all_conflicting_unverified() -> None:
    b = _bundle_8471()
    v = VerificationVerdict(
        candidate_heading="8471",
        verdict="mismatch",
        confidence=0.7,
        matched_clauses=[],
        conflicting_clauses=[
            CitationRef(source_kind="chapter_note", heading="8471", excerpt="가짜 배제 규정"),
        ],
        reasoning="x",
    )
    out = _apply_citation_guard(v, b)
    assert out.verdict == "uncertain"
    assert out.confidence == pytest.approx(0.7 * CITATION_DOWNGRADE_FACTOR)


def test_is_verdict_inconsistent_match_requires_matched_ge_conflicting() -> None:
    # match 주장 — matched 없거나 conflicting 이 더 많으면 모순
    assert _is_verdict_inconsistent("match", n_matched=0, n_conflicting=0) is True
    assert _is_verdict_inconsistent("match", n_matched=0, n_conflicting=1) is True
    assert _is_verdict_inconsistent("match", n_matched=1, n_conflicting=2) is True
    assert _is_verdict_inconsistent("match", n_matched=1, n_conflicting=0) is False
    assert _is_verdict_inconsistent("match", n_matched=2, n_conflicting=2) is False


def test_is_verdict_inconsistent_mismatch_requires_conflicting_ge_matched() -> None:
    # mismatch 주장 — conflicting 없거나 matched 가 더 많으면 모순
    assert _is_verdict_inconsistent("mismatch", n_matched=0, n_conflicting=0) is True
    assert _is_verdict_inconsistent("mismatch", n_matched=3, n_conflicting=1) is True  # SL-M2030 케이스
    assert _is_verdict_inconsistent("mismatch", n_matched=0, n_conflicting=1) is False
    assert _is_verdict_inconsistent("mismatch", n_matched=1, n_conflicting=1) is False


def test_is_verdict_inconsistent_uncertain_never_forced() -> None:
    # uncertain 은 수 기준으로 강제 판정하지 않음
    for m, c in [(0, 0), (5, 0), (0, 5), (3, 3)]:
        assert _is_verdict_inconsistent("uncertain", n_matched=m, n_conflicting=c) is False


def test_guard_downgrades_mismatch_when_matched_outnumber_conflicting() -> None:
    """실관찰된 SL-M2030 패턴: matched 3 · conflicting 1 · verdict=mismatch.

    Claude 가 reasoning 은 match 로 전개하고 verdict 만 뒤집는 프롬프트 결함 대응.
    citation 이 원문 substring 이어서 환각 가드는 통과하지만 일관성 가드가 강등.
    """
    b = _bundle_8471()
    v = VerificationVerdict(
        candidate_heading="8471",
        verdict="mismatch",
        confidence=0.85,
        matched_clauses=[
            CitationRef(source_kind="heading_note", heading="8471",
                        excerpt="휴대용 자동자료처리기계"),
            CitationRef(source_kind="heading_note", heading="8471",
                        excerpt="중량 10킬로그램 이하"),
        ],
        conflicting_clauses=[
            CitationRef(source_kind="chapter_note", heading="8471",
                        excerpt="기계의 부분품은 제84.31호"),
        ],
        reasoning="reasoning 은 match 로 기울지만 verdict 만 뒤집힘",
    )
    out = _apply_citation_guard(v, b)
    assert out.verdict == "uncertain"
    assert out.confidence == pytest.approx(0.85 * CONSISTENCY_DOWNGRADE_FACTOR)
    # clauses 는 유지 — 관세사가 양쪽 근거를 볼 수 있도록
    assert len(out.matched_clauses) == 2
    assert len(out.conflicting_clauses) == 1


def test_guard_downgrades_match_when_conflicting_outnumber_matched() -> None:
    """match 주장했는데 conflicting 이 더 많은 대칭 케이스."""
    b = _bundle_8471()
    v = VerificationVerdict(
        candidate_heading="8471",
        verdict="match",
        confidence=0.7,
        matched_clauses=[
            CitationRef(source_kind="heading_note", heading="8471",
                        excerpt="휴대용 자동자료처리기계"),
        ],
        conflicting_clauses=[
            CitationRef(source_kind="chapter_note", heading="8471",
                        excerpt="기계의 부분품은 제84.31호"),
            CitationRef(source_kind="heading_note", heading="8471",
                        excerpt="중량 10킬로그램 이하"),
        ],
        reasoning="x",
    )
    out = _apply_citation_guard(v, b)
    assert out.verdict == "uncertain"
    assert out.confidence == pytest.approx(0.7 * CONSISTENCY_DOWNGRADE_FACTOR)


def test_guard_keeps_mismatch_when_conflicting_outnumber_matched() -> None:
    """정합한 mismatch: conflicting 가 matched 보다 많거나 같으면 그대로 유지."""
    b = _bundle_8471()
    v = VerificationVerdict(
        candidate_heading="8471",
        verdict="mismatch",
        confidence=0.6,
        matched_clauses=[],
        conflicting_clauses=[
            CitationRef(source_kind="chapter_note", heading="8471",
                        excerpt="기계의 부분품은 제84.31호"),
        ],
        reasoning="x",
    )
    out = _apply_citation_guard(v, b)
    assert out.verdict == "mismatch"
    assert out.confidence == 0.6


def test_guard_passthrough_when_no_citations_provided() -> None:
    b = _bundle_8471()
    v = VerificationVerdict(
        candidate_heading="8471",
        verdict="uncertain",
        confidence=0.3,
        reasoning="insufficient",
    )
    out = _apply_citation_guard(v, b)
    assert out.verdict == "uncertain"
    assert out.confidence == 0.3


# ---- notes truncation (rate-limit 대응) ----


def test_render_notes_block_truncates_long_heading_note() -> None:
    b = NoteBundle(
        heading="8443",
        hsk_year=2022,
        notes={
            ("heading_note", "ko"): "가" * (MAX_HEADING_NOTE_CHARS + 2000),
        },
    )
    block = _render_notes_block(b)
    # 한 줄에 heading_note 내용이 있으므로 블록 전체 길이가 상한에 근접.
    inner = block.split('<heading_note lang="ko">\n', 1)[1].split("\n</heading_note>", 1)[0]
    assert len(inner) <= MAX_HEADING_NOTE_CHARS
    assert "원문 일부 생략" in inner


def test_render_notes_block_keeps_short_note_intact() -> None:
    short = "이 호에는 노트북 컴퓨터를 포함한다."
    b = NoteBundle(
        heading="8471",
        hsk_year=2022,
        notes={("heading_note", "ko"): short},
    )
    block = _render_notes_block(b)
    assert short in block
    assert "원문 일부 생략" not in block


def test_render_notes_block_applies_per_kind_limits() -> None:
    b = NoteBundle(
        heading="8443",
        hsk_year=2022,
        notes={
            ("section_note", "ko"): "부" * (MAX_SECTION_NOTE_CHARS + 500),
            ("chapter_note", "ko"): "류" * (MAX_CHAPTER_NOTE_CHARS + 500),
        },
    )
    block = _render_notes_block(b)
    sec = block.split('<section_note lang="ko">\n', 1)[1].split("\n</section_note>", 1)[0]
    chap = block.split('<chapter_note lang="ko">\n', 1)[1].split("\n</chapter_note>", 1)[0]
    assert len(sec) <= MAX_SECTION_NOTE_CHARS
    assert len(chap) <= MAX_CHAPTER_NOTE_CHARS


# ---- build_verification_messages ----


def test_build_messages_contains_sandbox_blocks_and_escapes_user_input() -> None:
    f = _features(
        product_name_normalized="<악성 태그> 노트북",
        primary_use="일",
    )
    c = _candidate()
    msgs = build_verification_messages(f, c, _bundle_8471())
    assert len(msgs) == 1
    text = msgs[0]["content"]
    assert "<product_features>" in text
    assert "<candidate" in text
    assert "<notes" in text
    # 사용자 입력의 <>는 이스케이프
    assert "&lt;악성 태그&gt;" in text
    # system 이 아닌 user content 에 기록 지시
    assert TOOL_NAME_VERIFY in text


def test_build_messages_includes_only_available_note_kinds() -> None:
    b = NoteBundle(
        heading="8471",
        hsk_year=2022,
        notes={("heading_note", "ko"): "호 용어 원문"},
    )
    msgs = build_verification_messages(_features(), _candidate(), b)
    text = msgs[0]["content"]
    assert "<heading_note" in text
    assert "<general_rule" not in text


# ---- _parse_verdict ----


def test_parse_verdict_injects_candidate_heading() -> None:
    tool_in = {
        "verdict": "match",
        "confidence": 0.9,
        "matched_clauses": [
            {"source_kind": "heading_note", "excerpt": "휴대용 자동자료처리기계"},
        ],
        "conflicting_clauses": [],
        "reasoning": "ok",
    }
    v = _parse_verdict(tool_in, "8471")
    assert v.candidate_heading == "8471"
    # heading 필드 누락 → candidate heading 으로 채워짐
    assert v.matched_clauses[0].heading == "8471"


def test_parse_verdict_preserves_null_heading_for_general_rule() -> None:
    tool_in = {
        "verdict": "match",
        "confidence": 0.9,
        "matched_clauses": [
            {"source_kind": "general_rule", "heading": None, "excerpt": "제1호부터"},
        ],
        "conflicting_clauses": [],
        "reasoning": "ok",
    }
    v = _parse_verdict(tool_in, "8471")
    assert v.matched_clauses[0].heading is None


# ---- verify_candidate (async, mocked) ----


@pytest.mark.asyncio
async def test_verify_candidate_happy_path_match() -> None:
    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=_make_response(
            {
                "verdict": "match",
                "confidence": 0.85,
                "matched_clauses": [
                    {
                        "source_kind": "heading_note",
                        "heading": "8471",
                        "excerpt": "휴대용 자동자료처리기계",
                    }
                ],
                "conflicting_clauses": [],
                "reasoning": "호 용어가 물품을 포함",
            }
        )
    )
    out = await verify_candidate(_features(), _candidate(), _bundle_8471(), client=client)
    assert out.verdict == "match"
    assert out.candidate_heading == "8471"
    assert len(out.matched_clauses) == 1
    assert out.confidence == 0.85

    kwargs = client.messages.create.await_args.kwargs
    assert kwargs["tool_choice"] == {"type": "tool", "name": TOOL_NAME_VERIFY}


@pytest.mark.asyncio
async def test_verify_candidate_empty_bundle_returns_uncertain_without_llm() -> None:
    client = AsyncMock()
    client.messages.create = AsyncMock()  # 호출되면 안 됨
    empty_bundle = NoteBundle(heading="9999", hsk_year=2022)

    out = await verify_candidate(_features(), _candidate("9999"), empty_bundle, client=client)
    assert out.verdict == "uncertain"
    assert out.confidence == 0.0
    assert client.messages.create.await_count == 0


@pytest.mark.asyncio
async def test_verify_candidate_raises_when_no_tool_use() -> None:
    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(type="text", text="no")],
            stop_reason="end_turn",
            usage=None,
        )
    )
    with pytest.raises(RuntimeError, match="tool_use"):
        await verify_candidate(_features(), _candidate(), _bundle_8471(), client=client)


@pytest.mark.asyncio
async def test_verify_candidate_applies_citation_guard() -> None:
    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=_make_response(
            {
                "verdict": "match",
                "confidence": 0.9,
                "matched_clauses": [
                    {
                        "source_kind": "heading_note",
                        "heading": "8471",
                        "excerpt": "이 원문에 없는 완전한 환각 문장입니다",
                    }
                ],
                "conflicting_clauses": [],
                "reasoning": "fake",
            }
        )
    )
    out = await verify_candidate(_features(), _candidate(), _bundle_8471(), client=client)
    assert out.verdict == "uncertain"
    assert out.confidence == pytest.approx(0.9 * CITATION_DOWNGRADE_FACTOR)
    assert out.matched_clauses == []
    assert len(out.unverified_citations) == 1


# ---- verify_candidates (Top-N parallel) ----


@pytest.mark.asyncio
async def test_verify_candidates_gathers_topn_and_records_errors(monkeypatch) -> None:
    # fetch_note_bundle 을 가짜로 치환: 두 후보 모두 non-empty bundle
    # (empty bundle 이면 LLM 을 안 부르고 즉시 uncertain 을 내므로 실패 경로를 못 탐)
    from api.services import rag_verify as rv

    def fake_fetch(session, heading, hsk_year=2022):
        # 두 heading 모두 non-empty bundle. 8471 의 원문은 아래 side_effect 의
        # excerpt ("휴대용 자동자료처리기계") 와 substring 매치하도록 구성.
        return NoteBundle(
            heading=heading,
            hsk_year=2022,
            notes={
                ("heading_note", "ko"): f"heading {heading} 휴대용 자동자료처리기계 원문",
            },
        )

    monkeypatch.setattr(rv, "fetch_note_bundle", fake_fetch)

    client = AsyncMock()

    def side_effect(**kwargs):
        # 두 번째 후보(6109)만 예외로 실패 시뮬레이션
        msgs = kwargs["messages"][0]["content"]
        if "6109" in msgs:
            raise RuntimeError("simulated LLM failure")
        return _make_response(
            {
                "verdict": "match",
                "confidence": 0.8,
                "matched_clauses": [
                    {
                        "source_kind": "heading_note",
                        "heading": "8471",
                        "excerpt": "휴대용 자동자료처리기계",
                    }
                ],
                "conflicting_clauses": [],
                "reasoning": "ok",
            }
        )

    client.messages.create = AsyncMock(side_effect=side_effect)

    candidates = [_candidate("8471"), _candidate("6109")]
    result = await verify_candidates(
        _features(), candidates, MagicMock(), claude_client=client, top_n=3
    )

    assert isinstance(result, DeepVerifyResult)
    assert len(result.verdicts) == 1
    assert result.verdicts[0].candidate_heading == "8471"
    assert len(result.errors) == 1
    assert result.errors[0]["heading"] == "6109"
    assert result.meta["success"] == 1
    assert result.meta["failed"] == 1


@pytest.mark.asyncio
async def test_verify_candidates_empty_input() -> None:
    result = await verify_candidates(
        _features(), [], MagicMock(), claude_client=AsyncMock(), top_n=3
    )
    assert result.verdicts == []
    assert result.errors == []
    assert result.meta["processed"] == 0


@pytest.mark.asyncio
async def test_verify_candidates_respects_top_n(monkeypatch) -> None:
    from api.services import rag_verify as rv

    fetch_calls: list[str] = []

    def fake_fetch(session, heading, hsk_year=2022):
        fetch_calls.append(heading)
        return _bundle_8471()

    monkeypatch.setattr(rv, "fetch_note_bundle", fake_fetch)

    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=_make_response(
            {
                "verdict": "match",
                "confidence": 0.7,
                "matched_clauses": [
                    {
                        "source_kind": "heading_note",
                        "heading": "8471",
                        "excerpt": "휴대용 자동자료처리기계",
                    }
                ],
                "conflicting_clauses": [],
                "reasoning": "ok",
            }
        )
    )

    candidates = [_candidate("8471"), _candidate("8472"), _candidate("8473"), _candidate("8474")]
    result = await verify_candidates(
        _features(), candidates, MagicMock(), claude_client=client, top_n=2
    )
    assert result.meta["processed"] == 2
    assert len(fetch_calls) == 2
    assert len(result.verdicts) == 2
