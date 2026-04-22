"""평가 메트릭 + 샘플링 + 스키마 단위 테스트 (DB/네트워크 없음)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.build_eval_set import (
    EvalRecord,
    _build_query,
    _normalize_hs,
    clip_cases_to_records,
    load_clip_cases,
    load_manual_records,
    stratified_sample,
    write_eval_set,
)
from scripts.eval_search import (
    mean_reciprocal_rank,
    rank_of_first_hit,
    recall_at_k,
)

# ---- rank_of_first_hit ----


def test_rank_of_first_hit_first_position() -> None:
    assert rank_of_first_hit(["8471", "8472"], "8471") == 1


def test_rank_of_first_hit_middle() -> None:
    assert rank_of_first_hit(["8472", "8473", "8471", "8474"], "8471") == 3


def test_rank_of_first_hit_miss() -> None:
    assert rank_of_first_hit(["8472", "8473"], "8471") is None


def test_rank_of_first_hit_empty() -> None:
    assert rank_of_first_hit([], "8471") is None


def test_rank_of_first_hit_dedupe_first_wins() -> None:
    # 중복 등장하더라도 최초 rank 만 기록
    assert rank_of_first_hit(["8471", "8472", "8471"], "8471") == 1


# ---- recall_at_k ----


def test_recall_at_k_counts_ranks_within_limit() -> None:
    ranks: list[int | None] = [1, 3, None, 7, 2]
    assert recall_at_k(ranks, 5) == 3 / 5  # 1, 3, 2 히트
    assert recall_at_k(ranks, 10) == 4 / 5  # + 7
    assert recall_at_k(ranks, 2) == 2 / 5  # 1, 2 만


def test_recall_at_k_empty_is_zero() -> None:
    assert recall_at_k([], 5) == 0.0


def test_recall_at_k_all_miss() -> None:
    assert recall_at_k([None, None, None], 5) == 0.0


# ---- mean_reciprocal_rank ----


def test_mrr_basic() -> None:
    # rank 1, 2, None, 4 → (1 + 0.5 + 0 + 0.25) / 4
    result = mean_reciprocal_rank([1, 2, None, 4])
    assert result == pytest.approx((1 + 0.5 + 0 + 0.25) / 4)


def test_mrr_empty_is_zero() -> None:
    assert mean_reciprocal_rank([]) == 0.0


def test_mrr_all_miss_is_zero() -> None:
    assert mean_reciprocal_rank([None, None]) == 0.0


def test_mrr_all_first_is_one() -> None:
    assert mean_reciprocal_rank([1, 1, 1]) == 1.0


# ---- _normalize_hs ----


def test_normalize_hs_full_ten_digits() -> None:
    assert _normalize_hs("8471.30-0000") == ("8471", "8471300000")


def test_normalize_hs_four_only() -> None:
    assert _normalize_hs("8471") == ("8471", None)


def test_normalize_hs_invalid_returns_none() -> None:
    assert _normalize_hs(None) == (None, None)
    assert _normalize_hs("") == (None, None)
    assert _normalize_hs("ab") == (None, None)


# ---- _build_query ----


def test_build_query_without_description() -> None:
    assert _build_query("노트북", None) == "노트북"


def test_build_query_combines_name_and_truncated_description() -> None:
    desc = "휴대용 컴퓨터입니다. " * 50  # 매우 김
    q = _build_query("노트북", desc)
    assert q.startswith("노트북. ")
    assert len(q) <= 200 + len("노트북. ") + 5  # 여유분


def test_build_query_description_only_when_name_empty() -> None:
    q = _build_query("", "설명만 있음")
    assert q == "설명만 있음"


# ---- EvalRecord 스키마 ----


def test_eval_record_valid() -> None:
    r = EvalRecord(
        id="clip_001",
        query="노트북",
        gt_heading="8471",
        gt_hs10="8471300000",
        source="clip_case",
        case_ref="2024-CLS-123",
    )
    assert r.gt_heading == "8471"


def test_eval_record_rejects_non_four_digit_heading() -> None:
    with pytest.raises(ValidationError):
        EvalRecord(id="x", query="q", gt_heading="847", source="clip_case")


def test_eval_record_rejects_non_ten_digit_hs10() -> None:
    with pytest.raises(ValidationError):
        EvalRecord(id="x", query="q", gt_heading="8471", gt_hs10="84713000", source="clip_case")


def test_eval_record_empty_query_rejected() -> None:
    with pytest.raises(ValidationError):
        EvalRecord(id="x", query="", gt_heading="8471", source="clip_case")


# ---- stratified_sample ----


def _mk_case(ref: str, hs: str, name: str = "x") -> dict:
    return {"case_ref": ref, "hs_code": hs, "product_name": name}


def test_stratified_sample_is_deterministic_with_seed() -> None:
    cases = [_mk_case(f"r{i}", f"{8471 + (i % 5):04d}300000") for i in range(30)]
    a = stratified_sample(cases, count=10, seed=42)
    b = stratified_sample(cases, count=10, seed=42)
    assert [c["case_ref"] for c in a] == [c["case_ref"] for c in b]


def test_stratified_sample_different_seed_differs() -> None:
    cases = [_mk_case(f"r{i}", f"{8471 + (i % 5):04d}300000") for i in range(30)]
    a = stratified_sample(cases, count=10, seed=42)
    b = stratified_sample(cases, count=10, seed=99)
    assert [c["case_ref"] for c in a] != [c["case_ref"] for c in b]


def test_stratified_sample_covers_all_headings_when_possible() -> None:
    # 5 개 heading 각 6건 → 5건 뽑으면 5 개 모두 최소 1건
    cases = [_mk_case(f"r{i}", f"{8471 + (i % 5):04d}300000") for i in range(30)]
    picked = stratified_sample(cases, count=5, seed=1)
    headings = {c["hs_code"][:4] for c in picked}
    assert len(headings) == 5


def test_stratified_sample_respects_count() -> None:
    cases = [_mk_case(f"r{i}", f"{8471 + (i % 3):04d}300000") for i in range(9)]
    picked = stratified_sample(cases, count=6, seed=7)
    assert len(picked) == 6


def test_stratified_sample_returns_less_than_count_if_exhausted() -> None:
    # 3 건만 있는데 10 건 요청
    cases = [_mk_case("a", "8471300000"), _mk_case("b", "8472000000")]
    picked = stratified_sample(cases, count=10, seed=1)
    assert len(picked) == 2


# ---- clip_cases_to_records ----


def test_clip_cases_to_records_skips_invalid_hs() -> None:
    cases = [
        {"case_ref": "a", "hs_code": "8471300000", "product_name": "노트북"},
        {"case_ref": "b", "hs_code": None, "product_name": "ignored"},
        {"case_ref": "c", "hs_code": "ab", "product_name": "ignored"},
    ]
    records = clip_cases_to_records(cases)
    assert len(records) == 1
    assert records[0].gt_heading == "8471"
    assert records[0].source == "clip_case"


# ---- load_clip_cases (JSONL I/O) ----


def test_load_clip_cases_dedupes_by_case_ref(tmp_path: Path) -> None:
    p1 = tmp_path / "a.jsonl"
    p2 = tmp_path / "b.jsonl"
    p1.write_text(
        json.dumps(
            {"case_ref": "X1", "hs_code": "8471300000", "product_name": "노트북"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    p2.write_text(
        json.dumps(
            {"case_ref": "X1", "hs_code": "8471300000", "product_name": "duplicate"},
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {"case_ref": "X2", "hs_code": "8472000000", "product_name": "other"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    cases = load_clip_cases([p1, p2])
    refs = [c["case_ref"] for c in cases]
    assert refs == ["X1", "X2"]


def test_load_clip_cases_skips_rows_missing_name_or_hs(tmp_path: Path) -> None:
    p = tmp_path / "a.jsonl"
    p.write_text(
        json.dumps({"case_ref": "a", "hs_code": None, "product_name": "x"}, ensure_ascii=False)
        + "\n"
        + json.dumps(
            {"case_ref": "b", "hs_code": "8471300000", "product_name": ""}, ensure_ascii=False
        )
        + "\n"
        + json.dumps(
            {"case_ref": "c", "hs_code": "8471300000", "product_name": "ok"}, ensure_ascii=False
        )
        + "\n",
        encoding="utf-8",
    )
    cases = load_clip_cases([p])
    assert [c["case_ref"] for c in cases] == ["c"]


# ---- manual ----


def test_load_manual_records_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_manual_records(tmp_path / "nope.jsonl") == []


def test_load_manual_records_reads_valid_and_skips_comments(tmp_path: Path) -> None:
    p = tmp_path / "manual.jsonl"
    p.write_text(
        "// 주석 무시\n"
        + json.dumps(
            {
                "id": "m_1",
                "query": "수기 질문",
                "gt_heading": "6109",
                "source": "manual",
                "notes": "티셔츠 판례",
            }
        )
        + "\n"
        + "\n"  # 빈 줄
        + json.dumps({"query": "자동 id", "gt_heading": "8471", "source": "manual"})
        + "\n",
        encoding="utf-8",
    )
    records = load_manual_records(p)
    assert len(records) == 2
    assert records[0].id == "m_1"
    # id 미기재 시 자동 채움
    assert records[1].id.startswith("manual_")


# ---- write_eval_set ----


def test_write_eval_set_roundtrip(tmp_path: Path) -> None:
    recs = [
        EvalRecord(id="a", query="q1", gt_heading="8471", source="clip_case", case_ref="CR"),
        EvalRecord(id="b", query="q2", gt_heading="6109", source="manual"),
    ]
    out = tmp_path / "set.jsonl"
    write_eval_set(recs, out)

    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["id"] == "a"
    assert first["gt_heading"] == "8471"
