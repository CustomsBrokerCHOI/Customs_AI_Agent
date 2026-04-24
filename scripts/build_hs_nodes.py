"""Sprint A: hs_nodes 테이블에 21부 + 97류 = 118 레코드 적재.

데이터 소스
-----------
- 부(Section) 메타: ``api/services/hs_sections.SECTIONS`` (이미 정의됨).
- 류(Chapter) 메타 및 원문: ``data/cache/notes/{heading}.json`` 의 ``chapter_note.ko``.
  chapter_note 는 같은 chapter 내 모든 heading 파일에 중복 저장되므로 대표 1건만 사용.
- 부주(Section note) 원문: 같은 파일의 ``section_note.ko``. chapter_note 와 동일한
  방식으로 section 단위 중복 저장 — chapter 의 section 소속을 통해 대표 1건 추출.

자동 파싱
---------
정규식으로 부주·류주 원문에서 다음 필드를 1차 추출:
- ``inclusion_keywords``: "포함한다", "이에 해당한다" 주변의 품목 키워드.
- ``exclusion_keywords``: "제외한다", "~는(은) 제외" 주변의 품목 키워드.
- ``essential_character``: 재질 / 용도 / 기능 중 어느 축으로 분기하는지 추론.
- ``processing_stage``: 원료 / 반제품 / 완제품 중 어디에 걸리는지 추론.

**수동 교정은 Sprint B** — Food Solution 우선 8개 류에 한해 Choi 님이 교정.

사용 예
-------
::

    # 118 레코드 일괄 적재 (드라이런으로 먼저)
    python -m scripts.build_hs_nodes --dry-run
    python -m scripts.build_hs_nodes

    # 부만 재적재
    python -m scripts.build_hs_nodes --sections-only

    # 특정 chapter 만
    python -m scripts.build_hs_nodes --chapters 04,15,22
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api.core.config import settings
from api.db.models import HSNode
from api.services.hs_sections import SECTIONS, section_by_roman

load_dotenv()
logger = logging.getLogger(__name__)

NOTES_CACHE_DIR = Path("data") / "cache" / "notes"
DEFAULT_VERSION = "HSK-2022"
DEFAULT_VALID_FROM = date(2022, 1, 1)

# -------- 정규식 파서 --------

# "~를 제외한다" / "~는 제외한다" / "~은 제외한다" / "다만, ~는 제외한다"
# 뒤 품목을 추출하기 위해 조사·어미 붙은 단어 덩어리를 긁는다.
_EXCL_RE = re.compile(
    r"(?:다만,?\s*)?([^。\.\s][^。\.]{2,60}?)(?:은|는|을|를|이|가)\s*제외한다",
)
# "~를 포함한다" / "~이 이에 해당한다"
_INCL_RE = re.compile(
    r"([^。\.\s][^。\.]{2,60}?)(?:은|는|을|를|이|가)?\s*(?:포함한다|이에 해당한다)",
)

# 본질적 특성 힌트 — 부주·류주에 흔히 나오는 키워드.
_ESSENTIAL_HINTS = {
    "재질": ["재질", "재료", "소재", "원료"],
    "용도": ["용도", "쓰임", "사용", "목적"],
    "기능": ["기능", "작동", "성능"],
}
_PROCESSING_HINTS = {
    "원료": ["원료", "생(生)", "가공하지 아니한"],
    "반제품": ["반제품", "중간재", "미조립"],
    "완제품": ["완제품", "최종품", "완성된"],
}


def _extract_keywords(text: str, pattern: re.Pattern[str], *, limit: int = 30) -> list[str]:
    """정규식 매치 → 중복 제거 + 길이 필터."""
    if not text:
        return []
    raw = pattern.findall(text)
    seen: set[str] = set()
    out: list[str] = []
    for m in raw:
        s = re.sub(r"\s+", " ", str(m)).strip()
        # 너무 짧은 토큰(2자 이하) 또는 순수 숫자·구두점은 제외.
        if len(s) < 3 or not any(ord(c) > 127 or c.isalpha() for c in s):
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)
        if len(out) >= limit:
            break
    return out


def _hint_by_count(text: str, hints: dict[str, list[str]]) -> str | None:
    """키워드 등장 빈도로 다수 축 선택. 전부 0 이면 None."""
    if not text:
        return None
    counts: dict[str, int] = {}
    for axis, kws in hints.items():
        counts[axis] = sum(text.count(kw) for kw in kws)
    best_axis, best_count = max(counts.items(), key=lambda kv: kv[1])
    return best_axis if best_count > 0 else None


def parse_notes(notes_excerpt: str) -> dict:
    """원문 → {inclusion_keywords, exclusion_keywords, essential_character, processing_stage}."""
    return {
        "inclusion_keywords": _extract_keywords(notes_excerpt, _INCL_RE),
        "exclusion_keywords": _extract_keywords(notes_excerpt, _EXCL_RE),
        "essential_character": _hint_by_count(notes_excerpt, _ESSENTIAL_HINTS),
        "processing_stage": _hint_by_count(notes_excerpt, _PROCESSING_HINTS),
    }


# -------- 소스 데이터 수집 --------


def _chapter_from_heading(heading: str) -> int | None:
    if len(heading) != 4 or not heading.isdigit():
        return None
    return int(heading[:2])


def collect_chapter_notes() -> dict[int, dict]:
    """chapter → {title_ko, title_en, notes_excerpt}.

    같은 chapter 의 모든 heading 파일에 chapter_note 가 중복 저장되므로
    비어있지 않은 첫 파일을 대표로 사용. title 은 파일의 heading_note 또는
    chapter_note 상단에서 추출 못 하면 plain 헤더만 채움.
    """
    out: dict[int, dict] = {}
    if not NOTES_CACHE_DIR.exists():
        logger.error("notes cache dir not found: %s", NOTES_CACHE_DIR)
        return out

    for path in sorted(NOTES_CACHE_DIR.glob("*.json")):
        heading = path.stem
        chapter = _chapter_from_heading(heading)
        if chapter is None or chapter in out:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            logger.warning("failed to read %s", path)
            continue
        ch_bi = data.get("chapter_note") or {}
        ko = (ch_bi.get("ko") or "").strip()
        en = (ch_bi.get("en") or "").strip()
        if not ko and not en:
            continue
        out[chapter] = {
            "title_ko": f"제{chapter:02d}류",
            "title_en": f"Chapter {chapter:02d}",
            "notes_excerpt": ko or en,
        }
    return out


def collect_section_notes() -> dict[str, dict]:
    """section roman → {notes_excerpt}.

    section_note 도 모든 heading 파일에 중복 저장. 각 section 의 소속 chapter 중
    **비어있지 않은 첫 파일** 을 대표로 사용.
    """
    out: dict[str, dict] = {}
    for section in SECTIONS:
        for chapter in section.chapters:
            # 이 chapter 에 속하는 heading 파일 중 비어있지 않은 첫 것.
            for path in sorted(NOTES_CACHE_DIR.glob(f"{chapter:02d}*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                sec_bi = data.get("section_note") or {}
                ko = (sec_bi.get("ko") or "").strip()
                en = (sec_bi.get("en") or "").strip()
                if ko or en:
                    out[section.roman] = {
                        "title_ko": section.title_kr,
                        "title_en": section.title_en,
                        "notes_excerpt": ko or en,
                    }
                    break
            if section.roman in out:
                break
    return out


# -------- 적재 --------


def _sync_engine():
    """Alembic·CLI 공용 sync 엔진 (asyncpg URL → psycopg v3 로 교체)."""
    url = settings.database_url.replace("+asyncpg", "+psycopg")
    return create_engine(url, future=True)


def upsert_node(
    session: Session,
    *,
    version: str,
    level: int,
    code: str,
    parent_code: str | None,
    title_ko: str,
    title_en: str | None,
    notes_excerpt: str,
    source_reference: str | None,
) -> tuple[HSNode, bool]:
    """UNIQUE(version, code) 키로 upsert. (node, created) 반환."""
    existing = session.execute(
        select(HSNode).where(HSNode.version == version, HSNode.code == code)
    ).scalar_one_or_none()

    parsed = parse_notes(notes_excerpt)
    if existing:
        existing.level = level
        existing.parent_code = parent_code
        existing.title_ko = title_ko
        existing.title_en = title_en
        existing.notes_excerpt = notes_excerpt
        existing.inclusion_keywords = parsed["inclusion_keywords"]
        existing.exclusion_keywords = parsed["exclusion_keywords"]
        existing.essential_character = parsed["essential_character"]
        existing.processing_stage = parsed["processing_stage"]
        existing.source_reference = source_reference
        return existing, False

    node = HSNode(
        version=version,
        level=level,
        code=code,
        parent_code=parent_code,
        title_ko=title_ko,
        title_en=title_en,
        notes_excerpt=notes_excerpt,
        inclusion_keywords=parsed["inclusion_keywords"],
        exclusion_keywords=parsed["exclusion_keywords"],
        essential_character=parsed["essential_character"],
        processing_stage=parsed["processing_stage"],
        source_reference=source_reference,
        valid_from=DEFAULT_VALID_FROM,
    )
    session.add(node)
    return node, True


def load_all(
    *,
    version: str = DEFAULT_VERSION,
    sections_only: bool = False,
    chapter_filter: set[int] | None = None,
    dry_run: bool = False,
) -> dict:
    """부 + 류 118 레코드 일괄 적재."""
    logger.info(
        "Sprint A load: version=%s sections_only=%s chapter_filter=%s dry_run=%s",
        version,
        sections_only,
        chapter_filter,
        dry_run,
    )

    section_notes = collect_section_notes()
    chapter_notes = {} if sections_only else collect_chapter_notes()

    logger.info(
        "collected: sections=%d chapters=%d (expected 21 / 97)",
        len(section_notes),
        len(chapter_notes),
    )

    engine = _sync_engine()
    stats = {"sections_created": 0, "sections_updated": 0, "chapters_created": 0, "chapters_updated": 0}

    with Session(engine) as session:
        # ---- 부 ----
        for section in SECTIONS:
            info = section_notes.get(section.roman)
            if info is None:
                logger.warning("section %s 원문 없음 — skip", section.roman)
                continue
            _, created = upsert_node(
                session,
                version=version,
                level=0,  # section
                code=section.roman,
                parent_code=None,
                title_ko=info["title_ko"],
                title_en=info["title_en"],
                notes_excerpt=info["notes_excerpt"],
                source_reference=f"HSK {version.split('-')[-1]} 제{section.roman}부 주",
            )
            if created:
                stats["sections_created"] += 1
            else:
                stats["sections_updated"] += 1

        # ---- 류 ----
        if not sections_only:
            for chapter, info in sorted(chapter_notes.items()):
                if chapter_filter is not None and chapter not in chapter_filter:
                    continue
                # 이 chapter 가 속한 section roman 찾기.
                parent_roman = None
                for sec in SECTIONS:
                    if chapter in sec.chapters:
                        parent_roman = sec.roman
                        break
                code = f"{chapter:02d}"
                _, created = upsert_node(
                    session,
                    version=version,
                    level=2,
                    code=code,
                    parent_code=parent_roman,
                    title_ko=info["title_ko"],
                    title_en=info["title_en"],
                    notes_excerpt=info["notes_excerpt"],
                    source_reference=f"HSK {version.split('-')[-1]} 제{code}류 주",
                )
                if created:
                    stats["chapters_created"] += 1
                else:
                    stats["chapters_updated"] += 1

        if dry_run:
            session.rollback()
            logger.info("dry-run — rolled back")
        else:
            session.commit()

    logger.info("done: %s", stats)
    return stats


# -------- CLI --------


def _parse_chapters(csv: str) -> set[int]:
    out: set[int] = set()
    for tok in csv.split(","):
        tok = tok.strip()
        if tok.isdigit():
            out.add(int(tok))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sprint A — hs_nodes 118 레코드 적재")
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--sections-only", action="store_true")
    parser.add_argument("--chapters", help="적재할 chapter CSV (예: 04,15,22). 없으면 전부.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    chapter_filter = _parse_chapters(args.chapters) if args.chapters else None

    stats = load_all(
        version=args.version,
        sections_only=args.sections_only,
        chapter_filter=chapter_filter,
        dry_run=args.dry_run,
    )
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
