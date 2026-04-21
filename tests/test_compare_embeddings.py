"""compare_embeddings 단위 테스트 (네트워크/DB/torch 없음)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from scripts.build_eval_set import EvalRecord
from scripts.compare_embeddings import (
    BgeM3Backend,
    CompareReport,
    OpenAIBackend,
    build_per_query,
    evaluate,
    format_table,
    normalize,
    top_k_indices,
)


# ---- normalize ----


def test_normalize_unit_vectors() -> None:
    v = np.array([[3.0, 4.0], [1.0, 0.0]], dtype=np.float32)
    out = normalize(v)
    norms = np.linalg.norm(out, axis=1)
    np.testing.assert_allclose(norms, [1.0, 1.0], atol=1e-6)


def test_normalize_preserves_zero_vector() -> None:
    v = np.array([[0.0, 0.0, 0.0]], dtype=np.float32)
    out = normalize(v)
    np.testing.assert_array_equal(out, v)


# ---- top_k_indices ----


def test_top_k_indices_returns_descending_rank() -> None:
    sims = np.array([[0.1, 0.5, 0.9, 0.3]])
    out = top_k_indices(sims, k=2)
    assert out.shape == (1, 2)
    assert out[0].tolist() == [2, 1]  # 0.9, 0.5


def test_top_k_indices_k_larger_than_docs() -> None:
    sims = np.array([[0.9, 0.1, 0.5]])
    out = top_k_indices(sims, k=10)
    assert out.shape == (1, 3)
    assert out[0].tolist() == [0, 2, 1]


def test_top_k_indices_empty_docs() -> None:
    sims = np.empty((2, 0))
    out = top_k_indices(sims, k=5)
    assert out.shape == (2, 0)


def test_top_k_indices_multiple_queries_independent() -> None:
    sims = np.array(
        [
            [0.1, 0.9, 0.5],
            [0.8, 0.1, 0.0],
        ]
    )
    out = top_k_indices(sims, k=2)
    assert out[0].tolist() == [1, 2]
    assert out[1].tolist() == [0, 1]


# ---- OpenAIBackend ----


def _fake_embeddings_response(vectors: list[list[float]]) -> SimpleNamespace:
    return SimpleNamespace(data=[SimpleNamespace(embedding=v) for v in vectors])


def test_openai_backend_calls_with_correct_model_and_dim() -> None:
    client = MagicMock()
    client.embeddings.create.return_value = _fake_embeddings_response(
        [[0.1] * 1536, [0.2] * 1536]
    )
    backend = OpenAIBackend(client=client, batch_size=10)
    out = backend.embed(["a", "b"])
    assert isinstance(out, np.ndarray)
    assert out.dtype == np.float32
    assert out.shape == (2, 1536)
    kwargs = client.embeddings.create.call_args.kwargs
    assert kwargs["model"] == backend.name
    assert kwargs["dimensions"] == 1536
    assert kwargs["input"] == ["a", "b"]


def test_openai_backend_batches() -> None:
    client = MagicMock()
    # 각 배치별로 다른 응답 반환
    client.embeddings.create.side_effect = [
        _fake_embeddings_response([[0.1] * 4, [0.2] * 4]),
        _fake_embeddings_response([[0.3] * 4]),
    ]
    # monkeypatch OPENAI_DIM 대신 수동 우회: backend.dim 덮어씀
    backend = OpenAIBackend(client=client, batch_size=2)
    backend.dim = 4  # 테스트 편의
    out = backend.embed(["x", "y", "z"])
    assert out.shape == (3, 4)
    assert client.embeddings.create.call_count == 2


# ---- BgeM3Backend (모델 주입으로 torch 우회) ----


def test_bge_backend_uses_injected_model() -> None:
    fake_model = MagicMock()
    fake_model.encode.return_value = np.ones((3, 1024), dtype=np.float32)
    backend = BgeM3Backend(model_name="mock/bge", model=fake_model)
    out = backend.embed(["a", "b", "c"])
    assert out.shape == (3, 1024)
    assert out.dtype == np.float32
    fake_model.encode.assert_called_once()


def test_bge_backend_converts_non_float32() -> None:
    fake_model = MagicMock()
    fake_model.encode.return_value = np.ones((2, 1024), dtype=np.float64)
    backend = BgeM3Backend(model_name="mock/bge", model=fake_model)
    out = backend.embed(["a", "b"])
    assert out.dtype == np.float32


# ---- evaluate (end-to-end, 가짜 백엔드) ----


class _DummyBackend:
    """쿼리/청크 텍스트 → 하드코딩 벡터 매핑."""

    name = "dummy"
    dim = 2

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self._mapping = mapping

    def embed(self, texts):
        return np.array(
            [self._mapping[t] for t in texts], dtype=np.float32
        )


def test_evaluate_computes_correct_ranks_and_recall() -> None:
    # 청크 3개, heading 각 8471/6109/8471
    chunks_texts = ["c_8471a", "c_6109", "c_8471b"]
    chunks_headings = ["8471", "6109", "8471"]

    # query1: 8471 에 가까움 (정답 8471)
    # query2: 6109 에 가까움 (정답 6109)
    # query3: 8471 에 가깝지만 gt=9999 → miss
    eval_records = [
        EvalRecord(id="q1", query="q1", gt_heading="8471", source="clip_case"),
        EvalRecord(id="q2", query="q2", gt_heading="6109", source="clip_case"),
        EvalRecord(id="q3", query="q3", gt_heading="9999", source="clip_case"),
    ]

    mapping = {
        "c_8471a": [1.0, 0.0],
        "c_6109": [0.0, 1.0],
        "c_8471b": [0.9, 0.1],
        "q1": [1.0, 0.0],  # 가장 가까운 건 c_8471a (8471)
        "q2": [0.0, 1.0],  # 가장 가까운 건 c_6109 (6109)
        "q3": [1.0, 0.0],  # 8471 만 상위 → 9999 없음
    }

    result = evaluate(
        _DummyBackend(mapping),
        chunks_texts,
        chunks_headings,
        eval_records,
        k=3,
    )

    assert result.total == 3
    assert result.ranks[0] == 1  # q1 → 8471 rank 1
    assert result.ranks[1] == 1  # q2 → 6109 rank 1
    assert result.ranks[2] is None  # q3 → miss
    assert result.hit_at_5 == 2
    assert result.recall_at_5 == pytest.approx(2 / 3)
    # MRR = (1 + 1 + 0) / 3
    assert result.mrr == pytest.approx(2 / 3)


# ---- build_per_query ----


def test_build_per_query_merges_ranks_across_backends() -> None:
    eval_records = [
        EvalRecord(id="q1", query="가", gt_heading="8471", source="clip_case"),
        EvalRecord(id="q2", query="나", gt_heading="6109", source="manual"),
    ]

    def _result(name: str, ranks: list[int | None]) -> Any:
        from scripts.compare_embeddings import BackendResult

        return BackendResult(
            backend=name,
            dim=1,
            total=2,
            hit_at_5=0,
            hit_at_10=0,
            recall_at_5=0.0,
            recall_at_10=0.0,
            mrr=0.0,
            ranks=ranks,
            retrieved_headings=[[], []],
        )

    results = {
        "openai": _result("openai", [1, None]),
        "bge": _result("bge", [3, 2]),
    }
    rows = build_per_query(eval_records, results)
    assert rows[0]["id"] == "q1"
    assert rows[0]["ranks"] == {"openai": 1, "bge": 3}
    assert rows[1]["ranks"] == {"openai": None, "bge": 2}


# ---- format_table ----


def test_format_table_contains_all_metrics() -> None:
    from scripts.compare_embeddings import BackendResult

    r = BackendResult(
        backend="openai",
        dim=1536,
        total=10,
        hit_at_5=7,
        hit_at_10=9,
        recall_at_5=0.7,
        recall_at_10=0.9,
        mrr=0.55,
    )
    report = CompareReport(k=10, eval_set_size=10, results={"openai": r})
    table = format_table(report)
    assert "recall@5" in table
    assert "recall@10" in table
    assert "MRR" in table
    assert "0.700" in table
    assert "0.550" in table
    assert "1536" in table
