"""CLIP FAQ 스크래퍼 end-to-end 검증 하네스.

사용 예::

    python -m scripts.dev_probe_faq 원산지
    python -m scripts.dev_probe_faq 분류 --max-pages 2 --headed
    python -m scripts.dev_probe_faq 8471 --no-detail

셀렉터 확정 전에는 ``--headed`` 로 실측 DOM 을 확인하면서
``scripts/clip_scraper.py`` 의 ``SEL_FAQ_*`` 상수와 ``_parse_faq_row`` 매핑을 조정한다.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime
from pathlib import Path

from scripts.clip_scraper import ClipScrapeError, ClipScraper

CACHE_DIR = Path("data") / "cache"
RAW_DIR = Path("data") / "raw" / "clip"
MANIFEST = Path("data") / "manifest.jsonl"


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="CLIP FAQ 수집 검증")
    parser.add_argument("query", help="검색어 (키워드)")
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--no-detail", action="store_true", help="상세 답변 수집 생략")
    parser.add_argument("--headed", action="store_true", help="브라우저 창 표시")
    args = parser.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = CACHE_DIR / f"clip_faq_{_safe(args.query)}_{ts}.jsonl"

    print(f"[FETCH] query={args.query!r} max_pages={args.max_pages} detail={not args.no_detail}")
    try:
        with ClipScraper(
            headless=not args.headed,
            raw_html_dir=RAW_DIR,
            manifest_path=MANIFEST,
        ) as scraper:
            entries = scraper.fetch_faq(
                args.query,
                max_pages=args.max_pages,
                fetch_detail=not args.no_detail,
            )
    except ClipScrapeError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    with out_path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e.as_dict(), ensure_ascii=False) + "\n")

    print(f"\n[SAVED] {out_path} ({len(entries)} 건)")
    if entries:
        first = entries[0]
        print(f"[PREVIEW] id={first.faq_id} cat={first.category} q={first.question[:40]}")
        if first.answer:
            print(f"  answer: {first.answer[:120]}...")
    return 0


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or "가" <= c <= "힣" else "_" for c in s)[:20] or "q"


if __name__ == "__main__":
    sys.exit(main())
