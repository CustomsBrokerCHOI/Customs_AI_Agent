"""Phase 3-B search + hs_sections 단위 테스트 (DB/네트워크 없음)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.db.models import HSCode
from api.services.hs_sections import (
    CHAPTER_TO_SECTION,
    SECTIONS,
    chapter_to_section,
    chapters_from_romans,
    heading_to_section,
    section_by_roman,
)
from api.services.input_gate import ProductFeatures
from api.services.search import (
    CASE_SCORE_WEIGHT,
    TOOL_NAME_SECTION,
    CaseHit,
    HSCandidate,
    NoteHit,
    _features_brief,
    aggregate_candidates,
    build_query_text,
    determine_sections,
)

# ---- hs_sections 구조 정합성 ----


def test_sections_cover_chapters_1_to_97() -> None:
    covered: set[int] = set()
    for s in SECTIONS:
        for ch in s.chapters:
            assert ch not in covered, f"chapter {ch} 중복"
            covered.add(ch)
    assert covered == set(range(1, 98))


def test_chapter_to_section_known_mappings() -> None:
    # Ch 84 (기계류) → XVI
    assert chapter_to_section(84) == "XVI"
    # Ch 61 (의류 편물) → XI
    assert chapter_to_section(61) == "XI"
    # Ch 1 (산 동물) → I
    assert chapter_to_section(1) == "I"
    # Ch 97 (예술품) → XXI
    assert chapter_to_section(97) == "XXI"


def test_chapter_to_section_out_of_range_returns_none() -> None:
    assert chapter_to_section(0) is None
    assert chapter_to_section(98) is None
    assert chapter_to_section(-1) is None


def test_heading_to_section_parses_first_two_digits() -> None:
    assert heading_to_section("8471") == "XVI"
    assert heading_to_section("6109") == "XI"
    assert heading_to_section("0101") == "I"


def test_heading_to_section_invalid_inputs() -> None:
    assert heading_to_section("") is None
    assert heading_to_section("xx") is None
    assert heading_to_section("99xx") is None  # 99는 정의 없음


def test_section_by_roman_roundtrip() -> None:
    s = section_by_roman("XVI")
    assert s is not None
    assert 84 in s.chapters
    assert 85 in s.chapters
    assert section_by_roman("ZZ") is None


def test_chapters_from_romans_union() -> None:
    chapters = chapters_from_romans(["VII", "XVI"])
    assert chapters == {39, 40, 84, 85}


def test_chapters_from_romans_unknown_ignored() -> None:
    chapters = chapters_from_romans(["XVI", "ZZ"])
    assert chapters == {84, 85}


def test_chapter_to_section_index_sizes_match() -> None:
    # 매핑 정의와 역인덱스가 일치
    assert len(CHAPTER_TO_SECTION) == sum(len(s.chapters) for s in SECTIONS)


# ---- build_query_text ----


def _mk_features(**kw) -> ProductFeatures:
    defaults = {
        "product_name_normalized": "노트북",
        "materials": [],
        "functions": [],
        "confidence": 0.8,
        "follow_up_questions": [],
    }
    defaults.update(kw)
    return ProductFeatures(**defaults)


def test_build_query_text_includes_all_filled_fields() -> None:
    f = _mk_features(
        product_name_normalized="노트북",
        materials=["알루미늄"],
        primary_use="업무",
        functions=["연산", "디스플레이"],
        form_factor="완제품",
        manufacturing_method="조립",
        key_specifications={"weight": "1.2kg"},
    )
    q = build_query_text(f)
    assert "노트북" in q
    assert "업무" in q
    assert "연산" in q and "디스플레이" in q
    assert "알루미늄" in q
    assert "완제품" in q
    assert "조립" in q
    assert "weight=1.2kg" in q


def test_build_query_text_skips_empty_fields() -> None:
    f = _mk_features(product_name_normalized="노트북", functions=[], materials=[])
    q = build_query_text(f)
    assert q == "노트북"
    assert "기능:" not in q
    assert "재질:" not in q


def test_features_brief_multiline() -> None:
    f = _mk_features(
        product_name_normalized="노트북",
        primary_use="이동 업무",
        materials=["알루미늄"],
    )
    brief = _features_brief(f)
    assert "품명: 노트북" in brief
    assert "주요 용도: 이동 업무" in brief
    assert "재질: 알루미늄" in brief


# ---- aggregate_candidates ----


def _note(heading: str, dist: float, text: str = "note", kind: str = "heading_note") -> NoteHit:
    return NoteHit(heading=heading, text=text, distance=dist, kind=kind)


def _case(heading: str, dist: float, name: str = "case", ref: str = "C1") -> CaseHit:
    return CaseHit(
        heading=heading,
        hs_code=f"{heading}000000" if heading else None,
        product_name=name,
        distance=dist,
        case_ref=ref,
    )


def test_aggregate_candidates_score_and_rank() -> None:
    notes = [_note("8471", 0.1), _note("8471", 0.3), _note("6109", 0.5)]
    cases = [_case("6109", 0.4, name="티셔츠")]  # effective = 0.4 * 0.85 = 0.34
    hs_master: dict[str, HSCode] = {}

    cands = aggregate_candidates(notes, cases, hs_master)

    # 8471 점수 = 1 - 0.1 = 0.9
    # 6109 점수 = 1 - (0.4 * 0.85) = 1 - 0.34 = 0.66
    assert cands[0].heading == "8471"
    assert cands[0].score == pytest.approx(0.9)
    assert cands[0].notes_hits == 2
    assert cands[0].cases_hits == 0
    assert cands[0].section_roman == "XVI"

    assert cands[1].heading == "6109"
    assert cands[1].score == pytest.approx(1 - 0.4 * CASE_SCORE_WEIGHT)
    assert cands[1].notes_hits == 1
    assert cands[1].cases_hits == 1
    assert cands[1].section_roman == "XI"


def test_aggregate_candidates_uses_hs_master_when_available() -> None:
    notes = [_note("8471", 0.2)]
    hs = HSCode(
        hs_code="8471300000",
        heading="8471",
        sub_heading="30",
        tariff_line="0000",
        name_kr="휴대용 자동자료처리기계",
        name_en="Portable ADP",
    )
    cands = aggregate_candidates(notes, [], {"8471": hs})
    assert cands[0].hs_code == "8471300000"
    assert cands[0].name_kr == "휴대용 자동자료처리기계"
    assert cands[0].name_en == "Portable ADP"


def test_aggregate_candidates_clips_negative_score_to_zero() -> None:
    # distance > 1 (예: cosine dist 1.5) → score = 0
    notes = [_note("8471", 1.5)]
    cands = aggregate_candidates(notes, [], {})
    assert cands[0].score == 0.0


def test_aggregate_candidates_ignores_case_with_no_heading() -> None:
    notes = [_note("8471", 0.2)]
    cases = [CaseHit(heading="", hs_code=None, product_name="x", distance=0.1, case_ref=None)]
    cands = aggregate_candidates(notes, cases, {})
    assert len(cands) == 1
    assert cands[0].heading == "8471"
    assert cands[0].cases_hits == 0


def test_aggregate_candidates_top_snippet_from_closest_hit() -> None:
    notes = [_note("8471", 0.5, text="far"), _note("8471", 0.1, text="closest")]
    cands = aggregate_candidates(notes, [], {})
    assert cands[0].top_snippet == "closest"


def test_aggregate_candidates_heading_hint_boosts_score() -> None:
    """hint_headings 완전 일치 → score ×HINT_HEADING_BOOST (상한 1.0 클립)."""
    from api.services.search import HINT_HEADING_BOOST

    notes = [_note("3304", 0.4), _note("8471", 0.1)]
    cands = aggregate_candidates(notes, [], {}, hint_headings={"3304"})
    by_h = {c.heading: c for c in cands}
    assert by_h["3304"].score == pytest.approx(min(1.0, 0.6 * HINT_HEADING_BOOST))
    assert by_h["8471"].score == pytest.approx(0.9)


def test_aggregate_candidates_chapter_hint_boosts_score() -> None:
    """heading 불일치 but chapter 일치 → ×1.1."""
    notes = [_note("3304", 0.5), _note("8471", 0.5)]
    cands = aggregate_candidates(notes, [], {}, hint_chapters={33})
    by_h = {c.heading: c for c in cands}
    assert by_h["3304"].score == pytest.approx(0.5 * 1.10)
    assert by_h["8471"].score == pytest.approx(0.5)


def test_aggregate_candidates_heading_hint_clips_to_one() -> None:
    """이미 점수 높은 heading 에 boost 적용해도 1.0 초과 금지."""
    notes = [_note("3304", 0.05)]  # 기본 0.95
    cands = aggregate_candidates(notes, [], {}, hint_headings={"3304"})
    # boost 후 1.0 초과분은 클립
    assert cands[0].score == 1.0


def test_aggregate_candidates_heading_boost_takes_precedence_over_chapter() -> None:
    """heading + chapter 모두 매치 시 heading boost 만 적용 (중복 없이)."""
    from api.services.search import HINT_HEADING_BOOST

    notes = [_note("3304", 0.5)]
    cands = aggregate_candidates(
        notes, [], {}, hint_headings={"3304"}, hint_chapters={33}
    )
    # heading boost 만 적용 (chapter boost 와 중첩 금지)
    assert cands[0].score == pytest.approx(min(1.0, 0.5 * HINT_HEADING_BOOST))


def test_aggregate_candidates_no_hints_unchanged() -> None:
    """힌트 없을 때 기존 동작 유지."""
    notes = [_note("8471", 0.1)]
    cands = aggregate_candidates(notes, [], {})  # no kwargs
    assert cands[0].score == pytest.approx(0.9)


def test_aggregate_candidates_tiebreak_by_hit_count() -> None:
    notes = [_note("8471", 0.2), _note("6109", 0.2), _note("8471", 0.2)]
    cands = aggregate_candidates(notes, [], {})
    # 점수 동률 → hit count 많은 8471 먼저
    assert cands[0].heading == "8471"
    assert cands[1].heading == "6109"


# ---- Sprint B: apply_inclusion_boost ----


def test_apply_inclusion_boost_lifts_matching_chapter_candidates() -> None:
    """매치된 chapter 의 후보 heading 이 +0.25 (additive) 로 부스트 + 재정렬."""
    from api.services.search import INCLUSION_BOOST_ADDITIVE, apply_inclusion_boost

    cands = [
        HSCandidate(heading="1106", score=0.80, notes_hits=3, cases_hits=2),
        HSCandidate(heading="0712", score=0.40, notes_hits=1, cases_hits=0),
        HSCandidate(heading="0902", score=0.55, notes_hits=2, cases_hits=0),
    ]
    # 07류 매치 (Kale Powder 케이스)
    matched = {"07": ["건조한 채소", "채소의 가루"]}

    out = apply_inclusion_boost(cands, matched)
    # 07류인 0712 가 0.40 + 0.25 = 0.65 로 올라감. 1106 (0.80) 다음.
    assert out[0].heading == "1106"
    assert out[0].score == pytest.approx(0.80)
    assert out[1].heading == "0712"
    assert out[1].score == pytest.approx(0.40 + INCLUSION_BOOST_ADDITIVE)
    assert out[2].heading == "0902"


def test_apply_inclusion_boost_clips_to_one() -> None:
    """부스트가 1.0 을 넘지 않도록 클램프."""
    from api.services.search import apply_inclusion_boost

    cands = [HSCandidate(heading="0712", score=0.90)]
    out = apply_inclusion_boost(cands, {"07": ["건조한 채소"]})
    assert out[0].score == pytest.approx(1.0)


def test_apply_inclusion_boost_empty_matches_returns_original() -> None:
    from api.services.search import apply_inclusion_boost

    cands = [HSCandidate(heading="1106", score=0.70)]
    out = apply_inclusion_boost(cands, {})
    assert out is cands  # early return — 동일 객체


def test_apply_inclusion_boost_ignores_non_matching_chapter() -> None:
    from api.services.search import apply_inclusion_boost

    cands = [
        HSCandidate(heading="1106", score=0.80),
        HSCandidate(heading="0712", score=0.40),
    ]
    # 20류 매치라고 가정 — 1106(11류), 0712(07류) 모두 해당 안 됨.
    out = apply_inclusion_boost(cands, {"20": ["조제한 채소"]})
    assert out[0].score == pytest.approx(0.80)
    assert out[1].score == pytest.approx(0.40)


def test_find_matching_chapters_by_inclusion_substring_hit(
    _fake_session_factory=None,
) -> None:
    """hs_nodes 테이블 쿼리 없이 find_matching 로직의 정규화·매칭 확인."""
    from api.services.search import _normalize_for_lookup

    # 한글 substring 매칭 확인.
    haystack = _normalize_for_lookup("Curly Kale Powder 건조한 채소 분말")
    needle = _normalize_for_lookup("건조한 채소")
    assert needle in haystack

    # 전각·반각 정규화.
    assert _normalize_for_lookup("케일（건조）") == _normalize_for_lookup("케일(건조)")


def test_matches_phrase_substring_mode() -> None:
    """phrase 가 haystack 에 통째 등장하면 hit."""
    from api.services.search import _matches_phrase

    haystack = "curly kale powder 컬리 케일 건조한 채소 분말"
    assert _matches_phrase("건조한 채소", haystack) is True
    assert _matches_phrase("케일", haystack) is True


def test_matches_phrase_token_and_mode() -> None:
    """phrase 가 그대로 없어도 모든 토큰이 haystack 에 있으면 hit."""
    from api.services.search import _matches_phrase

    haystack = "곱슬 케일 잎을 세척 건조 후 분쇄하여 분말"
    # "건조한 채소" phrase 그대로는 없음. 토큰 ["건조한","채소"] 중 "채소" 없음 → miss.
    assert _matches_phrase("건조한 채소", haystack) is False
    # 토큰 모두 있는 phrase → hit.
    assert _matches_phrase("케일 분쇄", haystack) is True
    # 단일 토큰 phrase 는 substring 으로만 hit. token AND 모드 발동 안 함.
    assert _matches_phrase("토마토", haystack) is False


def test_matches_phrase_short_tokens_not_token_mode() -> None:
    """토큰이 한 개뿐이면 token AND 매칭 모드 미발동 (거짓양성 방지)."""
    from api.services.search import _matches_phrase

    # "잎" 1자만으로는 매치 금지 (substring 검사도 길이 조건).
    haystack = "케일 잎"
    # 단일 토큰은 substring 매칭만 시도. "잎" substring 매칭은 됨.
    assert _matches_phrase("잎", haystack) is True
    # 한 개 토큰 phrase 가 haystack 에 없으면 miss.
    assert _matches_phrase("브로콜리", haystack) is False


# ---- determine_sections (PydanticAI TestModel) ----


def _section_override(candidates: list[dict]):
    """pydantic-ai TestModel 로 section agent 출력을 고정."""
    from pydantic_ai.models.test import TestModel

    from api.services.search import _section_agent

    return _section_agent().override(
        model=TestModel(custom_output_args={"candidates": candidates})
    )


@pytest.mark.asyncio
async def test_determine_sections_happy_path_sorted_by_confidence() -> None:
    f = _mk_features(product_name_normalized="노트북")
    with _section_override(
        [
            {"section_roman": "XI", "confidence": 0.4, "reasoning": "면 혼방"},
            {"section_roman": "XVI", "confidence": 0.8, "reasoning": "전자기기"},
        ]
    ):
        out = await determine_sections(f)
    # confidence 내림차순 정렬
    assert [s.section_roman for s in out] == ["XVI", "XI"]
    assert out[0].confidence == 0.8


@pytest.mark.asyncio
async def test_determine_sections_filters_unknown_roman() -> None:
    f = _mk_features()
    with _section_override(
        [
            {"section_roman": "XVI", "confidence": 0.9, "reasoning": "ok"},
            {"section_roman": "ZZ", "confidence": 0.5, "reasoning": "invalid"},
        ]
    ):
        out = await determine_sections(f)
    assert [s.section_roman for s in out] == ["XVI"]


def test_section_prompt_lists_all_21_sections() -> None:
    """system 프롬프트에 21개 섹션이 모두 나열되어 있어야 함."""
    from api.services.search import load_section_prompt

    prompt = load_section_prompt()
    for s in SECTIONS:
        assert s.roman in prompt, f"섹션 {s.roman} 프롬프트에 없음"
