"""build_embeddings 단위 테스트 (네트워크/DB 접근 없음)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from api.db.models import ClassificationCase
from scripts.build_embeddings import (
    EMBED_DIM,
    EMBED_MODEL,
    EMBED_PRICE_PER_1M_TOKENS_USD,
    _case_text,
    embed_batch,
    estimate_cost,
)

# ---- estimate_cost ----


def test_estimate_cost_counts_tokens_and_scales_price() -> None:
    est = estimate_cost(["hello world", "안녕하세요 세계"])
    assert est.row_count == 2
    assert est.total_tokens > 0
    assert est.estimated_usd == pytest.approx(
        est.total_tokens * EMBED_PRICE_PER_1M_TOKENS_USD / 1_000_000
    )


def test_estimate_cost_empty_input() -> None:
    est = estimate_cost([])
    assert est.row_count == 0
    assert est.total_tokens == 0
    assert est.estimated_usd == 0.0


# ---- _case_text ----


def test_case_text_concatenates_all_fields() -> None:
    case = ClassificationCase(
        product_name="노트북",
        description="휴대용 컴퓨터",
        reasoning="HS 8471 해당",
    )
    txt = _case_text(case)
    assert "노트북" in txt
    assert "휴대용 컴퓨터" in txt
    assert "HS 8471 해당" in txt
    # 빈 줄로 분리
    assert txt.count("\n\n") == 2


def test_case_text_skips_none_and_empty() -> None:
    case = ClassificationCase(
        product_name="노트북",
        description=None,
        reasoning="   ",
    )
    assert _case_text(case) == "노트북"


def test_case_text_strips_whitespace() -> None:
    case = ClassificationCase(
        product_name="  노트북  ",
        description="\n휴대용\t",
        reasoning=None,
    )
    txt = _case_text(case)
    assert txt == "노트북\n\n휴대용"


# ---- embed_batch ----


def _fake_openai_response(vectors: list[list[float]]) -> MagicMock:
    resp = MagicMock()
    resp.data = [MagicMock(embedding=v) for v in vectors]
    return resp


def test_embed_batch_calls_openai_with_model_and_dimensions() -> None:
    client = MagicMock()
    client.embeddings.create.return_value = _fake_openai_response([[0.1] * EMBED_DIM])

    out = embed_batch(client, ["hello"])

    client.embeddings.create.assert_called_once_with(
        model=EMBED_MODEL, input=["hello"], dimensions=EMBED_DIM
    )
    assert out == [[0.1] * EMBED_DIM]


def test_embed_batch_returns_vectors_in_order() -> None:
    client = MagicMock()
    client.embeddings.create.return_value = _fake_openai_response(
        [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]
    )
    out = embed_batch(client, ["a", "b", "c"], dimensions=2)
    assert out == [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]


def test_embed_batch_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def side_effect(**kw):
        calls.append(kw)
        if len(calls) < 3:
            raise RuntimeError("transient")
        return _fake_openai_response([[0.2] * 4])

    client = MagicMock()
    client.embeddings.create.side_effect = side_effect
    monkeypatch.setattr("scripts.build_embeddings.time.sleep", lambda s: None)

    out = embed_batch(client, ["x"], dimensions=4)

    assert len(calls) == 3
    assert out == [[0.2] * 4]


def test_embed_batch_raises_after_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.embeddings.create.side_effect = RuntimeError("down")
    monkeypatch.setattr("scripts.build_embeddings.time.sleep", lambda s: None)

    with pytest.raises(RuntimeError, match="embed_batch 최종 실패"):
        embed_batch(client, ["x"], max_retry=2)
    assert client.embeddings.create.call_count == 2
