"""임베딩 모델 비교 벤치마크 (``text-embedding-3-large`` vs ``bge-m3-ko``).

설계 결정
---------
- **Brute-force in-memory cosine**: 벤치마크 공정성을 위해 HNSW 대신 정확 검색.
  50 쿼리 × 수천 청크 규모에서 충분. pgvector 지연/리콜 트레이드오프는 별도 track.
- **스키마 변경 없음**: bge 임베딩은 메모리에만 상주. 벤치마크 후 선택된 모델만
  ``note_chunks.embedding`` 에 영구 적재.
- **백엔드 추상화**: ``EmbeddingBackend`` (openai / bge). bge 는
  ``sentence-transformers`` 옵션 의존 — 미설치 시 실행 시점에 안내.

사전 준비
---------
- ``data/eval_set.jsonl`` (``scripts.build_eval_set`` 산출물)
- DB 의 ``note_chunks`` + ``explanatory_notes`` 에 텍스트 적재 (``scripts.build_index notes ...``)
- ``OPENAI_API_KEY`` (openai 백엔드)
- ``pip install 'sentence-transformers>=2.7'`` (bge 백엔드)

사용 예::

    # 둘 다 비교 (기본)
    python -m scripts.compare_embeddings

    # 한쪽만
    python -m scripts.compare_embeddings --backends openai
    python -m scripts.compare_embeddings --backends bge

    # bge 모델 교체
    python -m scripts.compare_embeddings --bge-model BAAI/bge-m3

    # 결과 저장
    python -m scripts.compare_embeddings --out reports/compare_2026-04-21.json
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

import numpy as np
from dotenv import load_dotenv
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api.core.config import settings
from api.db.models import ExplanatoryNote, NoteChunk
from scripts.build_embeddings import (
    EMBED_DIM as OPENAI_DIM,
    EMBED_MODEL as OPENAI_MODEL,
    _openai_client,
)
from scripts.build_eval_set import EvalRecord
from scripts.build_index import _sync_db_url
from scripts.eval_search import (
    mean_reciprocal_rank,
    rank_of_first_hit,
    recall_at_k,
)

logger = logging.getLogger(__name__)

DEFAULT_BGE_MODEL = "dragonkue/bge-m3-ko"
DEFAULT_K = 10


# ---- 백엔드 ----


class EmbeddingBackend(Protocol):
    name: str
    dim: int

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """``(len(texts), dim)`` float32 numpy 배열 반환."""


class OpenAIBackend:
    """OpenAI ``text-embedding-3-large`` (dim=1536, Matryoshka)."""

    name = OPENAI_MODEL
    dim = OPENAI_DIM

    def __init__(self, client: Any = None, batch_size: int = 100) -> None:
        self._client = client or _openai_client()
        self._batch_size = batch_size

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            resp = self._client.embeddings.create(
                model=self.name, input=list(batch), dimensions=self.dim
            )
            vectors.extend(d.embedding for d in resp.data)
        return np.asarray(vectors, dtype=np.float32)


class BgeM3Backend:
    """Korean 파인튠 bge-m3 (기본 ``dragonkue/bge-m3-ko``, dim=1024).

    ``sentence-transformers`` 패키지 필요. 미설치 시 생성자에서 RuntimeError.
    """

    dim = 1024

    def __init__(
        self,
        model_name: str = DEFAULT_BGE_MODEL,
        batch_size: int = 32,
        model: Any = None,
    ) -> None:
        self.name = model_name
        self._batch_size = batch_size
        if model is not None:
            self._model = model
            return
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "bge 백엔드는 sentence-transformers 필요: "
                "pip install 'sentence-transformers>=2.7'"
            ) from exc
        logger.info("bge 모델 로드 중: %s", model_name)
        self._model = SentenceTransformer(model_name)

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        vec = self._model.encode(
            list(texts),
            batch_size=self._batch_size,
            normalize_embeddings=False,  # normalize 는 우리 쪽에서 통일
            convert_to_numpy=True,
        )
        return np.asarray(vec, dtype=np.float32)


# ---- 수치 유틸 ----


def normalize(vectors: np.ndarray) -> np.ndarray:
    """L2 정규화. 0 벡터는 그대로."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    safe = np.where(norms == 0, 1.0, norms)
    return vectors / safe


def top_k_indices(sims: np.ndarray, k: int) -> np.ndarray:
    """``(n_queries, n_docs)`` → ``(n_queries, k)`` top-k (내림차순).

    k 가 n_docs 이상이면 전체를 정렬한다.
    """
    n_docs = sims.shape[1]
    if n_docs == 0:
        return np.empty((sims.shape[0], 0), dtype=np.int64)
    k_eff = min(k, n_docs)
    if k_eff == n_docs:
        return np.argsort(-sims, axis=1)
    part = np.argpartition(-sims, k_eff - 1, axis=1)[:, :k_eff]
    row_idx = np.arange(sims.shape[0])[:, None]
    order = np.argsort(-sims[row_idx, part], axis=1)
    return part[row_idx, order]


# ---- 결과 구조 ----


@dataclass
class BackendResult:
    backend: str
    dim: int
    total: int
    hit_at_5: int
    hit_at_10: int
    recall_at_5: float
    recall_at_10: float
    mrr: float
    ranks: list[int | None] = field(default_factory=list)
    retrieved_headings: list[list[str]] = field(default_factory=list)


@dataclass
class CompareReport:
    k: int
    eval_set_size: int
    results: dict[str, BackendResult] = field(default_factory=dict)
    # per-query: id, query, gt_heading, ranks{backend: rank}
    per_query: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "k": self.k,
            "eval_set_size": self.eval_set_size,
            "results": {k: asdict(v) for k, v in self.results.items()},
            "per_query": self.per_query,
        }
        return d


# ---- 평가 코어 ----


def evaluate(
    backend: EmbeddingBackend,
    chunks_texts: Sequence[str],
    chunks_headings: Sequence[str],
    eval_records: list[EvalRecord],
    k: int,
) -> BackendResult:
    """한 백엔드로 chunk + query 임베딩 후 top-K heading rank 계산."""
    print(f"\n[{backend.name}] chunks={len(chunks_texts)} queries={len(eval_records)} dim={backend.dim}")
    chunk_vecs = normalize(backend.embed(chunks_texts))
    query_vecs = normalize(backend.embed([r.query for r in eval_records]))

    sims = query_vecs @ chunk_vecs.T  # (n_q, n_c), cosine sim (정규화 후 내적)
    top = top_k_indices(sims, k)

    ranks: list[int | None] = []
    retrieved: list[list[str]] = []
    for i, rec in enumerate(eval_records):
        h_list = [chunks_headings[idx] for idx in top[i]]
        retrieved.append(h_list)
        ranks.append(rank_of_first_hit(h_list, rec.gt_heading))

    result = BackendResult(
        backend=backend.name,
        dim=backend.dim,
        total=len(eval_records),
        hit_at_5=sum(1 for r in ranks if r is not None and r <= 5),
        hit_at_10=sum(1 for r in ranks if r is not None and r <= 10),
        recall_at_5=recall_at_k(ranks, 5),
        recall_at_10=recall_at_k(ranks, 10),
        mrr=mean_reciprocal_rank(ranks),
        ranks=ranks,
        retrieved_headings=retrieved,
    )
    print(
        f"[{backend.name}] recall@5={result.recall_at_5:.3f} "
        f"recall@10={result.recall_at_10:.3f} MRR={result.mrr:.3f}"
    )
    return result


# ---- 리포트 ----


def format_table(report: CompareReport) -> str:
    """side-by-side 텍스트 테이블."""
    rows = list(report.results.values())
    if not rows:
        return "(no results)"
    headers = ["metric"] + [r.backend for r in rows]
    lines = [
        "\n| " + " | ".join(headers) + " |",
        "|" + "|".join("-" * (len(h) + 2) for h in headers) + "|",
    ]

    def row(label: str, values: list[str]) -> str:
        return "| " + " | ".join([label] + values) + " |"

    lines.append(row("dim", [str(r.dim) for r in rows]))
    lines.append(row("hit@5", [f"{r.hit_at_5}/{r.total}" for r in rows]))
    lines.append(row("recall@5", [f"{r.recall_at_5:.3f}" for r in rows]))
    lines.append(row("hit@10", [f"{r.hit_at_10}/{r.total}" for r in rows]))
    lines.append(row("recall@10", [f"{r.recall_at_10:.3f}" for r in rows]))
    lines.append(row("MRR", [f"{r.mrr:.3f}" for r in rows]))
    return "\n".join(lines)


def build_per_query(
    eval_records: list[EvalRecord],
    results: dict[str, BackendResult],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    backends = list(results.keys())
    for i, rec in enumerate(eval_records):
        row: dict[str, Any] = {
            "id": rec.id,
            "query": rec.query[:100],
            "gt_heading": rec.gt_heading,
            "source": rec.source,
            "ranks": {b: results[b].ranks[i] for b in backends},
        }
        out.append(row)
    return out


# ---- DB 로더 ----


def load_chunks(session: Session) -> tuple[list[str], list[str]]:
    """``note_chunks.text`` 와 대응되는 ``explanatory_notes.heading`` 리턴."""
    stmt = (
        select(NoteChunk.text, ExplanatoryNote.heading)
        .join(ExplanatoryNote, NoteChunk.note_id == ExplanatoryNote.id)
        .order_by(NoteChunk.id)
    )
    rows = session.execute(stmt).all()
    texts = [r.text for r in rows]
    headings = [r.heading for r in rows]
    return texts, headings


def load_eval_set(path: Path) -> list[EvalRecord]:
    if not path.exists():
        raise FileNotFoundError(f"평가셋 파일 없음: {path}")
    out: list[EvalRecord] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(EvalRecord(**json.loads(line)))
    return out


# ---- CLI ----


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    parser = argparse.ArgumentParser(description="임베딩 모델 비교 벤치마크")
    parser.add_argument("--eval-set", default="data/eval_set.jsonl")
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument(
        "--backends",
        default="openai,bge",
        help="쉼표 구분. 'openai' / 'bge' (기본: openai,bge)",
    )
    parser.add_argument("--bge-model", default=DEFAULT_BGE_MODEL)
    parser.add_argument("--out", default=None, help="결과 JSON 저장 경로")
    args = parser.parse_args()

    wanted = [b.strip() for b in args.backends.split(",") if b.strip()]
    unknown = [b for b in wanted if b not in {"openai", "bge"}]
    if unknown:
        print(f"[ERROR] 알 수 없는 백엔드: {unknown}", file=sys.stderr)
        return 2

    eval_records = load_eval_set(Path(args.eval_set))
    if not eval_records:
        print("[ERROR] 평가셋 비어있음", file=sys.stderr)
        return 2
    print(f"[EVAL-SET] {len(eval_records)} records from {args.eval_set}")

    engine = create_engine(_sync_db_url(settings.database_url), pool_pre_ping=True)
    with Session(engine) as session:
        chunks_texts, chunks_headings = load_chunks(session)
    if not chunks_texts:
        print("[ERROR] note_chunks 비어있음 — build_index notes 먼저 실행", file=sys.stderr)
        return 2
    print(f"[CHUNKS] {len(chunks_texts)} chunks loaded from DB")

    report = CompareReport(k=args.k, eval_set_size=len(eval_records))

    for b in wanted:
        if b == "openai":
            if not settings.openai_api_key:
                print("[WARN] OPENAI_API_KEY 미설정 — openai 백엔드 skip")
                continue
            backend: EmbeddingBackend = OpenAIBackend()
        else:
            backend = BgeM3Backend(model_name=args.bge_model)
        result = evaluate(backend, chunks_texts, chunks_headings, eval_records, args.k)
        report.results[backend.name] = result

    report.per_query = build_per_query(eval_records, report.results)

    print(format_table(report))

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n[SAVED] {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
