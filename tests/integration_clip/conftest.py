"""실 CLIP 포털 대상 통합 테스트 공용 설정.

## 언제 실행하는가
- **기본 CI 에서 제외**: `.github/workflows/ci.yml` 는 ``tests/integration_clip`` 를
  ``--ignore`` 로 건너뜀. 네트워크 + Playwright 브라우저 의존으로 느리고 site 변경에
  따라 flaky.
- **수동**: ``.github/workflows/clip-live.yml`` 의 ``workflow_dispatch`` 로 실행.
  로컬에서는 ``python -m pytest tests/integration_clip/ -v -m clip_live``.

## Skip 조건
1. Playwright 미설치 또는 Chromium 브라우저 바이너리 없음
2. CLIP 포털 TLS/HTTP 연결 실패
3. 명시적 opt-out 환경변수 ``SKIP_CLIP_LIVE=1``

위 중 하나라도 해당하면 모듈 전체 skip — 테스트 실패가 아니라 사전 스킵.

## 마커
- ``@pytest.mark.clip_live`` 를 모든 파일에서 사용. 기본 실행에서 명시적 제외 가능.
"""

from __future__ import annotations

import os
import socket
import ssl
from urllib.parse import urlparse

import pytest

from scripts.clip_scraper import DEFAULT_BASE_URL


def _probe_playwright() -> str | None:
    """Playwright + Chromium 사용 가능 여부. 실패 사유 문자열 반환, None 이면 OK."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError as exc:
        return f"playwright 미설치: {exc}"

    # Chromium 바이너리가 설치되어 있는지 가볍게 확인
    try:
        from playwright.sync_api import sync_playwright as _sp

        with _sp() as pw:
            browser = pw.chromium.launch(headless=True)
            browser.close()
    except Exception as exc:  # noqa: BLE001
        return f"Chromium 런치 실패 (playwright install chromium 필요?): {exc}"
    return None


def _probe_clip_reachable(base_url: str, timeout_sec: float = 5.0) -> str | None:
    """CLIP 기본 도메인에 TCP+TLS 핸드쉐이크만 시도. 실패 시 사유 반환."""
    parsed = urlparse(base_url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return f"잘못된 URL: {base_url}"
    try:
        with socket.create_connection((host, port), timeout=timeout_sec) as sock:
            if parsed.scheme == "https":
                ctx = ssl.create_default_context()
                with ctx.wrap_socket(sock, server_hostname=host):
                    pass
    except (OSError, ssl.SSLError) as exc:
        return f"{host}:{port} 연결 실패: {exc}"
    return None


@pytest.fixture(scope="session", autouse=True)
def _clip_live_preflight() -> None:
    """모듈 전체 사전 검사. 실패 사유가 있으면 session 단위 skip."""
    if os.environ.get("SKIP_CLIP_LIVE", "").strip() in ("1", "true", "yes"):
        pytest.skip("SKIP_CLIP_LIVE 환경변수로 제외됨", allow_module_level=True)

    err = _probe_playwright()
    if err:
        pytest.skip(err, allow_module_level=True)

    err = _probe_clip_reachable(DEFAULT_BASE_URL)
    if err:
        pytest.skip(err, allow_module_level=True)


@pytest.fixture
def scraper():
    """``ClipScraper`` 인스턴스. headless Chromium, rate-limit 0.5s (사이트 부담 최소)."""
    from scripts.clip_scraper import ClipScraper

    with ClipScraper(headless=True, rate_limit_sec=0.5) as sc:
        yield sc
