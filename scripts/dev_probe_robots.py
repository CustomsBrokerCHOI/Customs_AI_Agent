"""robots.txt 프로브 — CLIP 포털.

``https://unipass.customs.go.kr/robots.txt`` 를 가져와
``data/raw/clip/robots/YYYYMMDD.txt`` 에 저장. 이전 저장본과 diff 를 출력.

분기마다 실행하고 변경이 있으면 ``docs/scraping-policy.md`` 업데이트.

사용 예::

    python -m scripts.dev_probe_robots
    python -m scripts.dev_probe_robots --url https://unipass.customs.go.kr/robots.txt

변경 감지 시 exit code 2 (CI 훅 가능).
"""

from __future__ import annotations

import argparse
import difflib
import io
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

DEFAULT_URL = "https://unipass.customs.go.kr/robots.txt"
DEFAULT_USER_AGENT = "CustomsAIAgent/0.1 (+contact: ops@example.com)"
DEFAULT_OUT_DIR = Path("data/raw/clip/robots")


def _latest_existing(out_dir: Path) -> Path | None:
    if not out_dir.exists():
        return None
    files = sorted(out_dir.glob("*.txt"))
    return files[-1] if files else None


def fetch_robots(url: str, user_agent: str, timeout: float = 10.0) -> str:
    resp = requests.get(url, headers={"User-Agent": user_agent}, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def probe(
    url: str = DEFAULT_URL,
    out_dir: Path = DEFAULT_OUT_DIR,
    user_agent: str = DEFAULT_USER_AGENT,
) -> tuple[Path, bool, list[str]]:
    """robots.txt 수집 + 저장 + 이전 버전과 비교.

    :returns: ``(saved_path, changed, diff_lines)``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    path = out_dir / f"{today}.txt"

    # 쓰기 **이전** 비교 대상: 오늘자 파일이 있으면 그 내용, 아니면 직전 날짜 파일.
    previous_path: Path | None
    previous_text: str | None
    if path.exists():
        previous_path = path
        previous_text = path.read_text(encoding="utf-8")
    else:
        # 오늘자 파일이 없으므로 가장 최근 과거 파일
        older = [p for p in sorted(out_dir.glob("*.txt"))]
        previous_path = older[-1] if older else None
        previous_text = (
            previous_path.read_text(encoding="utf-8") if previous_path else None
        )

    content = fetch_robots(url, user_agent)
    path.write_text(content, encoding="utf-8")

    if previous_text is None:
        return path, True, ["(최초 프로브 — 비교 대상 없음)"]

    if previous_text == content:
        return path, False, []

    diff = list(
        difflib.unified_diff(
            previous_text.splitlines(),
            content.splitlines(),
            fromfile=(previous_path.name if previous_path else "prev"),
            tofile=path.name,
            lineterm="",
        )
    )
    return path, True, diff


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s - %(message)s")

    parser = argparse.ArgumentParser(description="CLIP robots.txt 프로브")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    args = parser.parse_args()

    try:
        path, changed, diff = probe(
            url=args.url,
            out_dir=Path(args.out_dir),
            user_agent=args.user_agent,
        )
    except requests.RequestException as exc:
        print(f"[ERROR] robots.txt 요청 실패: {exc}", file=sys.stderr)
        return 1

    print(f"[SAVED] {path}")
    if changed:
        print("[CHANGED] 이전 프로브와 차이:")
        for line in diff:
            print(line)
        print(
            "\n→ docs/scraping-policy.md 의 '확인된 지침' 섹션을 갱신하세요."
        )
        return 2

    print("[NO-CHANGE] 이전 프로브와 동일.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
