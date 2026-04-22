"""CLIP 관세율표(``openULS0201002Q``) 전체 heading 스캔 → JSON 캐시.

Playwright 기반 ``ClipScraper.fetch_tariff_schedule`` 를 heading 범위에 걸쳐
순회한다. 한 heading 호출로 해당 호 아래 **10자리 세번 전체 + 한영 품명
+ 기본세율 + 탄력세율 + 협정세율 prefix** 가 한꺼번에 반환되므로 CLIP 만으로
``hs_codes`` + ``tariff_rates(기본세율)`` 를 완성할 수 있다.

사용 예::

    # 1개 chapter 만 검증
    python -m scripts.build_tariff_rates --chapters 84

    # 전체 (chapter 77 제외, 밤새)
    python -m scripts.build_tariff_rates --chapters 01-97 --skip-chapters 77

    # 이미 캐시된 heading 은 자동 skip. 중단 후 재실행 안전.

동작 특징
--------
- ``data/cache/tariff/{heading}.json`` 에 한 heading 당 ``TariffLine`` 리스트 저장.
- 특정 chapter 내 heading 이 연속 ``--max-gap`` 회 empty 면 해당 chapter 조기 종료.
- 네트워크 실패는 캐시하지 않고 다음 heading 으로 진행 (재실행 시 재시도).
- 적재는 ``scripts.build_index tariffs`` 로 분리.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv

from scripts.clip_scraper import ClipScrapeError, ClipScraper, TariffLine

logger = logging.getLogger(__name__)

TARIFF_CACHE_DIR = Path("data") / "cache" / "tariff"


# Windows SetThreadExecutionState flags
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


@contextmanager
def _prevent_windows_sleep():
    """Windows 에서만 적용. 장시간 스크래핑 중 자동 sleep 방지."""
    if sys.platform != "win32":
        yield
        return
    try:
        import ctypes  # type: ignore[import-not-found]

        ctypes.windll.kernel32.SetThreadExecutionState(
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED
        )
        logger.info("Windows 자동 sleep 억제 활성화 (SetThreadExecutionState)")
    except Exception:  # noqa: BLE001
        logger.warning("자동 sleep 억제 실패 — 노트북 전원옵션 수동으로 확인하세요")
    try:
        yield
    finally:
        try:
            import ctypes  # type: ignore[import-not-found]

            ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
        except Exception:  # noqa: BLE001
            pass


def parse_chapter_range(spec: str) -> list[int]:
    """``"01-05,08,10-12"`` → ``[1,2,3,4,5,8,10,11,12]``."""
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def _heading_cache_path(heading: str) -> Path:
    return TARIFF_CACHE_DIR / f"{heading}.json"


def _load_cached_heading(heading: str) -> list[dict] | None:
    p = _heading_cache_path(heading)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        logger.warning("캐시 JSON 손상 heading=%s, 재수집", heading)
        return None


def _save_heading_cache(heading: str, rows: list[TariffLine]) -> None:
    TARIFF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = [asdict(r) for r in rows]
    _heading_cache_path(heading).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def scrape_range(
    chapters: Iterable[int],
    skip: set[int],
    *,
    max_gap: int = 15,
    rate_limit_sec: float = 2.0,
    result_timeout_ms: int = 5_000,
) -> dict[str, int]:
    """chapter 범위 스캔. 통계 dict 반환."""
    TARIFF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    stats = {"hit": 0, "miss": 0, "cached": 0, "fail": 0}

    with _prevent_windows_sleep(), ClipScraper(
        headless=True, rate_limit_sec=rate_limit_sec
    ) as scraper:
        for chapter in chapters:
            if chapter in skip:
                print(f"[SKIP] chapter {chapter:02d}", flush=True)
                continue

            chapter_hits = 0
            consecutive_empty = 0
            t_ch = time.monotonic()

            for h in range(1, 100):
                heading = f"{chapter:02d}{h:02d}"
                cached = _load_cached_heading(heading)
                if cached is not None:
                    stats["cached"] += 1
                    has_data = bool(cached)
                    if has_data:
                        chapter_hits += 1
                        consecutive_empty = 0
                    else:
                        consecutive_empty += 1
                        if consecutive_empty >= max_gap:
                            print(
                                f"  [CACHE-GAP] ch{chapter:02d}: 연속 {max_gap} empty → 종료",
                                flush=True,
                            )
                            break
                    continue

                # transient 실패가 legit empty 로 오염되는 것 방지 — 1회 재시도.
                rows: list | None = None
                for attempt in range(2):
                    try:
                        rows = scraper.fetch_tariff_schedule(
                            heading, result_timeout_ms=result_timeout_ms
                        )
                        break
                    except ClipScrapeError:
                        if attempt == 0:
                            time.sleep(3)  # 짧은 대기 후 1회 retry
                            continue
                        rows = []  # 두 번째도 실패 — legit empty 로 간주
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "스크래핑 예외 heading=%s attempt=%s", heading, attempt
                        )
                        if attempt == 0:
                            time.sleep(5)  # 네트워크 문제면 조금 더 대기
                            continue
                        rows = None  # 최종 실패 — 캐시하지 않음
                if rows is None:
                    stats["fail"] += 1
                    continue

                _save_heading_cache(heading, rows)
                if rows:
                    stats["hit"] += 1
                    chapter_hits += 1
                    consecutive_empty = 0
                    tariff_rows = [r for r in rows if r.tariff_line and r.sub_heading]
                    print(
                        f"  [HIT] {heading}: rows={len(rows)} (10자리={len(tariff_rows)})",
                        flush=True,
                    )
                else:
                    stats["miss"] += 1
                    consecutive_empty += 1
                    if consecutive_empty >= max_gap:
                        print(
                            f"  [GAP] ch{chapter:02d}: 연속 {max_gap} empty → 종료 "
                            f"(hits={chapter_hits})",
                            flush=True,
                        )
                        break

            dt = time.monotonic() - t_ch
            print(
                f"[CHAPTER {chapter:02d}] hits={chapter_hits} elapsed={dt:.0f}s "
                f"(누적 hit={stats['hit']} cache={stats['cached']} fail={stats['fail']})",
                flush=True,
            )

    stats["elapsed_sec"] = int(time.monotonic() - t0)
    return stats


def main() -> int:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s"
    )

    parser = argparse.ArgumentParser(description="CLIP 관세율표 전체 스캔 캐시 구축")
    parser.add_argument(
        "--chapters",
        default="01-97",
        help='chapter 범위 (예: "01-97", "84,85", "21-29,84")',
    )
    parser.add_argument(
        "--skip-chapters", default="77", help="건너뛸 chapter (기본 77)"
    )
    parser.add_argument(
        "--max-gap",
        type=int,
        default=15,
        help="연속 empty heading N개 이상이면 해당 chapter 조기 종료 (기본 15)",
    )
    parser.add_argument(
        "--rate-limit", type=float, default=2.0, help="요청 간 최소 대기 초 (기본 2.0)"
    )
    args = parser.parse_args()

    chapters = parse_chapter_range(args.chapters)
    skip = set(parse_chapter_range(args.skip_chapters)) if args.skip_chapters else set()

    stats = scrape_range(
        chapters,
        skip,
        max_gap=args.max_gap,
        rate_limit_sec=args.rate_limit,
    )
    print(
        f"\n[COMPLETE] hit={stats['hit']} miss={stats['miss']} "
        f"cached={stats['cached']} fail={stats['fail']} "
        f"elapsed={stats['elapsed_sec']}s",
        flush=True,
    )
    print(f"\n캐시 위치: {TARIFF_CACHE_DIR}")
    print("DB 적재: python -m scripts.build_index tariffs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
