"""UNIPASS HS 부호 조회 실호출 검증 하네스.

실제 API 응답 XML 을 덤프하고, 현재 파서가 가정한 태그명이 맞는지 진단한다.
기본 HS 코드는 8471300000(자동자료처리기계, 노트북 계열).

사용 예::

    python -m scripts.dev_probe_hs
    python -m scripts.dev_probe_hs --hs-code 2203000000
"""

from __future__ import annotations

import argparse
import io
import sys
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from dotenv import load_dotenv

from scripts.unipass_client import UnipassClient, _configure_logging

ASSUMED_TAGS = ("hsSgn", "korePrnm", "englPrnm")
CACHE_DIR = Path("data") / "cache"


def pretty_xml(root: ET.Element) -> str:
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode")


def dump_xml(root: ET.Element, label: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = CACHE_DIR / f"probe_{label}_{ts}.xml"
    path.write_text(pretty_xml(root), encoding="utf-8")
    return path


def unique_tags(root: ET.Element) -> list[str]:
    seen: list[str] = []
    for el in root.iter():
        if el.tag not in seen:
            seen.append(el.tag)
    return seen


def diagnose(root: ET.Element) -> None:
    tags = unique_tags(root)

    print("\n[TAG DIAGNOSIS] 현재 파서가 가정한 태그:")
    for assumed in ASSUMED_TAGS:
        marker = "[OK]" if assumed in tags else "[MISS]"
        print(f"  {marker} {assumed}")

    print(f"\n[ALL TAGS IN RESPONSE] ({len(tags)}개)")
    for t in tags:
        print(f"  - {t}")

    rows = list(root.iterfind(".//hsSgnSrchRsltVo"))
    print(f"\n[PARSED ROWS] {len(rows)}건")
    for i, item in enumerate(rows[:3]):
        parsed = {
            "hs_code": item.findtext("hsSgn"),
            "name_kr": item.findtext("korePrnm"),
            "name_en": item.findtext("englPrnm"),
            "tax_rate": item.findtext("txrt"),
            "fta_code": item.findtext("txtpSgn"),
        }
        print(f"  [row {i}] {parsed}")
    if len(rows) > 3:
        print(f"  ... (+{len(rows) - 3}건 생략)")


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="UNIPASS HS 부호 조회 응답 검증 하네스")
    parser.add_argument("--hs-code", default="8471300000", help="조회할 HS 10자리 부호")
    parser.add_argument("--koen-tp", default="K", help="한영구분 (예: K=한글, E=영문)")
    parser.add_argument("--service", default="hsSgnQry")
    parser.add_argument("--operation", default="searchHsSgn")
    args = parser.parse_args()

    load_dotenv()
    _configure_logging()

    params = {"hsSgn": args.hs_code, "koenTp": args.koen_tp}
    client = UnipassClient()
    print(f"[CALL] {args.service}/{args.operation} params={params}")
    root = client.call(
        service_name=args.service,
        operation=args.operation,
        params=params,
    )

    saved = dump_xml(root, args.service)
    print(f"[SAVED] {saved}")

    print("\n[RAW XML DUMP]")
    print(pretty_xml(root))

    diagnose(root)

    print("\n[COMPLETE]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
