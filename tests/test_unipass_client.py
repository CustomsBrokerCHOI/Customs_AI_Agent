"""UnipassClient 단위 테스트 (네트워크 호출 없음)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from scripts.unipass_client import UnipassClient, UnipassError


def test_init_raises_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(UnipassError, match="인증키"):
        UnipassClient()


def test_init_accepts_default_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIPASS_API_KEY", "default_key")
    client = UnipassClient()
    assert client.get_api_key("anyService") == "default_key"


def test_get_api_key_prefers_service_specific(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIPASS_API_KEY", "fallback")
    monkeypatch.setenv("UNIPASS_API_KEY_HSSGNQRY", "hs_specific")
    client = UnipassClient()
    assert client.get_api_key("hsSgnQry") == "hs_specific"
    assert client.get_api_key("otherQry") == "fallback"


def test_get_api_key_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIPASS_API_KEY_TRRTQRY", "trrt_key")
    client = UnipassClient()
    assert client.get_api_key("trrtQry") == "trrt_key"
    assert client.get_api_key("TRRTQRY") == "trrt_key"


def test_get_api_key_raises_when_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIPASS_API_KEY_HSSGNQRY", "only_hs")
    client = UnipassClient()
    with pytest.raises(UnipassError, match="unknownSvc"):
        client.get_api_key("unknownSvc")


def test_cache_path_is_deterministic(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("UNIPASS_API_KEY", "k")
    client = UnipassClient(cache_dir=tmp_path)
    p1 = client._cache_path("svc", "op", {"a": "1", "b": "2"})
    p2 = client._cache_path("svc", "op", {"b": "2", "a": "1"})
    p3 = client._cache_path("svc", "op", {"a": "1", "b": "3"})
    assert p1 == p2
    assert p1 != p3


def test_daily_limits_requires_usage_log_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNIPASS_API_KEY", "k")
    with pytest.raises(UnipassError, match="usage_log_dir"):
        UnipassClient(daily_limits={"svc": 10})


def test_usage_logging_and_limit_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("UNIPASS_API_KEY", "k")
    client = UnipassClient(
        usage_log_dir=tmp_path,
        daily_limits={"svcA": 2},
    )
    client._log_usage("svcA", cache_hit=False)
    client._log_usage("svcA", cache_hit=False)
    client._log_usage("svcA", cache_hit=True)  # 캐시 히트는 카운트 제외
    assert client.get_daily_usage("svcA") == {"svcA": 2}
    with pytest.raises(UnipassError, match="한도 초과"):
        client.check_daily_limit("svcA")


def test_unlimited_service_bypasses_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("UNIPASS_API_KEY", "k")
    client = UnipassClient(
        usage_log_dir=tmp_path,
        daily_limits={"svcA": 1},
    )
    client._log_usage("svcA", cache_hit=False)
    # svcB 는 한도가 설정 안 돼 있어 무한 호출 가능
    for _ in range(5):
        client._log_usage("svcB", cache_hit=False)
    client.check_daily_limit("svcB")  # 예외 없어야 함


def test_usage_log_format(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("UNIPASS_API_KEY", "k")
    client = UnipassClient(usage_log_dir=tmp_path)
    client._log_usage("svcA", cache_hit=False)
    today = date.today().strftime("%Y%m%d")
    log_file = tmp_path / f"{today}.jsonl"
    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["service"] == "svcA"
    assert entry["cache_hit"] is False
    assert "ts" in entry
