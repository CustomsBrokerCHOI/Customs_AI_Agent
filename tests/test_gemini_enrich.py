"""Unit tests for gemini_enrich service — SDK 호출은 모킹, 순수 로직만 검증."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.gemini_enrich import (
    EnrichCitation,
    _build_prompt,
    _extract_citations,
    enrich_product_info,
)


# ---- _build_prompt ----


def test_build_prompt_includes_product_name_and_input_block() -> None:
    prompt = _build_prompt("세타필 로션", None)
    assert "<product_input>" in prompt
    assert "품명: 세타필 로션" in prompt
    assert "사진 URL" not in prompt


def test_build_prompt_includes_image_url_when_given() -> None:
    prompt = _build_prompt("데미그라스 소스", "https://example.com/x.jpg")
    assert "사진 URL: https://example.com/x.jpg" in prompt


def test_build_prompt_instructs_no_hs_code() -> None:
    prompt = _build_prompt("x", None)
    assert "HS CODE" in prompt  # 프롬프트에 HS CODE 금지 지시가 있음을 확인


# ---- _extract_citations ----


def _mk_response(chunks: list[dict] | None, queries: list[str] | None) -> SimpleNamespace:
    """grounding_metadata 를 가진 가짜 response 구성."""
    grounding_chunks = []
    for ch in chunks or []:
        web = SimpleNamespace(uri=ch.get("uri"), title=ch.get("title"))
        grounding_chunks.append(SimpleNamespace(web=web))
    meta = SimpleNamespace(
        grounding_chunks=grounding_chunks,
        web_search_queries=queries or [],
    )
    candidate = SimpleNamespace(grounding_metadata=meta)
    return SimpleNamespace(candidates=[candidate])


def test_extract_citations_parses_web_chunks() -> None:
    resp = _mk_response(
        chunks=[
            {"uri": "https://a.example", "title": "A"},
            {"uri": "https://b.example", "title": None},
        ],
        queries=["q1", "q2"],
    )
    cits, qs = _extract_citations(resp)
    assert cits == [
        EnrichCitation(url="https://a.example", title="A"),
        EnrichCitation(url="https://b.example", title=None),
    ]
    assert qs == ["q1", "q2"]


def test_extract_citations_honors_limit() -> None:
    resp = _mk_response(
        chunks=[{"uri": f"https://x{i}", "title": str(i)} for i in range(20)],
        queries=[],
    )
    cits, _ = _extract_citations(resp, limit=3)
    assert len(cits) == 3


def test_extract_citations_skips_chunk_without_uri() -> None:
    resp = _mk_response(
        chunks=[{"uri": None, "title": "no-uri"}, {"uri": "https://ok", "title": "ok"}],
        queries=[],
    )
    cits, _ = _extract_citations(resp)
    assert len(cits) == 1
    assert cits[0].url == "https://ok"


def test_extract_citations_handles_missing_metadata() -> None:
    # candidates 는 있지만 grounding_metadata=None
    cand = SimpleNamespace(grounding_metadata=None)
    resp = SimpleNamespace(candidates=[cand])
    cits, qs = _extract_citations(resp)
    assert cits == []
    assert qs == []


def test_extract_citations_handles_empty_candidates() -> None:
    resp = SimpleNamespace(candidates=[])
    cits, qs = _extract_citations(resp)
    assert cits == []
    assert qs == []


# ---- enrich_product_info (with injected mock client) ----


@pytest.mark.asyncio
async def test_enrich_product_info_returns_text_and_citations() -> None:
    fake_response = SimpleNamespace(
        text="세타필 로션은 피부 보습용 바디 로션이다.",
        candidates=[
            SimpleNamespace(
                grounding_metadata=SimpleNamespace(
                    grounding_chunks=[
                        SimpleNamespace(
                            web=SimpleNamespace(
                                uri="https://cetaphil.com", title="Cetaphil"
                            )
                        )
                    ],
                    web_search_queries=["세타필 로션 성분"],
                )
            )
        ],
    )
    mock_generate = AsyncMock(return_value=fake_response)
    # client.aio.models.generate_content 체인을 흉내
    fake_client = MagicMock()
    fake_client.aio.models.generate_content = mock_generate

    with patch("google.genai.types.Tool") as tool_mock, patch(
        "google.genai.types.GoogleSearch"
    ), patch("google.genai.types.GenerateContentConfig"):
        tool_mock.return_value = "TOOL_OBJ"
        result = await enrich_product_info(
            "세타필 로션", image_url=None, client=fake_client
        )

    assert "세타필" in result.description
    assert len(result.citations) == 1
    assert result.citations[0].url == "https://cetaphil.com"
    assert result.queries == ["세타필 로션 성분"]
    assert mock_generate.await_count == 1


@pytest.mark.asyncio
async def test_enrich_product_info_truncates_long_text() -> None:
    from api.services.gemini_enrich import MAX_DESCRIPTION_CHARS

    huge = "가" * (MAX_DESCRIPTION_CHARS + 500)
    fake_response = SimpleNamespace(text=huge, candidates=[])
    fake_client = MagicMock()
    fake_client.aio.models.generate_content = AsyncMock(return_value=fake_response)

    with patch("google.genai.types.Tool"), patch("google.genai.types.GoogleSearch"), patch(
        "google.genai.types.GenerateContentConfig"
    ):
        result = await enrich_product_info("x", client=fake_client)

    assert result.description.endswith("...")
    assert len(result.description) <= MAX_DESCRIPTION_CHARS + 3


@pytest.mark.asyncio
async def test_enrich_product_info_rejects_empty_input() -> None:
    with pytest.raises(ValueError):
        await enrich_product_info("", image_url=None, client=MagicMock())
    with pytest.raises(ValueError):
        await enrich_product_info("   ", image_url=None, client=MagicMock())
