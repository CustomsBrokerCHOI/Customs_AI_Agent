"""RAG 인덱스 적재 ETL (Phase 2, 임베딩 전 단계).

두 가지 서브커맨드 제공.

1. ``hs-codes`` — ``data/item_master.csv`` → ``hs_codes`` (+ ``tariff_rates`` FTA=A)
2. ``notes`` — ``scripts.dev_probe_clip`` 가 생성한 해설서 JSON → ``explanatory_notes`` + ``note_chunks``

임베딩(``NoteChunk.embedding``)은 이 단계에서 ``NULL`` 로 남긴다.
다음 스프린트의 임베딩 파이프라인이 값을 채운다.

사용 예::

    python -m scripts.build_index hs-codes --csv data/item_master.csv
    python -m scripts.build_index notes data/cache/clip_note_8471_2022_*.json
    python -m scripts.build_index notes data/cache/clip_note_8471_2022.json --replace-chunks

DB 연결은 ``.env`` 의 ``DATABASE_URL`` 을 사용. ``+asyncpg`` 드라이버는 자동으로
``+psycopg`` (sync) 로 변환된다.
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from api.core.config import settings
from api.db.models import ExplanatoryNote, HSCode, NoteChunk, TariffRate
from scripts.data_manager import DataManager

logger = logging.getLogger(__name__)

KIND_MAP = {
    "general_rule": "general_rule",
    "section_note": "section_note",
    "chapter_note": "chapter_note",
    "heading_note": "heading_note",
}


def _sync_db_url(url: str) -> str:
    """asyncpg 드라이버 지정 URL 을 동기(psycopg) 로 치환."""
    return url.replace("+asyncpg", "+psycopg")


def _split_hs(hs10: str) -> tuple[str, str, str] | None:
    """10자리 HS → (heading4, sub2, line4). 포맷 불량이면 None."""
    s = str(hs10).strip()
    if len(s) != 10 or not s.isdigit():
        return None
    return s[:4], s[4:6], s[6:]


def upsert_hs_codes(session: Session, csv_path: Path) -> tuple[int, int]:
    """``item_master.csv`` 를 ``hs_codes`` + ``tariff_rates`` 로 upsert.

    :returns: ``(hs_upserted, tariff_upserted)``
    """
    df = pd.read_csv(csv_path, dtype={"hs_code": str})
    hs_rows: list[dict[str, Any]] = []
    rate_rows: list[dict[str, Any]] = []
    for rec in df.to_dict(orient="records"):
        hs10 = str(rec.get("hs_code") or "").strip()
        parts = _split_hs(hs10)
        if parts is None:
            logger.warning("HS 10자리 포맷 불량 건너뜀: %r", hs10)
            continue
        heading, sub, line = parts
        hs_rows.append(
            {
                "hs_code": hs10,
                "heading": heading,
                "sub_heading": sub,
                "tariff_line": line,
                "name_kr": _nn(rec.get("name_kr")),
                "name_en": _nn(rec.get("name_en")),
                "source": _nn(rec.get("source")) or "UNIPASS",
            }
        )
        base_rate = rec.get("base_rate")
        if base_rate is not None and not pd.isna(base_rate):
            try:
                rate_val = float(base_rate)
            except (TypeError, ValueError):
                rate_val = None
            if rate_val is not None:
                rate_rows.append(
                    {
                        "hs_code": hs10,
                        "fta_code": "A",
                        "fta_name": "기본세율",
                        "tax_rate": rate_val,
                        "source": "UNIPASS",
                    }
                )

    if not hs_rows:
        return 0, 0

    hs_stmt = pg_insert(HSCode).values(hs_rows)
    hs_stmt = hs_stmt.on_conflict_do_update(
        index_elements=[HSCode.hs_code],
        set_={
            "name_kr": hs_stmt.excluded.name_kr,
            "name_en": hs_stmt.excluded.name_en,
            "heading": hs_stmt.excluded.heading,
            "sub_heading": hs_stmt.excluded.sub_heading,
            "tariff_line": hs_stmt.excluded.tariff_line,
            "source": hs_stmt.excluded.source,
        },
    )
    session.execute(hs_stmt)

    tariff_count = 0
    if rate_rows:
        # apply_start 가 NULL 이라 uq_tariff_hs_fta_start 으로 on_conflict 를 쓸 수 없어
        # 존재 여부 확인 후 insert.
        for row in rate_rows:
            exists = session.execute(
                select(TariffRate.id).where(
                    TariffRate.hs_code == row["hs_code"],
                    TariffRate.fta_code == row["fta_code"],
                    TariffRate.apply_start.is_(None),
                )
            ).first()
            if exists:
                session.execute(
                    TariffRate.__table__.update()
                    .where(
                        TariffRate.hs_code == row["hs_code"],
                        TariffRate.fta_code == row["fta_code"],
                        TariffRate.apply_start.is_(None),
                    )
                    .values(tax_rate=row["tax_rate"], fta_name=row["fta_name"])
                )
            else:
                session.execute(pg_insert(TariffRate).values(row))
            tariff_count += 1

    return len(hs_rows), tariff_count


def upsert_notes_from_json(
    session: Session, json_path: Path, replace_chunks: bool = False
) -> tuple[int, int]:
    """``dev_probe_clip`` JSON 한 건 → ``explanatory_notes`` + ``note_chunks``.

    :returns: ``(notes_upserted, chunks_inserted)``
    """
    data = json.loads(json_path.read_text(encoding="utf-8"))
    heading: str = str(data.get("heading") or "").strip()
    if len(heading) != 4 or not heading.isdigit():
        raise ValueError(f"JSON 의 heading 이 4자리가 아님: {heading!r}")
    year_raw = data.get("year") or 2022
    try:
        hsk_year = int(str(year_raw)[:4])
    except ValueError:
        hsk_year = 2022

    source = (data.get("metadata") or {}).get("source") or "CLIP"

    notes_upserted = 0
    chunks_inserted = 0
    for kind_key, kind_name in KIND_MAP.items():
        bi = data.get(kind_key) or {}
        for lang in ("ko", "en"):
            content = (bi.get(lang) or "").strip()
            if not content:
                continue
            note_id = _upsert_note_row(
                session,
                heading=heading,
                kind=kind_name,
                lang=lang,
                hsk_year=hsk_year,
                content=content,
                source=source,
            )
            notes_upserted += 1

            if replace_chunks:
                session.execute(
                    delete(NoteChunk).where(NoteChunk.note_id == note_id)
                )

            chunks = DataManager.chunk_explanatory_note(
                {
                    "heading": heading,
                    # 단일 kind 만 content 로 넘겨서 해당 항목만 청킹
                    "content": content,
                    "metadata": {
                        "heading": heading,
                        "kind": kind_name,
                        "lang": lang,
                        "hsk_year": hsk_year,
                        "source": source,
                    },
                }
            )
            if not chunks:
                continue
            session.execute(
                pg_insert(NoteChunk).values(
                    [
                        {
                            "note_id": note_id,
                            "chunk_index": idx,
                            "text": ch["text"],
                            "metadata_": ch["metadata"],
                        }
                        for idx, ch in enumerate(chunks)
                    ]
                )
            )
            chunks_inserted += len(chunks)

    return notes_upserted, chunks_inserted


def _upsert_note_row(
    session: Session,
    heading: str,
    kind: str,
    lang: str,
    hsk_year: int,
    content: str,
    source: str,
) -> Any:
    """(heading, kind, lang, hsk_year) 유니크 키로 upsert 후 id 반환."""
    stmt = pg_insert(ExplanatoryNote).values(
        heading=heading,
        kind=kind,
        lang=lang,
        hsk_year=hsk_year,
        content=content,
        source=source,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_note_scope",
        set_={"content": stmt.excluded.content, "source": stmt.excluded.source},
    ).returning(ExplanatoryNote.id)
    row = session.execute(stmt).first()
    if row is None:  # pragma: no cover
        raise RuntimeError("explanatory_notes upsert 결과 없음")
    return row[0]


def _nn(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    s = str(v).strip()
    return s or None


def _expand_paths(patterns: Iterable[str]) -> list[Path]:
    out: list[Path] = []
    for pat in patterns:
        matches = glob.glob(pat)
        if not matches:
            p = Path(pat)
            if p.exists():
                out.append(p)
            else:
                logger.warning("경로 매치 없음: %s", pat)
            continue
        out.extend(Path(m) for m in matches)
    return out


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    parser = argparse.ArgumentParser(description="RAG 인덱스 ETL (임베딩 제외)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_hs = sub.add_parser("hs-codes", help="item_master.csv → hs_codes/tariff_rates")
    p_hs.add_argument("--csv", default="data/item_master.csv")

    p_notes = sub.add_parser("notes", help="CLIP 해설서 JSON → explanatory_notes/note_chunks")
    p_notes.add_argument("paths", nargs="+", help="dev_probe_clip JSON 파일 또는 glob")
    p_notes.add_argument(
        "--replace-chunks",
        action="store_true",
        help="기존 note_chunks 삭제 후 재삽입 (chunking 파라미터 변경 시)",
    )

    args = parser.parse_args()

    engine = create_engine(_sync_db_url(settings.database_url), pool_pre_ping=True)

    with Session(engine) as session:
        if args.cmd == "hs-codes":
            csv_path = Path(args.csv)
            if not csv_path.exists():
                print(f"[ERROR] CSV 없음: {csv_path}", file=sys.stderr)
                return 2
            hs_n, rate_n = upsert_hs_codes(session, csv_path)
            session.commit()
            print(f"[HS] upserted={hs_n}, tariff_rates={rate_n}")
        elif args.cmd == "notes":
            paths = _expand_paths(args.paths)
            if not paths:
                print("[ERROR] 처리할 JSON 없음", file=sys.stderr)
                return 2
            total_notes = total_chunks = 0
            for p in paths:
                n, c = upsert_notes_from_json(
                    session, p, replace_chunks=args.replace_chunks
                )
                total_notes += n
                total_chunks += c
                print(f"[NOTE] {p.name}: notes={n}, chunks={c}")
            session.commit()
            print(f"[TOTAL] notes={total_notes}, chunks={total_chunks}")
        else:  # pragma: no cover
            parser.error(f"unknown cmd: {args.cmd}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
