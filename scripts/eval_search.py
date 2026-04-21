"""검색 품질 측정 (Recall@K · MRR).

``data/eval_set.jsonl`` 의 각 query 를 OpenAI 로 임베딩한 뒤 pgvector
``note_chunks`` 코사인 유사도 검색을 수행. Top-K 검색 결과의 heading 이
``gt_heading`` 과 매치하는지로 Recall@5, Recall@10, MRR 계산.

설계 결정
---------
- **평가 단위**: heading(4자리). Note chunks 는 heading 단위로 인덱싱되므로.
- **매치 기준**: top-K 결과 중 첫 번째 heading==gt_heading 의 **rank**.
  동일 heading 의 chunk 가 중복 등장해도 최초 rank 만 기록.
- **쿼리 임베딩 모델**: ``scripts.build_embeddings.EMBED_MODEL`` 을 재사용 —
  인덱스를 만든 모델과 동일해야 의미가 있음.

사용 예::

    python -m scripts.eval_search --eval-set data/eval_set.jsonl --k 10
    python -m scripts.eval_search --out reports/eval_search_2026-04-21.json
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api.core.config import settings
from api.db.models import ExplanatoryNote, NoteChunk
from scripts.build_embeddings import (
    EMBED_DIM,
    EMBED_MODEL,
    _openai_client,
    embed_batch,
)
from scripts.build_eval_set import EvalRecord
from scripts.build_index import _sync_db_url

logger = logging.getLogger(__name__)


# ---- 메트릭 (순수 함수) ----


def rank_of_first_hit(headings: list[str], gt_heading: str) -> int | None:
    """``headings`` 리스트에서 ``gt_heading`` 과 일치하는 첫 위치 (1-indexed).

    없으면 ``None``.
    """
    for i, h in enumerate(headings, 1):
        if h == gt_heading:
            return i
    return None


def recall_at_k(ranks: list[int | None], k: int) -> float:
    """rank ≤ k 인 레코드 비율. ``ranks`` 가 비어있으면 0.0."""
    if not ranks:
        return 0.0
    hit = sum(1 for r in ranks if r is not None and r <= k)
    return hit / len(ranks)


def mean_reciprocal_rank(ranks: list[int | None]) -> float:
    """Mean Reciprocal Rank. 미스는 0 으로 계산."""
    if not ranks:
        return 0.0
    total = sum((1.0 / r) for r in ranks if r is not None)
    return total / len(ranks)


# ---- 결과 구조 ----


@dataclass
class PerQueryResult:
    id: str
    query: str
    gt_heading: str
    retrieved_headings: list[str]
    rank_of_first_hit: int | None
    source: str


@dataclass
class EvalSummary:
    total: int
    hit_at_5: int
    hit_at_10: int
    recall_at_5: float
    recall_at_10: float
    mrr: float
    k: int
    model: str
    dim: int
    per_query: list[PerQueryResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


# ---- 평가셋 I/O ----


def load_eval_set(path: Path) -> list[EvalRecord]:
    if not path.exists():
        raise FileNotFoundError(f"평가셋 파일 없음: {path}")
    records: list[EvalRecord] = []
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            try:
                records.append(EvalRecord(**raw))
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s:%s EvalRecord 파싱 실패: %s", path.name, i, exc)
    return records


# ---- 검색 ----


def search_headings(
    session: Session, query_vec: list[float], k: int
) -> list[str]:
    """쿼리 임베딩으로 note_chunks cosine 검색, 매칭 heading 목록 반환 (rank 순)."""
    stmt = (
        select(
            ExplanatoryNote.heading,
            NoteChunk.embedding.cosine_distance(query_vec).label("dist"),
        )
        .join(ExplanatoryNote, NoteChunk.note_id == ExplanatoryNote.id)
        .where(NoteChunk.embedding.is_not(None))
        .order_by(NoteChunk.embedding.cosine_distance(query_vec))
        .limit(k)
    )
    rows = session.execute(stmt).all()
    return [row.heading for row in rows]


# ---- 실행 ----


def run_eval(
    eval_records: list[EvalRecord],
    session: Session,
    openai_client,
    k: int,
    batch_size: int = 50,
) -> EvalSummary:
    # 1) 쿼리 일괄 임베딩 (배치)
    queries = [r.query for r in eval_records]
    vectors: list[list[float]] = []
    for start in range(0, len(queries), batch_size):
        batch = queries[start : start + batch_size]
        vectors.extend(embed_batch(openai_client, batch))
        print(f"  embed progress: {min(start + batch_size, len(queries))}/{len(queries)}")

    # 2) 각 쿼리에 대해 pgvector 검색 + rank 계산
    per_q: list[PerQueryResult] = []
    ranks: list[int | None] = []
    for rec, vec in zip(eval_records, vectors):
        retrieved = search_headings(session, vec, k)
        rnk = rank_of_first_hit(retrieved, rec.gt_heading)
        ranks.append(rnk)
        per_q.append(
            PerQueryResult(
                id=rec.id,
                query=rec.query[:100],
                gt_heading=rec.gt_heading,
                retrieved_headings=retrieved,
                rank_of_first_hit=rnk,
                source=rec.source,
            )
        )

    summary = EvalSummary(
        total=len(eval_records),
        hit_at_5=sum(1 for r in ranks if r is not None and r <= 5),
        hit_at_10=sum(1 for r in ranks if r is not None and r <= 10),
        recall_at_5=recall_at_k(ranks, 5),
        recall_at_10=recall_at_k(ranks, 10),
        mrr=mean_reciprocal_rank(ranks),
        k=k,
        model=EMBED_MODEL,
        dim=EMBED_DIM,
        per_query=per_q,
    )
    return summary


def print_summary(s: EvalSummary) -> None:
    print(
        f"\n[RESULT] total={s.total} "
        f"recall@5={s.recall_at_5:.3f} ({s.hit_at_5}/{s.total})  "
        f"recall@10={s.recall_at_10:.3f} ({s.hit_at_10}/{s.total})  "
        f"MRR={s.mrr:.3f}  k={s.k}  model={s.model}"
    )
    misses = [q for q in s.per_query if q.rank_of_first_hit is None]
    if misses:
        print(f"\n[MISSES] {len(misses)} 건:")
        for m in misses[:10]:
            print(f"  {m.id} gt={m.gt_heading} retrieved={m.retrieved_headings[:5]} | {m.query[:60]}")
        if len(misses) > 10:
            print(f"  ... (+{len(misses) - 10} more)")


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    parser = argparse.ArgumentParser(description="검색 품질 측정 (Recall@K, MRR)")
    parser.add_argument("--eval-set", default="data/eval_set.jsonl")
    parser.add_argument("--k", type=int, default=10, help="검색 상한 (기본 10)")
    parser.add_argument("--batch-size", type=int, default=50, help="쿼리 임베딩 배치 크기")
    parser.add_argument("--out", default=None, help="결과 JSON 저장 경로")
    args = parser.parse_args()

    eval_records = load_eval_set(Path(args.eval_set))
    if not eval_records:
        print("[ERROR] 평가셋이 비어있음", file=sys.stderr)
        return 2
    print(f"[EVAL-SET] {len(eval_records)} records loaded from {args.eval_set}")

    if not settings.openai_api_key:
        print("[ERROR] OPENAI_API_KEY 미설정 (.env)", file=sys.stderr)
        return 2

    client = _openai_client()
    engine = create_engine(_sync_db_url(settings.database_url), pool_pre_ping=True)

    with Session(engine) as session:
        summary = run_eval(eval_records, session, client, args.k, args.batch_size)

    print_summary(summary)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(summary.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n[SAVED] {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
