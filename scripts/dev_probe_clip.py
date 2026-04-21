"""CLIP HS해설서 스크래퍼 end-to-end 검증 하네스.

한 개 호(heading, 4자리)를 실수집하여:
- 원본 ``ExplanatoryNote`` 를 JSON 으로 ``data/cache/`` 에 덤프
- ``DataManager.chunk_explanatory_note`` 로 청킹 → JSONL 저장
- 청크 개수·평균 길이 요약 출력

사용 예::

    python -m scripts.dev_probe_clip 8471
    python -m scripts.dev_probe_clip 8471 --year 2022 --headed
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime
from pathlib import Path

from scripts.clip_scraper import ClipScraper, ClipScrapeError
from scripts.data_manager import DataManager

CACHE_DIR = Path("data") / "cache"


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="CLIP 해설서 E2E 수집 + 청킹 검증")
    parser.add_argument("heading", help="4자리 호 번호 (예: 8471)")
    parser.add_argument("--year", default="2022", help="HSK 연도 (기본 2022)")
    parser.add_argument(
        "--headed", action="store_true", help="브라우저 창 표시 (디버깅용)"
    )
    parser.add_argument(
        "--chunk-tokens", type=int, default=800, help="청크당 최대 토큰 (기본 800)"
    )
    parser.add_argument(
        "--overlap", type=int, default=100, help="청크 오버랩 토큰 (기본 100)"
    )
    args = parser.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    note_path = CACHE_DIR / f"clip_note_{args.heading}_{args.year}_{ts}.json"
    chunks_path = CACHE_DIR / f"clip_chunks_{args.heading}_{args.year}_{ts}.jsonl"

    print(f"[FETCH] heading={args.heading} year={args.year} headed={args.headed}")
    try:
        with ClipScraper(headless=not args.headed) as scraper:
            note = scraper.fetch_explanatory_note(args.heading, year=args.year)
    except ClipScrapeError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # Playwright 세부 예외 포함
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    note_dict = note.as_dict()
    note_path.write_text(
        json.dumps(note_dict, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[SAVED NOTE] {note_path}")

    print("\n[NOTE SUMMARY]")
    for kind in ("general_rule", "section_note", "chapter_note", "heading_note"):
        bi = note_dict[kind]
        ko_len = len(bi.get("ko") or "")
        en_len = len(bi.get("en") or "")
        marker_ko = "O" if ko_len else "-"
        marker_en = "O" if en_len else "-"
        print(f"  {kind:15} ko[{marker_ko}] {ko_len:6}자  en[{marker_en}] {en_len:6}자")

    chunks = DataManager.chunk_explanatory_note(
        {
            "heading": note.heading,
            "section_note": note.section_note.ko,
            "chapter_note": note.chapter_note.ko,
            "content": note.heading_note.ko,
            "metadata": {
                "heading": note.heading,
                "year": note.year,
                "source": "CLIP",
                "lang": "ko",
            },
        },
        chunk_tokens=args.chunk_tokens,
        overlap_tokens=args.overlap,
    )

    with chunks_path.open("w", encoding="utf-8") as f:
        for ch in chunks:
            f.write(json.dumps(ch, ensure_ascii=False) + "\n")
    print(f"\n[SAVED CHUNKS] {chunks_path} ({len(chunks)} 청크)")

    if chunks:
        avg_tokens = sum(len((ch["text"] or "").split()) for ch in chunks) / len(chunks)
        print(f"[CHUNK STATS] avg_tokens={avg_tokens:.0f}, first_chunk_preview:")
        print(f"  {chunks[0]['text'][:200]}...")

    print("\n[COMPLETE]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
