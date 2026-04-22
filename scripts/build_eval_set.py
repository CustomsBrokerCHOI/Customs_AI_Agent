"""검색 평가셋 빌더 (Phase 2).

``data/cache/clip_cases_*.jsonl`` (``scripts.dev_probe_cases`` 출력) 에서
heading-stratified 로 45건을 추출하고, ``data/eval_manual.jsonl`` 이
존재하면 수기 라벨 5건을 merge 하여 ``data/eval_set.jsonl`` 생성.

평가 레코드 스키마
------------------

``EvalRecord``:
- ``id``: 고유 식별자 (source 접두어 + ordinal).
- ``query``: 관세사 입력 시뮬레이션 문자열. 기본은 품명; 설명이 있으면
  ``품명. 설명(앞부분)`` 으로 결합.
- ``gt_heading``: 정답 heading 4자리 (검색 평가의 일차 정답).
- ``gt_hs10``: 정답 10자리 (가능한 경우).
- ``source``: ``"clip_case"`` 또는 ``"manual"``.
- ``case_ref``: CLIP 사례 참조 (clip_case 소스).
- ``notes``: 자유 기록 (manual 소스 메모 등).

사용 예::

    # 기본 (CLIP 45 + manual 5 = 50)
    python -m scripts.build_eval_set

    # 개수/시드 조정
    python -m scripts.build_eval_set --clip-count 45 --seed 42

    # 소스 지정
    python -m scripts.build_eval_set --clip-glob 'data/cache/clip_cases_*.jsonl'
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import logging
import random
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

DEFAULT_CLIP_GLOB = "data/cache/clip_cases_*.jsonl"
DEFAULT_MANUAL_PATH = Path("data/eval_manual.jsonl")
DEFAULT_OUT_PATH = Path("data/eval_set.jsonl")
DEFAULT_CLIP_COUNT = 45
DEFAULT_SEED = 42
MAX_DESC_CHARS_IN_QUERY = 200


# ---- 스키마 ----


class EvalRecord(BaseModel):
    id: str
    query: str = Field(..., min_length=1)
    gt_heading: str = Field(..., pattern=r"^\d{4}$")
    gt_hs10: str | None = Field(None, pattern=r"^\d{10}$")
    source: str  # "clip_case" | "manual"
    case_ref: str | None = None
    notes: str | None = None


# ---- CLIP 케이스 → EvalRecord ----


def _normalize_hs(raw: str | None) -> tuple[str | None, str | None]:
    """raw hs_code → (heading4, hs10) 튜플. 10자리 아니면 heading 만."""
    if not raw:
        return None, None
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) >= 10:
        return digits[:4], digits[:10]
    if len(digits) >= 4:
        return digits[:4], None
    return None, None


def _build_query(product_name: str, description: str | None) -> str:
    name = (product_name or "").strip()
    desc = (description or "").strip()
    if not desc:
        return name
    snippet = desc[:MAX_DESC_CHARS_IN_QUERY].rstrip()
    return f"{name}. {snippet}" if name else snippet


def load_clip_cases(paths: Iterable[Path]) -> list[dict]:
    """JSONL 파일 여러 개를 읽어 valid case dict 리스트 반환 (dedupe by case_ref)."""
    seen: set[str] = set()
    cases: list[dict] = []
    for p in paths:
        if not p.exists():
            logger.warning("파일 없음, 건너뜀: %s", p)
            continue
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("JSON 파싱 실패: %s:%s", p.name, line[:60])
                    continue
                heading4, _ = _normalize_hs(rec.get("hs_code"))
                if not heading4:
                    continue
                if not (rec.get("product_name") or "").strip():
                    continue
                key = str(rec.get("case_ref") or f"{rec.get('product_name')}|{rec.get('hs_code')}")
                if key in seen:
                    continue
                seen.add(key)
                cases.append(rec)
    return cases


def stratified_sample(cases: list[dict], count: int, seed: int) -> list[dict]:
    """heading(4자리) 별 stratified 샘플. 헤딩당 1건 우선, 부족하면 오버샘플.

    결정 규칙:
      1. heading 별 bucket.
      2. 각 bucket 에서 임의 1건씩 꺼내 rotation.
      3. count 도달할 때까지 반복 (헤딩당 최대 2건, 그래도 부족하면 3건까지).
    """
    rng = random.Random(seed)

    buckets: dict[str, list[dict]] = defaultdict(list)
    for c in cases:
        heading4, _ = _normalize_hs(c.get("hs_code"))
        if heading4:
            buckets[heading4].append(c)

    # bucket 내부 순서 무작위화
    for h in buckets:
        rng.shuffle(buckets[h])

    picked: list[dict] = []
    # round-robin: 각 라운드에서 모든 heading 에서 1건씩 뽑음
    for _round in range(3):  # 헤딩당 최대 3건
        headings_in_round = list(buckets.keys())
        rng.shuffle(headings_in_round)
        for h in headings_in_round:
            if len(picked) >= count:
                break
            if buckets[h]:
                picked.append(buckets[h].pop())
        if len(picked) >= count:
            break

    return picked[:count]


def clip_cases_to_records(cases: list[dict]) -> list[EvalRecord]:
    records: list[EvalRecord] = []
    for i, c in enumerate(cases, 1):
        heading4, hs10 = _normalize_hs(c.get("hs_code"))
        if not heading4:
            continue
        try:
            rec = EvalRecord(
                id=f"clip_{i:03d}",
                query=_build_query(c.get("product_name") or "", c.get("description")),
                gt_heading=heading4,
                gt_hs10=hs10,
                source="clip_case",
                case_ref=c.get("case_ref"),
                notes=None,
            )
        except ValidationError as exc:
            logger.warning("EvalRecord 검증 실패 (case_ref=%s): %s", c.get("case_ref"), exc)
            continue
        records.append(rec)
    return records


# ---- 수기 라벨 ----


def load_manual_records(path: Path) -> list[EvalRecord]:
    if not path.exists():
        logger.warning("수기 라벨 파일 없음 (%s). CLIP 만으로 평가셋 생성.", path)
        return []
    records: list[EvalRecord] = []
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("//") or line.startswith("#"):
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("%s:%s JSON 파싱 실패: %s", path.name, i, exc)
                continue
            raw.setdefault("id", f"manual_{i:03d}")
            raw.setdefault("source", "manual")
            try:
                records.append(EvalRecord(**raw))
            except ValidationError as exc:
                logger.warning("%s:%s EvalRecord 검증 실패: %s", path.name, i, exc)
    return records


# ---- 출력 ----


def write_eval_set(records: list[EvalRecord], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(r.model_dump_json(exclude_none=False) + "\n")


# ---- 메인 빌더 ----


def build_eval_set(
    clip_glob: str,
    manual_path: Path,
    out_path: Path,
    clip_count: int,
    seed: int,
) -> tuple[int, int]:
    clip_paths = [Path(p) for p in sorted(glob.glob(clip_glob))]
    cases = load_clip_cases(clip_paths)
    sampled = stratified_sample(cases, clip_count, seed)
    clip_records = clip_cases_to_records(sampled)

    manual_records = load_manual_records(manual_path)

    all_records = clip_records + manual_records
    write_eval_set(all_records, out_path)
    return len(clip_records), len(manual_records)


# ---- CLI ----


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s - %(message)s")

    parser = argparse.ArgumentParser(description="검색 평가셋 빌더")
    parser.add_argument(
        "--clip-glob",
        default=DEFAULT_CLIP_GLOB,
        help=f"CLIP 사례 JSONL glob (기본: {DEFAULT_CLIP_GLOB})",
    )
    parser.add_argument(
        "--manual",
        default=str(DEFAULT_MANUAL_PATH),
        help=f"수기 라벨 JSONL (기본: {DEFAULT_MANUAL_PATH})",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT_PATH),
        help=f"출력 경로 (기본: {DEFAULT_OUT_PATH})",
    )
    parser.add_argument("--clip-count", type=int, default=DEFAULT_CLIP_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)

    args = parser.parse_args()

    clip_n, manual_n = build_eval_set(
        clip_glob=args.clip_glob,
        manual_path=Path(args.manual),
        out_path=Path(args.out),
        clip_count=args.clip_count,
        seed=args.seed,
    )
    print(f"[EVAL-SET] clip={clip_n}, manual={manual_n}, total={clip_n + manual_n}")
    print(f"[OUT] {args.out}")
    if clip_n + manual_n == 0:
        print("[WARN] 레코드 0건. CLIP JSONL 캐시와 수기 라벨 경로를 확인하세요.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
