"""Phase 3-B search + hs_sections 단위 테스트 (DB/네트워크 없음)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

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
    SectionCandidate,
    _features_brief,
    aggregate_candidates,
    build_query_text,
    determine_sections,
)
from api.db.models import HSCode


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
    defaults = dict(
        product_name_normalized="노트북",
        materials=[],
        functions=[],
        confidence=0.8,
        follow_up_questions=[],
    )
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


def test_aggregate_candidates_tiebreak_by_hit_count() -> None:
    notes = [_note("8471", 0.2), _note("6109", 0.2), _note("8471", 0.2)]
    cands = aggregate_candidates(notes, [], {})
    # 점수 동률 → hit count 많은 8471 먼저
    assert cands[0].heading == "8471"
    assert cands[1].heading == "6109"


# ---- determine_sections (Claude 모킹) ----


def _tool_use_block(name: str, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=payload)


def _make_section_response(candidates: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        content=[_tool_use_block(TOOL_NAME_SECTION, {"candidates": candidates})],
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=50, output_tokens=30),
    )


@pytest.mark.asyncio
async def test_determine_sections_happy_path_sorted_by_confidence() -> None:
    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=_make_section_response(
            [
                {"section_roman": "XI", "confidence": 0.4, "reasoning": "면 혼방"},
                {"section_roman": "XVI", "confidence": 0.8, "reasoning": "전자기기"},
            ]
        )
    )

    f = _mk_features(product_name_normalized="노트북")
    out = await determine_sections(f, client=client)

    # confidence 내림차순 정렬
    assert [s.section_roman for s in out] == ["XVI", "XI"]
    assert out[0].confidence == 0.8


@pytest.mark.asyncio
async def test_determine_sections_filters_unknown_roman() -> None:
    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=_make_section_response(
            [
                {"section_roman": "XVI", "confidence": 0.9, "reasoning": "ok"},
                {"section_roman": "ZZ", "confidence": 0.5, "reasoning": "invalid"},
            ]
        )
    )
    f = _mk_features()
    out = await determine_sections(f, client=client)
    assert [s.section_roman for s in out] == ["XVI"]


@pytest.mark.asyncio
async def test_determine_sections_raises_when_no_tool_use() -> None:
    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(type="text", text="no call")],
            stop_reason="end_turn",
            usage=None,
        )
    )
    with pytest.raises(RuntimeError, match="tool_use"):
        await determine_sections(_mk_features(), client=client)


@pytest.mark.asyncio
async def test_determine_sections_passes_correct_tool_config() -> None:
    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=_make_section_response(
            [{"section_roman": "XVI", "confidence": 0.9, "reasoning": "ok"}]
        )
    )
    await determine_sections(_mk_features(), client=client)
    kwargs = client.messages.create.await_args.kwargs
    assert kwargs["tool_choice"] == {"type": "tool", "name": TOOL_NAME_SECTION}
    assert len(kwargs["tools"]) == 1
    assert kwargs["tools"][0]["name"] == TOOL_NAME_SECTION
    # system 프롬프트에 21개 섹션이 나열되어 있어야 함
    assert "XVI" in kwargs["system"]
    assert "XI" in kwargs["system"]
