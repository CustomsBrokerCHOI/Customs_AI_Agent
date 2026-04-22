"""usage_report 집계 로직 단위 테스트 (DB/네트워크 없음)."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import pytest

from scripts.usage_report import (
    PRICE_INPUT_PER_1M_USD,
    PRICE_OUTPUT_PER_1M_USD,
    _resolve_range,
    aggregate,
    format_json,
    format_text,
    iter_usage_files,
    load_events,
)


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _event(**overrides) -> dict:
    base = {
        "timestamp": "2026-04-22T12:00:00+00:00",
        "job_id": "job-1",
        "user_id": "u-alice",
        "engine": "classify_engine_v1",
        "input_tokens": 100,
        "output_tokens": 50,
        "calls": 2,
        "stopped_at": None,
        "notice": None,
        "stages": {},
    }
    base.update(overrides)
    return base


# ---- iter_usage_files ----


def test_iter_usage_files_filters_by_range(tmp_path: Path) -> None:
    _write_jsonl(tmp_path / "20260420.jsonl", [_event()])
    _write_jsonl(tmp_path / "20260421.jsonl", [_event()])
    _write_jsonl(tmp_path / "20260422.jsonl", [_event()])
    _write_jsonl(tmp_path / "not-a-date.jsonl", [_event()])

    files = iter_usage_files(tmp_path, date(2026, 4, 21), date(2026, 4, 22))
    names = [f.name for f in files]
    assert names == ["20260421.jsonl", "20260422.jsonl"]


def test_iter_usage_files_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert iter_usage_files(tmp_path / "does-not-exist", date(2026, 4, 1), date(2026, 4, 22)) == []


# ---- load_events ----


def test_load_events_flattens_across_files(tmp_path: Path) -> None:
    _write_jsonl(tmp_path / "20260421.jsonl", [_event(job_id="a"), _event(job_id="b")])
    _write_jsonl(tmp_path / "20260422.jsonl", [_event(job_id="c")])

    events = load_events(tmp_path, date(2026, 4, 21), date(2026, 4, 22))
    assert [e["job_id"] for e in events] == ["a", "b", "c"]


def test_load_events_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "20260422.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "{ not valid",
                json.dumps(_event(job_id="ok")),
                "",  # 빈 라인
            ]
        ),
        encoding="utf-8",
    )
    events = load_events(tmp_path, date(2026, 4, 22), date(2026, 4, 22))
    assert len(events) == 1
    assert events[0]["job_id"] == "ok"


# ---- aggregate ----


def test_aggregate_empty_returns_zero_report() -> None:
    r = aggregate([], since=date(2026, 4, 1), until=date(2026, 4, 7))
    assert r.event_count == 0
    assert r.total_input_tokens == 0
    assert r.estimated_cost_usd == 0.0
    assert r.avg_input_tokens == 0.0
    assert r.completed_count == 0


def test_aggregate_counts_tokens_and_engines() -> None:
    events = [
        _event(engine="classify_engine_v1", input_tokens=100, output_tokens=50, calls=2),
        _event(engine="classify_engine_v1", input_tokens=200, output_tokens=80, calls=3),
        _event(engine="mock", input_tokens=0, output_tokens=0, calls=0),
    ]
    r = aggregate(events, since=date(2026, 4, 22), until=date(2026, 4, 22))

    assert r.event_count == 3
    assert r.total_input_tokens == 300
    assert r.total_output_tokens == 130
    assert r.total_calls == 5
    assert r.engines == {"classify_engine_v1": 2, "mock": 1}
    assert r.avg_input_tokens == pytest.approx(100.0)
    assert r.avg_output_tokens == pytest.approx(130 / 3)


def test_aggregate_cost_uses_sonnet_pricing() -> None:
    events = [_event(input_tokens=1_000_000, output_tokens=500_000)]
    r = aggregate(events, since=date(2026, 4, 22), until=date(2026, 4, 22))
    expected = (
        1_000_000 * PRICE_INPUT_PER_1M_USD / 1_000_000
        + 500_000 * PRICE_OUTPUT_PER_1M_USD / 1_000_000
    )
    assert r.estimated_cost_usd == pytest.approx(expected)


def test_aggregate_stopped_at_breakdown_and_completed() -> None:
    events = [
        _event(stopped_at=None),
        _event(stopped_at=None),
        _event(stopped_at="input_gate"),
        _event(stopped_at="deep_verify"),
    ]
    r = aggregate(events, since=date(2026, 4, 22), until=date(2026, 4, 22))
    assert r.stopped_at == {"(완료)": 2, "input_gate": 1, "deep_verify": 1}
    assert r.completed_count == 2


def test_aggregate_per_user_counts_only_present_user_ids() -> None:
    events = [
        _event(user_id="alice"),
        _event(user_id="alice"),
        _event(user_id="bob"),
        _event(user_id=None),
    ]
    r = aggregate(events, since=date(2026, 4, 22), until=date(2026, 4, 22))
    assert r.per_user == {"alice": 2, "bob": 1}


def test_aggregate_treats_null_numeric_fields_as_zero() -> None:
    events = [_event(input_tokens=None, output_tokens=None, calls=None)]
    r = aggregate(events, since=date(2026, 4, 22), until=date(2026, 4, 22))
    assert r.total_input_tokens == 0
    assert r.total_output_tokens == 0
    assert r.total_calls == 0


# ---- format_text / format_json ----


def test_format_text_empty_report_short_circuits() -> None:
    r = aggregate([], since=date(2026, 4, 1), until=date(2026, 4, 7))
    out = format_text(r)
    assert "기간" in out
    assert "이벤트가 없습니다" in out


def test_format_text_contains_key_sections() -> None:
    events = [
        _event(engine="classify_engine_v1"),
        _event(stopped_at="deep_verify"),
    ]
    r = aggregate(events, since=date(2026, 4, 22), until=date(2026, 4, 22))
    out = format_text(r)
    assert "총 분류 건수" in out
    assert "엔진 분포" in out
    assert "중단 단계 분포" in out
    assert "Sonnet 4.6" in out
    assert "상위 사용자" in out


def test_format_json_is_round_trippable() -> None:
    r = aggregate([_event()], since=date(2026, 4, 22), until=date(2026, 4, 22))
    doc = json.loads(format_json(r))
    assert doc["event_count"] == 1
    assert doc["since"] == "2026-04-22"
    assert doc["until"] == "2026-04-22"
    assert "estimated_cost_usd" in doc
    assert "completed_count" in doc


# ---- _resolve_range ----


def test_resolve_range_defaults_to_last_seven_days() -> None:
    args = argparse.Namespace(days=7, since=None, until=None)
    since, until = _resolve_range(args, today=date(2026, 4, 22))
    assert until == date(2026, 4, 22)
    assert since == date(2026, 4, 16)  # 7일 범위 (포함)


def test_resolve_range_since_takes_precedence_over_days() -> None:
    args = argparse.Namespace(days=30, since="2026-04-20", until="2026-04-22")
    since, until = _resolve_range(args, today=date(2026, 4, 22))
    assert (since, until) == (date(2026, 4, 20), date(2026, 4, 22))


def test_resolve_range_rejects_inverted_range() -> None:
    args = argparse.Namespace(days=7, since="2026-04-22", until="2026-04-20")
    with pytest.raises(SystemExit):
        _resolve_range(args, today=date(2026, 4, 22))
