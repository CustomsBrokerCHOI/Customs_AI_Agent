"""실 pgvector cosine 검색 통합 검증.

``api.services.search`` 의 sync 함수(Session 기반)를 ``await db.run_sync(...)``
브리지로 호출 — classify_engine 이 실제 프로덕션에서 돌리는 경로와 동일.
"""

from __future__ import annotations

import pytest

from api.services.search import (
    _load_hs_master_for_headings,
    aggregate_candidates,
    search_cases,
    search_note_chunks,
)

from .conftest import (
    _seed_embedding,
    seed_case,
    seed_hs_code,
    seed_note_with_chunks,
)


@pytest.mark.asyncio
async def test_note_search_returns_closest_heading(db) -> None:
    """쿼리 시드와 정확히 일치하는 청크가 distance≈0 으로 최상위."""
    await seed_hs_code(db, hs_code="8471300000")
    await seed_hs_code(db, hs_code="3004900000")
    await seed_note_with_chunks(
        db,
        heading="8471",
        content="휴대용 자동자료처리기계 해설",
        chunk_texts=["휴대용 자동자료처리기계 해설"],
        embedding_seed="target",
    )
    await seed_note_with_chunks(
        db,
        heading="3004",
        content="의약품 해설",
        chunk_texts=["의약품 해설"],
        embedding_seed="pharma",
    )

    query_vec = _seed_embedding("target:0")  # note 첫 청크 임베딩과 동일 시드

    def _run(s):
        return search_note_chunks(s, query_vec, k=5, chapters_filter=None)

    hits = await db.run_sync(_run)
    assert len(hits) == 2
    assert hits[0].heading == "8471"
    assert hits[0].distance < 1e-3  # ≈0
    assert hits[1].heading == "3004"
    assert hits[1].distance > hits[0].distance


@pytest.mark.asyncio
async def test_note_search_respects_chapter_filter(db) -> None:
    """chapter 필터 적용 시 heading prefix 가 맞는 청크만 반환."""
    await seed_hs_code(db, hs_code="8471300000")
    await seed_hs_code(db, hs_code="3004900000")
    await seed_note_with_chunks(
        db, heading="8471", chunk_texts=["a"], embedding_seed="k1"
    )
    await seed_note_with_chunks(
        db, heading="3004", chunk_texts=["b"], embedding_seed="k2"
    )

    query_vec = _seed_embedding("k1:0")

    def _run_filtered(s):
        return search_note_chunks(s, query_vec, k=10, chapters_filter={84})

    hits = await db.run_sync(_run_filtered)
    assert [h.heading for h in hits] == ["8471"]

    def _run_unfiltered(s):
        return search_note_chunks(s, query_vec, k=10, chapters_filter=None)

    hits_all = await db.run_sync(_run_unfiltered)
    assert {h.heading for h in hits_all} == {"8471", "3004"}


@pytest.mark.asyncio
async def test_note_search_limits_k(db) -> None:
    await seed_hs_code(db, hs_code="8471300000")
    await seed_note_with_chunks(
        db,
        heading="8471",
        chunk_texts=[f"chunk {i}" for i in range(10)],
        embedding_seed="many",
    )
    query_vec = _seed_embedding("many:0")

    def _run(s):
        return search_note_chunks(s, query_vec, k=3, chapters_filter=None)

    hits = await db.run_sync(_run)
    assert len(hits) == 3


@pytest.mark.asyncio
async def test_case_search_excludes_null_hs_code(db) -> None:
    """사례의 ``hs_code IS NULL`` 은 검색 결과에서 제외되어야 한다."""
    await seed_hs_code(db, hs_code="8471300000")
    await seed_case(db, case_ref="C-OK", hs_code="8471300000", embedding_seed="c1")
    await seed_case(db, case_ref="C-NULL", hs_code=None, embedding_seed="c1-close")

    query_vec = _seed_embedding("c1")

    def _run(s):
        return search_cases(s, query_vec, k=10, chapters_filter=None)

    hits = await db.run_sync(_run)
    assert [h.case_ref for h in hits] == ["C-OK"]
    assert hits[0].heading == "8471"


@pytest.mark.asyncio
async def test_case_search_chapter_filter(db) -> None:
    await seed_hs_code(db, hs_code="8471300000")
    await seed_hs_code(db, hs_code="3004900000")
    await seed_case(db, case_ref="C-84", hs_code="8471300000", embedding_seed="s")
    await seed_case(db, case_ref="C-30", hs_code="3004900000", embedding_seed="s2")
    query_vec = _seed_embedding("s")

    def _run(s):
        return search_cases(s, query_vec, k=10, chapters_filter={30})

    hits = await db.run_sync(_run)
    assert [h.case_ref for h in hits] == ["C-30"]


@pytest.mark.asyncio
async def test_aggregate_candidates_end_to_end(db) -> None:
    """note + case 히트 병합 → HSCandidate 리스트 반환. HS 마스터 조인 확인."""
    await seed_hs_code(db, hs_code="8471300000", name_kr="휴대용 ADP", name_en="Portable ADP")
    await seed_hs_code(db, hs_code="3004900000", name_kr="의약품", name_en="Medicaments")
    await seed_note_with_chunks(
        db, heading="8471", chunk_texts=["c1"], embedding_seed="near"
    )
    await seed_note_with_chunks(
        db, heading="3004", chunk_texts=["c2"], embedding_seed="far"
    )
    await seed_case(db, case_ref="CASE-1", hs_code="8471300000", embedding_seed="near")

    query_vec = _seed_embedding("near:0")

    def _run(s):
        note_hits = search_note_chunks(s, query_vec, k=10, chapters_filter=None)
        case_hits = search_cases(s, query_vec, k=10, chapters_filter=None)
        headings = list(
            {h.heading for h in note_hits} | {c.heading for c in case_hits if c.heading}
        )
        master = _load_hs_master_for_headings(s, headings)
        return aggregate_candidates(note_hits, case_hits, master)

    cands = await db.run_sync(_run)
    assert len(cands) == 2
    top = cands[0]
    assert top.heading == "8471"
    assert top.name_kr == "휴대용 ADP"
    assert top.name_en == "Portable ADP"
    assert top.notes_hits == 1
    # 'near:0' note 청크 정확 일치 → score 최대치 근접
    assert top.score > 0.95
    # 2순위는 멀리 있는 3004
    assert cands[1].heading == "3004"
    assert cands[1].score < top.score


@pytest.mark.asyncio
async def test_load_hs_master_returns_first_per_heading(db) -> None:
    """``_load_hs_master_for_headings`` 은 heading 당 첫 HS(정렬 순) 한 건만 반환."""
    await seed_hs_code(db, hs_code="8471300000", name_kr="A")
    await seed_hs_code(db, hs_code="8471400000", name_kr="B")

    def _run(s):
        return _load_hs_master_for_headings(s, ["8471", "0000"])

    master = await db.run_sync(_run)
    assert "8471" in master
    assert master["8471"].hs_code == "8471300000"  # 정렬상 앞
    assert "0000" not in master
