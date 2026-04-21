"""UNIPASS ``search_hs_sgn`` 로 ``data/item_master.csv`` 빌드/갱신.

사용 예::

    python -m scripts.build_item_master 8471300000 2203000000
    python -m scripts.build_item_master --hs-codes-file hs_list.txt

각 HS 코드에 대해 국문/영문 두 번 호출하여 품명을 수집하고,
FTA 구분 ``A`` (기본세율) 행의 ``tax_rate`` 를 ``base_rate`` 로 사용한다.
결과는 ``DataManager.upsert_items`` 로 dedupe 업서트된다.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from dotenv import load_dotenv

from scripts.data_manager import DataManager
from scripts.unipass_client import UnipassClient, UnipassError, _configure_logging


def build_master_row(
    client: UnipassClient, hs_code: str
) -> dict[str, str | None] | None:
    """단일 HS 코드에 대한 master row 구성. 한/영 이중 호출."""
    kr_rows = client.search_hs_sgn(hs_code, koen_tp="1")
    en_rows = client.search_hs_sgn(hs_code, koen_tp="2")
    if not kr_rows and not en_rows:
        return None

    name_kr = kr_rows[0].get("name_kr") if kr_rows else None
    name_en = en_rows[0].get("name_en") if en_rows else None

    base_rate: str | None = None
    for row in kr_rows:
        if row.get("fta_code") == "A":
            base_rate = row.get("tax_rate")
            break

    return {
        "hs_code": hs_code,
        "name_kr": name_kr,
        "name_en": name_en,
        "base_rate": base_rate,
        "source": "UNIPASS",
    }


def load_hs_codes(args: argparse.Namespace) -> list[str]:
    codes: list[str] = list(args.hs_codes)
    if args.hs_codes_file:
        with open(args.hs_codes_file, encoding="utf-8") as f:
            for line in f:
                code = line.strip()
                if code and not code.startswith("#"):
                    codes.append(code)
    seen: set[str] = set()
    unique: list[str] = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="UNIPASS search_hs_sgn 로 item_master 빌드")
    parser.add_argument("hs_codes", nargs="*", help="HS 10자리 부호")
    parser.add_argument(
        "--hs-codes-file",
        help="HS 코드가 한 줄씩 담긴 파일 (# 주석 허용)",
    )
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--usage-log-dir", default="data/usage")
    parser.add_argument(
        "--master-path",
        default=str(DataManager.__dataclass_fields__["item_master_path"].default),
    )
    args = parser.parse_args()

    load_dotenv()
    _configure_logging()

    hs_codes = load_hs_codes(args)
    if not hs_codes:
        print("[ERROR] HS 코드를 지정하세요 (인자 또는 --hs-codes-file).", file=sys.stderr)
        return 2

    client = UnipassClient(cache_dir=args.cache_dir, usage_log_dir=args.usage_log_dir)
    manager = DataManager(item_master_path=Path(args.master_path))

    rows: list[dict[str, str | None]] = []
    for hs in hs_codes:
        try:
            row = build_master_row(client, hs)
        except UnipassError as exc:
            print(f"[FAIL] {hs}: {exc}")
            continue
        if row is None:
            print(f"[EMPTY] {hs}: UNIPASS 결과 없음")
            continue
        snippet = (row["name_kr"] or "")[:50]
        print(f"[OK] {hs}: base_rate={row['base_rate']!s:>4}  {snippet}")
        rows.append(row)

    if rows:
        changed = manager.upsert_items(rows)
        print(f"\n[SAVED] {manager.item_master_path}: {changed} 행 추가/갱신. {len(rows)} HS 처리.")
    else:
        print("\n[SKIPPED] 저장할 행 없음.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
