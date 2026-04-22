"""분류 사용량 집계 CLI (Phase 5-C 모니터링 dashboard 의 MVP).

``api.services.llm_client.UsageLogger`` 가 ``data/usage/YYYYMMDD.jsonl`` 에 쌓은
이벤트를 읽어, 기간 내 분류 건수·중단 단계 분포·엔진별 분포·토큰·비용 추정을
표 형태로 출력한다.

사용 예::

    python -m scripts.usage_report                      # 최근 7일
    python -m scripts.usage_report --days 30
    python -m scripts.usage_report --since 2026-04-01 --until 2026-04-22
    python -m scripts.usage_report --format json
    python -m scripts.usage_report --usage-dir data/usage

.. note::
   비용 추정은 **Anthropic Claude Sonnet 4.6 단가 기준의 단일 가정**.
   실제 엔진은 Anthropic + OpenAI 임베딩을 함께 사용하지만 ``UsageEvent`` 는
   입·출력 토큰 단일 합산만 담기 때문에 대략치로만 참고한다.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_USAGE_DIR = Path("data/usage")

# Anthropic Claude Sonnet 4.6 공개 단가 (USD / 1M tokens) — 추정치.
# 실제 단가 변경 시 조정.
PRICE_INPUT_PER_1M_USD = 3.0
PRICE_OUTPUT_PER_1M_USD = 15.0


# ---- 데이터 모델 ----


@dataclass
class UsageReport:
    since: date
    until: date
    event_count: int = 0
    engines: dict[str, int] = field(default_factory=dict)
    stopped_at: dict[str, int] = field(default_factory=dict)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_calls: int = 0
    per_user: dict[str, int] = field(default_factory=dict)

    @property
    def completed_count(self) -> int:
        """``stopped_at`` 가 ``null`` 또는 누락(=완료) 인 이벤트 수."""
        return self.stopped_at.get("(완료)", 0)

    @property
    def avg_input_tokens(self) -> float:
        return self.total_input_tokens / self.event_count if self.event_count else 0.0

    @property
    def avg_output_tokens(self) -> float:
        return self.total_output_tokens / self.event_count if self.event_count else 0.0

    @property
    def estimated_cost_usd(self) -> float:
        """Sonnet 4.6 단가 기준 추정. 실제 모델 믹스 반영 안 됨."""
        return (
            self.total_input_tokens * PRICE_INPUT_PER_1M_USD / 1_000_000
            + self.total_output_tokens * PRICE_OUTPUT_PER_1M_USD / 1_000_000
        )

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["since"] = self.since.isoformat()
        d["until"] = self.until.isoformat()
        d["completed_count"] = self.completed_count
        d["avg_input_tokens"] = round(self.avg_input_tokens, 2)
        d["avg_output_tokens"] = round(self.avg_output_tokens, 2)
        d["estimated_cost_usd"] = round(self.estimated_cost_usd, 4)
        return d


# ---- 로딩 ----


def iter_usage_files(usage_dir: Path, since: date, until: date) -> list[Path]:
    """``YYYYMMDD.jsonl`` 파일 중 ``[since, until]`` 범위에 속하는 것만 반환."""
    if not usage_dir.is_dir():
        return []
    paths: list[Path] = []
    for p in sorted(usage_dir.glob("*.jsonl")):
        stem = p.stem
        if len(stem) != 8 or not stem.isdigit():
            continue
        try:
            d = datetime.strptime(stem, "%Y%m%d").date()
        except ValueError:
            continue
        if since <= d <= until:
            paths.append(p)
    return paths


def load_events(usage_dir: Path, since: date, until: date) -> list[dict[str, Any]]:
    """범위 내 JSONL 파일의 모든 이벤트를 평탄화해서 반환."""
    events: list[dict[str, Any]] = []
    for path in iter_usage_files(usage_dir, since, until):
        try:
            with path.open("r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning("JSONL 파싱 실패: %s (%s)", path.name, line[:80])
        except OSError as exc:
            logger.warning("파일 열기 실패: %s (%s)", path, exc)
    return events


# ---- 집계 ----


def _inc(d: dict[str, int], key: str) -> None:
    d[key] = d.get(key, 0) + 1


def aggregate(events: Iterable[dict[str, Any]], since: date, until: date) -> UsageReport:
    """이벤트 리스트를 ``UsageReport`` 로 집계."""
    report = UsageReport(since=since, until=until)
    for e in events:
        report.event_count += 1
        report.total_input_tokens += int(e.get("input_tokens") or 0)
        report.total_output_tokens += int(e.get("output_tokens") or 0)
        report.total_calls += int(e.get("calls") or 0)
        _inc(report.engines, str(e.get("engine") or "unknown"))
        _inc(report.stopped_at, e.get("stopped_at") or "(완료)")
        user = e.get("user_id")
        if user:
            _inc(report.per_user, str(user))
    return report


# ---- 렌더링 ----


def format_text(report: UsageReport, top_users: int = 5) -> str:
    lines: list[str] = []
    lines.append(f"[기간] {report.since} ~ {report.until}")
    lines.append(f"[총 분류 건수] {report.event_count}")
    if report.event_count == 0:
        lines.append("(해당 기간에 이벤트가 없습니다.)")
        return "\n".join(lines)

    completed = report.completed_count
    lines.append(f"[완료/중단] 완료 {completed}건, 중단 {report.event_count - completed}건")

    if report.stopped_at:
        lines.append("[중단 단계 분포]")
        for k, v in sorted(report.stopped_at.items(), key=lambda x: -x[1]):
            lines.append(f"  - {k}: {v}")

    if report.engines:
        lines.append("[엔진 분포]")
        for k, v in sorted(report.engines.items(), key=lambda x: -x[1]):
            lines.append(f"  - {k}: {v}")

    lines.append("[LLM 토큰]")
    lines.append(
        f"  - 입력 합계 {report.total_input_tokens:,} (평균 {report.avg_input_tokens:.0f})"
    )
    lines.append(
        f"  - 출력 합계 {report.total_output_tokens:,} (평균 {report.avg_output_tokens:.0f})"
    )
    lines.append(f"  - LLM 호출 수 {report.total_calls:,}")
    lines.append(
        f"  - 추정 비용 ${report.estimated_cost_usd:.4f} "
        f"(Sonnet 4.6 단가 기준, 실제 모델 믹스 미반영)"
    )

    if report.per_user:
        top = sorted(report.per_user.items(), key=lambda x: -x[1])[:top_users]
        lines.append(f"[상위 사용자 {len(top)}명]")
        for user_id, count in top:
            lines.append(f"  - {user_id}: {count}건")

    return "\n".join(lines)


def format_json(report: UsageReport) -> str:
    return json.dumps(report.as_dict(), ensure_ascii=False, indent=2)


# ---- CLI ----


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _resolve_range(args: argparse.Namespace, today: date | None = None) -> tuple[date, date]:
    today = today or datetime.now(timezone.utc).date()
    until = _parse_date(args.until) if args.until else today
    if args.since:
        since = _parse_date(args.since)
    else:
        since = until - timedelta(days=max(args.days - 1, 0))
    if since > until:
        raise SystemExit(f"[ERROR] since({since}) 가 until({until}) 보다 뒤입니다.")
    return since, until


def main(argv: list[str] | None = None) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="분류 사용량 집계 리포트")
    parser.add_argument("--days", type=int, default=7, help="최근 N 일 (default 7)")
    parser.add_argument("--since", help="시작일 YYYY-MM-DD (우선)")
    parser.add_argument("--until", help="종료일 YYYY-MM-DD (default 오늘 UTC)")
    parser.add_argument("--usage-dir", default=str(DEFAULT_USAGE_DIR))
    parser.add_argument("--format", choices=("text", "json"), default="text", help="출력 포맷")
    parser.add_argument("--top-users", type=int, default=5, help="텍스트 출력에서 상위 사용자 N 명")
    args = parser.parse_args(argv)

    since, until = _resolve_range(args)
    usage_dir = Path(args.usage_dir)
    events = load_events(usage_dir, since, until)
    report = aggregate(events, since=since, until=until)

    if args.format == "json":
        print(format_json(report))
    else:
        print(format_text(report, top_users=args.top_users))
    return 0


if __name__ == "__main__":
    sys.exit(main())
