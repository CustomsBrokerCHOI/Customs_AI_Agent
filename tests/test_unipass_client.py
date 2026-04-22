"""UnipassClient 단위 테스트 (네트워크 호출 없음)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from unittest.mock import MagicMock

import pytest
import requests

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


def test_cache_path_is_deterministic(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
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


def test_usage_logging_and_limit_block(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
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


def test_unlimited_service_bypasses_check(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
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


# ---- call() 모킹 테스트 (HTTP 응답 주입) ----


@dataclass
class _FakeResponse:
    """requests.Response 최소 인터페이스 모사."""

    content: bytes
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error")


_SUCCESS_XML = (
    "<?xml version='1.0' encoding='UTF-8'?>"
    "<hsSgnSrchRtnVo>"
    "<tCnt>1</tCnt>"
    "<hsSgnSrchRsltVo>"
    "<hsSgn>8471300000</hsSgn>"
    "<korePrnm>휴대용 자동자료처리기기</korePrnm>"
    "</hsSgnSrchRsltVo>"
    "</hsSgnSrchRtnVo>"
).encode()


def _make_client_with_mock(
    monkeypatch: pytest.MonkeyPatch,
    response: _FakeResponse,
    **client_kwargs: object,
) -> tuple[UnipassClient, MagicMock]:
    """세션 mock 이 주어진 응답을 반환하는 UnipassClient 를 생성."""
    monkeypatch.setenv("UNIPASS_API_KEY", "test_key")
    client = UnipassClient(**client_kwargs)  # type: ignore[arg-type]
    mock_session = MagicMock()
    mock_session.get.return_value = response
    client.session = mock_session
    return client, mock_session


def test_call_parses_success_response_and_injects_auth_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    client, mock_session = _make_client_with_mock(
        monkeypatch,
        _FakeResponse(_SUCCESS_XML),
        usage_log_dir=tmp_path,
    )

    root = client.call("hsSgnQry", "searchHsSgn", {"hsSgn": "8471300000"})

    assert root.findtext(".//hsSgn") == "8471300000"
    mock_session.get.assert_called_once()
    call_kwargs = mock_session.get.call_args
    assert call_kwargs.args[0].endswith("/hsSgnQry/searchHsSgn")
    assert call_kwargs.kwargs["params"]["crkyCn"] == "test_key"
    assert call_kwargs.kwargs["params"]["hsSgn"] == "8471300000"


def test_call_raises_on_http_error(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, _ = _make_client_with_mock(
        monkeypatch,
        _FakeResponse(b"", status_code=503),
        usage_log_dir=tmp_path,
    )
    with pytest.raises(requests.HTTPError):
        client.call("svc", "op")


def test_call_raises_on_malformed_xml(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, _ = _make_client_with_mock(
        monkeypatch,
        _FakeResponse(b"<broken xml"),
        usage_log_dir=tmp_path,
    )
    with pytest.raises(UnipassError, match="XML 파싱 실패"):
        client.call("svc", "op")


def test_call_raises_on_business_error_with_err_msg(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    xml = ("<root><tCnt>-1</tCnt><errMsgCn>인증키 오류</errMsgCn></root>").encode()
    client, _ = _make_client_with_mock(monkeypatch, _FakeResponse(xml), usage_log_dir=tmp_path)
    with pytest.raises(UnipassError, match="인증키 오류"):
        client.call("svc", "op")


def test_call_falls_back_to_ntce_info_when_no_err_msg(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    xml = ("<root><tCnt>-2</tCnt><ntceInfo>조회 결과 없음</ntceInfo></root>").encode()
    client, _ = _make_client_with_mock(monkeypatch, _FakeResponse(xml), usage_log_dir=tmp_path)
    with pytest.raises(UnipassError, match="조회 결과 없음"):
        client.call("svc", "op")


def test_call_writes_cache_on_success(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    cache_dir = tmp_path / "cache"
    usage_dir = tmp_path / "usage"
    client, mock_session = _make_client_with_mock(
        monkeypatch,
        _FakeResponse(_SUCCESS_XML),
        cache_dir=cache_dir,
        usage_log_dir=usage_dir,
    )

    client.call("hsSgnQry", "searchHsSgn", {"hsSgn": "8471300000"})

    cache_files = list(cache_dir.glob("*.xml"))
    assert len(cache_files) == 1
    assert cache_files[0].read_bytes() == _SUCCESS_XML
    assert mock_session.get.call_count == 1


def test_call_uses_cache_on_second_call_and_logs_hit(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    cache_dir = tmp_path / "cache"
    usage_dir = tmp_path / "usage"
    client, mock_session = _make_client_with_mock(
        monkeypatch,
        _FakeResponse(_SUCCESS_XML),
        cache_dir=cache_dir,
        usage_log_dir=usage_dir,
    )

    client.call("hsSgnQry", "searchHsSgn", {"hsSgn": "8471300000"})
    client.call("hsSgnQry", "searchHsSgn", {"hsSgn": "8471300000"})

    # 두 번째는 캐시 히트 → session.get 호출 수 그대로 1
    assert mock_session.get.call_count == 1

    today = date.today().strftime("%Y%m%d")
    log_lines = (usage_dir / f"{today}.jsonl").read_text("utf-8").splitlines()
    entries = [json.loads(line) for line in log_lines]
    assert [e["cache_hit"] for e in entries] == [False, True]


def test_call_force_refresh_bypasses_cache(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    cache_dir = tmp_path / "cache"
    usage_dir = tmp_path / "usage"
    client, mock_session = _make_client_with_mock(
        monkeypatch,
        _FakeResponse(_SUCCESS_XML),
        cache_dir=cache_dir,
        usage_log_dir=usage_dir,
    )
    client.call("svc", "op", {"k": "v"})
    client.call("svc", "op", {"k": "v"}, force_refresh=True)
    assert mock_session.get.call_count == 2


def test_call_use_cache_false_skips_cache_read_and_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    cache_dir = tmp_path / "cache"
    usage_dir = tmp_path / "usage"
    client, _ = _make_client_with_mock(
        monkeypatch,
        _FakeResponse(_SUCCESS_XML),
        cache_dir=cache_dir,
        usage_log_dir=usage_dir,
    )
    client.call("svc", "op", {"k": "v"}, use_cache=False)
    assert list(cache_dir.glob("*.xml")) == []


def test_call_blocks_when_daily_limit_exceeded(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    usage_dir = tmp_path / "usage"
    client, mock_session = _make_client_with_mock(
        monkeypatch,
        _FakeResponse(_SUCCESS_XML),
        usage_log_dir=usage_dir,
        daily_limits={"svcA": 1},
    )
    client.call("svcA", "op")  # 1회차 성공
    with pytest.raises(UnipassError, match="한도 초과"):
        client.call("svcA", "op", {"nonce": "2"})  # 한도 초과 차단
    # 차단 시 HTTP 호출 없음 → 첫 호출만 카운트
    assert mock_session.get.call_count == 1
