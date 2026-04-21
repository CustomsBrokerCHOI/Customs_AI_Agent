"""임베딩 생성 ETL (Phase 2).

``note_chunks.embedding`` 과 ``classification_cases.embedding`` 을 OpenAI
``text-embedding-3-large`` (dim=1536, Matryoshka 압축) 로 채운다.

설계 결정
---------
- **모델**: ``text-embedding-3-large`` + ``dimensions=1536``.
  스키마 ``Vector(1536)`` 에 맞추기 위해 Matryoshka 로 truncate.
  bge-m3-ko 는 후속 벤치마크 후 검토.
- **버전 관리**: ``EmbeddingVersion`` 테이블. active 는 한 행.
  새 버전 등록 시 기존 active 를 False 로 내림.
- **Idempotency**: 기본은 ``embedding IS NULL OR embedding_version != active_id`` 만 대상.
  ``--force`` 로 전체 재임베딩.
- **부분 저장**: 배치마다 commit — 중단되어도 다음 실행이 이어받음.

사용 예::

    # 비용 견적만 (API 호출 없음)
    python -m scripts.build_embeddings notes --dry-run
    python -m scripts.build_embeddings cases --dry-run

    # 실행 (확인 프롬프트 skip)
    python -m scripts.build_embeddings notes --yes

    # 모델 변경 시 전체 재임베딩
    python -m scripts.build_embeddings notes --force --yes

    # 디버그: 처음 N개만
    python -m scripts.build_embeddings notes --limit 20 --yes
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
import time
from dataclasses import dataclass
from typing import Sequence

from dotenv import load_dotenv
from sqlalchemy import create_engine, or_, select, update
from sqlalchemy.orm import Session

from api.core.config import settings
from api.db.models import ClassificationCase, EmbeddingVersion, NoteChunk
from scripts.build_index import _sync_db_url

logger = logging.getLogger(__name__)


# ---- 모델 상수 ----

EMBED_MODEL = "text-embedding-3-large"
EMBED_DIM = 1536
EMBED_VERSION = "v1-2024-01"  # OpenAI 모델 스냅샷 식별자
EMBED_PRICE_PER_1M_TOKENS_USD = 0.13  # text-embedding-3-large (2024-01 기준)
TIKTOKEN_ENCODING = "cl100k_base"

DEFAULT_BATCH_SIZE = 100
DEFAULT_MAX_RETRY = 3


# ---- 버전 관리 ----


def ensure_active_version(
    session: Session,
    model_name: str = EMBED_MODEL,
    model_version: str = EMBED_VERSION,
    dimensions: int = EMBED_DIM,
) -> EmbeddingVersion:
    """active ``EmbeddingVersion`` 을 get-or-create. 기존 active 는 False 로 내림."""
    existing = session.execute(
        select(EmbeddingVersion).where(
            EmbeddingVersion.model_name == model_name,
            EmbeddingVersion.model_version == model_version,
        )
    ).scalar_one_or_none()

    if existing is None:
        session.execute(
            update(EmbeddingVersion)
            .where(EmbeddingVersion.is_active.is_(True))
            .values(is_active=False)
        )
        existing = EmbeddingVersion(
            model_name=model_name,
            model_version=model_version,
            dimensions=dimensions,
            is_active=True,
        )
        session.add(existing)
        session.flush()
        logger.info(
            "EmbeddingVersion 신규 등록: id=%s name=%s version=%s dim=%s",
            existing.id,
            model_name,
            model_version,
            dimensions,
        )
    elif not existing.is_active:
        session.execute(
            update(EmbeddingVersion)
            .where(EmbeddingVersion.is_active.is_(True))
            .values(is_active=False)
        )
        existing.is_active = True
        session.flush()
        logger.info("EmbeddingVersion id=%s 를 active 로 전환", existing.id)

    return existing


# ---- 대상 선택 ----


def _note_chunks_pending(
    session: Session, version_id: int, force: bool, limit: int | None
) -> list[NoteChunk]:
    stmt = select(NoteChunk)
    if not force:
        stmt = stmt.where(
            or_(
                NoteChunk.embedding.is_(None),
                NoteChunk.embedding_version != version_id,
            )
        )
    stmt = stmt.order_by(NoteChunk.id)
    if limit:
        stmt = stmt.limit(limit)
    return list(session.execute(stmt).scalars().all())


def _cases_pending(
    session: Session, version_id: int, force: bool, limit: int | None
) -> list[ClassificationCase]:
    stmt = select(ClassificationCase)
    if not force:
        stmt = stmt.where(
            or_(
                ClassificationCase.embedding.is_(None),
                ClassificationCase.embedding_version != version_id,
            )
        )
    stmt = stmt.order_by(ClassificationCase.id)
    if limit:
        stmt = stmt.limit(limit)
    return list(session.execute(stmt).scalars().all())


def _case_text(row: ClassificationCase) -> str:
    """``ClassificationCase`` 임베딩 대상 텍스트.

    product_name + description + reasoning 을 빈 줄로 이어 붙인다.
    None/빈 문자열은 건너뜀.
    """
    parts: list[str] = [row.product_name.strip()]
    if row.description and row.description.strip():
        parts.append(row.description.strip())
    if row.reasoning and row.reasoning.strip():
        parts.append(row.reasoning.strip())
    return "\n\n".join(parts)


# ---- 비용 견적 ----


@dataclass
class CostEstimate:
    row_count: int
    total_tokens: int
    estimated_usd: float


def estimate_cost(texts: Sequence[str]) -> CostEstimate:
    """tiktoken 으로 토큰 수를 세고 ``$/1M tokens`` 로 환산.

    tiktoken 미설치 시 ``len(text) // 4`` 근사.
    """
    try:
        import tiktoken  # type: ignore[import-not-found]

        enc = tiktoken.get_encoding(TIKTOKEN_ENCODING)
        total = sum(len(enc.encode(t)) for t in texts)
    except Exception as exc:  # noqa: BLE001
        logger.warning("tiktoken 미사용, len/4 근사로 폴백: %s", exc)
        total = sum(len(t) for t in texts) // 4
    usd = total * EMBED_PRICE_PER_1M_TOKENS_USD / 1_000_000
    return CostEstimate(row_count=len(texts), total_tokens=total, estimated_usd=usd)


# ---- OpenAI 호출 ----


def _openai_client():
    """OpenAI SDK 클라이언트. ``settings.openai_api_key`` 로 인증."""
    try:
        from openai import OpenAI  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("openai 패키지 미설치: pip install 'openai>=1.50'") from exc
    return OpenAI(api_key=settings.openai_api_key)


def embed_batch(
    client,
    texts: Sequence[str],
    model: str = EMBED_MODEL,
    dimensions: int = EMBED_DIM,
    max_retry: int = DEFAULT_MAX_RETRY,
) -> list[list[float]]:
    """OpenAI 임베딩 API 호출. 지수 백오프 재시도."""
    last_exc: Exception | None = None
    for attempt in range(max_retry):
        try:
            resp = client.embeddings.create(
                model=model,
                input=list(texts),
                dimensions=dimensions,
            )
            return [d.embedding for d in resp.data]
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            wait = 2**attempt
            logger.warning(
                "embed_batch 실패 attempt=%s/%s: %s (sleep %ss)",
                attempt + 1,
                max_retry,
                exc,
                wait,
            )
            time.sleep(wait)
    raise RuntimeError(f"embed_batch 최종 실패: {last_exc}")


# ---- 실행 ----


def run_notes(
    session: Session,
    client,
    version_id: int,
    rows: Sequence[NoteChunk],
    batch_size: int,
) -> int:
    if not rows:
        print("[NOTES] 대상 없음")
        return 0
    print(f"[NOTES] 임베딩 대상: {len(rows)} 청크")
    done = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        vectors = embed_batch(client, [r.text for r in batch])
        for r, v in zip(batch, vectors):
            r.embedding = v
            r.embedding_model = EMBED_MODEL
            r.embedding_version = version_id
        session.commit()
        done += len(batch)
        print(f"  progress: {done}/{len(rows)}")
    print(f"[NOTES] 완료: {done}")
    return done


def run_cases(
    session: Session,
    client,
    version_id: int,
    rows: Sequence[ClassificationCase],
    batch_size: int,
) -> int:
    if not rows:
        print("[CASES] 대상 없음")
        return 0
    print(f"[CASES] 임베딩 대상: {len(rows)} 건")
    done = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        texts = [_case_text(r) for r in batch]
        vectors = embed_batch(client, texts)
        for r, v in zip(batch, vectors):
            r.embedding = v
            r.embedding_model = EMBED_MODEL
            r.embedding_version = version_id
        session.commit()
        done += len(batch)
        print(f"  progress: {done}/{len(rows)}")
    print(f"[CASES] 완료: {done}")
    return done


# ---- CLI ----


def _confirm(msg: str, yes: bool) -> bool:
    if yes:
        return True
    try:
        answer = input(f"{msg} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="임베딩 생성 ETL (OpenAI text-embedding-3-large, 1536 dim)"
    )
    parser.add_argument(
        "target",
        choices=("notes", "cases"),
        help="대상 테이블 — notes=note_chunks, cases=classification_cases",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="API 호출 없이 대상 개수 + 토큰 + 비용 견적만",
    )
    parser.add_argument("--yes", action="store_true", help="확인 프롬프트 skip")
    parser.add_argument(
        "--force",
        action="store_true",
        help="기존 embedding 도 전부 재생성 (모델/버전 변경 시)",
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--limit", type=int, default=None, help="디버그용 처리 상한")

    args = parser.parse_args()

    engine = create_engine(_sync_db_url(settings.database_url), pool_pre_ping=True)

    with Session(engine) as session:
        version = ensure_active_version(session)
        version_id = version.id
        session.commit()

        if args.target == "notes":
            rows = _note_chunks_pending(session, version_id, args.force, args.limit)
            texts = [r.text for r in rows]
        else:
            rows = _cases_pending(session, version_id, args.force, args.limit)
            texts = [_case_text(r) for r in rows]

        est = estimate_cost(texts)
        print(
            f"[{args.target.upper()}] rows={est.row_count} "
            f"tokens={est.total_tokens:,} est_cost=${est.estimated_usd:.4f} "
            f"model={EMBED_MODEL} dim={EMBED_DIM} version_id={version_id}"
        )

        if args.dry_run or est.row_count == 0:
            return 0

        if not _confirm(
            f"{est.row_count} 건 임베딩 (약 ${est.estimated_usd:.4f}). 진행?",
            args.yes,
        ):
            print("취소됨.")
            return 1

        if not settings.openai_api_key:
            print("[ERROR] OPENAI_API_KEY 미설정 (.env).", file=sys.stderr)
            return 2

        client = _openai_client()

        if args.target == "notes":
            run_notes(session, client, version_id, rows, args.batch_size)
        else:
            run_cases(session, client, version_id, rows, args.batch_size)

    return 0


if __name__ == "__main__":
    sys.exit(main())
