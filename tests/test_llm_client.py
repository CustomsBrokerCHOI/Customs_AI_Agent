"""Phase 3-F LLM 클라이언트 팩토리 + UsageLogger 단위 테스트 (네트워크 없음)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from api.services.llm_client import (
    DEFAULT_ANTHROPIC_TIMEOUT,
    DEFAULT_MAX_RETRIES,
    DEFAULT_OPENAI_TIMEOUT,
    UsageEvent,
    UsageLogger,
    make_anthropic_client,
    make_openai_async_client,
)

# ---- 팩토리 ----


def test_make_anthropic_client_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("api.services.llm_client.settings", _SettingsStub(anthropic=None))
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        make_anthropic_client()


def test_make_anthropic_client_sets_timeout_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("api.services.llm_client.settings", _SettingsStub(anthropic="sk-test"))
    client = make_anthropic_client(timeout=12.5, max_retries=5)
    assert getattr(client, "timeout", None) is not None
    # SDK 에 따라 timeout 은 float 또는 httpx.Timeout 이며 값 반영 확인
    assert float(getattr(client.timeout, "read", client.timeout)) == pytest.approx(12.5)
    assert getattr(client, "max_retries", None) == 5


def test_make_openai_async_client_requires_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("api.services.llm_client.settings", _SettingsStub(openai=None))
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        make_openai_async_client()


def test_make_openai_async_client_applies_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("api.services.llm_client.settings", _SettingsStub(openai="sk-test"))
    client = make_openai_async_client()
    # timeout 이 설정됨 (기본값)
    assert getattr(client, "timeout", None) is not None
    assert getattr(client, "max_retries", None) == DEFAULT_MAX_RETRIES


def test_default_timeouts_are_positive() -> None:
    assert DEFAULT_ANTHROPIC_TIMEOUT > 0
    assert DEFAULT_OPENAI_TIMEOUT > 0
    assert DEFAULT_MAX_RETRIES >= 1


# ---- UsageLogger ----


def test_usage_logger_writes_jsonl(tmp_path: Path) -> None:
    logger = UsageLogger(usage_dir=tmp_path)
    event = UsageEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        job_id="job-1",
        user_id="user-1",
        engine="real",
        input_tokens=120,
        output_tokens=40,
        calls=3,
    )
    out_path = logger.log(event)

    assert out_path.exists()
    lines = out_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["job_id"] == "job-1"
    assert payload["input_tokens"] == 120
    assert payload["engine"] == "real"


def test_usage_logger_appends_multiple(tmp_path: Path) -> None:
    logger = UsageLogger(usage_dir=tmp_path)
    for i in range(3):
        logger.log(
            UsageEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                job_id=f"j{i}",
                user_id="u",
                engine="real",
                input_tokens=i,
                output_tokens=i,
                calls=1,
            )
        )
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert [json.loads(ln)["job_id"] for ln in lines] == ["j0", "j1", "j2"]


def test_usage_logger_filename_format(tmp_path: Path) -> None:
    logger = UsageLogger(usage_dir=tmp_path)
    fixed = datetime(2026, 4, 21, 13, 0, 0, tzinfo=timezone.utc)
    path = logger.log(
        UsageEvent(
            timestamp=fixed.isoformat(),
            job_id="j",
            user_id="u",
            engine="real",
            input_tokens=0,
            output_tokens=0,
            calls=0,
        ),
        now=fixed,
    )
    assert path.name == "20260421.jsonl"


def test_usage_logger_accepts_dict_event(tmp_path: Path) -> None:
    logger = UsageLogger(usage_dir=tmp_path)
    path = logger.log({"job_id": "X", "engine": "stub", "raw": True})
    payload = json.loads(path.read_text(encoding="utf-8").strip())
    assert payload["job_id"] == "X"
    assert payload["engine"] == "stub"
    assert payload["raw"] is True


def test_usage_logger_log_job_extracts_meta(tmp_path: Path) -> None:
    logger = UsageLogger(usage_dir=tmp_path)
    meta = {
        "engine": "real",
        "draft": True,
        "usage": {"input_tokens": 500, "output_tokens": 200, "calls": 4},
        "stages": {"input_gate": {"confidence": 0.9}},
        "stopped_at": None,
    }
    path = logger.log_job(
        job_id="job-123",
        user_id="user-42",
        engine_result_meta=meta,
        notice="Draft.",
    )
    payload = json.loads(path.read_text(encoding="utf-8").strip())
    assert payload["job_id"] == "job-123"
    assert payload["user_id"] == "user-42"
    assert payload["input_tokens"] == 500
    assert payload["output_tokens"] == 200
    assert payload["calls"] == 4
    assert payload["engine"] == "real"
    assert payload["notice"] == "Draft."
    assert payload["stages"]["input_gate"]["confidence"] == 0.9


def test_usage_logger_log_job_handles_missing_usage(tmp_path: Path) -> None:
    logger = UsageLogger(usage_dir=tmp_path)
    meta = {"engine": "stub"}  # usage 키 없음
    path = logger.log_job(job_id="j", user_id="u", engine_result_meta=meta, notice=None)
    payload = json.loads(path.read_text(encoding="utf-8").strip())
    assert payload["input_tokens"] == 0
    assert payload["output_tokens"] == 0
    assert payload["calls"] == 0


def test_usage_logger_directory_property(tmp_path: Path) -> None:
    logger = UsageLogger(usage_dir=tmp_path)
    assert logger.directory == tmp_path


# ---- 헬퍼 ----


class _SettingsStub:
    """monkeypatch 용 가짜 settings."""

    def __init__(self, anthropic: str | None = "sk-ant", openai: str | None = "sk-oai"):
        self.anthropic_api_key = anthropic
        self.openai_api_key = openai
