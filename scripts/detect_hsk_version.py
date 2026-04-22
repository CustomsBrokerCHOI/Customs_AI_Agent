"""HSK 버전 개정 감지.

CLIP 해설서 페이지의 연도 드롭다운 관측값과 DB 에 적재된 ``explanatory_notes.hsk_year``
최대값을 비교해 **새 HSK 버전** 이 등장했는지 확인.

신 버전 감지 시 stderr 로 경보 출력 + 종료 코드 **2** (cron / GitHub Actions 훅 가능).
CLIP 접근 실패 시 `--skip-clip` 로 DB 만 검사.

사용 예::

    python -m scripts.detect_hsk_version
    python -m scripts.detect_hsk_version --skip-clip  # CI 오프라인용

설계
----
실제 자동 재임베딩 파이프라인은 ``docs/hsk-version-migration.md`` 런북을 수동 실행.
본 스크립트는 **감지 + 알림** 만. 자동 실행은 후속 (Railway cron / webhook).
"""

from __future__ import annotations

import argparse
import io
import logging
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from api.core.config import settings
from api.db.models import ExplanatoryNote
from scripts.build_index import _sync_db_url

logger = logging.getLogger(__name__)


def db_max_hsk_year(database_url: str) -> int | None:
    """``explanatory_notes.hsk_year`` 의 현재 최대값. 테이블이 비어있으면 None."""
    engine = create_engine(_sync_db_url(database_url), pool_pre_ping=True)
    with Session(engine) as session:
        row = session.execute(select(func.max(ExplanatoryNote.hsk_year))).scalar_one()
    return int(row) if row is not None else None


def probe_clip_years() -> list[int]:
    """CLIP 해설서 페이지 연도 드롭다운 관측. Playwright 필요."""
    from scripts.clip_scraper import ClipScraper

    with ClipScraper(headless=True) as scraper:
        return scraper.list_available_hsk_years()


def compare(db_max: int | None, clip_years: list[int]) -> tuple[bool, list[int], int | None]:
    """CLIP 관측 연도 중 DB 최대값을 초과하는 것이 있으면 신버전으로 판단.

    :returns: ``(new_version_detected, unseen_years, latest_clip)``.
    """
    if not clip_years:
        return False, [], None
    latest = max(clip_years)
    if db_max is None:
        return True, clip_years, latest
    unseen = [y for y in clip_years if y > db_max]
    return bool(unseen), unseen, latest


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s - %(message)s")

    parser = argparse.ArgumentParser(description="HSK 버전 개정 감지")
    parser.add_argument(
        "--skip-clip",
        action="store_true",
        help="CLIP 프로브 생략 (오프라인 환경/CI 용 — DB 상태만 보고)",
    )
    args = parser.parse_args()

    try:
        db_max = db_max_hsk_year(settings.database_url)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] DB 조회 실패, DB 미확인: {exc}", file=sys.stderr)
        db_max = None
    print(f"[DB]   max(hsk_year) = {db_max!r}")

    if args.skip_clip:
        print("[CLIP] 스킵")
        return 0

    try:
        clip_years = probe_clip_years()
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] CLIP 프로브 실패: {exc}", file=sys.stderr)
        return 1
    print(f"[CLIP] 연도 옵션 = {clip_years}")

    detected, unseen, latest = compare(db_max, clip_years)
    if detected:
        print(
            f"[ALERT] 신 HSK 버전 감지 — unseen={unseen} latest_clip={latest} db={db_max}",
            file=sys.stderr,
        )
        print(
            "\n→ docs/hsk-version-migration.md 런북을 수행하세요 "
            "(스크래핑 → 적재 → 재임베딩 → 듀얼 쿼리).",
            file=sys.stderr,
        )
        return 2

    print(f"[OK] DB 가 최신 CLIP 연도({latest}) 를 이미 커버")
    return 0


if __name__ == "__main__":
    sys.exit(main())
