"""CLIP 품목분류 사례 대량 수집.

다양한 품목 키워드를 돌아가며 ``ClipScraper.fetch_classification_cases`` 를 호출,
쿼리별로 JSONL 을 ``data/cache/clip_cases_<query>_<ts>.jsonl`` 로 저장한다.

기본 동작:
- 상세 수집(fetch_detail) **비활성** — 속도 우선. reasoning 은 RAG 에 필수 아님.
- 쿼리당 ``--max-pages`` 만큼만 수집.
- 한 쿼리 실패해도 나머지 진행.

사용::

    python -m scripts.bulk_collect_cases                     # 내장 기본 쿼리 리스트
    python -m scripts.bulk_collect_cases --queries 로션 과자  # 수동 리스트
    python -m scripts.bulk_collect_cases --max-pages 5 --detail

수집 후::

    python -m scripts.build_index cases data/cache/clip_cases_*.jsonl
    python -m scripts.build_embeddings cases
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from scripts.clip_scraper import ClipScrapeError, ClipScraper

logger = logging.getLogger(__name__)

CACHE_DIR = Path("data") / "cache"
RAW_DIR = Path("data") / "raw" / "clip"
MANIFEST = Path("data") / "manifest.jsonl"

# 섹션·류 전반을 커버하는 광범위 키워드. 중복 히트는 DB upsert 에서 정리.
DEFAULT_QUERIES = [
    # 섹션 I (동식물)
    "육류", "어류", "유제품", "계란",
    # 섹션 II-III (식물·지방)
    "곡물", "과일", "채소", "식용유",
    # 섹션 IV (조제식료품)
    "소스", "과자", "초콜릿", "커피", "음료", "주류", "담배",
    # 섹션 VI (화학·의약·화장품)
    "의약품", "건강보조식품", "비타민",
    "화장품", "로션", "크림", "샴푸", "비누", "세정제",
    "염료", "향료",
    # 섹션 VII (플라스틱·고무)
    "플라스틱", "고무", "타이어",
    # 섹션 VIII (가죽·가방)
    "가죽", "가방", "지갑",
    # 섹션 XI (섬유·의류)
    "의류", "셔츠", "바지", "스웨터", "양말",
    # 섹션 XII (신발·모자)
    "운동화", "신발", "모자",
    # 섹션 XIII-XV (건축자재·금속)
    "유리", "세라믹", "알루미늄", "철강",
    # 섹션 XVI (기계·전자)
    "노트북", "스마트폰", "TV", "반도체", "배터리",
    "모터", "펌프", "센서",
    # 섹션 XVII (운송)
    "자동차", "자전거", "엔진", "부품",
    # 섹션 XVIII (정밀기기)
    "시계", "안경", "카메라", "의료기기",
    # 섹션 XX (잡품)
    "가구", "장난감", "문구",
    # 섹션 XXI (미술품)
    "그림",
]


def _safe(s: str) -> str:
    """파일명 안전 변환 (한글 허용)."""
    return "".join(c if c.isalnum() or "가" <= c <= "힣" else "_" for c in s)[:20] or "q"


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    parser = argparse.ArgumentParser(description="CLIP 사례 대량 수집")
    parser.add_argument(
        "--queries",
        nargs="*",
        default=None,
        help=f"수집 쿼리 리스트 (기본: 내장 {len(DEFAULT_QUERIES)}개)",
    )
    parser.add_argument("--max-pages", type=int, default=3, help="쿼리당 페이지 수 (기본 3)")
    parser.add_argument(
        "--detail", action="store_true", help="상세 페이지도 클릭(reasoning 수집). 속도 느려짐."
    )
    parser.add_argument(
        "--rate-limit-sec",
        type=float,
        default=0.8,
        help="요청 간 최소 간격 초 (기본 0.8s)",
    )
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--no-headless", dest="headless", action="store_false")
    parser.add_argument(
        "--out-dir", default=str(CACHE_DIR), help="JSONL 출력 디렉토리 (기본 data/cache)"
    )
    args = parser.parse_args()

    queries = args.queries if args.queries else DEFAULT_QUERIES
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ok = fail = total_cases = 0
    failed_queries: list[tuple[str, str]] = []

    print(
        f"[START] {len(queries)} 쿼리 × max_pages={args.max_pages} "
        f"detail={args.detail} headless={args.headless}"
    )

    with ClipScraper(
        headless=args.headless,
        rate_limit_sec=args.rate_limit_sec,
        raw_html_dir=RAW_DIR,
        manifest_path=MANIFEST,
    ) as scraper:
        for i, q in enumerate(queries, 1):
            prefix = f"[{i}/{len(queries)}] {q!r}"
            try:
                cases = scraper.fetch_classification_cases(
                    q, max_pages=args.max_pages, fetch_detail=args.detail
                )
            except ClipScrapeError as exc:
                print(f"{prefix} FAIL (scrape): {exc}")
                failed_queries.append((q, str(exc)))
                fail += 1
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"{prefix} FAIL ({type(exc).__name__}): {exc}")
                failed_queries.append((q, f"{type(exc).__name__}: {exc}"))
                fail += 1
                continue

            out = out_dir / f"clip_cases_{_safe(q)}_{ts}.jsonl"
            with out.open("w", encoding="utf-8") as f:
                for c in cases:
                    f.write(json.dumps(c.as_dict(), ensure_ascii=False) + "\n")
            print(f"{prefix} OK: {len(cases)} 건 → {out.name}")
            ok += 1
            total_cases += len(cases)

    print()
    print(f"[DONE] success={ok}/{len(queries)}, failures={fail}, 수집 합계={total_cases} 건")
    if failed_queries:
        print("[FAIL 목록]")
        for q, err in failed_queries:
            print(f"  - {q!r}: {err[:120]}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
