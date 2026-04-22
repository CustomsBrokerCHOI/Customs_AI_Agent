"""``fetch_note_bundle`` 실 DB 통합 검증.

Deep Verify 단계에서 호마다 주/해설서 4종(통칙/부주/류주/호주) × 2언어(ko/en) 를
한 번에 가져온다. UNIQUE (heading, kind, lang, hsk_year) 제약이 있어 동일 4-tuple 은
한 건만 존재. bundle 은 (kind, lang) → content dict 로 펼쳐 반환.
"""

from __future__ import annotations

import pytest

from api.services.rag_verify import fetch_note_bundle

from .conftest import seed_note_with_chunks


@pytest.mark.asyncio
async def test_bundle_groups_kind_and_lang(db) -> None:
    await seed_note_with_chunks(
        db,
        heading="8471",
        kind="heading_note",
        lang="ko",
        content="호 8471 국문 해설",
        chunk_texts=["ko-chunk"],
        embedding_seed="8471-ko-note",
    )
    await seed_note_with_chunks(
        db,
        heading="8471",
        kind="heading_note",
        lang="en",
        content="Heading 8471 English EN",
        chunk_texts=["en-chunk"],
        embedding_seed="8471-en-note",
    )
    await seed_note_with_chunks(
        db,
        heading="8471",
        kind="chapter_note",
        lang="ko",
        content="제84류 주",
        chunk_texts=["chap-chunk"],
        embedding_seed="84-chap-ko",
    )

    def _run(s):
        return fetch_note_bundle(s, "8471", hsk_year=2022)

    bundle = await db.run_sync(_run)
    assert bundle.has_any()
    assert bundle.get("heading_note", "ko") == "호 8471 국문 해설"
    assert bundle.get("heading_note", "en") == "Heading 8471 English EN"
    assert bundle.get("chapter_note", "ko") == "제84류 주"
    # 국문 우선
    pick = bundle.best("heading_note")
    assert pick is not None
    assert pick[0] == "ko"
    assert "국문 해설" in pick[1]


@pytest.mark.asyncio
async def test_bundle_respects_hsk_year(db) -> None:
    """hsk_year 필터가 올바르게 적용되어 다른 연도 레코드를 섞지 않는다."""
    await seed_note_with_chunks(
        db,
        heading="8471",
        hsk_year=2022,
        content="2022년 해설",
        chunk_texts=["c22"],
        embedding_seed="8471-22",
    )
    await seed_note_with_chunks(
        db,
        heading="8471",
        hsk_year=2017,
        content="2017년 해설",
        chunk_texts=["c17"],
        embedding_seed="8471-17",
    )

    def _run_22(s):
        return fetch_note_bundle(s, "8471", hsk_year=2022)

    def _run_17(s):
        return fetch_note_bundle(s, "8471", hsk_year=2017)

    b22 = await db.run_sync(_run_22)
    b17 = await db.run_sync(_run_17)
    assert b22.get("heading_note", "ko") == "2022년 해설"
    assert b17.get("heading_note", "ko") == "2017년 해설"


@pytest.mark.asyncio
async def test_bundle_empty_for_missing_heading(db) -> None:
    def _run(s):
        return fetch_note_bundle(s, "9999", hsk_year=2022)

    bundle = await db.run_sync(_run)
    assert not bundle.has_any()
    assert bundle.best("heading_note") is None


@pytest.mark.asyncio
async def test_bundle_skips_invalid_kind(db) -> None:
    """``VALID_KINDS`` 외 kind 는 bundle 에 포함되지 않는다 (모델 상 제약은 없지만
    서비스 계층에서 화이트리스트 필터)."""
    await seed_note_with_chunks(
        db,
        heading="8471",
        kind="weird_unknown",
        lang="ko",
        content="알 수 없는 종류",
        chunk_texts=["x"],
        embedding_seed="unk",
    )
    await seed_note_with_chunks(
        db,
        heading="8471",
        kind="heading_note",
        lang="ko",
        content="정상 heading_note",
        chunk_texts=["y"],
        embedding_seed="ok",
    )

    def _run(s):
        return fetch_note_bundle(s, "8471", hsk_year=2022)

    bundle = await db.run_sync(_run)
    # weird_unknown 은 제외
    assert bundle.get("weird_unknown", "ko") is None
    assert bundle.get("heading_note", "ko") == "정상 heading_note"
