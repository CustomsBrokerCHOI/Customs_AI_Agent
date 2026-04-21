"""UNIPASS API 범용 탐사 하네스.

``UnipassClient`` 의 에러 탐지를 우회하고 원본 XML 을 ``data/cache/`` 에 덤프한다.
신규 서비스의 응답 구조와 필수 파라미터를 파악할 때 사용한다.

사용 예:
    python -m scripts.dev_probe trrfQry retrieveTrrfInfo --param hsSgn=8471300000
    python -m scripts.dev_probe hsSgnQry searchHsSgn --param hsSgn=8471300000 --param koenTp=1
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import requests
from dotenv import load_dotenv

from scripts.unipass_client import DEFAULT_BASE_URL, UnipassClient, UnipassError

BASE_URL = DEFAULT_BASE_URL
CACHE_DIR = Path("data") / "cache"


def pretty_xml(root: ET.Element) -> str:
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode")


def dump(root: ET.Element, label: str) -> Path:
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


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="UNIPASS API 범용 탐사 하네스")
    parser.add_argument("service", help="서비스명 (예: trrfQry)")
    parser.add_argument("operation", help="오퍼레이션명 (예: retrieveTrrfInfo)")
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="쿼리 파라미터. 여러 번 지정 가능",
    )
    args = parser.parse_args()

    load_dotenv()
    try:
        client = UnipassClient()
        api_key = client.get_api_key(args.service)
    except UnipassError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    params: dict[str, str] = {"crkyCn": api_key}
    for raw in args.param:
        if "=" not in raw:
            parser.error(f"--param 값은 KEY=VALUE 형식: {raw}")
        k, v = raw.split("=", 1)
        params[k] = v

    url = f"{BASE_URL}/{args.service}/{args.operation}"
    log_params = {k: v for k, v in params.items() if k != "crkyCn"}
    print(f"[CALL] {url} params={log_params}")

    try:
        response = requests.get(url, params=params, timeout=10)
    except requests.RequestException as exc:
        print(f"[NETWORK ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if response.status_code >= 400:
        print(f"[HTTP {response.status_code}] {url} (본문 {len(response.content)} bytes)")
        if response.content:
            print(f"[BODY PREVIEW] {response.text[:500]}")
        return 1

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        print(f"[PARSE ERROR] {exc}", file=sys.stderr)
        print(f"[RAW BYTES] {response.content[:500]!r}...")
        return 1

    label = f"{args.service}_{args.operation}".replace("/", "_")
    saved = dump(root, label)
    print(f"[SAVED] {saved}")

    print("\n[RAW XML DUMP]")
    print(pretty_xml(root))

    print("\n[UNIQUE TAGS]")
    for t in unique_tags(root):
        print(f"  - {t}")

    ntce = (root.findtext(".//ntceInfo") or "").strip()
    tcnt = (root.findtext(".//tCnt") or "").strip()
    if ntce:
        print(f"\n[NTCE INFO] {ntce}")
    if tcnt:
        print(f"[TOTAL COUNT] {tcnt}")

    print("\n[COMPLETE]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
