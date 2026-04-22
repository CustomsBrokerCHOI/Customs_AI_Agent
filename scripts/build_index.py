"""RAG 인덱스 적재 ETL (Phase 2, 임베딩 전 단계).

세 가지 서브커맨드 제공.

1. ``hs-codes`` — ``data/item_master.csv`` → ``hs_codes`` (+ ``tariff_rates`` FTA=A)
2. ``notes`` — ``scripts.dev_probe_clip`` 가 생성한 해설서 JSON → ``explanatory_notes`` + ``note_chunks``
3. ``cases`` — ``scripts.dev_probe_cases`` 가 생성한 JSONL → ``classification_cases``

임베딩(``NoteChunk.embedding`` / ``ClassificationCase.embedding``)은 이 단계에서
``NULL`` 로 남긴다. 다음 스프린트의 임베딩 파이프라인이 값을 채운다.

사용 예::

    python -m scripts.build_index hs-codes --csv data/item_master.csv
    python -m scripts.build_index notes data/cache/clip_note_8471_2022_*.json
    python -m scripts.build_index notes data/cache/clip_note_8471_2022.json --replace-chunks
    python -m scripts.build_index cases data/cache/clip_cases_*.jsonl

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
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from api.core.config import settings
from api.db.models import (
    ClassificationCase,
    ExplanatoryNote,
    HSCode,
    NoteChunk,
    TariffRate,
)
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


def _parse_base_rate_pct(s: str | None) -> float | None:
    """CLIP 의 ``"8%"`` / ``"8"`` / ``"무세"`` → float. 파싱 불가면 None."""
    if not s:
        return None
    t = str(s).strip().rstrip("%").strip()
    if not t or t in {"무세", "N", "-"}:
        return 0.0 if t == "무세" else None
    try:
        return float(t)
    except ValueError:
        return None


def upsert_tariffs_from_cache(
    session: Session, cache_dir: Path
) -> tuple[int, int, int]:
    """``scripts.build_tariff_rates`` 가 생성한 JSON 캐시 전체 → DB upsert.

    캐시 구조: ``{cache_dir}/{heading}.json`` — ``TariffLine`` 배열.
    한 파일에 heading-level/subheading-level/10자리 행이 섞여 있음. 10자리만 적재.

    :returns: ``(hs_upserted, tariff_upserted, non_10digit_skipped)``
    """
    files = sorted(cache_dir.glob("*.json"))
    hs_rows: list[dict[str, Any]] = []
    rate_rows: list[dict[str, Any]] = []
    skipped = 0
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            logger.warning("JSON 파싱 실패: %s — 스킵", path)
            continue
        for rec in data:
            heading = (rec.get("heading") or "").strip()
            sub = (rec.get("sub_heading") or "").strip()
            line = (rec.get("tariff_line") or "").strip()
            hs10 = f"{heading}{sub}{line}"
            if len(hs10) != 10 or not hs10.isdigit():
                skipped += 1
                continue
            hs_rows.append(
                {
                    "hs_code": hs10,
                    "heading": heading,
                    "sub_heading": sub,
                    "tariff_line": line,
                    "name_kr": _nn(rec.get("name_kr")),
                    "name_en": _nn(rec.get("name_en")),
                    "source": "CLIP",
                }
            )
            br = _parse_base_rate_pct(rec.get("base_rate"))
            if br is not None:
                rate_rows.append(
                    {
                        "hs_code": hs10,
                        "fta_code": "A",
                        "fta_name": "기본세율",
                        "tax_rate": br,
                        "source": "CLIP",
                    }
                )

    if not hs_rows:
        return 0, 0, skipped

    # hs_codes: batched upsert
    BATCH = 1000
    for i in range(0, len(hs_rows), BATCH):
        chunk = hs_rows[i : i + BATCH]
        stmt = pg_insert(HSCode).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=[HSCode.hs_code],
            set_={
                "name_kr": stmt.excluded.name_kr,
                "name_en": stmt.excluded.name_en,
                "heading": stmt.excluded.heading,
                "sub_heading": stmt.excluded.sub_heading,
                "tariff_line": stmt.excluded.tariff_line,
                "source": stmt.excluded.source,
            },
        )
        session.execute(stmt)

    # tariff_rates: apply_start=NULL 행 기준 존재 여부 확인 후 update/insert
    tariff_count = 0
    for rr in rate_rows:
        exists = session.execute(
            select(TariffRate.id).where(
                TariffRate.hs_code == rr["hs_code"],
                TariffRate.fta_code == rr["fta_code"],
                TariffRate.apply_start.is_(None),
            )
        ).first()
        if exists:
            session.execute(
                TariffRate.__table__.update()
                .where(
                    TariffRate.hs_code == rr["hs_code"],
                    TariffRate.fta_code == rr["fta_code"],
                    TariffRate.apply_start.is_(None),
                )
                .values(tax_rate=rr["tax_rate"], fta_name=rr["fta_name"])
            )
        else:
            session.execute(pg_insert(TariffRate).values(rr))
        tariff_count += 1

    return len(hs_rows), tariff_count, skipped


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
                session.execute(delete(NoteChunk).where(NoteChunk.note_id == note_id))

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


def _parse_decision_date(v: Any) -> date | None:
    """문자열 날짜를 ``date`` 로 파싱. 파싱 실패 시 ``None``.

    CLIP 사이트는 ``YYYY-MM-DD`` / ``YYYY.MM.DD`` / ``YYYY/MM/DD`` / ``YYYYMMDD``
    등 다양한 표기를 쓰므로 관용적 파서를 사용한다.
    """
    if not v:
        return None
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_hs(v: Any) -> str | None:
    """HS 후보 문자열을 10자리 숫자만 남겨서 반환. 아니면 ``None``."""
    if not v:
        return None
    s = str(v).strip().replace("-", "").replace(".", "").replace(" ", "")
    if len(s) == 10 and s.isdigit():
        return s
    return None


def upsert_cases_from_jsonl(session: Session, jsonl_path: Path) -> tuple[int, int, int]:
    """``dev_probe_cases`` JSONL → ``classification_cases``.

    - ``hs_code`` 가 ``hs_codes`` 에 없으면 FK 오류 방지로 ``NULL`` 로 강등.
      (분류 사례 데이터는 HS 가 아직 적재 안 된 호도 포함할 수 있음.)
    - ``case_ref`` 가 있으면 ``on_conflict_do_update`` 로 업서트.
      없으면 unique 대상이 없어 매 실행마다 중복이 쌓일 수 있다(재실행 시 주의).
    - 빈 ``product_name`` 행은 건너뛴다.

    :returns: ``(inserted_or_updated, hs_code_nulled, total_rows)``
    """
    existing_hs: set[str] = {row[0] for row in session.execute(select(HSCode.hs_code)).all()}

    inserted = 0
    hs_nulled = 0
    total = 0

    with jsonl_path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            total += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("JSONL 파싱 실패: %s", line[:120])
                continue

            product_name = str(rec.get("product_name") or "").strip()
            if not product_name:
                logger.info("product_name 없음 → 건너뜀 (case_ref=%r)", rec.get("case_ref"))
                continue

            hs_code = _normalize_hs(rec.get("hs_code"))
            if hs_code and hs_code not in existing_hs:
                logger.info(
                    "hs_code %s 가 hs_codes 에 없음 → NULL 로 강등 (case_ref=%r)",
                    hs_code,
                    rec.get("case_ref"),
                )
                hs_code = None
                hs_nulled += 1

            case_ref = _nn(rec.get("case_ref"))
            row: dict[str, Any] = {
                "hs_code": hs_code,
                "case_ref": case_ref,
                "product_name": product_name,
                "description": _nn(rec.get("description")),
                "reasoning": _nn(rec.get("reasoning")),
                "decision_date": _parse_decision_date(rec.get("decision_date")),
                "source_url": _nn(rec.get("source_url")),
            }

            stmt = pg_insert(ClassificationCase).values(row)
            if case_ref:
                stmt = stmt.on_conflict_do_update(
                    index_elements=[ClassificationCase.case_ref],
                    set_={
                        "hs_code": stmt.excluded.hs_code,
                        "product_name": stmt.excluded.product_name,
                        "description": stmt.excluded.description,
                        "reasoning": stmt.excluded.reasoning,
                        "decision_date": stmt.excluded.decision_date,
                        "source_url": stmt.excluded.source_url,
                    },
                )
            session.execute(stmt)
            inserted += 1

    return inserted, hs_nulled, total


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

    p_t = sub.add_parser(
        "tariffs",
        help="build_tariff_rates 캐시(JSON) → hs_codes/tariff_rates",
    )
    p_t.add_argument(
        "--cache-dir",
        default="data/cache/tariff",
        help="build_tariff_rates 가 생성한 JSON 캐시 디렉토리",
    )

    p_notes = sub.add_parser("notes", help="CLIP 해설서 JSON → explanatory_notes/note_chunks")
    p_notes.add_argument("paths", nargs="+", help="dev_probe_clip JSON 파일 또는 glob")
    p_notes.add_argument(
        "--replace-chunks",
        action="store_true",
        help="기존 note_chunks 삭제 후 재삽입 (chunking 파라미터 변경 시)",
    )

    p_cases = sub.add_parser("cases", help="CLIP 품목분류 사례 JSONL → classification_cases")
    p_cases.add_argument("paths", nargs="+", help="dev_probe_cases JSONL 파일 또는 glob")

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
        elif args.cmd == "tariffs":
            cache_dir = Path(args.cache_dir)
            if not cache_dir.exists():
                print(
                    f"[ERROR] 캐시 디렉토리 없음: {cache_dir}\n"
                    f"먼저 `python -m scripts.build_tariff_rates` 로 스크래핑하세요.",
                    file=sys.stderr,
                )
                return 2
            hs_n, rate_n, skipped = upsert_tariffs_from_cache(session, cache_dir)
            session.commit()
            print(
                f"[TARIFFS] hs_codes upsert={hs_n} tariff_rates upsert={rate_n} "
                f"skipped_non_10digit={skipped}"
            )
        elif args.cmd == "notes":
            paths = _expand_paths(args.paths)
            if not paths:
                print("[ERROR] 처리할 JSON 없음", file=sys.stderr)
                return 2
            total_notes = total_chunks = 0
            for p in paths:
                n, c = upsert_notes_from_json(session, p, replace_chunks=args.replace_chunks)
                total_notes += n
                total_chunks += c
                print(f"[NOTE] {p.name}: notes={n}, chunks={c}")
            session.commit()
            print(f"[TOTAL] notes={total_notes}, chunks={total_chunks}")
        elif args.cmd == "cases":
            paths = _expand_paths(args.paths)
            if not paths:
                print("[ERROR] 처리할 JSONL 없음", file=sys.stderr)
                return 2
            total_cases = total_nulled = total_rows = 0
            for p in paths:
                n, nulled, rows = upsert_cases_from_jsonl(session, p)
                total_cases += n
                total_nulled += nulled
                total_rows += rows
                print(f"[CASE] {p.name}: upserted={n}, hs_nulled={nulled}, rows={rows}")
            session.commit()
            print(f"[TOTAL] cases={total_cases}, hs_nulled={total_nulled}, rows={total_rows}")
        else:  # pragma: no cover
            parser.error(f"unknown cmd: {args.cmd}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
