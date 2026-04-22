"""Phase 3-C Verification Gate 단위 테스트."""

from __future__ import annotations

from api.services.search import HSCandidate, SearchResult, SectionCandidate
from api.services.verify import (
    MAX_SECTION_REDETERMINE_RETRIES,
    REJECT_CHAPTER_NOT_IN_SECTIONS,
    REJECT_INVALID_HEADING,
    partition_candidates,
    should_ask_user_more_info,
    summarize_rejected,
    verify_heading,
    verify_search_result,
)

# ---- verify_heading ----


def test_verify_heading_ok_when_chapter_allowed() -> None:
    ok, reason = verify_heading("8471", {84, 85})
    assert ok is True
    assert reason == ""


def test_verify_heading_reject_chapter_not_in_sections() -> None:
    ok, reason = verify_heading("6109", {84, 85})
    assert ok is False
    assert reason == REJECT_CHAPTER_NOT_IN_SECTIONS


def test_verify_heading_reject_empty() -> None:
    ok, reason = verify_heading("", {84})
    assert ok is False
    assert reason == REJECT_INVALID_HEADING


def test_verify_heading_reject_single_char() -> None:
    ok, reason = verify_heading("8", {84})
    assert ok is False
    assert reason == REJECT_INVALID_HEADING


def test_verify_heading_reject_non_digit_prefix() -> None:
    ok, reason = verify_heading("xx71", {84})
    assert ok is False
    assert reason == REJECT_INVALID_HEADING


def test_verify_heading_accepts_frozenset_and_set() -> None:
    assert verify_heading("8471", frozenset({84}))[0] is True
    assert verify_heading("8471", {84})[0] is True


# ---- partition_candidates ----


def _mk_candidate(heading: str, score: float = 0.9) -> HSCandidate:
    return HSCandidate(heading=heading, score=score)


def test_partition_candidates_splits_verified_and_rejected() -> None:
    candidates = [
        _mk_candidate("8471"),
        _mk_candidate("6109"),
        _mk_candidate("8528"),
    ]
    verified, rejected = partition_candidates(candidates, {84, 85})
    assert [v.heading for v in verified] == ["8471", "8528"]
    assert len(rejected) == 1
    assert rejected[0].candidate.heading == "6109"
    assert rejected[0].reason == REJECT_CHAPTER_NOT_IN_SECTIONS


def test_partition_candidates_preserves_input_order() -> None:
    candidates = [
        _mk_candidate("8528"),
        _mk_candidate("8471"),
    ]
    verified, _ = partition_candidates(candidates, {84, 85})
    assert [v.heading for v in verified] == ["8528", "8471"]


def test_partition_candidates_empty_input() -> None:
    verified, rejected = partition_candidates([], {84})
    assert verified == []
    assert rejected == []


def test_partition_candidates_all_rejected() -> None:
    candidates = [_mk_candidate("6109"), _mk_candidate("7301")]
    verified, rejected = partition_candidates(candidates, {84, 85})
    assert verified == []
    assert len(rejected) == 2


# ---- verify_search_result ----


def _mk_search_result(sections: list[str], headings: list[str]) -> SearchResult:
    return SearchResult(
        section_candidates=[
            SectionCandidate(section_roman=r, confidence=0.7, reasoning="x") for r in sections
        ],
        hs_candidates=[_mk_candidate(h) for h in headings],
        query="q",
    )


def test_verify_search_result_happy_path() -> None:
    sr = _mk_search_result(["XVI"], ["8471", "6109", "8528"])
    result = verify_search_result(sr)
    assert [c.heading for c in result.verified] == ["8471", "8528"]
    assert len(result.rejected) == 1
    assert result.allowed_chapters == frozenset({84, 85})
    assert result.should_re_determine is False
    assert result.meta["verified_count"] == 2
    assert result.meta["rejected_count"] == 1


def test_verify_search_result_all_rejected_triggers_redetermine() -> None:
    sr = _mk_search_result(["XVI"], ["6109", "7301"])
    result = verify_search_result(sr)
    assert result.verified == []
    assert len(result.rejected) == 2
    assert result.should_re_determine is True


def test_verify_search_result_no_candidates_does_not_trigger_redetermine() -> None:
    # 후보 자체가 없는 경우는 3-B 의 문제이지 3-C 의 재결정 대상이 아님
    sr = _mk_search_result(["XVI"], [])
    result = verify_search_result(sr)
    assert result.verified == []
    assert result.should_re_determine is False


def test_verify_search_result_empty_sections_is_fail_open() -> None:
    sr = _mk_search_result([], ["8471", "6109"])
    result = verify_search_result(sr)
    # 섹션 없으면 모든 후보 통과
    assert [c.heading for c in result.verified] == ["8471", "6109"]
    assert result.rejected == []
    assert result.should_re_determine is False
    assert result.meta["skipped"] is True


def test_verify_search_result_unknown_roman_treated_as_empty() -> None:
    # 알 수 없는 로마 → chapters_from_romans 는 공집합 → fail-open
    sr = _mk_search_result(["ZZ"], ["8471"])
    result = verify_search_result(sr)
    assert result.meta["skipped"] is True
    assert [c.heading for c in result.verified] == ["8471"]


def test_verify_search_result_top_n_limits_verified() -> None:
    sr = _mk_search_result(["XVI"], ["8471", "8472", "8473", "6109"])
    result = verify_search_result(sr, top_n=2)
    assert len(result.verified) == 2
    assert [c.heading for c in result.verified] == ["8471", "8472"]


def test_verify_search_result_multi_section_union() -> None:
    sr = _mk_search_result(["VII", "XVI"], ["3923", "8471", "6109"])
    result = verify_search_result(sr)
    assert {c.heading for c in result.verified} == {"3923", "8471"}
    assert result.rejected[0].candidate.heading == "6109"


# ---- summarize_rejected ----


def test_summarize_rejected_counts_by_reason() -> None:
    # HSCandidate.heading 이 Pydantic pattern 으로 4자리 강제되므로 실제 상황에서
    # partition 경유로는 INVALID_HEADING 이 안 나온다. 직접 RejectedCandidate 구성.
    from api.services.verify import RejectedCandidate

    rejected = [
        RejectedCandidate(candidate=_mk_candidate("6109"), reason=REJECT_CHAPTER_NOT_IN_SECTIONS),
        RejectedCandidate(candidate=_mk_candidate("7301"), reason=REJECT_CHAPTER_NOT_IN_SECTIONS),
        RejectedCandidate(candidate=_mk_candidate("9999"), reason=REJECT_INVALID_HEADING),
    ]
    summary = summarize_rejected(rejected)
    assert summary[REJECT_INVALID_HEADING] == 1
    assert summary[REJECT_CHAPTER_NOT_IN_SECTIONS] == 2


def test_summarize_rejected_empty() -> None:
    assert summarize_rejected([]) == {}


# ---- should_ask_user_more_info ----


def test_should_ask_user_more_info_when_retries_exhausted() -> None:
    sr = _mk_search_result(["XVI"], ["6109"])
    result = verify_search_result(sr)
    assert result.should_re_determine is True
    # attempt 0/1 → not yet; attempt == max → ask
    assert should_ask_user_more_info(0, result, max_retries=2) is False
    assert should_ask_user_more_info(1, result, max_retries=2) is False
    assert should_ask_user_more_info(2, result, max_retries=2) is True


def test_should_ask_user_more_info_skips_when_verified_present() -> None:
    sr = _mk_search_result(["XVI"], ["8471"])
    result = verify_search_result(sr)
    assert result.should_re_determine is False
    assert should_ask_user_more_info(5, result, max_retries=2) is False


def test_max_retries_constant_is_reasonable() -> None:
    # 상수가 무한 루프를 방어할 수 있는 값인지 가드
    assert isinstance(MAX_SECTION_REDETERMINE_RETRIES, int)
    assert 1 <= MAX_SECTION_REDETERMINE_RETRIES <= 5
