"""CLIP HS해설서 전체 heading 수집 → JSON 캐시.

``scripts.build_tariff_rates`` 가 만든 ``data/cache/tariff/{heading}.json`` 에서
유효한 heading 목록을 얻어, 각 heading 에 대해 CLIP
``fetch_explanatory_note`` 를 호출하고 ``data/cache/notes/{heading}.json`` 으로
저장한다. 이미 캐시된 heading 은 자동 skip (재개 가능).

사용 예::

    # 전체 (관세율표 캐시가 먼저 있어야 함)
    python -m scripts.build_explanatory_notes

    # 특정 heading 만
    python -m scripts.build_explanatory_notes --headings 8471,8517,2203

    # 특정 chapter 만
    python -m scripts.build_explanatory_notes --chapters 84,85

    # DB 적재 (별도)
    python -m scripts.build_index notes "data/cache/notes/*.json"

주의: 관세율표 스크래핑과 **동시 실행 금지** (CLIP 서버 부하 + Playwright 충돌).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from scripts.build_tariff_rates import parse_chapter_range
from scripts.clip_scraper import ClipScrapeError, ClipScraper

logger = logging.getLogger(__name__)

TARIFF_CACHE_DIR = Path("data") / "cache" / "tariff"
NOTES_CACHE_DIR = Path("data") / "cache" / "notes"


def _note_cache_path(heading: str) -> Path:
    return NOTES_CACHE_DIR / f"{heading}.json"


def _is_note_empty(note_dict: dict) -> bool:
    for kind in ("general_rule", "section_note", "chapter_note", "heading_note"):
        bi = note_dict.get(kind) or {}
        if (bi.get("ko") or "").strip() or (bi.get("en") or "").strip():
            return False
    return True


def _valid_headings_from_tariff_cache() -> list[str]:
    """관세율표 캐시에서 **10자리 행이 있는** heading 만 추출.

    empty 파일(heading-level 없음)은 스킵.
    """
    if not TARIFF_CACHE_DIR.exists():
        return []
    valid: list[str] = []
    for p in sorted(TARIFF_CACHE_DIR.glob("*.json")):
        heading = p.stem
        if len(heading) != 4 or not heading.isdigit():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        # 10자리 행이 하나라도 있어야 유효 heading 으로 간주
        has_tariff_line = any(
            (r.get("tariff_line") or "").strip() for r in (data or [])
        )
        if has_tariff_line:
            valid.append(heading)
    return valid


def _filter_headings(
    valid: list[str],
    headings_arg: str | None,
    chapters_arg: str | None,
) -> list[str]:
    if headings_arg:
        requested = {h.strip() for h in headings_arg.split(",") if h.strip()}
        return [h for h in valid if h in requested]
    if chapters_arg:
        chapters = {f"{c:02d}" for c in parse_chapter_range(chapters_arg)}
        return [h for h in valid if h[:2] in chapters]
    return valid


def scrape_notes(
    headings: list[str],
    year: str = "2022",
    rate_limit_sec: float = 2.0,
) -> dict[str, int]:
    NOTES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    stats = {"hit": 0, "empty": 0, "cached": 0, "fail": 0}

    with ClipScraper(headless=True, rate_limit_sec=rate_limit_sec) as scraper:
        for i, heading in enumerate(headings, 1):
            cache_path = _note_cache_path(heading)
            if cache_path.exists():
                stats["cached"] += 1
                if i % 50 == 0:
                    print(
                        f"  [{i}/{len(headings)}] cached={stats['cached']} "
                        f"hit={stats['hit']} empty={stats['empty']} fail={stats['fail']}",
                        flush=True,
                    )
                continue

            try:
                note = scraper.fetch_explanatory_note(heading, year=year)
            except ClipScrapeError as exc:
                logger.warning("해설서 fetch 실패 heading=%s: %s", heading, exc)
                stats["fail"] += 1
                continue
            except Exception:  # noqa: BLE001
                logger.exception("해설서 스크래핑 예외 heading=%s", heading)
                stats["fail"] += 1
                continue

            note_dict = note.as_dict()
            cache_path.write_text(
                json.dumps(note_dict, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            if _is_note_empty(note_dict):
                stats["empty"] += 1
                print(f"  [EMPTY] {heading}", flush=True)
            else:
                stats["hit"] += 1
                ko_lens = [
                    len((note_dict.get(k) or {}).get("ko") or "")
                    for k in (
                        "general_rule",
                        "section_note",
                        "chapter_note",
                        "heading_note",
                    )
                ]
                print(
                    f"  [HIT] {heading} ko_lens={ko_lens} "
                    f"[{i}/{len(headings)}]",
                    flush=True,
                )

    stats["elapsed_sec"] = int(time.monotonic() - t0)
    return stats


def main() -> int:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s"
    )

    parser = argparse.ArgumentParser(description="CLIP 해설서 전체 수집 캐시 구축")
    parser.add_argument(
        "--headings",
        help='명시 heading 목록 (쉼표 구분, 예: "8471,8517"). 지정 시 --chapters 무시',
    )
    parser.add_argument(
        "--chapters",
        help='chapter 범위 (예: "84,85"). --headings 가 있으면 무시',
    )
    parser.add_argument("--year", default="2022")
    parser.add_argument("--rate-limit", type=float, default=2.0)
    args = parser.parse_args()

    valid = _valid_headings_from_tariff_cache()
    if not valid:
        print(
            f"[ERROR] 관세율표 캐시 없음: {TARIFF_CACHE_DIR}\n"
            f"먼저 `python -m scripts.build_tariff_rates` 로 heading 목록을 확보하세요.",
            file=sys.stderr,
        )
        return 2

    targets = _filter_headings(valid, args.headings, args.chapters)
    if not targets:
        print("[ERROR] 처리 대상 heading 이 없습니다.", file=sys.stderr)
        return 2

    print(
        f"[START] 대상 heading={len(targets)} (전체 유효={len(valid)}) "
        f"year={args.year} rate_limit={args.rate_limit}s",
        flush=True,
    )

    stats = scrape_notes(targets, year=args.year, rate_limit_sec=args.rate_limit)

    print(
        f"\n[COMPLETE] hit={stats['hit']} empty={stats['empty']} "
        f"cached={stats['cached']} fail={stats['fail']} "
        f"elapsed={stats['elapsed_sec']}s",
        flush=True,
    )
    print(f"\n캐시 위치: {NOTES_CACHE_DIR}")
    print(
        "DB 적재: python -m scripts.build_index notes "
        f'"{NOTES_CACHE_DIR}/*.json"'
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
