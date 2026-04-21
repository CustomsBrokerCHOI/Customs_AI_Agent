"""ClipScraper 재시도·실패 manifest 단위 테스트 (Playwright 없이 동작).

``_retry_transient`` 및 ``_append_manifest`` 는 ClipScraper 의 인스턴스 메서드이므로
Playwright 런타임 없이 테스트하기 위해 ``__init__`` 을 우회하여 빈 인스턴스에
필요한 속성만 직접 세팅한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from scripts.clip_scraper import ClipScraper


def _make_bare(manifest_path: Path | None = None, rate_limit_sec: float = 0.0) -> ClipScraper:
    """Playwright 초기화 없이 테스트용 얕은 ClipScraper 인스턴스."""
    s = ClipScraper.__new__(ClipScraper)
    s.base_url = "http://local"
    s.user_agent = "test"
    s.timeout_ms = 1000
    s.rate_limit_sec = rate_limit_sec
    s.headless = True
    s.storage_state = None
    s.raw_html_dir = None
    s.manifest_path = manifest_path
    s._playwright = None
    s._browser = None
    s._context = None
    s._page = None
    s._last_request_at = 0.0
    return s


# ---- _retry_transient ----


def test_retry_succeeds_on_first_try(tmp_path) -> None:
    s = _make_bare(manifest_path=tmp_path / "m.jsonl")
    called = {"count": 0}

    def fn():
        called["count"] += 1
        return "ok"

    result = s._retry_transient(fn, kind="note")
    assert result == "ok"
    assert called["count"] == 1
    # 성공 시 manifest 에는 실패 이벤트가 기록되지 않음
    assert not (tmp_path / "m.jsonl").exists()


def test_retry_recovers_after_transient_failures(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("scripts.clip_scraper.time.sleep", lambda s: None)
    s = _make_bare(manifest_path=tmp_path / "m.jsonl")
    calls = {"count": 0}

    def fn():
        calls["count"] += 1
        if calls["count"] < 3:
            raise PlaywrightTimeoutError("transient")
        return "finally"

    result = s._retry_transient(fn, kind="note", max_attempts=3)
    assert result == "finally"
    assert calls["count"] == 3
    # 성공 경로 → 실패 manifest 없음
    assert not (tmp_path / "m.jsonl").exists()


def test_retry_raises_and_records_failure_after_exhaustion(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("scripts.clip_scraper.time.sleep", lambda s: None)
    manifest = tmp_path / "m.jsonl"
    s = _make_bare(manifest_path=manifest)

    def fn():
        raise PlaywrightTimeoutError("down")

    with pytest.raises(PlaywrightTimeoutError):
        s._retry_transient(
            fn,
            kind="explanatory_note",
            max_attempts=2,
            context={"heading": "8471", "year": "2022"},
        )

    assert manifest.exists()
    line = manifest.read_text(encoding="utf-8").strip()
    rec = json.loads(line)
    assert rec["event"] == "failure"
    assert rec["kind"] == "explanatory_note"
    assert rec["heading"] == "8471"
    assert rec["year"] == "2022"
    assert rec["attempts"] == 2
    assert "TimeoutError" in rec["error_type"] or rec["error_type"] == "TimeoutError"
    assert "down" in rec["error"]


def test_retry_does_not_retry_non_transient(tmp_path) -> None:
    """업무 예외(ClipScrapeError 등) 는 재시도 없이 그대로 전파."""
    from scripts.clip_scraper import ClipScrapeError

    s = _make_bare(manifest_path=tmp_path / "m.jsonl")
    calls = {"count": 0}

    def fn():
        calls["count"] += 1
        raise ClipScrapeError("업무 오류")

    with pytest.raises(ClipScrapeError):
        s._retry_transient(fn, kind="note", max_attempts=5)
    assert calls["count"] == 1  # 재시도 없음


def test_retry_sleep_uses_exponential_backoff(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실패 간격이 지수적으로 증가하는지 sleep 호출을 관측."""
    sleeps: list[float] = []
    monkeypatch.setattr(
        "scripts.clip_scraper.time.sleep", lambda s: sleeps.append(s)
    )
    s = _make_bare(manifest_path=tmp_path / "m.jsonl", rate_limit_sec=1.0)

    def fn():
        raise PlaywrightTimeoutError("still")

    with pytest.raises(PlaywrightTimeoutError):
        s._retry_transient(fn, kind="x", max_attempts=3, base_delay=2.0)

    # attempt 1 실패 → base_delay^0 * rate = 1
    # attempt 2 실패 → base_delay^1 * rate = 2
    # attempt 3 실패 → 추가 sleep 없이 raise
    assert sleeps == [1.0, 2.0]


# ---- _append_manifest ----


def test_append_manifest_defaults_to_success_event(tmp_path) -> None:
    manifest = tmp_path / "m.jsonl"
    s = _make_bare(manifest_path=manifest)
    s._append_manifest({"kind": "note", "heading": "8471"})

    rec = json.loads(manifest.read_text(encoding="utf-8").strip())
    assert rec["event"] == "success"
    assert rec["kind"] == "note"
    assert rec["heading"] == "8471"
    assert "fetched_at" in rec


def test_append_manifest_respects_explicit_event(tmp_path) -> None:
    manifest = tmp_path / "m.jsonl"
    s = _make_bare(manifest_path=manifest)
    s._append_manifest(
        {"event": "failure", "kind": "note", "error": "boom"}
    )
    rec = json.loads(manifest.read_text(encoding="utf-8").strip())
    assert rec["event"] == "failure"


def test_append_manifest_no_op_when_path_unset(tmp_path) -> None:
    s = _make_bare(manifest_path=None)
    s._append_manifest({"kind": "x"})  # 예외 없이 통과


def test_append_manifest_appends_multiple_lines(tmp_path) -> None:
    manifest = tmp_path / "m.jsonl"
    s = _make_bare(manifest_path=manifest)
    s._append_manifest({"kind": "note", "heading": "8471"})
    s._append_manifest({"event": "failure", "kind": "note", "heading": "9999"})
    lines = manifest.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    events = [json.loads(ln)["event"] for ln in lines]
    assert events == ["success", "failure"]


# ---- dev_probe_robots 모듈 함수 (간단) ----


def test_robots_probe_saves_and_detects_no_change(tmp_path, monkeypatch) -> None:
    from scripts import dev_probe_robots

    # fetch_robots 고정 반환
    monkeypatch.setattr(dev_probe_robots, "fetch_robots", lambda url, ua: "User-agent: *\nAllow: /\n")

    path1, changed1, _ = dev_probe_robots.probe(
        url="http://x", out_dir=tmp_path, user_agent="test"
    )
    assert path1.exists()
    assert changed1 is True  # 최초

    path2, changed2, diff = dev_probe_robots.probe(
        url="http://x", out_dir=tmp_path, user_agent="test"
    )
    # 같은 날 같은 내용 → 동일 파일 덮어쓰기, no change
    assert changed2 is False
    assert diff == []


def test_robots_probe_detects_change(tmp_path, monkeypatch) -> None:
    from scripts import dev_probe_robots

    # 기존 파일 미리 생성 (날짜 다른 파일)
    old = tmp_path / "20250101.txt"
    old.write_text("User-agent: *\nAllow: /\n", encoding="utf-8")

    monkeypatch.setattr(
        dev_probe_robots,
        "fetch_robots",
        lambda url, ua: "User-agent: *\nDisallow: /clip/\n",
    )
    path, changed, diff = dev_probe_robots.probe(
        url="http://x", out_dir=tmp_path, user_agent="test"
    )
    assert changed is True
    assert any("Disallow" in line for line in diff)
